#!/usr/bin/env python3
"""Benchmark: N thief drains against an Airlock vault, each alerted by the watcher and vetoed by the cold key.

Per drain (scripts/thief.py, which imports no Airlock code, announces a sweep of the vault's whole sweepable balance):
  alert latency     announce block's on-chain timestamp (Timestamp.Now) -> the watcher's alert written to its
                    JSON-lines log (wall clock, same machine as the node); in blocks: head when the alert was built -
                    announce block. Telegram is not in this path (its delivery time is not measured here).
  veto inclusion    the alert's veto sent automatically (no human: this measures the chain, not a person) -> included;
                    seconds as the client observed it, blocks = veto block - head when the veto was sent
  announce -> veto  on-chain: veto block timestamp - announce block timestamp, and veto block - announce block,
                    against the delay D (blocks to spare = announce block + D - veto block)
Then, once every drain's delay has passed, the thief executes each one (`--execute`): each must fail
Proxy.Unannounced. Value moved = the thief payee's balance after - before (and the vault's total, before vs after).

--ablate adds ONE control drain: the same thief script against a control vault whose hot key has delay 0 — a plain
proxy, no Airlock — with `--direct` (proxy.proxy, no announcement). It moves the control vault's sweepable balance in
one extrinsic. Run once per invocation; the control vault is then empty.

Percentiles are nearest-rank (p95 of 20 = the 19th smallest). Local interim dev chains only (dev keys
//AirlockBench//…, funded by //Alice, //Dave on interim-1002); every row carries the profile label.

usage: python scripts/bench.py [--profile interim-sdk] [--n 20] [--delay-s 60] [--ablate] [--md docs/BENCH.md]
       python scripts/bench.py --render evidence/bench-….json [--md docs/BENCH.md]
"""

from __future__ import annotations

import argparse
import json
import math
import os
import subprocess
import sys
import tempfile
import time
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from substrateinterface import Keypair  # noqa: E402

from airlock.chain_profile import Chain, connect  # noqa: E402
from airlock.ops import (Sender, as_vault, blocks_for, code_version, create_vault, deposits, transfer,  # noqa: E402
                         veto)

FUNDER = {"interim-sdk": "//Alice", "interim-1002": "//Dave"}
ENDPOINTS = {"interim-sdk": "ws://127.0.0.1:29944", "interim-1002": "ws://127.0.0.1:19944"}
KEYS = ("cold", "hot", "thief-payee", "control-hot", "control-payee")
VAULT_FUND = Decimal("1000")
CONTROL_FUND = Decimal("100")
ALERT_TIMEOUT_S = 120


def percentile(values: list[float], q: float) -> float:
    """Nearest-rank percentile: the smallest value with at least q of the sample at or below it."""
    if not values:
        raise ValueError("no values")
    ordered = sorted(values)
    return ordered[max(1, math.ceil(q * len(ordered))) - 1]


def stats(values: list[float]) -> dict:
    return {"n": len(values), "p50": percentile(values, 0.50), "p95": percentile(values, 0.95),
            "min": min(values), "max": max(values)}


def key(name: str) -> Keypair:
    return Keypair.create_from_uri(f"//AirlockBench//{name}")


def timestamp_s(chain: Chain, block_hash: str) -> float:
    return int(chain.substrate.query("Timestamp", "Now", block_hash=block_hash).value) / 1000


def total(chain: Chain, address: str) -> int:
    data = chain.substrate.query("System", "Account", [address]).value["data"]
    return int(data["free"]) + int(data["reserved"])


def thief(url: str, *args: str) -> dict:
    """Run scripts/thief.py (no Airlock imports) and return its last JSON line."""
    proc = subprocess.run([sys.executable, str(ROOT / "scripts" / "thief.py"), "--url", url, *args],
                          capture_output=True, text=True, timeout=300)
    lines = [ln for ln in proc.stdout.splitlines() if ln.startswith("{")]
    if not lines:
        raise RuntimeError(f"thief.py printed no result (exit {proc.returncode}): {proc.stderr.strip()[-400:]}")
    return json.loads(lines[-1])


def wait_alert(path: Path, call_hash: str, block: int, timeout_s: float = ALERT_TIMEOUT_S) -> dict:
    end = time.monotonic() + timeout_s
    while time.monotonic() < end:
        if path.exists():
            for line in path.read_text().splitlines():
                a = json.loads(line)
                if a["call_hash"] == call_hash and a["announced_block"] == block:
                    return a
        time.sleep(0.05)
    raise TimeoutError(f"no alert for {call_hash} at #{block} within {timeout_s} s")


