"""Vault operations against a fake chain: the pre-checks that refuse before signing, and the read-backs that refuse to
call an included extrinsic a success when the state does not show it."""

from types import SimpleNamespace

import pytest

from airlock.callhash import Announcement, call_hash
from airlock.chain_profile import INTERIM_SDK, Chain
from airlock.ops import (NotConfirmed, Pending, Refused, Sender, TxFailed, VaultIncomplete, announce, blocks_for,
                         create_vault, definitions, delay_of, execute, find_pending, is_pending, panic, pending_for,
                         veto, withdraw)
from substrateinterface import Keypair

COLD = Keypair.create_from_uri("//Alice")
HOT = Keypair.create_from_uri("//Bob")
VAULT = "5Hb37SqmVJSzREN7ziBw1ZQJWLgDS7DLaEjKisf8a6KUYsLv"
HOT_HEX = "0x" + HOT.public_key.hex()
NEW_VAULT = "5EYCAe5ijiYfyeZ2JM3THnX8Wxo1XxR1JQ7y3tsk7m5uNvEu"  # any address: the fake's create_pure result
H = "0x" + "ab" * 32


class FakeCall:
    def __init__(self, module, function, params):
        self.call_module = {"name": module}
        self.call_function = {"name": function}
        self.value = {"call_module": module, "call_function": function, "call_args": params}
        self.params = params
        self.data = SimpleNamespace(data=f"{module}.{function}:{sorted(params.items())}".encode())

    def __repr__(self):  # deterministic, so a nested call's bytes depend on its content only
        return self.data.data.decode()

    def get_used_bytes(self):
        return self.data.data


