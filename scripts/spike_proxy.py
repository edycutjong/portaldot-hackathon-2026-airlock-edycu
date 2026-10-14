#!/usr/bin/env python3
"""End-to-end Proxy-pallet spike: the airlock flow on a real chain, with real transaction hashes.

  1. cold   proxy.create_pure(Any, 0, index)                     -> the vault (a keyless pure account)
  2. cold   balances.transfer_keep_alive(vault, fund)            -> the vault holds funds
  3. cold   proxy.proxy(vault, add_proxy(hot, Any, D))           -> hot becomes a DELAYED delegate
  4. hot    proxy.proxy(vault, transfer_keep_alive(thief, amt))  -> must FAIL Proxy.Unannounced   (I1)
  5. hot    proxy.announce(vault, blake2_256(call))              -> Proxy.Announced, hash stored  (I3)
  6. cold   proxy.proxy(vault, reject_announcement(hot, hash))   -> the veto
  7. hot    proxy.proxy_announced(hot, vault, call) after D      -> must FAIL Proxy.Unannounced   (I2)

Signers are dev keys (//Alice cold, //Bob hot, //Eve the thief's payee): local interim chains only.
Every row is printed with the chain profile label and written to evidence/ as JSON.

usage: python scripts/spike_proxy.py [--profile interim-sdk] [--url ws://…] [--delay 5] [--proof docs/PROOF.md]
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from substrateinterface import Keypair  # noqa: E402

from airlock.chain_profile import Chain, connect, event_field, pure_address  # noqa: E402


class StepFailed(RuntimeError):
    pass


def events(receipt):
    return [(e.value["module_id"], e.value["event_id"], e.value["attributes"]) for e in receipt.triggered_events]


def find_event(receipt, module, event):
    for m, e, attrs in events(receipt):
        if (m, e) == (module, event):
            return attrs
    return None


def call_hash(call) -> str:
    """blake2_256 of the SCALE-encoded call: what proxy.announce stores."""
    return "0x" + hashlib.blake2b(call.data.data, digest_size=32).hexdigest()


def outcome(receipt) -> str:
    """'ok', or 'Pallet.Error' — checking BOTH the outer dispatch result and an inner ProxyExecuted result."""
    if not receipt.is_success:
        err = receipt.error_message or {}
        return f"{failed_pallet(receipt)}.{err.get('name')}"
    executed = find_event(receipt, "Proxy", "ProxyExecuted")
    if executed is not None:
        result = event_field(executed, "result", 0)
        if not (result == "Ok" or (isinstance(result, dict) and "Ok" in result)):
            return f"inner-error:{result}"
    return "ok"


def failed_pallet(receipt) -> str:
    """Name of the pallet that raised a module error (error_message carries only the error name)."""
    attrs = find_event(receipt, "System", "ExtrinsicFailed")
    err = event_field(attrs, "dispatch_error", 0)
    if not isinstance(err, dict) or "Module" not in err:
        return str(err)
    index = err["Module"]["index"]
    for pallet in receipt.substrate.metadata.pallets:
        if pallet["index"] == index:
            return pallet.name
    return f"pallet#{index}"


def call_name(call) -> str:
    """`Proxy.proxy(Proxy.add_proxy)`: name a proxied call by what it wraps."""
    name = f"{call.call_module['name']}.{call.call_function['name']}"
    inner = call.value.get("call_args", {}).get("call") if isinstance(call.value, dict) else None
    inner = getattr(inner, "value", inner)  # a composed GenericCall, or an already-decoded dict
    if isinstance(inner, dict) and "call_module" in inner:
        name += f"({inner['call_module']}.{inner['call_function']})"
    return name


class Spike:
    def __init__(self, chain: Chain):
        self.chain = chain
        self.s = chain.substrate
        self.rows: list[dict] = []

    def submit(self, step: str, signer_name: str, signer: Keypair, call, expect: str, note: str = ""):
        xt = self.s.create_signed_extrinsic(call=call, keypair=signer)
        receipt = self.s.submit_extrinsic(xt, wait_for_inclusion=True)
        got = outcome(receipt)
        row = {
            "step": step,
            "call": call_name(call),
            "signer": signer_name,
            "expected": expect,
            "outcome": got,
            "extrinsic_id": receipt.get_extrinsic_identifier(),
            "block_hash": receipt.block_hash,
            "extrinsic_hash": receipt.extrinsic_hash,
            "profile": self.chain.profile.id,
            "note": note,
        }
        self.rows.append(row)
        mark = "PASS" if got == expect else "FAIL"
        print(f"[{self.chain.profile.id}] {mark} {step:<26} {row['call']:<44} {row['signer']:<5} "
              f"expected={expect:<20} got={got:<20} id={row['extrinsic_id']} tx={row['extrinsic_hash']}")
        if got != expect:
            raise StepFailed(f"{step}: expected {expect}, got {got}")
        return receipt

    def wait_until_block(self, target: int):
        while (n := self.s.get_block_number(None)) < target:
            time.sleep(max((self.chain.block_time_ms or 3000) / 3000, 0.5))
        return n


def run(chain: Chain, delay: int, index: int, fund: float, amount: float) -> Spike:
    sp = Spike(chain)
    s, p = chain.substrate, chain.profile
    cold, hot, thief = (Keypair.create_from_uri(u) for u in ("//Alice", "//Bob", "//Eve"))
    print(f"chain: {p.label} · {chain.spec_name}/{chain.spec_version} · genesis {chain.genesis_hash} · "
          f"{chain.url} · {chain.decimals} decimals · block {chain.block_time_ms} ms · revive={chain.has_revive}")

    # 1. the vault
    r = sp.submit("create vault", "cold", cold,
                  s.compose_call("Proxy", p.create_pure_call, {"proxy_type": "Any", "delay": 0, "index": index}), "ok")
    vault = pure_address(p, find_event(r, "Proxy", p.pure_created_event))
    print(f"vault {vault}")

    # 2. fund it
    sp.submit("fund vault", "cold", cold,
              s.compose_call("Balances", "transfer_keep_alive", {"dest": vault, "value": chain.units(fund)}), "ok")

    # 3. hot key as a delayed delegate, added BY the vault (cold acts as the vault through proxy.proxy)
    add = s.compose_call("Proxy", "add_proxy", {"delegate": hot.ss58_address, "proxy_type": "Any", "delay": delay})
    sp.submit("add delayed hot key", "cold", cold,
              s.compose_call("Proxy", "proxy", {"real": vault, "force_proxy_type": None, "call": add}), "ok",
              note=f"delay {delay} blocks")
    proxies = s.query("Proxy", "Proxies", [vault]).value
    print(f"Proxy.Proxies(vault) = {proxies}")

    # 4. the thief holds the hot key and tries to spend immediately
    theft = s.compose_call("Balances", "transfer_keep_alive", {"dest": thief.ss58_address, "value": chain.units(amount)})
    sp.submit("hot spends unannounced", "hot", hot,
              s.compose_call("Proxy", "proxy", {"real": vault, "force_proxy_type": None, "call": theft}),
              "Proxy.Unannounced", note="I1")

    # 5. so the thief announces it — this is what the watcher sees
    h = call_hash(theft)
    r = sp.submit("hot announces", "hot", hot, s.compose_call("Proxy", "announce", {"real": vault, "call_hash": h}), "ok",
                  note=f"call hash {h}")
    announced_at = s.get_block_number(r.block_hash)
    pending = s.query("Proxy", "Announcements", [hot.ss58_address]).value
    stored = [a["call_hash"] for a in pending[0]]
    if h not in stored:
        raise StepFailed(f"I3: announced hash {h} not in Proxy.Announcements(hot) {stored}")
    print(f"I3 PASS blake2_256(call) == Proxy.Announcements(hot) entry {h}")

    # 6. the veto
    veto = s.compose_call("Proxy", "reject_announcement", {"delegate": hot.ss58_address, "call_hash": h})
    sp.submit("cold vetoes", "cold", cold,
              s.compose_call("Proxy", "proxy", {"real": vault, "force_proxy_type": None, "call": veto}), "ok")
    left = s.query("Proxy", "Announcements", [hot.ss58_address]).value[0]
    print(f"Proxy.Announcements(hot) after veto = {left}")

    # 7. the delay passes; the vetoed call still cannot execute
    before_vault = s.query("System", "Account", [vault]).value["data"]["free"]
    before_thief = s.query("System", "Account", [thief.ss58_address]).value["data"]["free"]
    sp.wait_until_block(announced_at + delay + 1)
    sp.submit("hot executes after delay", "hot", hot,
              s.compose_call("Proxy", "proxy_announced",
                             {"delegate": hot.ss58_address, "real": vault, "force_proxy_type": None, "call": theft}),
              "Proxy.Unannounced", note="I2")
    after_vault = s.query("System", "Account", [vault]).value["data"]["free"]
    after_thief = s.query("System", "Account", [thief.ss58_address]).value["data"]["free"]
    if after_vault != before_vault or after_thief != before_thief:
        raise StepFailed(f"I2: balances moved (vault {before_vault}->{after_vault}, thief {before_thief}->{after_thief})")
    print(f"I2 PASS vault {chain.fmt(after_vault)} and thief balance unchanged after the vetoed call's delay")
    sp.vault = vault
    return sp


def proof_markdown(chain: Chain, sp: Spike, started: str) -> str:
    lines = [
        f"### spike_proxy — {started} — {chain.profile.label}",
        "",
        f"Profile `{chain.profile.id}` · runtime `{chain.spec_name}/{chain.spec_version}` · genesis `{chain.genesis_hash}` · "
        f"vault `{sp.vault}`",
        "",
        "| # | step | call | signer | expected | outcome | extrinsic id | block hash | tx hash | profile |",
        "|---|---|---|---|---|---|---|---|---|---|",
    ]
    for i, r in enumerate(sp.rows, 1):
        lines.append(f"| {i} | {r['step']} | `{r['call']}` | {r['signer']} | {r['expected']} | {r['outcome']} | "
                     f"`{r['extrinsic_id']}` | `{r['block_hash']}` | `{r['extrinsic_hash']}` | {r['profile']} |")
    return "\n".join(lines) + "\n"


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--profile", default=None, help="expected chain profile (default $AIRLOCK_PROFILE, else auto)")
    ap.add_argument("--url", default=None, help="node websocket (default $AIRLOCK_WSS, else the profile default)")
    ap.add_argument("--delay", type=int, default=5, help="announcement delay D in blocks (default 5)")
    ap.add_argument("--index", type=int, default=0, help="create_pure disambiguation index")
    ap.add_argument("--fund", type=float, default=1000, help="tokens sent to the vault")
    ap.add_argument("--amount", type=float, default=100, help="tokens the thief tries to move")
    ap.add_argument("--proof", type=Path, default=None, help="append the run's rows to this PROOF.md")
    args = ap.parse_args(argv)
    if args.delay < 1:
        ap.error("--delay must be >= 1: a zero-delay delegate is exactly what Airlock exists to avoid")

    chain = connect(args.url, args.profile or None)
    started = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    try:
        sp = run(chain, args.delay, args.index, args.fund, args.amount)
    except StepFailed as e:
        print(f"SPIKE FAILED: {e}", file=sys.stderr)
        return 1

    out = ROOT / "evidence" / f"spike-proxy-{chain.profile.id}-{started.replace(':', '')}.json"
    out.write_text(json.dumps({"started": started, "profile": chain.profile.id, "label": chain.profile.label,
                               "endpoint": chain.url, "genesis_hash": chain.genesis_hash, "code_hash": chain.code_hash,
                               "runtime": f"{chain.spec_name}/{chain.spec_version}", "delay_blocks": args.delay,
                               "vault": sp.vault, "rows": sp.rows}, indent=2) + "\n")
    print(f"{len(sp.rows)} extrinsics, all as expected -> {out.relative_to(ROOT)}")
    if args.proof:
        with args.proof.open("a") as f:
            f.write("\n" + proof_markdown(chain, sp, started))
        print(f"rows appended to {args.proof}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