def fund(tx: Sender, funder: Keypair, who: dict[str, Keypair], need: dict[str, int]) -> None:
    for name, amount in need.items():
        have = tx.free(who[name].ss58_address)
        if have < amount:
            tx.send(f"bench setup: fund {name}", "funder", funder, transfer(tx.chain, who[name].ss58_address,
                                                                           amount - have))


def control_vault(tx: Sender, cold: Keypair, control_hot: str, amount: int) -> str:
    """A vault whose hot key has delay 0: a plain proxy, what Airlock replaces. Built raw (create_vault refuses 0)."""
    from airlock.chain_profile import pure_address
    from airlock.dispatch import find_event

    chain, s, p = tx.chain, tx.s, tx.chain.profile
    r = tx.send("ablation: create control vault", "cold", cold,
                s.compose_call("Proxy", p.create_pure_call, {"proxy_type": "Any", "delay": 0, "index": 1}))
    vault = pure_address(p, find_event(r, "Proxy", p.pure_created_event))
    tx.send("ablation: fund control vault", "cold", cold, transfer(chain, vault, amount))
    add = s.compose_call("Proxy", "add_proxy", {"delegate": control_hot, "proxy_type": "Any", "delay": 0})
    tx.send("ablation: add delay-0 hot key", "cold", cold, as_vault(chain, vault, add), note="delay 0 blocks")
    return vault