class FakeSubstrate:
    """Proxies + Announcements in memory; each submitted extrinsic applies its effect (or a scripted failure)."""

    def __init__(self, head=100):
        self.head = head
        self.proxies = {VAULT: [{"delegate": HOT.ss58_address, "proxy_type": "Any", "delay": 5},
                                {"delegate": COLD.ss58_address, "proxy_type": "Any", "delay": 0}]}
        self.announcements: dict[str, list[dict]] = {}
        self.sent: list[FakeCall] = []
        self.fail_next: str | None = None
        self.fail_on: tuple[str, str] | None = None  # (function, error): fail the next extrinsic whose call is this
        self.ignore_effect = False
        self.free = {COLD.ss58_address: 10_000, VAULT: 1_000}
        self.constants = {"ProxyDepositBase": 6, "ProxyDepositFactor": 20, "AnnouncementDepositBase": 6,
                          "AnnouncementDepositFactor": 40, "MaxPending": 32, "MaxProxies": 32}

    def get_metadata(self):
        return SimpleNamespace(pallets=[])

    def get_constant(self, pallet, name):
        return SimpleNamespace(value=self.constants[name])

    def get_payment_info(self, call, keypair):
        return {"partialFee": 1}

    def query(self, module, storage, params, block_hash=None):
        if (module, storage) == ("Proxy", "Proxies"):
            return SimpleNamespace(value=(self.proxies.get(params[0], []), 0))
        if (module, storage) == ("Proxy", "Announcements"):
            return SimpleNamespace(value=(self.announcements.get(params[0], []), 0))
        if (module, storage) == ("System", "Account"):
            data = {"free": self.free.get(params[0], 0), "reserved": 0, "frozen": 0, "flags": 0}
            return SimpleNamespace(value={"data": data, "consumers": 0, "providers": 1})
        raise KeyError((module, storage))

    def get_block_number(self, block_hash):
        return self.head

    def compose_call(self, module, function, params):
        return FakeCall(module, function, params)

    def create_signed_extrinsic(self, call, keypair):
        return (call, keypair)

    def submit_extrinsic(self, xt, wait_for_inclusion):
        call, keypair = xt
        self.sent.append(call)
        self.head += 1
        failed = self.fail_next
        self.fail_next = None
        inner = call.params.get("call") if call.call_function["name"] == "proxy" else None
        if self.fail_on and self.fail_on[0] in (call.call_function["name"], inner and inner.call_function["name"]):
            failed, self.fail_on = self.fail_on[1], None
        if not failed and not self.ignore_effect:
            failed = self._apply(call, keypair)
        events = []
        if getattr(self, "pure_event", False):
            self.pure_event = False
            events.append(SimpleNamespace(value={"module_id": "Proxy", "event_id": "PureCreated",
                                                 "attributes": {"pure": NEW_VAULT, "who": keypair.ss58_address}}))
        if failed:
            events.append(SimpleNamespace(value={"module_id": "System", "event_id": "ExtrinsicFailed",
                                                 "attributes": {"dispatch_error": {"Other": failed}}}))
        return SimpleNamespace(is_success=not failed, triggered_events=events, block_hash=f"0xblock{self.head}",
                               extrinsic_hash="0x" + "ee" * 32, get_extrinsic_identifier=lambda: f"{self.head}-1")

    def _apply(self, call, keypair):
        """Apply the effect; return an error name when the pallet would refuse (like the real maturity rule)."""
        name = (call.call_module["name"], call.call_function["name"])
        if name == ("Proxy", "create_pure"):
            self.created = getattr(self, "created", 0) + 1
            self.proxies[NEW_VAULT] = [{"delegate": keypair.ss58_address, "proxy_type": "Any", "delay": 0}]
            self.pure_event = True
        elif name == ("Proxy", "announce"):
            self.announcements.setdefault(keypair.ss58_address, []).append(
                {"real": call.params["real"], "call_hash": call.params["call_hash"], "height": self.head})
        elif name == ("Proxy", "proxy"):
            inner = call.params["call"]
            if inner.call_function["name"] == "reject_announcement":
                d = inner.params["delegate"]
                self.announcements[d] = [a for a in self.announcements.get(d, [])
                                         if a["call_hash"] != inner.params["call_hash"]]
            elif inner.call_function["name"] == "remove_proxy":
                real = call.params["real"]
                self.proxies[real] = [p for p in self.proxies[real] if p["delegate"] != inner.params["delegate"]]
            elif inner.call_function["name"] == "add_proxy":
                self.proxies[call.params["real"]].append({k: inner.params[k] for k in ("delegate", "proxy_type",
                                                                                      "delay")})
        elif name == ("Proxy", "proxy_announced"):
            # the pallet's rule: remove every MATURE entry with this hash for this real; none mature -> Unannounced
            h, me = call_hash(call.params["call"]), keypair.ss58_address
            delay = next(p["delay"] for p in self.proxies[call.params["real"]] if p["delegate"] == me)
            keep = [a for a in self.announcements.get(me, [])
                    if not (a["call_hash"] == h and self.head - a["height"] >= delay)]
            if len(keep) == len(self.announcements.get(me, [])):
                return "Unannounced"
            self.announcements[me] = keep
        elif name == ("Proxy", "remove_announcement"):
            h = call.params["call_hash"]
            self.announcements[keypair.ss58_address] = [a for a in self.announcements.get(keypair.ss58_address, [])
                                                        if a["call_hash"] != h]
        elif name == ("Balances", "transfer_keep_alive"):
            self.free[call.params["dest"]] = self.free.get(call.params["dest"], 0) + call.params["value"]
        return None


def make(head=100):
    s = FakeSubstrate(head)
    chain = Chain(substrate=s, profile=INTERIM_SDK, url="ws://fake", genesis_hash=INTERIM_SDK.genesis_hash,
                  code_hash=INTERIM_SDK.code_hashes[0], spec_name="node", spec_version=268, decimals=12, symbol="UNIT",
                  block_time_ms=3000, block_time_source="test", has_revive=True, existential_deposit=10)
    return s, Sender(chain)


def spend(s):
    return s.compose_call("Balances", "transfer_keep_alive", {"dest": COLD.ss58_address, "value": 5})


# pure helpers -------------------------------------------------------------------------------------------------------

def test_definitions_all_shapes():
    named = ([{"delegate": "a", "proxy_type": "Any", "delay": 3}], 7)
    positional = [("b", {"Any": None}, 0)]
    assert definitions(named) == definitions(named[0]) and definitions(named)[0].delay == 3
    assert definitions(positional)[0].proxy_type == "Any"
    assert definitions(None) == [] and definitions(([], 0)) == []


def test_delay_of_is_the_first_definition_like_the_pallet():
    """find_proxy / announce take the FIRST definition for the delegate in storage order, not the shortest."""
    defs = definitions([{"delegate": HOT_HEX, "proxy_type": "Any", "delay": 9},
                        {"delegate": HOT.ss58_address, "proxy_type": "Transfer", "delay": 4}])
    assert delay_of(defs, HOT.ss58_address) == 9
    assert delay_of(defs, COLD.ss58_address) is None


