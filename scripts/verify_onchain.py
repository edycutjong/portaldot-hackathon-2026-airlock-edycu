#!/usr/bin/env python3
"""Verify docs/PROOF.md against the chain: every row, re-fetched by extrinsic id, from a fresh process.

For each table row that carries an extrinsic id (`block-index`), the row's profile label (`interim-sdk — not V3`,
`interim-1002 — not V3`, `v3`) picks the chain. The endpoint must identify as that profile by genesis hash and runtime
code hash (`airlock/chain_profile.py`) — a label that does not match the chain behind it fails every row under it. Then
the extrinsic is looked up by id alone and its block hash (when the row has one), transaction hash and outcome must be
what the row says. Evidence JSON files (a bench, seed or spike run: `{"rows": [...], "profile": ...}`) can be given too;
their rows are checked the same way.

By default it reads over each local node's HTTP endpoint, not the websocket the runs used. A profile whose node is not
running is reported UNREACHABLE and fails the run (`--skip-unreachable` reports it and goes on); an endpoint that
answers as a different chain than the label is a MISMATCH and always fails. A table line that looks like a proof row
(a backticked `block-index`) but cannot be read is UNPARSED and fails too, so no row is skipped silently. Exit 0 only
if every row checked matched and at least one did.

usage: python scripts/verify_onchain.py [docs/PROOF.md] [evidence/bench-….json …] [--url interim-sdk=http://…]
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from airlock.chain_profile import PROFILES, ChainUnreachable, ProfileMismatch, UnknownChain, connect  # noqa: E402
from airlock.dispatch import error_namer  # noqa: E402
from airlock.receipts import refetch  # noqa: E402

HTTP = {"interim-sdk": "http://127.0.0.1:29944", "interim-1002": "http://127.0.0.1:19933"}
ID = re.compile(r"^\d+-\d+$")
ID_LIKE = re.compile(r"`\d+-\d+`")


def cell(value: str) -> str:
    return value.strip().strip("`").strip()


def profile_of(label: str) -> str | None:
    """`interim-sdk — not V3` -> interim-sdk; anything not starting with a known profile id -> None."""
    first = cell(label).split(" ")[0] if cell(label) else ""
    return first if first in PROFILES else None


def proof_rows(text: str, source: str = "docs/PROOF.md") -> list[dict]:
    """Every markdown table row with an extrinsic id, keyed by its table's header (extrinsic id, tx hash, profile,
    and block hash / outcome when the table has them)."""
    rows, header, section = [], None, ""
    for n, line in enumerate(text.splitlines(), 1):
        if line.startswith("### "):
            section, header = line[4:].strip(), None
            continue
        if not line.startswith("|"):
            header = None
            continue
        cells = [c.strip() for c in line.strip().strip("|").split("|")]
        if header is None:
            header = [c.lower() for c in cells]
            continue
        if set("".join(cells)) <= set("-: "):
            continue  # the |---| separator
        row = dict(zip(header, cells))
        xid = cell(row.get("extrinsic id", ""))
        if not ID.match(xid):
            if ID_LIKE.search(line):  # it carries an id, but not in a column this reader understands
                rows.append({"source": f"{source}:{n}", "section": section, "extrinsic_id": None, "profile": None,
                             "label": "", "block_hash": None, "extrinsic_hash": None, "outcome": None,
                             "unparsed": line.strip()[:160]})
            continue
        rows.append({"source": f"{source}:{n}", "section": section, "extrinsic_id": xid,
                     "profile": profile_of(row.get("profile", "")), "label": cell(row.get("profile", "")),
                     "block_hash": cell(row.get("block hash", "")) or None,
                     "extrinsic_hash": cell(row.get("tx hash", "")) or None,
                     "outcome": cell(row.get("outcome", "")) or None})
    return rows


def evidence_rows(path: Path) -> list[dict]:
    data = json.loads(path.read_text())
    default = data.get("profile")
    out = []
    for i, r in enumerate(data.get("rows", [])):
        label = r.get("profile") or default or ""
        out.append({"source": f"{path.relative_to(ROOT) if path.is_relative_to(ROOT) else path}#rows[{i}]",
                    "section": data.get("started", ""), "extrinsic_id": r["extrinsic_id"],
                    "profile": profile_of(label) or default, "label": label, "block_hash": r.get("block_hash"),
                    "extrinsic_hash": r.get("extrinsic_hash"), "outcome": r.get("outcome"),
                    "genesis_hash": data.get("genesis_hash")})
    return out


def check(row: dict, got) -> list[str]:
    diffs = []
    for key, value in (("block_hash", got.block_hash), ("extrinsic_hash", got.extrinsic_hash), ("outcome", got.outcome)):
        want = row.get(key)
        if want is not None and str(want).lower() != str(value).lower():
            diffs.append(f"{key}: row says {want}, chain says {value}")
    return diffs


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("sources", nargs="*", type=Path, help="PROOF.md and/or evidence JSON files (default docs/PROOF.md)")
    ap.add_argument("--url", action="append", default=[], metavar="PROFILE=URL", help="endpoint for a profile")
    ap.add_argument("--skip-unreachable", action="store_true", help="report a profile whose node is down, do not fail")
    args = ap.parse_args(argv)

    urls = dict(HTTP)
    for spec in args.url:
        name, _, url = spec.partition("=")
        if name not in PROFILES or not url:
            ap.error(f"--url {spec!r}: expected PROFILE=URL with PROFILE in {sorted(PROFILES)}")
        urls[name] = url
    sources = args.sources or [ROOT / "docs" / "PROOF.md"]
    rows = []
    for src in sources:
        rows += evidence_rows(src) if src.suffix == ".json" else proof_rows(src.read_text(), str(src))
    if not rows:
        print("no rows with an extrinsic id found", file=sys.stderr)
        return 1

    started = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    chains, cache, results = {}, {}, []
    unreachable, mismatched = set(), set()
    for row in rows:
        p = row["profile"]
        if row.get("unparsed"):
            results.append({**row, "status": "UNPARSED", "diffs": ["a table row with an extrinsic id this reader "
                                                                   "cannot place (no 'extrinsic id' column?)"]})
            continue
        if p is None:
            results.append({**row, "status": "UNLABELLED", "diffs": [f"profile label {row['label']!r} names no profile"]})
            continue
        if p not in chains and p not in unreachable | mismatched:
            try:
                c = connect(urls.get(p) or PROFILES[p].default_endpoint, p)
                chains[p] = (c, error_namer(c.substrate.get_metadata().pallets), c.substrate.get_block_number(None))
                print(f"{p}: {c.url} identifies as {c.profile.label} · genesis {c.genesis_hash} · "
                      f"head #{chains[p][2]}")
            except ChainUnreachable as e:
                unreachable.add(p)
                print(f"{p}: UNREACHABLE ({e})")
            except (UnknownChain, ProfileMismatch) as e:  # the label does not match the chain behind the endpoint
                mismatched.add(p)
                print(f"{p}: LABEL MISMATCH — {type(e).__name__}: {e}")
        if p in unreachable:
            results.append({**row, "status": "UNREACHABLE", "diffs": []})
            continue
        if p in mismatched:
            results.append({**row, "status": "MISMATCH", "diffs": [f"the {p} endpoint is not a {p} chain"]})
            continue
        chain, namer, _ = chains[p]
        if row.get("genesis_hash") and row["genesis_hash"].lower() != chain.genesis_hash.lower():
            results.append({**row, "status": "DIFF", "diffs": [f"recorded on genesis {row['genesis_hash']}"]})
            continue
        key = (p, row["extrinsic_id"])
        if key not in cache:
            try:
                cache[key] = refetch(chain.substrate, row["extrinsic_id"], namer)
            except Exception as e:  # a missing extrinsic is a finding, not a crash
                cache[key] = e
        got = cache[key]
        if isinstance(got, Exception):
            results.append({**row, "status": "MISSING", "diffs": [f"{type(got).__name__}: {got}"]})
            continue
        diffs = check(row, got)
        results.append({**row, "status": "MATCH" if not diffs else "DIFF", "diffs": diffs, "call": got.call,
                        "chain_outcome": got.outcome})
    for c, _, _ in chains.values():
        c.substrate.close()

    bad = [r for r in results if r["status"] not in ("MATCH",) and not (r["status"] == "UNREACHABLE"
                                                                           and args.skip_unreachable)]
    for r in results:
        if r["status"] != "MATCH":
            print(f"{r['status']:<11} {r['extrinsic_id'] or '-':<9} {r['source']}  {'; '.join(r['diffs'])}")
    by_profile = {}
    for r in results:
        s = by_profile.setdefault(r["profile"] or "unlabelled", {"rows": 0, "match": 0, "ids": set()})
        s["rows"] += 1
        s["match"] += r["status"] == "MATCH"
        if r["extrinsic_id"]:
            s["ids"].add(r["extrinsic_id"])
    for p, s in sorted(by_profile.items()):
        print(f"{p}: {s['match']}/{s['rows']} rows matched ({len(s['ids'])} distinct extrinsics)")
    out = ROOT / "evidence" / f"verify-onchain-{started.replace(':', '')}.json"
    out.write_text(json.dumps({"started": started, "sources": [str(s) for s in sources],
                               "endpoints": {p: c.url for p, (c, _, _) in chains.items()},
                               "heads": {p: h for p, (_, _, h) in chains.items()},
                               "summary": {p: {"rows": s["rows"], "matched": s["match"], "distinct": len(s["ids"])}
                                           for p, s in by_profile.items()},
                               "results": results}, indent=2, default=str) + "\n")
    matched = sum(r["status"] == "MATCH" for r in results)
    ok = not bad and matched > 0
    print(f"VERIFY {'PASS' if ok else 'FAIL'}: {matched}/{len(results)} rows matched"
          + (f" ({len(results) - matched} unreachable, skipped)" if ok and matched < len(results) else "")
          + f" -> {out.relative_to(ROOT)}")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
