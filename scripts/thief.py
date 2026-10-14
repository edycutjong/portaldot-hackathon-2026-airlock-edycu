#!/usr/bin/env python3
"""The thief: what someone holding a phished hot key would run. Plain substrate-interface, no Airlock code.

It imports nothing from `airlock`, so the alert it triggers and the veto that stops it are tested against a raw
`proxy.announce`, not against Airlock talking to itself. No intent is registered and no preimage is published: the
chain sees a call hash only.

  python scripts/thief.py --vault <V> --to <address> --amount all        # announce a sweep of the vault
  python scripts/thief.py --vault <V> --to <address> --amount all --execute   # after the delay: try to take it
  python scripts/thief.py --vault <V> --to <address> --amount 10 --direct     # skip the announcement (I1)

--post-intent <registry URL>: before announcing, also try to register the sweep as an owner intent — POST
/api/intents with the call's bytes but no owner token (the thief has the hot key, not the token). The registry answers
401 and stores nothing, so the announcement stays NOT YOURS even though its preimage is now public (I6).

--execute and --direct report the chain's verdict instead of failing: after a veto, --execute is expected to end in
Proxy.Unannounced, and --direct always does for a delayed key. `--amount all` = the vault's free balance minus locks
and the existential deposit, read at announce time and recorded in --state, so --execute rebuilds the identical call.

The hot key: --hot file:<path> (a mnemonic), or by default the demo key //AirlockDemo//hot — a public dev derivation,
accepted only on the two local interim dev chains. Each step prints one JSON line (extrinsic id, block hash, tx hash,
call hash, outcome, genesis), re-fetchable with scripts/refetch.py.
"""

import argparse
import hashlib
import json
import sys
import urllib.error
import urllib.request
from decimal import Decimal, InvalidOperation
from pathlib import Path

from substrateinterface import Keypair, SubstrateInterface

DEV_1002_GENESIS = "0x53c36c0fc46f5bb2429d701b5ad7a0577b343aa53c4964333f1922611974dd22"  # needs the legacy decoder
DEV_SDK_GENESIS = "0x0c4402ef4ccc34d28b16523e1cf26831716527c61a65d8e0840ab26e1636c8a2"
INTERIM_GENESES = (DEV_SDK_GENESIS, DEV_1002_GENESIS)  # copied, so this file imports no airlock code
# the 1002 metadata spells the Announcements value with stray spaces the legacy preset cannot parse
LEGACY_TYPES = {"(BoundedVec<Announcement<AccountId, CallHashOf,  BlockNumber>, MaxPending,>, BalanceOf,)":
                "(Vec<ProxyAnnouncement>, BalanceOf)"}
DEMO_HOT = "//AirlockDemo//hot"


def out(**fields):
    print(json.dumps(fields), flush=True)


def connect(url):
    s = SubstrateInterface(url=url)
    genesis = s.get_block_hash(0)
    if genesis == DEV_1002_GENESIS:
        s.close()
        s = SubstrateInterface(url=url, type_registry_preset="legacy", type_registry={"types": LEGACY_TYPES})
    return s, genesis


def hot_key(spec, genesis):
    if spec.startswith("file:"):
        return Keypair.create_from_uri(Path(spec[5:]).expanduser().read_text().strip())
    if not spec.startswith("//"):
        sys.exit("--hot must be file:<path> or a //dev URI (never a seed on the command line)")
    if genesis not in INTERIM_GENESES:
        sys.exit(f"refusing a public dev key on genesis {genesis}: not a local interim dev chain")
    return Keypair.create_from_uri(spec)


def decimals_of(s):
    d = (s.properties or {}).get("tokenDecimals")
    d = d[0] if isinstance(d, list) else d
    return 14 if d is None else int(d)  # the 1002 dev node reports none; Portaldot POT has 14


def sweepable(s, vault):
    """Everything a transfer_keep_alive could take: free - locks - existential deposit."""
    data = s.query("System", "Account", [vault]).value["data"]
    locked = int(data["frozen"]) if "frozen" in data else int(data.get("misc_frozen", 0))
    ed = int(s.get_constant("Balances", "ExistentialDeposit").value)
    return max(int(data["free"]) - locked - ed, 0)


def _unwrap(value):
    return value["value"] if isinstance(value, dict) and set(value) == {"type", "value"} else value


def _field(attrs, name, position):
    return _unwrap(attrs[name] if isinstance(attrs, dict) else attrs[position])


def _error_name(s, err):
    """'Pallet.Error' for a module error (index -> name from metadata), else the DispatchError variant."""
    err = _unwrap(err)
    if isinstance(err, dict) and "Module" in err:
        module = err["Module"]
        try:
            raw = module["error"]
            i = bytes.fromhex(raw[2:])[0] if isinstance(raw, str) else int(raw if isinstance(raw, int) else raw[0])
        except (KeyError, ValueError, TypeError, IndexError):
            return f"Module.unreadable:{module!r}"
        for pallet in s.get_metadata().pallets:
            if int(getattr(pallet["index"], "value", pallet["index"])) == int(module["index"]):
                errors = pallet.errors or []
                return f"{pallet.name}.{errors[i].name if i < len(errors) else f'error#{i}'}"
        return f"pallet#{module['index']}.error#{i}"
    if isinstance(err, dict) and len(err) == 1:
        variant, detail = next(iter(err.items()))
        return f"{variant}.{detail}" if isinstance(detail, str) else variant
    return str(err)