def test_blocks_for_rounds_up_exactly():
    assert blocks_for(60, 3000) == 20
    assert blocks_for(61, 3000) == 21
    assert blocks_for(0.1, 6000) == 1
    assert blocks_for("0.57", 570) == 1  # 0.57 * 1000 in floats is 569.99…; exact decimal arithmetic gives 1
    assert blocks_for("0.571", 570) == 2
    with pytest.raises(Refused, match="block time is unknown"):
        blocks_for(60, None)
    for bad in ("nan", "inf", "0", "-5", "soon"):
        with pytest.raises(Refused):
            blocks_for(bad, 3000)


def test_release_block_and_dead_announcement():
    assert Pending(HOT.ss58_address, H, 100, 5).release_block == 105
    assert Pending(HOT.ss58_address, H, 100, None).release_block is None


def test_is_pending_needs_hash_and_vault():
    entries = [Announcement(real=VAULT, call_hash=H, height=1)]
    assert is_pending(entries, VAULT, H.upper().replace("0X", "0x"))
    assert is_pending(entries, COLD.ss58_address, H) is None
    assert is_pending(entries, VAULT, "0x" + "cd" * 32) is None


# announce / execute -------------------------------------------------------------------------------------------------

def test_announce_records_height_and_release():
    s, tx = make()
    p = announce(tx, HOT, VAULT, spend(s))
    assert (p.height, p.delay, p.release_block) == (101, 5, 106)
    assert tx.rows[0]["call"] == "Proxy.announce" and tx.rows[0]["profile"] == "interim-sdk — not V3"


def test_announce_refused_for_a_non_delegate():
    s, tx = make()
    with pytest.raises(Refused, match="NotProxy"):
        announce(tx, Keypair.create_from_uri("//Eve"), VAULT, spend(s))
    assert s.sent == []


def test_announce_not_confirmed_when_storage_lacks_it():
    s, tx = make()
    s.ignore_effect = True
    with pytest.raises(NotConfirmed, match="I3"):
        announce(tx, HOT, VAULT, spend(s))


def test_execute_refused_before_delay_without_wait():
    s, tx = make()
    announce(tx, HOT, VAULT, spend(s))
    with pytest.raises(Refused, match="executable from #106"):
        execute(tx, HOT, VAULT, spend(s), wait=False)
    assert len(s.sent) == 1  # only the announce


def test_execute_after_delay_clears_the_announcement():
    s, tx = make()
    announce(tx, HOT, VAULT, spend(s))
    s.head = 106
    execute(tx, HOT, VAULT, spend(s), wait=False)
    assert tx.rows[-1]["call"] == "Proxy.proxy_announced(Balances.transfer_keep_alive)" and not find_pending(tx, VAULT, HOT.ss58_address,
                                                                                 call_hash(spend(s)))


def test_execute_refused_when_not_pending():
    s, tx = make()
    with pytest.raises(Refused, match="Unannounced"):
        execute(tx, HOT, VAULT, spend(s), wait=False)


def test_execute_refused_after_panic():
    s, tx = make()
    announce(tx, HOT, VAULT, spend(s))
    panic(tx, COLD, VAULT, HOT.ss58_address)
    s.head = 200
    with pytest.raises(Refused, match="no longer a delegate"):
        execute(tx, HOT, VAULT, spend(s), wait=False)


# veto / panic / withdraw --------------------------------------------------------------------------------------------

def test_veto_removes_the_announcement():
    s, tx = make()
    p = announce(tx, HOT, VAULT, spend(s))
    veto(tx, COLD, VAULT, HOT.ss58_address, p.call_hash.upper().replace("0X", "0x"))
    assert tx.rows[-1]["call"] == "Proxy.proxy(Proxy.reject_announcement)"
    assert find_pending(tx, VAULT, HOT.ss58_address, p.call_hash) is None


def test_veto_refusals():
    s, tx = make()
    with pytest.raises(Refused, match="64 hex"):
        veto(tx, COLD, VAULT, HOT.ss58_address, "0x1234")
    with pytest.raises(Refused, match="nothing to veto"):
        veto(tx, COLD, VAULT, HOT.ss58_address, H)
    with pytest.raises(Refused, match="not a delay-0 delegate"):
        veto(tx, HOT, VAULT, HOT.ss58_address, H)  # the delayed key cannot veto as the vault
    assert s.sent == []


