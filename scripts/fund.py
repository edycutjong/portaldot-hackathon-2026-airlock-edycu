#!/usr/bin/env python3
"""Top up accounts from one funder key on a real testnet (interim dev chains fund themselves from //Alice).

Each --to ADDRESS=TOKENS is raised to at least TOKENS of free balance: only the shortfall is sent, with
transfer_keep_alive, so a re-run sends nothing. Every transfer is a recorded row (extrinsic id, block hash, tx hash,
profile label) in evidence/fund-<profile>-<time>.json. --plan prints the shortfalls and the funder's balance only.

usage: python scripts/fund.py --profile v3 --from file:~/.config/airlock/v3-funder.seed \
           --to 5Cold…=55 --to 5Hot…=30 [--plan]
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from airlock.chain_profile import ChainUnreachable, ProfileMismatch, UnknownChain, connect  # noqa: E402
from airlock.keys import KeyRefused, load_key  # noqa: E402
from airlock.ops import NotConfirmed, Refused, Sender, SubmitRejected, TxFailed, code_version, transfer  # noqa: E402


def parse_target(text: str) -> tuple[str, str]:
    address, sep, amount = text.partition("=")
    if not sep or not address or not amount:
        raise argparse.ArgumentTypeError(f"expected ADDRESS=TOKENS, got {text!r}")
    return address, amount


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--profile", default=None, help="expected chain profile (default $AIRLOCK_PROFILE, else auto)")
    ap.add_argument("--url", default=None, help="node websocket (default $AIRLOCK_WSS, else the profile default)")
    ap.add_argument("--from", dest="funder", required=True, help="funder key spec: file:<path> or env:<VAR>")
    ap.add_argument("--to", type=parse_target, action="append", required=True, metavar="ADDRESS=TOKENS")
    ap.add_argument("--plan", action="store_true", help="print the shortfalls and exit; send nothing")
    args = ap.parse_args(argv)

    try:
        chain = connect(args.url, args.profile)
    except (UnknownChain, ProfileMismatch, ChainUnreachable) as e:
        print(f"FUND REFUSED: {e}", file=sys.stderr)
        return 2
    try:
        try:
            funder = load_key(args.funder, chain.profile, "funder")
            targets = [(address, chain.units(amount)) for address, amount in args.to]
        except (KeyRefused, ValueError) as e:
            print(f"FUND REFUSED: {e}", file=sys.stderr)
            return 2
        sp = Sender(chain, echo=lambda r: print(f"[{r['profile']}] {r['step']} id={r['extrinsic_id']} "
                                                f"block={r['block_hash']} tx={r['extrinsic_hash']} {r['outcome']}"))
        short = [(a, want - sp.free(a)) for a, want in targets]
        short = [(a, n) for a, n in short if n > 0]
        have = sp.free(funder.ss58_address)
        need = sum(n for _, n in short)
        print(f"{chain.profile.label}: funder {funder.ss58_address} holds {chain.fmt(have)}; "
              f"{len(short)} account(s) short by {chain.fmt(need)} in total")
        for a, n in short:
            print(f"  {a} needs {chain.fmt(n)}")
        if args.plan or not short:
            return 0
        if have < need + chain.existential_deposit:
            print(f"FUND REFUSED: the funder needs {chain.fmt(need + chain.existential_deposit)} (+ fees), "
                  f"holds {chain.fmt(have)}", file=sys.stderr)
            return 3
        started = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        failure = None
        try:
            for a, n in short:
                sp.send(f"fund {a[:8]}…", "funder", funder, transfer(chain, a, n), note=chain.fmt(n))
        except (TxFailed, Refused, SubmitRejected, NotConfirmed) as e:
            failure = f"{type(e).__name__}: {e}"
            print(f"FUND FAILED: {failure}", file=sys.stderr)
        out = ROOT / "evidence" / f"fund-{chain.profile.id}-{started.replace(':', '')}.json"
        out.write_text(json.dumps({"started": started, "status": "failed" if failure else "passed", "failure": failure,
                                   "code": code_version(), "profile": chain.profile.id, "label": chain.profile.label,
                                   "endpoint": chain.url, "genesis_hash": chain.genesis_hash,
                                   "code_hash": chain.code_hash, "runtime": f"{chain.spec_name}/{chain.spec_version}",
                                   "funder": funder.ss58_address, "rows": sp.rows}, indent=2) + "\n")
        print(f"{len(sp.rows)} transfers -> {out.relative_to(ROOT)}")
        return 1 if failure else 0
    finally:
        chain.substrate.close()


if __name__ == "__main__":
    sys.exit(main())