def run(args) -> int:
    chain = connect(args.url, args.profile)
    if not chain.profile.is_interim:
        sys.exit("bench uses public dev keys: local interim dev chains only")
    url = args.url or ENDPOINTS[chain.profile.id]
    started = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    version = code_version()
    tx = Sender(chain)
    who = {k: key(k) for k in KEYS}
    funder = Keypair.create_from_uri(FUNDER[chain.profile.id])
    delay = blocks_for(args.delay_s, chain.block_time_ms)
    d = deposits(chain)
    ed = chain.existential_deposit
    fee = int(chain.substrate.get_payment_info(transfer(chain, who["hot"].ss58_address, ed), funder)["partialFee"])
    # the control vault reserves the delay-0 hot key's deposit and keeps the existential deposit on top of what can move
    vault_fund, control_fund = chain.units(VAULT_FUND), chain.units(CONTROL_FUND) + d["ProxyDepositFactor"] + ed
    fund(tx, funder, who, {
        "cold": 2 * (d["ProxyDepositBase"] + d["ProxyDepositFactor"]) + vault_fund + control_fund + 4 * ed
        + (10 + 5 * args.n) * fee,
        "hot": d["AnnouncementDepositBase"] + d["AnnouncementDepositFactor"] + 2 * ed + 10 * (2 * args.n + 2) * fee,
        "control-hot": 2 * ed + 20 * fee,
    })
    cold, hot = who["cold"], who["hot"]
    vault = create_vault(tx, cold, hot.ss58_address, delay, index=0, fund=vault_fund, label="bench: ")
    print(f"bench vault {vault} on {chain.profile.label}: D = {delay} blocks, {args.n} drains", flush=True)

    tmp = Path(tempfile.mkdtemp(prefix="airlock-bench-"))
    alerts_path = tmp / "alerts.jsonl"
    log = (tmp / "watcher.log").open("w")
    watcher = subprocess.Popen([sys.executable, "-u", "-m", "airlock.watcher", "--db", str(tmp / "w.db"), "--jsonl",
                                str(alerts_path), "--vault", vault, "--profile", chain.profile.id, "--url", url,
                                "--no-telegram"], cwd=ROOT, stdout=subprocess.DEVNULL, stderr=log)
    drains, thief_rows = [], []
    try:
        deadline = time.monotonic() + 60
        while "watching" not in (tmp / "watcher.log").read_text():
            if watcher.poll() is not None or time.monotonic() > deadline:
                raise RuntimeError(f"watcher did not start: {(tmp / 'watcher.log').read_text()[-600:]}")
            time.sleep(0.2)
        payee = who["thief-payee"].ss58_address
        vault_before, payee_before = total(chain, vault), total(chain, payee)
        for i in range(1, args.n + 1):
            state = tmp / f"thief-{i}.json"
            ann = thief(url, "--vault", vault, "--to", payee, "--amount", "all", "--hot", "//AirlockBench//hot",
                        "--state", str(state))
            if ann["outcome"] != "ok":
                raise RuntimeError(f"drain {i}: thief announce {ann['outcome']}")
            thief_rows.append({**ann, "step": f"drain {i} announce (thief.py)", "expected": "ok"})
            ann_ts = timestamp_s(chain, ann["block_hash"])
            alert = wait_alert(alerts_path, ann["call_hash"], ann["block"])
            tap_head, tap_t = tx.head(), time.time()
            r = veto(tx, cold, vault, hot.ss58_address, ann["call_hash"], step=f"drain {i} veto")
            seen_t = time.time()
            veto_block = chain.substrate.get_block_number(r.block_hash)
            veto_ts = timestamp_s(chain, r.block_hash)
            drains.append({
                "drain": i, "call_hash": ann["call_hash"], "verdict": alert["verdict"], "announce_block": ann["block"],
                "announce_id": ann["extrinsic_id"], "veto_block": veto_block, "veto_id": tx.rows[-1]["extrinsic_id"],
                "alert_latency_s": round(alert["sent_at"] - ann_ts, 3), "alert_head": alert["head"],
                "alert_latency_blocks": alert["head"] - ann["block"],
                "veto_inclusion_s": round(seen_t - tap_t, 3), "veto_inclusion_blocks": veto_block - tap_head,
                "announce_to_veto_s": round(veto_ts - ann_ts, 3), "announce_to_veto_blocks": veto_block - ann["block"],
                "blocks_to_spare": ann["block"] + delay - veto_block, "state": str(state)})
            x = drains[-1]
            print(f"drain {i:>2}: {alert['verdict']} alert {x['alert_latency_s']:.3f} s ({x['alert_latency_blocks']} "
                  f"blk) · veto in {x['veto_inclusion_blocks']} blk · announce->veto {x['announce_to_veto_blocks']} blk "
                  f"of D={delay}", flush=True)
        last = max(x["announce_block"] for x in drains)
        tx.wait_until_block(last + delay + 1)
        print(f"delay passed (head >= #{last + delay + 1}); the thief executes every vetoed drain", flush=True)
        for x in drains:
            ex = thief(url, "--vault", vault, "--to", payee, "--amount", "all", "--hot", "//AirlockBench//hot",
                       "--state", x["state"], "--execute")
            x["execute_outcome"], x["execute_id"] = ex["outcome"], ex["extrinsic_id"]
            thief_rows.append({**ex, "step": f"drain {x['drain']} execute after D (thief.py)",
                               "expected": "Proxy.Unannounced"})
        vault_after, payee_after = total(chain, vault), total(chain, payee)
    finally:
        watcher.terminate()
        watcher.wait(10)
        log.close()

    ablation = None
    if args.ablate:
        control = control_vault(tx, cold, who["control-hot"].ss58_address, control_fund)
        cpayee = who["control-payee"].ss58_address
        c_before, cp_before = total(chain, control), total(chain, cpayee)
        head = tx.head()
        direct = thief(url, "--vault", control, "--to", cpayee, "--amount", "all", "--hot", "//AirlockBench//control-hot",
                       "--direct")
        thief_rows.append({**direct, "step": "ablation: thief proxy.proxy sweep, delay 0 (thief.py --direct)",
                           "expected": "ok"})
        ablation = {"control_vault": control, "delay": 0, "outcome": direct["outcome"], "id": direct["extrinsic_id"],
                    "moved": total(chain, cpayee) - cp_before, "moved_fmt": chain.fmt(total(chain, cpayee) - cp_before),
                    "control_total_before": c_before, "control_total_before_fmt": chain.fmt(c_before),
                    "extrinsics": 1, "blocks_after_head": direct["block"] - head}

    vetoed = sum(1 for x in drains if x["execute_outcome"] == "Proxy.Unannounced")
    summary = {
        "drains": len(drains), "alerted_not_yours": sum(1 for x in drains if x["verdict"] == "NOT YOURS"),
        "vetoed_then_unannounced": vetoed, "moved_to_thief": payee_after - payee_before,
        "vault_total_before": vault_before, "vault_total_after": vault_after,
        "alert_latency_s": stats([x["alert_latency_s"] for x in drains]),
        "alert_latency_blocks": stats([x["alert_latency_blocks"] for x in drains]),
        "veto_inclusion_s": stats([x["veto_inclusion_s"] for x in drains]),
        "veto_inclusion_blocks": stats([x["veto_inclusion_blocks"] for x in drains]),
        "announce_to_veto_s": stats([x["announce_to_veto_s"] for x in drains]),
        "announce_to_veto_blocks": stats([x["announce_to_veto_blocks"] for x in drains]),
        "blocks_to_spare_min": min(x["blocks_to_spare"] for x in drains),
    }
    ok = (vetoed == len(drains) == args.n and summary["moved_to_thief"] == 0 and vault_after == vault_before
          and summary["alerted_not_yours"] == args.n and (ablation is None or ablation["outcome"] == "ok"))
    for x in drains:
        x.pop("state")
    out = ROOT / "evidence" / f"bench-{chain.profile.id}-{started.replace(':', '')}.json"
    result = {"started": started, "status": "passed" if ok else "failed", "code": version, "profile": chain.profile.id,
              "label": chain.profile.label, "genesis_hash": chain.genesis_hash, "endpoint": url,
              "block_time_ms": chain.block_time_ms, "delay_blocks": delay, "delay_s": args.delay_s, "vault": vault,
              "thief_payee": payee, "decimals": chain.decimals, "symbol": chain.symbol, "summary": summary,
              "ablation": ablation, "drains": drains, "rows": tx.rows + thief_rows}
    out.write_text(json.dumps(result, indent=2) + "\n")
    print(markdown(result, out))
    print(f"BENCH {'PASS' if ok else 'FAIL'} -> {out.relative_to(ROOT)}")
    if args.md and ok:
        with args.md.open("a") as f:
            f.write("\n" + markdown(result, out) + "\n")
        print(f"summary appended to {args.md}")
    chain.substrate.close()
    return 0 if ok else 1


