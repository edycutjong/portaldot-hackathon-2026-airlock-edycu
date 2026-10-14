"""The vault operations behind the CLI: create, announce, execute, veto, panic (+ a read-only status).

Each operation reads the chain before it signs (is the hot key a delegate? is this hash pending? has the delay
passed?), so a call that cannot do what was asked is refused with a reason instead of being sent and failing, and
reads it again afterwards (is the announcement gone? is the delegate gone?), so a reported success is one the chain
confirms, not just an included extrinsic. Every extrinsic sent is kept as a row (extrinsic id, block hash, tx hash,
profile label) in the same shape as the spike's evidence, so a run can be re-fetched by id later.

  create   cold  proxy.create_pure(Any, 0, index)                        -> the vault; cold is its delay-0 delegate
           cold  balances.transfer_keep_alive(vault, fund)               (optional; add_proxy reserves a deposit)
           cold  proxy.proxy(vault, proxy.add_proxy(hot, Any, D))        -> hot is a DELAYED delegate
  announce hot   proxy.announce(vault, blake2_256(call))                 -> executable once D blocks have passed
  execute  hot   proxy.proxy_announced(hot, vault, None, call)           -> only after D, only if not vetoed
  veto     cold  proxy.proxy(vault, proxy.reject_announcement(hot, h))   -> the announced call can never execute
  panic    cold  proxy.proxy(vault, proxy.remove_proxy(hot, type, D))    -> the hot key is no longer a delegate
  withdraw hot   proxy.remove_announcement(vault, h)                     -> the hot key takes back its own announcement
"""

from __future__ import annotations

import math
import subprocess
import time
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any, Callable

from substrateinterface import Keypair

from .callhash import Announcement, call_bytes, call_hash, pending, same_account
from .chain_profile import Chain, at_risk, pure_address
from .dispatch import OK, error_namer, find_event, outcome

ROOT = Path(__file__).resolve().parent.parent
MAX_DELAY = 2**32 - 1  # the pallet's BlockNumber is a u32


class Refused(RuntimeError):
    """A pre-check failed: nothing was signed or sent."""


class TxFailed(RuntimeError):
    """An extrinsic was sent and its outcome is not the expected one (the row is kept as evidence)."""

    def __init__(self, row: dict):
        super().__init__(f"{row['step']}: expected {row['expected']}, got {row['outcome']} (extrinsic {row['extrinsic_id']})")
        self.row = row


class NotConfirmed(RuntimeError):
    """The extrinsic succeeded but the state read afterwards does not show its effect."""


class SubmitRejected(RuntimeError):
    """The node refused the extrinsic at submission (invalid, or dropped from the pool): it was not included."""


class VaultIncomplete(NotConfirmed):
    """create_pure succeeded (its deposit is spent) but a later setup step did not: the vault exists, unfinished."""

    def __init__(self, vault: str, cause: Exception):
        reason = str(cause) or type(cause).__name__
        super().__init__(f"vault {vault} was created but its setup may not have finished ({reason}); "
                         f"`airlock status --vault {vault}` shows its balance and delegates")
        self.vault = vault


@dataclass(frozen=True)
class Delegate:
    account: str
    proxy_type: str
    delay: int


@dataclass(frozen=True)
class Pending:
    """An announcement for a vault, with the delay that applies to it."""

    delegate: str
    call_hash: str
    height: int
    delay: int | None  # None: the delegate is no longer a proxy of the vault (panic) — it can never execute

    @property
    def release_block(self) -> int | None:
        """First block in which `proxy_announced` passes: the pallet requires now - height >= delay."""
        return None if self.delay is None else self.height + self.delay


def profile_tag(chain: Chain) -> str:
    return chain.profile.id + (" — not V3" if chain.profile.is_interim else "")


def code_version(root: Path = ROOT) -> str:
    """The commit that produced a run, marked dirty if the working tree differs from it."""
    try:
        sha = subprocess.run(["git", "-C", str(root), "rev-parse", "--short", "HEAD"], capture_output=True, text=True,
                             check=True).stdout.strip()
        dirty = subprocess.run(["git", "-C", str(root), "status", "--porcelain", "--", "airlock", "scripts"],
                               capture_output=True, text=True, check=True).stdout.strip()
        return sha + ("-dirty" if dirty else "")
    except (OSError, subprocess.CalledProcessError):
        return "unknown"


