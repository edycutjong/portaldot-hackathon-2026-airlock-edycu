#!/usr/bin/env python3
"""Watcher test harness: a delegate's raw `Proxy.announce`, written with plain substrate-interface.

It deliberately imports nothing from `airlock`, so an alert it triggers shows the watcher catching an announcement made
by any tool, not just Airlock's own. Dev keys on local interim chains only: `//Charlie` is the delegate, the vault's
creator is `//Alice` unless COLD names another dev key.

  python scripts/raw_announce.py setup    <ws-url> <index> [delay]   # new pure vault, //Charlie as a delayed delegate
  python scripts/raw_announce.py announce <ws-url> <vault> <tokens>  # //Charlie announces transfer_keep_alive(//Eve, …)

Each step prints one JSON line (vault / extrinsic id / block hash / tx hash / call hash).
"""
import hashlib
import json
import os
import sys

from substrateinterface import Keypair, SubstrateInterface

DEV_1002_GENESIS = "0x53c36c0fc46f5bb2429d701b5ad7a0577b343aa53c4964333f1922611974dd22"  # needs the legacy decoder
DEV_SDK_GENESIS = "0x0c4402ef4ccc34d28b16523e1cf26831716527c61a65d8e0840ab26e1636c8a2"
# Dev keys are public: sign only on the two local interim dev chains (copied here so this file imports no airlock code)
INTERIM_GENESES = (DEV_SDK_GENESIS, DEV_1002_GENESIS)
DEV_KEYS = ("//Alice", "//Charlie", "//Dave")


def out(**fields):
    print(json.dumps(fields), flush=True)


def main(argv):
    if len(argv) < 4 or argv[1] not in ("setup", "announce"):
        sys.exit(__doc__)
    cmd, url = argv[1], argv[2]
    cold_uri = os.environ.get("COLD", "//Alice")
    if cold_uri not in DEV_KEYS:
        sys.exit(f"COLD must be one of the dev keys {DEV_KEYS}")
    s = SubstrateInterface(url=url)
    genesis = s.get_block_hash(0)
    if genesis not in INTERIM_GENESES:
        sys.exit(f"refusing to sign with public dev keys on {url}: genesis {genesis} is not a local interim dev chain")
    legacy = genesis == DEV_1002_GENESIS
    if legacy:
        s = SubstrateInterface(url=url, type_registry_preset="legacy")
    decimals = (s.properties or {}).get("tokenDecimals") or 14
    decimals = decimals[0] if isinstance(decimals, list) else decimals
    cold, hot, eve = (Keypair.create_from_uri(u) for u in (cold_uri, "//Charlie", "//Eve"))

    def send(keypair, call):
        receipt = s.submit_extrinsic(s.create_signed_extrinsic(call=call, keypair=keypair), wait_for_inclusion=True)
        if not receipt.is_success:
            sys.exit(f"failed: {receipt.error_message}")
        return receipt

    if cmd == "setup":
        index, delay = int(argv[3]), int(argv[4]) if len(argv) > 4 else 20
        receipt = send(cold, s.compose_call("Proxy", "anonymous" if legacy else "create_pure",
                                            {"proxy_type": "Any", "delay": 0, "index": index}))
        event = next(e.value for e in receipt.triggered_events
                     if e.value["event_id"] in ("PureCreated", "AnonymousCreated"))
        attrs = event["attributes"]
        vault = attrs["pure"] if isinstance(attrs, dict) else (attrs[0]["value"] if isinstance(attrs[0], dict) else attrs[0])
        send(cold, s.compose_call("Balances", "transfer_keep_alive", {"dest": vault, "value": 500 * 10**decimals}))
        add = s.compose_call("Proxy", "add_proxy", {"delegate": hot.ss58_address, "proxy_type": "Any", "delay": delay})
        send(cold, s.compose_call("Proxy", "proxy", {"real": vault, "force_proxy_type": None, "call": add}))
        out(step="setup", vault=vault, hot=hot.ss58_address, delay=delay)
    else:
        vault, tokens = argv[3], int(argv[4])
        theft = s.compose_call("Balances", "transfer_keep_alive",
                               {"dest": eve.ss58_address, "value": tokens * 10**decimals})
        call_hash = "0x" + hashlib.blake2b(theft.data.data, digest_size=32).hexdigest()
        receipt = send(hot, s.compose_call("Proxy", "announce", {"real": vault, "call_hash": call_hash}))
        out(step="announce", script="scripts/raw_announce.py (no airlock imports)", vault=vault,
            delegate=hot.ss58_address, call_hash=call_hash, extrinsic_id=receipt.get_extrinsic_identifier(),
            block_hash=receipt.block_hash, extrinsic_hash=receipt.extrinsic_hash,
            block=s.get_block_number(receipt.block_hash))


if __name__ == "__main__":
    main(sys.argv)