def markdown(r: dict, out: Path) -> str:
    s, a = r["summary"], r["ablation"]

    def row(name: str, key: str, unit: str) -> str:
        v = s[key]
        unit_ = unit if unit != "blocks" else "block" if v["p50"] == v["p95"] == v["max"] == 1 else unit
        return f"| {name} | {v['p50']} {unit_} | {v['p95']} {unit_} | {v['min']}–{v['max']} {unit_} |"

    lines = [f"### bench — {r['started']} — {r['label']}", "",
             f"`{out.relative_to(ROOT)}` · code `{r['code']}` · genesis `{r['genesis_hash']}` · block time "
             f"{r['block_time_ms']} ms · D = {r['delay_blocks']} blocks ({r['delay_s']} s) · vault `{r['vault']}`", "",
             f"**{s['drains']} thief drains** (scripts/thief.py, no Airlock code): {s['alerted_not_yours']}/{s['drains']} "
             f"alerted NOT YOURS, {s['vetoed_then_unannounced']}/{s['drains']} vetoed and then failed "
             f"`Proxy.Unannounced` when executed after the delay; moved to the thief: {s['moved_to_thief']} planck; "
             f"vault total {s['vault_total_before']} planck before, {s['vault_total_after']} after. Fewest blocks to "
             f"spare before a drain could have executed: {s['blocks_to_spare_min']} of {r['delay_blocks']}.", "",
             "| metric (nearest-rank) | p50 | p95 | range |", "|---|---|---|---|",
             row("alert latency: announce block timestamp → alert written", "alert_latency_s", "s"),
             row("alert latency in blocks (head at alert − announce block)", "alert_latency_blocks", "blocks"),
             row("veto inclusion: sent → included (automated, no human)", "veto_inclusion_s", "s"),
             row("veto inclusion in blocks (veto block − head when sent)", "veto_inclusion_blocks", "blocks"),
             row("announce → veto, on-chain timestamps", "announce_to_veto_s", "s"),
             row("announce → veto in blocks", "announce_to_veto_blocks", "blocks")]
    if a:
        lines += ["", f"**Ablation (`--ablate`, one control drain):** the same thief script with `--direct` against a "
                      f"control vault whose hot key has delay 0 moved {a['moved_fmt']} ({a['moved']} planck) of the "
                      f"control vault's {a['control_total_before_fmt']} in {a['extrinsics']} extrinsic (`{a['id']}`, "
                      f"outcome {a['outcome']}), {a['blocks_after_head']} block after the head it was sent at."]
    return "\n".join(lines)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--profile", default=os.environ.get("AIRLOCK_PROFILE", "interim-sdk"))
    ap.add_argument("--url", default=None, help="node websocket (default: the local interim endpoint)")
    ap.add_argument("--n", type=int, default=20, help="number of thief drains (default 20)")
    ap.add_argument("--delay-s", type=float, default=60, help="vault delay D in seconds (default 60)")
    ap.add_argument("--ablate", action="store_true", help="also run ONE delay-0 control drain")
    ap.add_argument("--md", type=Path, default=None, help="append the summary table to this markdown file (PASS only)")
    ap.add_argument("--render", type=Path, default=None, metavar="JSON",
                    help="no run: print (and with --md append) the table of a saved bench JSON")
    args = ap.parse_args(argv)
    if args.render:
        result = json.loads(args.render.read_text())
        text = markdown(result, args.render.resolve())
        print(text)
        if args.md and result["status"] == "passed":
            with args.md.open("a") as f:
                f.write("\n" + text + "\n")
        return 0 if result["status"] == "passed" else 1
    if args.n < 1:
        ap.error("--n must be at least 1")
    return run(args)


if __name__ == "__main__":
    sys.exit(main())