def test_veto_failed_on_chain_is_reported_with_the_row():
    s, tx = make()
    p = announce(tx, HOT, VAULT, spend(s))
    s.fail_next = "BadOrigin"
    with pytest.raises(TxFailed) as e:
        veto(tx, COLD, VAULT, HOT.ss58_address, p.call_hash)
    assert e.value.row["outcome"] != "ok" and e.value.row["extrinsic_id"]


def test_veto_included_but_still_pending_is_not_a_success():
    s, tx = make()
    p = announce(tx, HOT, VAULT, spend(s))
    s.ignore_effect = True
    with pytest.raises(NotConfirmed, match="still pending"):
        veto(tx, COLD, VAULT, HOT.ss58_address, p.call_hash)


def test_panic_removes_the_delegate_and_its_announcements_die():
    s, tx = make()
    announce(tx, HOT, VAULT, spend(s))
    panic(tx, COLD, VAULT, HOT.ss58_address)
    assert delay_of(tx.delegates(VAULT), HOT.ss58_address) is None
    assert [p.release_block for p in pending_for(tx, VAULT)] == []  # pending_for lists current delegates only
    assert find_pending(tx, VAULT, HOT.ss58_address, call_hash(spend(s))).release_block is None


def test_panic_refusals():
    s, tx = make()
    with pytest.raises(Refused, match="cold key itself"):
        panic(tx, COLD, VAULT, COLD.ss58_address)
    with pytest.raises(Refused, match="nothing to remove"):
        panic(tx, COLD, VAULT, Keypair.create_from_uri("//Eve").ss58_address)
    assert s.sent == []


def test_withdraw_own_announcement():
    s, tx = make()
    p = announce(tx, HOT, VAULT, spend(s))
    withdraw(tx, HOT, VAULT, p.call_hash)
    assert tx.rows[-1]["call"] == "Proxy.remove_announcement" and not pending_for(tx, VAULT)


# round-1 QA fixes ---------------------------------------------------------------------------------------------------

def test_identical_pending_call_is_refused():
    """One proxy_announced clears every mature copy of a hash: a second identical spend would silently be lost."""
    s, tx = make()
    announce(tx, HOT, VAULT, spend(s))
    with pytest.raises(Refused, match="already pending"):
        announce(tx, HOT, VAULT, spend(s))
    assert len(s.sent) == 1


def test_execute_read_back_tolerates_a_later_identical_copy():
    """Someone else's tooling announced the same hash twice: execute clears the mature copy; the younger one stays and
    that is not a failure."""
    s, tx = make()
    announce(tx, HOT, VAULT, spend(s))  # at #101
    s.announcements[HOT.ss58_address].append({"real": VAULT, "call_hash": call_hash(spend(s)), "height": 104})
    s.head = 106
    execute(tx, HOT, VAULT, spend(s), wait=False)  # lands at #107: #101 is mature, #104 is not
    assert [a["height"] for a in s.announcements[HOT.ss58_address]] == [104]


def test_announce_read_back_wants_the_entry_at_the_inclusion_block():
    s, tx = make()
    s.ignore_effect = True
    s.announcements[HOT.ss58_address] = []

    def stale_entry(*a, **k):  # storage shows the hash, but at another height than the inclusion block
        s.announcements[HOT.ss58_address] = [{"real": VAULT, "call_hash": call_hash(spend(s)), "height": 50}]
    s.create_signed_extrinsic = lambda call, keypair: (stale_entry(), (call, keypair))[1]
    with pytest.raises(NotConfirmed, match="inclusion block"):
        announce(tx, HOT, VAULT, spend(s))


def test_execute_no_wait_boundary():
    s, tx = make()
    announce(tx, HOT, VAULT, spend(s))  # #101, delay 5 -> executable from #106
    s.head = 105
    with pytest.raises(Refused, match="not sending now"):
        execute(tx, HOT, VAULT, spend(s), wait=False)
    s.head = 106
    execute(tx, HOT, VAULT, spend(s), wait=False)