def call_name(call: Any) -> str:
    """`Proxy.proxy(Proxy.add_proxy)`: name a proxied call by what it wraps."""
    name = f"{call.call_module['name']}.{call.call_function['name']}"
    inner = call.value.get("call_args", {}).get("call") if isinstance(call.value, dict) else None
    inner = getattr(inner, "value", inner)
    if isinstance(inner, dict) and "call_module" in inner:
        name += f"({inner['call_module']}.{inner['call_function']})"
    return name


def _type_name(proxy_type: Any) -> str:
    if isinstance(proxy_type, dict) and len(proxy_type) == 1:
        return next(iter(proxy_type))
    return str(proxy_type)


def definitions(proxies_value: Any) -> list[Delegate]:
    """Every proxy definition in a Proxy.Proxies(vault) value ((definitions, deposit) or a bare list)."""
    if not proxies_value:
        return []
    defs = proxies_value[0] if (isinstance(proxies_value, (list, tuple)) and len(proxies_value) == 2
                                and isinstance(proxies_value[1], int)) else proxies_value
    out = []
    for d in defs:
        if isinstance(d, dict):
            out.append(Delegate(d["delegate"], _type_name(d["proxy_type"]), int(d["delay"])))
        else:
            out.append(Delegate(d[0], _type_name(d[1]), int(d[2])))
    return out


def delay_of(defs: list[Delegate], account: str) -> int | None:
    """The delay the pallet applies to `account`: that of its FIRST definition in storage order (`find_proxy`, and
    `announce`'s own lookup, take the first match when no proxy type is forced)."""
    return next((d.delay for d in defs if same_account(d.account, account)), None)


def blocks_for(seconds: float | str, block_time_ms: int | None) -> int:
    """A delay set in time, in whole blocks of this chain (rounded up exactly, at least 1)."""
    if not block_time_ms:
        raise Refused("this chain's block time is unknown: give the delay in blocks (--delay)")
    try:
        s = Decimal(str(seconds))
    except InvalidOperation as e:
        raise Refused(f"delay {seconds!r} is not a number of seconds") from e
    if not s.is_finite() or s <= 0:
        raise Refused(f"delay must be a positive, finite number of seconds, got {seconds!r}")
    blocks = max(1, math.ceil(s * 1000 / block_time_ms))
    if blocks > MAX_DELAY:
        raise Refused(f"{seconds} s is {blocks} blocks, beyond the pallet's u32 block numbers")
    return blocks


def matching(entries: list[Announcement], vault: str, h: str) -> list[Announcement]:
    """Every pending entry for this call hash on this vault (a delegate may announce the same hash twice)."""
    h = h.lower()
    return [a for a in entries if a.call_hash == h and same_account(a.real, vault)]


def is_pending(entries: list[Announcement], vault: str, h: str) -> Announcement | None:
    """The oldest pending entry for this hash on this vault (the first to mature), or None."""
    found = matching(entries, vault, h)
    return min(found, key=lambda a: a.height) if found else None


class Sender:
    """Signs, submits, waits for inclusion, judges the outcome (outer + proxied dispatch), keeps the row."""

    def __init__(self, chain: Chain, echo: Callable[[dict], None] | None = None):
        self.chain = chain
        self.s = chain.substrate
        self.name_error = error_namer(self.s.get_metadata().pallets)
        self.rows: list[dict] = []
        self.echo = echo

    def send(self, step: str, signer_name: str, signer: Keypair, call: Any, expect: str = OK, note: str = "") -> Any:
        from substrateinterface.exceptions import SubstrateRequestException

        xt = self.s.create_signed_extrinsic(call=call, keypair=signer)
        try:
            receipt = self.s.submit_extrinsic(xt, wait_for_inclusion=True)
        except SubstrateRequestException as e:  # only errors raised by the submission itself mean "not included"
            raise SubmitRejected(f"{step}: {e}") from e
        except ValueError as e:  # substrate-interface 1.8.1 reports a pool "dropped" status this way
            if str(e).startswith("Submit extrinsic failed"):
                raise SubmitRejected(f"{step}: {e}") from e
            raise
        got = outcome(receipt, self.name_error)
        row = {
            "step": step,
            "call": call_name(call),
            "signer": signer_name,
            "expected": expect,
            "outcome": got,
            "extrinsic_id": receipt.get_extrinsic_identifier(),
            "block_hash": receipt.block_hash,
            "block": self.s.get_block_number(receipt.block_hash),
            "extrinsic_hash": receipt.extrinsic_hash,
            "profile": profile_tag(self.chain),
            "note": note,
        }
        self.rows.append(row)
        if self.echo:
            self.echo(row)
        if got != expect:
            raise TxFailed(row)
        return receipt

    # reads -------------------------------------------------------------------------------------------------------

    def head(self) -> int:
        return self.s.get_block_number(None)

    def delegates(self, vault: str) -> list[Delegate]:
        return definitions(self.s.query("Proxy", "Proxies", [vault]).value)

    def free(self, who: str) -> int:
        return int(self.s.query("System", "Account", [who]).value["data"]["free"])

    def wait_until_block(self, target: int, on_tick: Callable[[int], None] | None = None) -> int:
        block_s = (self.chain.block_time_ms or 6000) / 1000
        deadline = time.monotonic() + max(target - self.head(), 1) * block_s * 5 + 30
        last = None
        while (n := self.head()) < target:
            if time.monotonic() > deadline:
                raise NotConfirmed(f"chain stalled at block #{n}, waiting for #{target}")
            if on_tick and n != last:
                on_tick(n)
                last = n
            time.sleep(max(block_s / 3, 0.5))
        return n


