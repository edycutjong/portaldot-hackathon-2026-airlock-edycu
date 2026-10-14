"""airlock — the vault owner's command line: create a vault, announce and execute spends, veto, panic.

  airlock create   --cold KEY --hot ADDRESS (--delay BLOCKS | --delay-s SECONDS) [--fund AMOUNT] [--index N]
  airlock announce --hot KEY --vault VAULT --to ADDRESS --amount AMOUNT
  airlock execute  --hot KEY --vault VAULT (--to ADDRESS --amount AMOUNT | --call 0x…) [--no-wait]
  airlock veto     --cold KEY --vault VAULT --delegate HOT --hash 0x…
  airlock panic    --cold KEY --vault VAULT --delegate HOT
  airlock status   --vault VAULT

KEY is file:<path>, env:<VAR>, or a //dev URI on an interim dev chain (see airlock/keys.py); --cold/--hot default to
$AIRLOCK_COLD_KEY/$AIRLOCK_HOT_KEY, so the veto command an alert prints runs as pasted. The chain is $AIRLOCK_PROFILE /
$AIRLOCK_WSS (or --profile/--url); an unknown chain is refused before anything is signed.

Exit status: 0 done and confirmed by a read-back; 1 an extrinsic failed or its effect is not visible; 2 refused
before signing (bad key spec, unknown chain, nothing pending, delay not passed with --no-wait …).
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from typing import Any

from substrateinterface.utils.ss58 import ss58_decode

from .callhash import call_bytes, call_hash, same_account
from .chain_profile import Chain, ChainUnreachable, ProfileMismatch, UnknownChain, _is_connection_error, connect
from .keys import KeyRefused, load_key
from .ops import (NotConfirmed, Refused, Sender, SubmitRejected, TxFailed, VaultIncomplete, announce, blocks_for, create_vault, deposits, execute,
                  panic, pending_for, transfer, veto)

PROG = "airlock"


class Out:
    """Human lines on stdout, or one JSON object per line with --json (rows as they land, then the result)."""

    def __init__(self, as_json: bool):
        self.as_json = as_json

    def say(self, text: str) -> None:
        if not self.as_json:
            print(text, flush=True)

    def row(self, row: dict) -> None:
        if self.as_json:
            print(json.dumps({"tx": row}), flush=True)
        else:
            mark = "ok" if row["outcome"] == row["expected"] else "FAILED"
            print(f"[{row['profile']}] {mark:<6} {row['step']:<20} {row['call']:<44} by {row['signer']:<4} "
                  f"#{row['block']} id {row['extrinsic_id']} tx {row['extrinsic_hash']}"
                  + ("" if row["outcome"] == "ok" else f" outcome {row['outcome']}"), flush=True)

    def result(self, **fields: Any) -> None:
        if self.as_json:
            print(json.dumps({"result": fields}), flush=True)


def address(value: str, what: str) -> str:
    """An SS58 address (any prefix). A 0x public key is refused: ss58_decode passes any hex through unchecked."""
    try:
        if value.startswith("0x"):
            raise ValueError("hex")
        ss58_decode(value)
    except (ValueError, TypeError) as e:
        raise Refused(f"{what} {value!r} is not an SS58 address") from e
    return value


def seconds(blocks: int, chain: Chain) -> str:
    if not chain.block_time_ms:
        return ""
    s = blocks * chain.block_time_ms / 1000
    return f" (≈ {s:.0f} s)" if s < 120 else f" (≈ {s / 60:.0f} min)" if s < 7200 else f" (≈ {s / 3600:.1f} h)"


def hot_address(spec: str, chain: Chain) -> str:
    """create's --hot: the hot key's address, or a key spec whose address is used (nothing is signed with it)."""
    try:
        return address(spec, "--hot")
    except Refused:
        return load_key(spec, chain.profile, "hot").ss58_address


def planck(chain: Chain, value: str, flag: str) -> int:
    try:
        return chain.units(value)
    except (ValueError, ArithmeticError) as e:
        raise Refused(f"{flag} {value!r}: {e}") from e


def spend_call(chain: Chain, args) -> Any:
    """The held call: transfer_keep_alive(to, amount), or a pre-encoded call given as --call 0x…"""
    if getattr(args, "call", None):
        from scalecodec.base import ScaleBytes

        try:
            obj = chain.substrate.runtime_config.create_scale_object(
                "Call", data=ScaleBytes(args.call), metadata=chain.substrate.metadata)
            obj.decode()
        except Exception as e:
            raise Refused(f"--call is not a call this runtime can decode: {type(e).__name__}: {e}") from e
        return obj
    if not (args.to and args.amount):
        raise Refused("give --to and --amount (or --call 0x…)")
    return transfer(chain, address(args.to, "--to"), planck(chain, args.amount, "--amount"))


def cmd_create(chain: Chain, tx: Sender, out: Out, args) -> int:
    cold = load_key(args.cold, chain.profile, "cold")
    hot = hot_address(args.hot, chain)
    delay = args.delay if args.delay is not None else blocks_for(args.delay_s, chain.block_time_ms)
    fund = planck(chain, args.fund, "--fund") if args.fund else None
    d = deposits(chain)
    out.say(f"vault for hot key {hot}: delay {delay} blocks{seconds(delay, chain)} on {chain.profile.label}")
    out.say(f"deposits (read from chain): create {chain.fmt(d['ProxyDepositBase'] + d['ProxyDepositFactor'])} "
            f"reserved from the cold key; add hot key {chain.fmt(d['ProxyDepositFactor'])} reserved from the vault; "
            f"each pending announcement {chain.fmt(d['AnnouncementDepositBase'])} + "
            f"{chain.fmt(d['AnnouncementDepositFactor'])} reserved from the hot key (max {d['MaxPending']} pending)")
    vault = create_vault(tx, cold, hot, delay, args.index, fund)
    out.say(f"vault {vault}: cold {cold.ss58_address} delay 0 (veto/panic), hot {hot} delay {delay}")
    out.say(f"watch it:  python -m airlock.watcher --vault {vault}")
    out.result(command="create", vault=vault, cold=cold.ss58_address, hot=hot, delay=delay,
               profile=chain.profile.id, rows=tx.rows)
    return 0


def cmd_announce(chain: Chain, tx: Sender, out: Out, args) -> int:
    hot = load_key(args.hot, chain.profile, "hot")
    vault = address(args.vault, "--vault")
    call = spend_call(chain, args)
    p = announce(tx, hot, vault, call)
    left = p.release_block - p.height
    encoded = "0x" + call_bytes(call).hex()
    out.say(f"announced {p.call_hash} at #{p.height}: executable from #{p.release_block} ({left} blocks"
            f"{seconds(left, chain)})")
    out.say("the watcher marks this NOT YOURS: the owner intent registry that marks your own spends YOURS is not "
            "built yet")
    if args.to and args.amount:
        out.say(f"execute:  airlock execute --vault {vault} --to {args.to} --amount {args.amount}")
    else:
        out.say(f"execute:  airlock execute --vault {vault} --call {encoded}")
    out.say(f"cancel:   airlock veto --vault {vault} --delegate {hot.ss58_address} --hash {p.call_hash}")
    out.result(command="announce", vault=vault, delegate=hot.ss58_address, call_hash=p.call_hash, call=encoded,
               announced_block=p.height, release_block=p.release_block, profile=chain.profile.id, rows=tx.rows)
    return 0


def cmd_execute(chain: Chain, tx: Sender, out: Out, args) -> int:
    hot = load_key(args.hot, chain.profile, "hot")
    vault = address(args.vault, "--vault")
    call = spend_call(chain, args)

    def tick(n: int) -> None:
        out.say(f"  head #{n} …")

    out.say(f"executing {call_hash(call)} from {vault}")
    needs = None if args.call else planck(chain, args.amount, "--amount")
    execute(tx, hot, vault, call, wait=not args.no_wait, on_tick=tick, needs=needs)
    out.say("executed: the announced call ran as the vault")
    out.result(command="execute", vault=vault, call_hash=call_hash(call), profile=chain.profile.id, rows=tx.rows)
    return 0


def cmd_veto(chain: Chain, tx: Sender, out: Out, args) -> int:
    cold = load_key(args.cold, chain.profile, "cold")
    vault, delegate = address(args.vault, "--vault"), address(args.delegate, "--delegate")
    veto(tx, cold, vault, delegate, args.hash)
    row = tx.rows[-1]
    out.say(f"VETOED {args.hash.lower()} — removed from Proxy.Announcements; proxy_announced for it now fails "
            f"Proxy.Unannounced (veto tx {row['extrinsic_hash']}, id {row['extrinsic_id']})")
    out.result(command="veto", vault=vault, delegate=delegate, call_hash=args.hash.lower(), profile=chain.profile.id,
               rows=tx.rows)
    return 0


def cmd_panic(chain: Chain, tx: Sender, out: Out, args) -> int:
    cold = load_key(args.cold, chain.profile, "cold")
    vault, delegate = address(args.vault, "--vault"), address(args.delegate, "--delegate")
    left = [p for p in pending_for(tx, vault) if same_account(p.delegate, delegate)]
    panic(tx, cold, vault, delegate)
    out.say(f"PANIC done: {delegate} is no longer a delegate of {vault}; it cannot announce or execute "
            f"(Proxy.NotProxy)")
    if left:
        out.say(f"its {len(left)} pending announcement(s) stay in storage (its deposit stays reserved) and cannot "
                f"execute while it is not a delegate — never re-add this key without vetoing them first: "
                + ", ".join(p.call_hash for p in left))
    out.result(command="panic", vault=vault, delegate=delegate, profile=chain.profile.id, rows=tx.rows)
    return 0


def cmd_status(chain: Chain, tx: Sender, out: Out, args) -> int:
    vault = address(args.vault, "--vault")
    defs = tx.delegates(vault)
    risk = chain.at_risk(vault)
    head = tx.head()
    items = pending_for(tx, vault)
    out.say(f"vault {vault} on {chain.profile.label} · head #{head}")
    out.say(f"  at risk {chain.fmt(risk.transferable)} (free {chain.fmt(risk.free)}, reserved "
            f"{chain.fmt(risk.reserved)}, locked {chain.fmt(risk.frozen)})")
    for d in defs:
        out.say(f"  delegate {d.account} {d.proxy_type} delay {d.delay}" + ("  (cold: veto/panic)" if d.delay == 0
                                                                            else ""))
    if not defs:
        out.say("  no delegates")
    for p in items:
        state = f"executable from #{p.release_block}" if p.release_block > head else "EXECUTABLE NOW"
        out.say(f"  pending {p.call_hash} by {p.delegate} at #{p.height}: {state}")
    if not items:
        out.say("  no pending announcements")
    out.result(command="status", vault=vault, head=head, at_risk=risk.transferable,
               delegates=[d.__dict__ for d in defs],
               pending=[{**p.__dict__, "release_block": p.release_block} for p in items], profile=chain.profile.id)
    return 0


COMMANDS = {"create": cmd_create, "announce": cmd_announce, "execute": cmd_execute, "veto": cmd_veto,
            "panic": cmd_panic, "status": cmd_status}


def parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(prog=PROG, description=__doc__.split("\n\n")[0],
                                 formatter_class=argparse.RawDescriptionHelpFormatter,
                                 epilog="\n\n".join(__doc__.split("\n\n")[1:]))
    ap.add_argument("--profile", default=None, help="expected chain profile (default $AIRLOCK_PROFILE, else auto)")
    ap.add_argument("--url", default=None, help="node websocket (default $AIRLOCK_WSS, else the profile default)")
    ap.add_argument("--json", action="store_true", help="one JSON object per line: each tx, then the result")
    sub = ap.add_subparsers(dest="command", required=True, metavar="command")
    cold_default, hot_default = os.environ.get("AIRLOCK_COLD_KEY"), os.environ.get("AIRLOCK_HOT_KEY")

    c = sub.add_parser("create", help="create a vault: cold = instant delegate, hot = delayed delegate")
    c.add_argument("--cold", default=cold_default, help="cold key spec (signs; pays the create deposit)")
    c.add_argument("--hot", required=True, help="hot key address (or key spec; only its address is used)")
    g = c.add_mutually_exclusive_group(required=True)
    g.add_argument("--delay", type=int, help="announcement delay in blocks")
    g.add_argument("--delay-s", type=float, help="announcement delay in seconds, converted with the chain's block time")
    c.add_argument("--fund", default=None, help="tokens the cold key sends to the new vault")
    c.add_argument("--index", type=int, default=0, help="create_pure disambiguation index (default 0)")

    for name, help_ in (("announce", "hot key announces a transfer from the vault"),
                        ("execute", "hot key executes an announced transfer after the delay")):
        a = sub.add_parser(name, help=help_)
        a.add_argument("--hot", default=hot_default, help="hot key spec")
        a.add_argument("--vault", required=True)
        a.add_argument("--to", default=None, help="payee address")
        a.add_argument("--amount", default=None, help="tokens (exact; no sub-planck amounts)")
        if name == "execute":
            a.add_argument("--call", default=None, help="the announced call as 0x-encoded bytes (instead of --to/--amount)")
            a.add_argument("--no-wait", action="store_true", help="refuse instead of waiting when the delay has not passed")

    v = sub.add_parser("veto", help="cold key rejects a pending announcement (the veto)")
    v.add_argument("--cold", default=cold_default, help="cold key spec")
    v.add_argument("--vault", required=True)
    v.add_argument("--delegate", required=True, help="the key that announced (the hot key)")
    v.add_argument("--hash", required=True, help="the announced call hash (0x…)")

    p = sub.add_parser("panic", help="cold key removes a delegate (retire a compromised hot key)")
    p.add_argument("--cold", default=cold_default, help="cold key spec")
    p.add_argument("--vault", required=True)
    p.add_argument("--delegate", required=True, help="the key to remove")

    s = sub.add_parser("status", help="delegates, at-risk balance and pending announcements of a vault (read-only)")
    s.add_argument("--vault", required=True)
    return ap


def main(argv: list[str] | None = None) -> int:
    args = parser().parse_args(argv)
    if args.command == "create" and args.delay is not None and args.delay < 1:
        print(f"{PROG}: refused: --delay must be at least 1 block", file=sys.stderr)
        return 2
    out = Out(args.json)
    try:
        chain = connect(args.url, args.profile)
    except (UnknownChain, ProfileMismatch, ChainUnreachable) as e:
        print(f"{PROG}: refused: {type(e).__name__}: {e}", file=sys.stderr)
        return 2
    try:
        out.say(f"chain: {chain.profile.label} · {chain.spec_name}/{chain.spec_version} · genesis "
                f"{chain.genesis_hash} · {chain.url}")
        tx = Sender(chain, echo=out.row)
        return COMMANDS[args.command](chain, tx, out, args)
    except (Refused, KeyRefused) as e:
        print(f"{PROG}: refused, nothing sent: {e}", file=sys.stderr)
        return 2
    except TxFailed as e:
        hint = (" — the announcement was used up: announce again" if e.row["outcome"].startswith("inner:")
                and e.row["call"].startswith("Proxy.proxy_announced") else "")
        print(f"{PROG}: FAILED on chain: {e}{hint}", file=sys.stderr)
        return 1
    except SubmitRejected as e:
        print(f"{PROG}: the node rejected the transaction, it was not included: {e}", file=sys.stderr)
        return 1
    except VaultIncomplete as e:
        print(f"{PROG}: INCOMPLETE: {e}", file=sys.stderr)
        return 130 if isinstance(e.__cause__, KeyboardInterrupt) else 1
    except NotConfirmed as e:
        print(f"{PROG}: NOT CONFIRMED: {e}", file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        print(f"{PROG}: interrupted — if a transaction was already submitted it may still be included; check with "
              f"`airlock status`", file=sys.stderr)
        return 130
    except Exception as e:
        if not _is_connection_error(e):
            raise
        # a read failed or the connection dropped — possibly after a transaction was included
        print(f"{PROG}: node or connection error ({type(e).__name__}: {e}) — outcome unknown: check with "
              f"`airlock status`", file=sys.stderr)
        return 1
    finally:
        try:
            chain.substrate.close()
        except Exception:  # closing a socket that already dropped must not mask the exit status
            pass


if __name__ == "__main__":
    sys.exit(main())