def test_execute_refused_when_the_vault_cannot_pay():
    s, tx = make()
    big = s.compose_call("Balances", "transfer_keep_alive", {"dest": COLD.ss58_address, "value": 5000})
    announce(tx, HOT, VAULT, big)
    s.head = 200
    with pytest.raises(Refused, match="use up the announcement"):
        execute(tx, HOT, VAULT, big, wait=False, needs=5000)


def test_create_vault_happy_path_and_read_back():
    s, tx = make()
    vault = create_vault(tx, COLD, HOT.ss58_address, 7, fund=500)
    assert vault == NEW_VAULT and delay_of(tx.delegates(vault), HOT.ss58_address) == 7
    assert [r["step"] for r in tx.rows] == ["create vault", "fund vault", "add delayed hot key"]


def test_create_vault_refusals_send_nothing():
    s, tx = make()
    for kwargs, msg in (({"fund": None}, "at least"), ({"fund": 25}, "at least"),  # min = factor 20 + ED 10 = 30
                        ({"fund": 50_000}, "cold key can spend")):
        with pytest.raises(Refused, match=msg):
            create_vault(tx, COLD, HOT.ss58_address, 5, **kwargs)
    with pytest.raises(Refused, match="must not be the cold key"):
        create_vault(tx, COLD, COLD.ss58_address, 5, fund=500)
    with pytest.raises(Refused, match="delay must be"):
        create_vault(tx, COLD, HOT.ss58_address, 0, fund=500)
    with pytest.raises(Refused, match="delay must be"):
        create_vault(tx, COLD, HOT.ss58_address, 2**32, fund=500)
    assert s.sent == []


def test_create_vault_failure_after_create_names_the_vault():
    s, tx = make()
    s.fail_on = ("add_proxy", "Balances.InsufficientBalance")
    with pytest.raises(VaultIncomplete) as e:
        create_vault(tx, COLD, HOT.ss58_address, 5, fund=500)
    assert e.value.vault == NEW_VAULT and NEW_VAULT in str(e.value)


def test_panic_reads_the_first_definition_for_the_cold_check():
    s, tx = make()
    s.proxies[VAULT].insert(0, {"delegate": COLD.ss58_address, "proxy_type": "Any", "delay": 3})
    with pytest.raises(Refused, match="not a delay-0 delegate"):
        panic(tx, COLD, VAULT, HOT.ss58_address)


def test_execute_uses_the_keep_alive_limit():
    """The vault can lose free − ED to a transfer_keep_alive, not free: a spend in that margin would fail inside
    proxy_announced and burn the announcement, so it is refused first (fake: free 1000, ED 10)."""
    s, tx = make()
    edge = s.compose_call("Balances", "transfer_keep_alive", {"dest": COLD.ss58_address, "value": 995})
    announce(tx, HOT, VAULT, edge)
    s.head = 200
    with pytest.raises(Refused, match="transfer_keep_alive"):
        execute(tx, HOT, VAULT, edge, wait=False, needs=995)
    ok = s.compose_call("Balances", "transfer_keep_alive", {"dest": COLD.ss58_address, "value": 990})
    announce(tx, HOT, VAULT, ok)
    s.head = 300
    execute(tx, HOT, VAULT, ok, wait=False, needs=990)


def test_any_interruption_after_create_names_the_vault():
    s, tx = make()
    s.fail_on = None
    real_submit = s.submit_extrinsic

    def drop_after_create(xt, wait_for_inclusion):
        if xt[0].call_function["name"] == "transfer_keep_alive":
            raise ConnectionResetError(54, "Connection reset by peer")
        return real_submit(xt, wait_for_inclusion)
    s.submit_extrinsic = drop_after_create
    with pytest.raises(VaultIncomplete) as e:
        create_vault(tx, COLD, HOT.ss58_address, 5, fund=500)
    assert NEW_VAULT in str(e.value) and isinstance(e.value.__cause__, ConnectionResetError)


def test_submission_rejection_is_marked_not_included():
    from substrateinterface.exceptions import SubstrateRequestException

    from airlock.ops import SubmitRejected
    s, tx = make()

    def reject(xt, wait_for_inclusion):
        raise SubstrateRequestException({"code": 1010, "message": "Invalid Transaction"})
    s.submit_extrinsic = reject
    with pytest.raises(SubmitRejected, match="1010"):
        announce(tx, HOT, VAULT, spend(s))