def as_vault(chain: Chain, vault: str, call: Any) -> Any:
    """The vault dispatches `call` through a delegate: proxy.proxy(vault, None, call)."""
    return chain.substrate.compose_call("Proxy", "proxy", {"real": vault, "force_proxy_type": None, "call": call})


def transfer(chain: Chain, to: str, planck: int) -> Any:
    return chain.substrate.compose_call("Balances", "transfer_keep_alive", {"dest": to, "value": planck})


def deposits(chain: Chain) -> dict[str, int]:
    """The Proxy pallet's deposit constants, read from the chain (never hard-coded)."""
    names = ("ProxyDepositBase", "ProxyDepositFactor", "AnnouncementDepositBase", "AnnouncementDepositFactor",
             "MaxPending", "MaxProxies")
    return {n: int(chain.substrate.get_constant("Proxy", n).value) for n in names}


# operations ---------------------------------------------------------------------------------------------------------


def create_minimums(tx: Sender, cold: Keypair, index: int = 0) -> tuple[int, int]:
    """(least the vault must be funded with, least the cold key must be able to spend) — from chain constants and a
    fee quote: the vault reserves the hot-key slot and keeps the existential deposit; cold pays the create deposit,
    the funding and three fees."""
    chain = tx.chain
    d = deposits(chain)
    ed = chain.existential_deposit
    vault_min = d["ProxyDepositFactor"] + ed
    create = tx.s.compose_call("Proxy", chain.profile.create_pure_call, {"proxy_type": "Any", "delay": 0, "index": index})
    fee = int(tx.s.get_payment_info(create, cold)["partialFee"])
    return vault_min, d["ProxyDepositBase"] + d["ProxyDepositFactor"] + ed + 3 * fee


def create_vault(tx: Sender, cold: Keypair, hot: str, delay: int, index: int = 0, fund: int | None = None,
                 label: str = "") -> str:
    """cold creates a pure vault (cold = its delay-0 delegate), funds it, and adds `hot` as an Any delegate with
    `delay` blocks. Refused before anything is sent if the funding or the cold key's balance cannot cover the
    deposits: an unfunded vault would cost the create deposit and fail at add_proxy. `label` prefixes the step names
    of the rows (e.g. "throwaway: ")."""
    chain, s, p = tx.chain, tx.s, tx.chain.profile
    if not 1 <= delay <= MAX_DELAY:
        raise Refused(f"delay must be 1..{MAX_DELAY} blocks: a delay-0 hot key is exactly what Airlock exists to avoid")
    if same_account(hot, cold.ss58_address):
        raise Refused("the hot key must not be the cold key")
    vault_min, cold_extra = create_minimums(tx, cold, index)
    if fund is None or fund < vault_min:
        raise Refused(f"fund the vault with at least {chain.fmt(vault_min)} (--fund): it reserves the hot-key deposit "
                      f"and must keep the existential deposit")
    spendable = chain.at_risk(cold.ss58_address).transferable
    if spendable < fund + cold_extra:
        raise Refused(f"the cold key can spend {chain.fmt(spendable)}; creating and funding this vault needs "
                      f"{chain.fmt(fund + cold_extra)} (funding + create deposit + fees)")
    r = tx.send(f"{label}create vault", "cold", cold,
                s.compose_call("Proxy", p.create_pure_call, {"proxy_type": "Any", "delay": 0, "index": index}))
    attrs = find_event(r, "Proxy", p.pure_created_event)
    if attrs is None:
        raise NotConfirmed(f"create vault succeeded but emitted no Proxy.{p.pure_created_event}")
    vault = pure_address(p, attrs)
    try:
        tx.send(f"{label}fund vault", "cold", cold, transfer(chain, vault, fund), note=chain.fmt(fund))
        add = s.compose_call("Proxy", "add_proxy", {"delegate": hot, "proxy_type": "Any", "delay": delay})
        tx.send(f"{label}add delayed hot key", "cold", cold, as_vault(chain, vault, add), note=f"delay {delay} blocks")
        defs = tx.delegates(vault)
        if delay_of(defs, hot) != delay or delay_of(defs, cold.ss58_address) != 0:
            raise NotConfirmed(f"Proxy.Proxies should hold hot with delay {delay} and cold with delay 0: {defs}")
    except (Exception, KeyboardInterrupt) as e:  # whatever stops the setup, the user must learn the vault's address
        raise VaultIncomplete(vault, e) from e
    return vault