def verdict(s, receipt):
    """'ok', 'Pallet.Error' when the extrinsic failed, 'inner:Pallet.Error' when the proxied call failed inside it."""
    for e in receipt.triggered_events:
        v = e.value
        if (v["module_id"], v["event_id"]) == ("System", "ExtrinsicFailed"):
            return _error_name(s, _field(v["attributes"], "dispatch_error", 0))
        if (v["module_id"], v["event_id"]) == ("Proxy", "ProxyExecuted"):
            result = _field(v["attributes"], "result", 0)
            if not (result == "Ok" or (isinstance(result, dict) and set(result) == {"Ok"})):
                inner = result.get("Err", result.get("Error")) if isinstance(result, dict) else result
                return f"inner:{_error_name(s, inner)}"
    return "ok" if receipt.is_success else "failed"


def post_intent(registry, vault, call_bytes, call_hash):
    """What a thief can send the registry: the preimage, without the owner token. Returns the HTTP status."""
    body = json.dumps({"vault": vault, "call_hex": "0x" + call_bytes.hex(), "call_hash": call_hash}).encode()
    request = urllib.request.Request(registry.rstrip("/") + "/api/intents", data=body, method="POST",
                                     headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(request, timeout=10) as response:
            return response.status
    except urllib.error.HTTPError as e:
        return e.code
    except (urllib.error.URLError, OSError) as e:
        return f"unreachable:{type(e).__name__}"


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--url", default="ws://127.0.0.1:29944", help="node websocket (default: local interim-sdk)")
    ap.add_argument("--vault", required=True)
    ap.add_argument("--to", required=True, help="where the stolen funds would go")
    ap.add_argument("--amount", required=True, help="tokens, or 'all' (the vault's whole sweepable balance)")
    ap.add_argument("--hot", default=DEMO_HOT, help="the stolen hot key: file:<path>, or a //dev URI on interim chains")
    ap.add_argument("--state", type=Path, default=Path("/tmp/airlock-thief.json"),
                    help="where the announced amount is kept for --execute (default /tmp/airlock-thief.json)")
    ap.add_argument("--post-intent", default=None, metavar="URL",
                    help="before announcing, POST the sweep to this intent registry without the owner token")
    mode = ap.add_mutually_exclusive_group()
    mode.add_argument("--execute", action="store_true", help="proxy_announced the announced sweep (after the delay)")
    mode.add_argument("--direct", action="store_true", help="proxy.proxy the sweep with no announcement")
    args = ap.parse_args(argv)

    s, genesis = connect(args.url)
    hot = hot_key(args.hot, genesis)
    if args.amount == "all":
        if args.execute:  # rebuild the exact call that was announced
            st = json.loads(args.state.read_text())
            if (st["vault"], st["to"]) != (args.vault, args.to):
                sys.exit(f"{args.state} records a sweep of {st['vault']} to {st['to']}, not this one")
            planck = int(st["planck"])
        else:
            planck = sweepable(s, args.vault)
    else:
        try:
            exact = Decimal(args.amount).scaleb(decimals_of(s))
        except InvalidOperation:
            sys.exit(f"--amount {args.amount!r} is not a number (or 'all')")
        if not exact.is_finite() or exact <= 0 or exact != exact.to_integral_value():
            sys.exit(f"--amount {args.amount!r} must be positive and a whole number of planck")
        planck = int(exact)
    sweep = s.compose_call("Balances", "transfer_keep_alive", {"dest": args.to, "value": planck})
    call_hash = "0x" + hashlib.blake2b(sweep.data.data, digest_size=32).hexdigest()
    if args.execute and args.amount == "all" and call_hash != st["call_hash"]:
        sys.exit(f"rebuilt call hash {call_hash} differs from the announced {st['call_hash']}: not sending")

    if args.execute:
        step, call = "execute", s.compose_call("Proxy", "proxy_announced", {
            "delegate": hot.ss58_address, "real": args.vault, "force_proxy_type": None, "call": sweep})
    elif args.direct:
        step, call = "direct", s.compose_call("Proxy", "proxy", {"real": args.vault, "force_proxy_type": None,
                                                                  "call": sweep})
    else:
        step, call = "announce", s.compose_call("Proxy", "announce", {"real": args.vault, "call_hash": call_hash})
        if args.post_intent:
            status = post_intent(args.post_intent, args.vault, bytes(sweep.data.data), call_hash)
            out(step="post-intent", script="scripts/thief.py (no airlock imports)", registry=args.post_intent,
                vault=args.vault, call_hash=call_hash, http_status=status, token="none")
    receipt = s.submit_extrinsic(s.create_signed_extrinsic(call=call, keypair=hot), wait_for_inclusion=True)
    try:
        result = verdict(s, receipt)
    except Exception as e:  # never lose the evidence line of a transaction that was sent
        result = f"unreadable:{type(e).__name__}"
    if step == "announce" and result == "ok":
        args.state.write_text(json.dumps({"vault": args.vault, "to": args.to, "planck": str(planck),
                                          "call_hash": call_hash}) + "\n")
    out(step=step, script="scripts/thief.py (no airlock imports)", vault=args.vault, delegate=hot.ss58_address,
        to=args.to, planck=str(planck), call_hash=call_hash, outcome=result,
        extrinsic_id=receipt.get_extrinsic_identifier(), block_hash=receipt.block_hash,
        extrinsic_hash=receipt.extrinsic_hash, block=s.get_block_number(receipt.block_hash), genesis_hash=genesis)
    s.close()
    return 0 if step != "announce" or result == "ok" else 1


if __name__ == "__main__":
    sys.exit(main())
