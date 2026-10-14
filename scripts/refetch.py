#!/usr/bin/env python3
"""Re-fetch recorded extrinsics by extrinsic id, in a fresh process, and check them against the recorded evidence.

Point it at a spike evidence file and, ideally, at a different connection than the one that sent the transactions
(e.g. the node's HTTP endpoint instead of its websocket, or another machine's view of the same chain). Each row is
looked up by its `block-index` id alone; block hash, transaction hash and outcome must all match what was recorded.

usage: python scripts/refetch.py evidence/spike-proxy-….json [--url http://127.0.0.1:29944] [--proof docs/PROOF.md]
       python scripts/refetch.py evidence/raw-announce-….jsonl [--url …]   # the watcher-evidence announces
       python scripts/refetch.py --id 85-2 --id 86-2 [--url …]              # found by id, compared with nothing
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from airlock.chain_profile import ProfileMismatch, UnknownChain, connect  # noqa: E402
from airlock.dispatch import error_namer  # noqa: E402
from airlock.receipts import as_dict, compare, refetch  # noqa: E402


def transport(url: str) -> str:
    return url.split(":", 1)[0]


def shown(path: Path) -> str:
    """A path as written in evidence: relative to the repo when inside it."""
    try:
        return str(path.resolve().relative_to(ROOT))
    except ValueError:
        return str(path)


def load(path: Path) -> dict:
    """A spike evidence JSON, or a raw-announce JSON-lines file (its announce lines become the rows)."""
    if path.suffix != ".jsonl":
        return json.loads(path.read_text())
    lines = [json.loads(line) for line in path.read_text().splitlines() if line.strip()]
    rows = [r for r in lines if "extrinsic_id" in r]
    geneses = {r["genesis_hash"] for r in lines if r.get("genesis_hash")}
    if len(geneses) > 1:
        raise SystemExit(f"{path}: rows from more than one chain")
    return {"status": "passed", "rows": rows, "genesis_hash": next(iter(geneses), None), "profile": None,
            "started": path.stem, "endpoint": "ws://"}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("evidence", nargs="?", type=Path, help="spike evidence JSON whose rows to re-fetch")
    ap.add_argument("--id", action="append", default=[], help="an extrinsic id to look up (repeatable)")
    ap.add_argument("--profile", default=None, help="expected profile (default: the evidence file's, else auto)")
    ap.add_argument("--url", default=None, help="node endpoint, ws:// or http:// (default $AIRLOCK_WSS, else profile)")
    ap.add_argument("--proof", type=Path, default=None, help="append a re-fetch table to this PROOF.md (all found/matched only)")
    args = ap.parse_args(argv)

    recorded = load(args.evidence) if args.evidence else None
    if recorded is None and not args.id:
        ap.error("give an evidence file or at least one --id")
    if recorded is not None and args.id:
        ap.error("give an evidence file or --id, not both")
    if recorded is not None and recorded.get("status") != "passed":
        ap.error(f"{args.evidence} is a {recorded.get('status')} run; re-fetch passing runs only")
    rows = recorded["rows"] if recorded else [{"extrinsic_id": i} for i in args.id]
    if not rows:
        ap.error("nothing to re-fetch: the evidence file has no rows")
    profile = args.profile or (recorded or {}).get("profile")

    try:
        chain = connect(args.url, profile)
    except (UnknownChain, ProfileMismatch) as e:
        print(f"REFETCH FAILED: {type(e).__name__}: {e}", file=sys.stderr)
        return 1
    if recorded and recorded.get("genesis_hash") and chain.genesis_hash != recorded["genesis_hash"]:
        print(f"REFETCH FAILED: genesis {chain.genesis_hash} is not the recorded {recorded['genesis_hash']}",
              file=sys.stderr)
        chain.substrate.close()
        return 1

    started = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    name_error = error_namer(chain.substrate.get_metadata().pallets)
    results, bad = [], 0
    try:
        head = chain.substrate.get_block_number(None)
        for row in rows:
            try:
                got = refetch(chain.substrate, row["extrinsic_id"], name_error)
                diffs = compare(row, got) if recorded else []
                # with --id there is no recorded row to compare against: found, not matched
                entry = {**as_dict(got), "match": (not diffs) if recorded else None, "diffs": diffs}
            except Exception as e:  # a missing extrinsic is a result to report, not a crash
                entry = {"extrinsic_id": row["extrinsic_id"], "match": False, "diffs": [f"{type(e).__name__}: {e}"]}
            ok = entry["match"] is not False
            bad += 0 if ok else 1
            results.append(entry)
            mark = "FOUND" if entry["match"] is None else ("MATCH" if ok else "DIFF ")
            print(f"{mark} {entry['extrinsic_id']:<8} {entry.get('call', '?'):<52} "
                  f"outcome={entry.get('outcome', '?'):<18} tx={entry.get('extrinsic_hash', '?')}"
                  + ("" if ok else f"  {'; '.join(entry['diffs'])}"))
    finally:
        chain.substrate.close()

    label = f"{chain.profile.id}" + (" — not V3" if chain.profile.is_interim else "")
    verb = "re-fetched and matched" if recorded else "found"
    summary = f"{len(results) - bad}/{len(results)} {verb} by extrinsic id over {transport(chain.url)} ({label})"
    print(summary)
    out = ROOT / "evidence" / f"refetch-{chain.profile.id}-{started.replace(':', '')}.json"
    out.parent.mkdir(exist_ok=True)
    out.write_text(json.dumps({"started": started, "endpoint": chain.url, "transport": transport(chain.url),
                               "profile": chain.profile.id, "label": chain.profile.label,
                               "genesis_hash": chain.genesis_hash, "head_at_refetch": head,
                               "source": shown(args.evidence) if args.evidence else None,
                               "matched": len(results) - bad, "total": len(results), "rows": results}, indent=2) + "\n")
    print(f"-> {shown(out)}")

    if bad:
        return 1
    if args.proof:
        if recorded:
            title = f"the {recorded['started']} {chain.profile.id} run, by extrinsic id"
            detail = (f"Fresh process over {transport(chain.url)} (`{chain.url}`; the run itself was sent over "
                      f"{transport(recorded['endpoint'])}) at head #{head} · `{shown(out)}` · every row: same block "
                      f"hash, tx hash and outcome as recorded.")
        else:
            title = f"{', '.join(args.id)} on {chain.profile.id}, found by extrinsic id"
            detail = (f"Fresh process over {transport(chain.url)} (`{chain.url}`) at head #{head} · `{shown(out)}` · "
                      f"looked up by id alone: no recorded row to compare with — the block hash, call, outcome and tx "
                      f"hash below are what the chain returns.")
        lines = [f"### re-fetch — {started} — {title}", "", detail, "",
                 "| extrinsic id | call | outcome | block hash | tx hash | profile |",
                 "|---|---|---|---|---|---|"]
        lines += [f"| `{r['extrinsic_id']}` | `{r['call']}` | {r['outcome']} | `{r['block_hash']}` | "
                  f"`{r['extrinsic_hash']}` | {label} |" for r in results]
        with args.proof.open("a") as f:
            f.write("\n" + "\n".join(lines) + "\n")
        print(f"re-fetch table appended to {args.proof}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