def keep_alive_limit(chain: Chain, address: str) -> int:
    """A conservative bound on what a `transfer_keep_alive` out of `address` can move: free − max(locks − reserved,
    existential deposit), whatever the account's reference counts. Where reserved funds already keep the account alive
    (the 1002 runtime) the chain may allow up to one ED more; refusing that margin only costs a re-announce."""
    account = chain.substrate.query("System", "Account", [address]).value
    return at_risk(account["data"], chain.existential_deposit, must_stay_alive=True).transferable


def announce(tx: Sender, hot: Keypair, vault: str, call: Any, step: str = "announce",
             register: Callable[[bytes, str], Any] | None = None) -> Pending:
    """hot announces `call` for the vault. Refused if hot is not a delayed delegate of the vault.

    `register(call_bytes, call_hash)`: register-before-announce — called after the pre-checks and BEFORE anything is
    signed; if it raises, nothing is sent (an owner spend the registry does not know would alert NOT YOURS)."""
    delay = delay_of(tx.delegates(vault), hot.ss58_address)
    if delay is None:
        raise Refused(f"{hot.ss58_address} is not a delegate of {vault}: the announcement would fail Proxy.NotProxy")
    h = call_hash(call)
    if matching(pending(tx.s, hot.ss58_address), vault, h):
        raise Refused(f"an identical call ({h}) is already pending on {vault}: one execution clears every mature copy, "
                      f"so a second identical spend would be lost — execute the first (or veto it), or change the amount")
    if register is not None:
        register(call_bytes(call), h)
    r = tx.send(step, "hot", hot, tx.s.compose_call("Proxy", "announce", {"real": vault, "call_hash": h}),
                note=f"call hash {h}")
    included = tx.s.get_block_number(r.block_hash)
    if not any(a.height == included for a in matching(pending(tx.s, hot.ss58_address), vault, h)):
        raise NotConfirmed(f"I3: {h} is not in Proxy.Announcements({hot.ss58_address}) at the inclusion block "
                           f"#{included}")
    return Pending(hot.ss58_address, h, included, delay)


def find_pending(tx: Sender, vault: str, delegate: str, h: str) -> Pending | None:
    entry = is_pending(pending(tx.s, delegate), vault, h)
    if entry is None:
        return None
    return Pending(delegate, entry.call_hash, entry.height, delay_of(tx.delegates(vault), delegate))


def pending_for(tx: Sender, vault: str) -> list[Pending]:
    """Every pending announcement against the vault, by each of its current delegates."""
    defs = tx.delegates(vault)
    out = []
    for account in {d.account for d in defs}:
        for a in pending(tx.s, account):
            if same_account(a.real, vault):
                out.append(Pending(account, a.call_hash, a.height, delay_of(defs, account)))
    return sorted(out, key=lambda p: (p.height, p.call_hash))


