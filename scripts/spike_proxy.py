#!/usr/bin/env python3
"""End-to-end Proxy-pallet spike: the airlock flow on a real chain, with real transaction hashes.

  1. cold   proxy.create_pure(Any, 0, index)                       -> the vault (a keyless pure account)
  2. cold   balances.transfer_keep_alive(vault, fund)              -> the vault holds funds
  3. cold   proxy.proxy(vault, add_proxy(hot, Any, D))             -> hot becomes a DELAYED delegate
  4. hot    proxy.proxy(vault, transfer_keep_alive(thief, amt))    -> must FAIL Proxy.Unannounced    (I1)
  5. hot    proxy.announce(vault, blake2_256(theft))               -> Proxy.Announced, hash stored   (I3)
  6. cold   proxy.proxy(vault, reject_announcement(hot, hash))     -> the veto
  7. hot    proxy.proxy_announced(hot, vault, theft) after D       -> must FAIL Proxy.Unannounced    (I2)
  8. hot    proxy.announce(vault, blake2_256(payment))             -> a legitimate spend, not vetoed
  9. hot    proxy.proxy_announced(hot, vault, payment) before D    -> must FAIL Proxy.Unannounced    (delay enforced)
 10. hot    proxy.proxy_announced(hot, vault, payment) after D     -> succeeds; payee +amount exactly (control)

Step 10 is the control for step 7: the same delegate and delay, and the only difference is the veto. Step 9 shows the
delay itself is enforced, not just stored.

Signers are dev keys (//Alice cold, //Bob hot, //Eve the thief's payee, //Ferdie a legitimate payee): local interim
chains only. Every row carries the chain profile label and is written to evidence/ as JSON, failed runs included.

usage: python scripts/spike_proxy.py [--profile interim-sdk] [--url ws://…] [--delay 5] [--proof docs/PROOF.md]
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from substrateinterface import Keypair  # noqa: E402

from airlock.callhash import call_hash, is_announced, pending, same_account  # noqa: E402
from airlock.chain_profile import Chain, ProfileMismatch, UnknownChain, connect, pure_address  # noqa: E402
from airlock.dispatch import OK, error_namer, find_event, outcome  # noqa: E402


class StepFailed(RuntimeError):
    pass


def call_name(call) -> str:
    """`Proxy.proxy(Proxy.add_proxy)`: name a proxied call by what it wraps."""
    name = f"{call.call_module['name']}.{call.call_function['name']}"
    inner = call.value.get("call_args", {}).get("call") if isinstance(call.value, dict) else None
    inner = getattr(inner, "value", inner)  # a composed call object, or an already-decoded dict
    if isinstance(inner, dict) and "call_module" in inner:
        name += f"({inner['call_module']}.{inner['call_function']})"
    return name


def profile_tag(chain: Chain) -> str:
    return chain.profile.id + (" — not V3" if chain.profile.is_interim else "")


def code_version() -> str:
    """The commit that produced a run, marked dirty if the working tree differs from it."""
    try:
        sha = subprocess.run(["git", "-C", str(ROOT), "rev-parse", "--short", "HEAD"], capture_output=True, text=True,
                             check=True).stdout.strip()
        dirty = subprocess.run(["git", "-C", str(ROOT), "status", "--porcelain", "--", "airlock", "scripts"],
                               capture_output=True, text=True, check=True).stdout.strip()
        return sha + ("-dirty" if dirty else "")
    except (OSError, subprocess.CalledProcessError):
        return "unknown"


class Spike:
    def __init__(self, chain: Chain):
        self.chain = chain
        self.s = chain.substrate
        self.name_error = error_namer(self.s.get_metadata().pallets)
        self.rows: list[dict] = []
        self.vault: str | None = None

    def submit(self, step: str, signer_name: str, signer: Keypair, call, expect: str, note: str = ""):
        xt = self.s.create_signed_extrinsic(call=call, keypair=signer)
        receipt = self.s.submit_extrinsic(xt, wait_for_inclusion=True)
        got = outcome(receipt, self.name_error)
        row = {
            "step": step,
            "call": call_name(call),
            "signer": signer_name,
            "expected": expect,
            "outcome": got,
            "extrinsic_id": receipt.get_extrinsic_identifier(),
            "block_hash": receipt.block_hash,
            "extrinsic_hash": receipt.extrinsic_hash,
            "profile": profile_tag(self.chain),
            "note": note,
        }
        self.rows.append(row)
        mark = "PASS" if got == expect else "FAIL"
        print(f"[{row['profile']}] {mark} {step:<30} {row['call']:<52} {signer_name:<4} "
              f"expected={expect:<18} got={got:<18} id={row['extrinsic_id']} tx={row['extrinsic_hash']}")
        if got != expect:
            raise StepFailed(f"{step}: expected {expect}, got {got}")
        return receipt

    def block_of(self, receipt) -> int:
        return self.s.get_block_number(receipt.block_hash)

    def wait_until_block(self, target: int) -> int:
        block_s = (self.chain.block_time_ms or 6000) / 1000
        deadline = time.monotonic() + max(target - self.s.get_block_number(None), 1) * block_s * 5 + 30
        while (n := self.s.get_block_number(None)) < target:
            if time.monotonic() > deadline:
                raise StepFailed(f"chain stalled: block {n}, waiting for {target}")
            time.sleep(max(block_s / 3, 0.5))
        return n

    def free(self, who: str) -> int:
        return int(self.s.query("System", "Account", [who]).value["data"]["free"])


def as_vault(s, vault: str, call):
    """cold acts as the vault: proxy.proxy(vault, None, call)."""
    return s.compose_call("Proxy", "proxy", {"real": vault, "force_proxy_type": None, "call": call})


def run(sp: Spike, delay: int, index: int, fund: str, amount: str) -> None:
    chain, s, p = sp.chain, sp.s, sp.chain.profile
    if not p.is_interim:
        raise StepFailed(f"{p.id} is not an interim chain: this spike signs with public dev keys (//Alice …)")
    cold, hot, thief, payee = (Keypair.create_from_uri(u) for u in ("//Alice", "//Bob", "//Eve", "//Ferdie"))
    print(f"chain: {p.label} · {chain.spec_name}/{chain.spec_version} · genesis {chain.genesis_hash} · {chain.url} · "
          f"{chain.decimals} decimals · block {chain.block_time_ms} ms ({chain.block_time_source}) · "
          f"revive={chain.has_revive} · code {code_version()}")

    # 1. the vault
    r = sp.submit("create vault", "cold", cold,
                  s.compose_call("Proxy", p.create_pure_call, {"proxy_type": "Any", "delay": 0, "index": index}), OK)
    sp.vault = vault = pure_address(p, find_event(r, "Proxy", p.pure_created_event))
    print(f"vault {vault}")

    # 2. fund it
    sp.submit("fund vault", "cold", cold,
              s.compose_call("Balances", "transfer_keep_alive", {"dest": vault, "value": chain.units(fund)}), OK)

    # 3. hot key as a delayed delegate, added BY the vault
    add = s.compose_call("Proxy", "add_proxy", {"delegate": hot.ss58_address, "proxy_type": "Any", "delay": delay})
    sp.submit("add delayed hot key", "cold", cold, as_vault(s, vault, add), OK, note=f"delay {delay} blocks")
    delegates = s.query("Proxy", "Proxies", [vault]).value[0]
    def delays_of(account):
        return [int(d["delay"]) for d in delegates if same_account(d["delegate"], account)]

    if delays_of(hot.ss58_address) != [delay] or delays_of(cold.ss58_address) != [0]:
        raise StepFailed(f"Proxy.Proxies(vault) should hold hot with delay {delay} and cold with delay 0: {delegates}")
    print(f"Proxy.Proxies(vault): hot delay {delay}, cold delay 0")

    # 4. the thief holds the hot key and tries to spend immediately
    theft = s.compose_call("Balances", "transfer_keep_alive", {"dest": thief.ss58_address, "value": chain.units(amount)})
    sp.submit("hot spends unannounced", "hot", hot, as_vault(s, vault, theft), "Proxy.Unannounced", note="I1")

    # 5. so the thief announces it: this is what the watcher sees
    h = call_hash(theft)
    r = sp.submit("hot announces theft", "hot", hot, s.compose_call("Proxy", "announce", {"real": vault, "call_hash": h}),
                  OK, note=f"call hash {h}")
    theft_at = sp.block_of(r)
    if not is_announced(pending(s, hot.ss58_address), vault, theft):
        raise StepFailed(f"I3: blake2_256(call) {h} not in Proxy.Announcements(hot)")
    print(f"I3 PASS blake2_256(call) == Proxy.Announcements(hot) entry {h}")

    # 6. the veto
    veto = s.compose_call("Proxy", "reject_announcement", {"delegate": hot.ss58_address, "call_hash": h})
    sp.submit("cold vetoes", "cold", cold, as_vault(s, vault, veto), OK)
    if is_announced(pending(s, hot.ss58_address), vault, theft):
        raise StepFailed(f"veto: {h} still pending after reject_announcement")
    print("announcement removed by the veto")

    # 7. the delay passes; the vetoed call still cannot execute
    vault_before, thief_before = sp.free(vault), sp.free(thief.ss58_address)
    sp.wait_until_block(theft_at + delay + 1)
    sp.submit("vetoed theft after delay", "hot", hot,
              s.compose_call("Proxy", "proxy_announced",
                             {"delegate": hot.ss58_address, "real": vault, "force_proxy_type": None, "call": theft}),
              "Proxy.Unannounced", note="I2")
    if (sp.free(vault), sp.free(thief.ss58_address)) != (vault_before, thief_before):
        raise StepFailed("I2: balances moved after a vetoed announcement")
    print(f"I2 PASS vault {chain.fmt(sp.free(vault))} and thief balance unchanged")

    # 8–9. control: an announced spend that is NOT vetoed executes after the same delay
    payment = s.compose_call("Balances", "transfer_keep_alive", {"dest": payee.ss58_address, "value": chain.units(amount)})
    r = sp.submit("hot announces payment", "hot", hot,
                  s.compose_call("Proxy", "announce", {"real": vault, "call_hash": call_hash(payment)}), OK,
                  note=f"call hash {call_hash(payment)}")
    payment_at = sp.block_of(r)
    payee_before = sp.free(payee.ss58_address)
    execute = s.compose_call("Proxy", "proxy_announced",
                             {"delegate": hot.ss58_address, "real": vault, "force_proxy_type": None, "call": payment})
    r = sp.submit("payment before delay", "hot", hot, execute, "Proxy.Unannounced", note="delay enforced")
    if sp.block_of(r) - payment_at >= delay:
        raise StepFailed(f"early execution landed {sp.block_of(r) - payment_at} blocks after the announcement, "
                         f"not before D = {delay}; re-run with a larger --delay")
    sp.wait_until_block(payment_at + delay + 1)
    sp.submit("payment executes after delay", "hot", hot, execute, OK, note="control for I2")
    received = sp.free(payee.ss58_address) - payee_before
    if received != chain.units(amount):
        raise StepFailed(f"control: payee received {received}, expected {chain.units(amount)}")
    print(f"control PASS payee +{chain.fmt(received)} exactly, after {delay} blocks with no veto")


def proof_markdown(chain: Chain, sp: Spike, started: str, version: str, delay: int) -> str:
    lines = [
        f"### spike_proxy — {started} — {chain.profile.label}",
        "",
        f"Runtime `{chain.spec_name}/{chain.spec_version}` · genesis `{chain.genesis_hash}` · vault `{sp.vault}` · "
        f"delay D = {delay} blocks · code `{version}`",
        "",
        "| # | step | call | signer | expected | outcome | extrinsic id | block hash | tx hash | profile | note |",
        "|---|---|---|---|---|---|---|---|---|---|---|",
    ]
    for i, r in enumerate(sp.rows, 1):
        note = f"`{r['note']}`" if r["note"].startswith("call hash") else r["note"]
        lines.append(f"| {i} | {r['step']} | `{r['call']}` | {r['signer']} | {r['expected']} | {r['outcome']} | "
                     f"`{r['extrinsic_id']}` | `{r['block_hash']}` | `{r['extrinsic_hash']}` | {r['profile']} | {note} |")
    return "\n".join(lines) + "\n"


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--profile", default=None, help="expected chain profile (default $AIRLOCK_PROFILE, else auto)")
    ap.add_argument("--url", default=None, help="node websocket (default $AIRLOCK_WSS, else the profile default)")
    ap.add_argument("--delay", type=int, default=5, help="announcement delay D in blocks (default 5)")
    ap.add_argument("--index", type=int, default=0, help="create_pure disambiguation index")
    ap.add_argument("--fund", default="1000", help="tokens sent to the vault")
    ap.add_argument("--amount", default="100", help="tokens each spend tries to move")
    ap.add_argument("--proof", type=Path, default=None, help="append the run's rows to this PROOF.md (passing runs only)")
    args = ap.parse_args(argv)
    if args.delay < 1:
        ap.error("--delay must be >= 1: a zero-delay delegate is exactly what Airlock exists to avoid")

    try:
        chain = connect(args.url, args.profile or None)
    except (UnknownChain, ProfileMismatch) as e:  # nothing was sent, so there is no evidence to keep
        print(f"SPIKE FAILED: {type(e).__name__}: {e}", file=sys.stderr)
        return 1
    try:
        return record(chain, args)
    finally:
        chain.substrate.close()


def record(chain: Chain, args) -> int:
    started = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    version = code_version()
    sp = Spike(chain)
    failure = None
    try:
        run(sp, args.delay, args.index, args.fund, args.amount)
    except Exception as e:  # any failure still leaves an evidence file with the rows collected so far
        failure = str(e) if isinstance(e, StepFailed) else f"{type(e).__name__}: {e}"
        print(f"SPIKE FAILED: {failure}", file=sys.stderr)

    status = "failed" if failure else "passed"
    out = ROOT / "evidence" / f"spike-proxy-{chain.profile.id}-{started.replace(':', '')}{'-FAILED' if failure else ''}.json"
    out.write_text(json.dumps({"started": started, "status": status, "failure": failure, "code": version,
                               "profile": chain.profile.id, "label": chain.profile.label, "endpoint": chain.url,
                               "genesis_hash": chain.genesis_hash, "code_hash": chain.code_hash,
                               "runtime": f"{chain.spec_name}/{chain.spec_version}", "delay_blocks": args.delay,
                               "vault": sp.vault, "rows": sp.rows}, indent=2) + "\n")
    print(f"{len(sp.rows)} extrinsics, {status} -> {out.relative_to(ROOT)}")
    if failure:
        return 1
    if args.proof:
        with args.proof.open("a") as f:
            f.write("\n" + proof_markdown(chain, sp, started, version, args.delay))
        print(f"rows appended to {args.proof}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
