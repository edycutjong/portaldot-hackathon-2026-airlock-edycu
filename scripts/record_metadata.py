#!/usr/bin/env python3
"""Record a live chain's runtime metadata as a test fixture, keyed by its chain profile.

Writes tests/fixtures/<profile>.metadata.scale.gz (raw state_getMetadata bytes) and <profile>.json (identity).

usage: python scripts/record_metadata.py [--profile interim-sdk] [--url ws://…]
"""

from __future__ import annotations

import argparse
import gzip
import hashlib
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from airlock.chain_profile import connect  # noqa: E402
from airlock.offline import FIXTURES  # noqa: E402


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--profile", default=None)
    ap.add_argument("--url", default=None)
    args = ap.parse_args(argv)

    chain = connect(args.url, args.profile)
    raw = bytes.fromhex(chain.substrate.rpc_request("state_getMetadata", [])["result"][2:])
    FIXTURES.mkdir(parents=True, exist_ok=True)
    pid = chain.profile.id
    # mtime=0 keeps the gzip byte-identical across re-recordings of the same metadata
    (FIXTURES / f"{pid}.metadata.scale.gz").write_bytes(gzip.compress(raw, 9, mtime=0))
    info = {
        "profile": pid,
        "label": chain.profile.label,
        "genesis_hash": chain.genesis_hash,
        "code_hash": chain.code_hash,
        "spec_name": chain.spec_name,
        "spec_version": chain.spec_version,
        "metadata_sha256": hashlib.sha256(raw).hexdigest(),
        "metadata_bytes": len(raw),
    }
    (FIXTURES / f"{pid}.json").write_text(json.dumps(info, indent=2) + "\n")
    print(f"{pid}: {len(raw):,} bytes of metadata -> {FIXTURES.relative_to(ROOT)}/{pid}.metadata.scale.gz")
    return 0


if __name__ == "__main__":
    sys.exit(main())