def execute(tx: Sender, hot: Keypair, vault: str, call: Any, wait: bool = True,
            on_tick: Callable[[int], None] | None = None, step: str = "execute", needs: int | None = None) -> Any:
    """hot executes an announced call once its delay has passed (waits for it unless `wait` is False). `needs`: the
    amount the call moves out of the vault, checked first — a call that fails inside proxy_announced still uses up
    its announcement."""
    h = call_hash(call)
    p = find_pending(tx, vault, hot.ss58_address, h)
    if p is None:
        raise Refused(f"{h} is not pending for {hot.ss58_address} on {vault}: never announced, already executed, "
                      f"or vetoed — proxy_announced would fail Proxy.Unannounced")
    if p.delay is None:
        raise Refused(f"{hot.ss58_address} is no longer a delegate of {vault} (panic?): it can never execute")
    def can_pay() -> None:
        if needs is None:
            return
        can = keep_alive_limit(tx.chain, vault)
        if can < needs:
            raise Refused(f"the vault can move {tx.chain.fmt(can)} with transfer_keep_alive, the call moves "
                          f"{tx.chain.fmt(needs)}: it would fail inside proxy_announced and use up the announcement")

    can_pay()
    head = tx.head()
    # the extrinsic lands in a block after the head; waiting for head >= release keeps one block of margin
    if head < p.release_block:
        if not wait:
            raise Refused(f"announced at #{p.height} with delay {p.delay}: executable from #{p.release_block}, head is "
                          f"#{head} — not sending now (it could land before #{p.release_block} and fail "
                          f"Proxy.Unannounced)")
        tx.wait_until_block(p.release_block, on_tick)
        can_pay()  # the balance may have changed during the wait
    executed = tx.s.compose_call("Proxy", "proxy_announced",
                                 {"delegate": hot.ss58_address, "real": vault, "force_proxy_type": None, "call": call})
    r = tx.send(step, "hot", hot, executed, note=f"call hash {h}, announced #{p.height}")
    # the pallet removes every MATURE copy of the hash; an identical later announcement may legitimately remain
    at = tx.s.get_block_number(r.block_hash)
    if any(at - a.height >= p.delay for a in matching(pending(tx.s, hot.ss58_address), vault, h)):
        raise NotConfirmed(f"{h} is still pending (mature) after proxy_announced")
    return r


def veto(tx: Sender, cold: Keypair, vault: str, delegate: str, h: str, step: str = "veto") -> Any:
    """cold, as the vault, rejects a pending announcement: it can then never execute (I2)."""
    h = h.lower()
    if not (h.startswith("0x") and len(h) == 66):
        raise Refused(f"call hash must be 0x + 64 hex characters, got {h!r}")
    if delay_of(tx.delegates(vault), cold.ss58_address) != 0:
        raise Refused(f"{cold.ss58_address} is not a delay-0 delegate of {vault}: it cannot veto as the vault")
    p = find_pending(tx, vault, delegate, h)
    if p is None:
        raise Refused(f"{h} is not pending for delegate {delegate} on {vault}: nothing to veto "
                      f"(already vetoed, executed, withdrawn, or never announced)")
    reject = tx.s.compose_call("Proxy", "reject_announcement", {"delegate": delegate, "call_hash": h})
    r = tx.send(step, "cold", cold, as_vault(tx.chain, vault, reject), note=f"call hash {h}, announced #{p.height}")
    if find_pending(tx, vault, delegate, h) is not None:
        raise NotConfirmed(f"{h} is still pending after reject_announcement")
    return r


def panic(tx: Sender, cold: Keypair, vault: str, delegate: str, step: str = "panic") -> list[Any]:
    """cold, as the vault, removes every proxy definition of `delegate`: that key can no longer act for the vault,
    and anything it announced can never execute (proxy_announced then fails Proxy.NotProxy)."""
    if same_account(delegate, cold.ss58_address):
        raise Refused("refusing to remove the cold key itself: that would leave the vault with no instant delegate")
    defs = tx.delegates(vault)
    if delay_of(defs, cold.ss58_address) != 0:
        raise Refused(f"{cold.ss58_address} is not a delay-0 delegate of {vault}: it cannot act as the vault")
    mine = [d for d in defs if same_account(d.account, delegate)]
    if not mine:
        raise Refused(f"{delegate} is not a delegate of {vault}: nothing to remove")
    receipts = []
    for d in mine:
        remove = tx.s.compose_call("Proxy", "remove_proxy",
                                   {"delegate": delegate, "proxy_type": d.proxy_type, "delay": d.delay})
        receipts.append(tx.send(step, "cold", cold, as_vault(tx.chain, vault, remove),
                                note=f"{d.proxy_type}, delay {d.delay}"))
    if delay_of(tx.delegates(vault), delegate) is not None:
        raise NotConfirmed(f"{delegate} is still a delegate of {vault} after remove_proxy")
    return receipts


def withdraw(tx: Sender, hot: Keypair, vault: str, h: str, step: str = "withdraw") -> Any:
    """hot takes back its own pending announcement (and its deposit)."""
    if find_pending(tx, vault, hot.ss58_address, h) is None:
        raise Refused(f"{h} is not pending for {hot.ss58_address} on {vault}")
    r = tx.send(step, "hot", hot, tx.s.compose_call("Proxy", "remove_announcement", {"real": vault, "call_hash": h}),
                note=f"call hash {h}")
    if find_pending(tx, vault, hot.ss58_address, h) is not None:
        raise NotConfirmed(f"{h} is still pending after remove_announcement")
    return r
