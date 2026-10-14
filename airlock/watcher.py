"""The Airlock watcher: follow new blocks, catch every `Proxy.Announced` on a watched vault, alert the owner.

  new header (subscription) -> process every block since the last one processed (no gaps, even after downtime)
    -> System.Events -> Proxy.Announced { real, proxy, call_hash } with real ∈ watched vaults
    -> delay of that delegate from Proxy.Proxies(vault) -> executable-from block
    -> at-risk = transferable balance of the vault (chain_profile, per AccountData layout)
    -> verdict (NOT YOURS until the owner intent registry exists) -> SQLite -> notifiers

It never signs anything. Two connections: one carries the header subscription, the other the reads, so a read never
interleaves with subscription traffic. On a dropped connection it reconnects, re-checks the genesis hash, and rescans
from the last processed block; a different genesis stops it (the database belongs to another chain).

usage: python -m airlock.watcher --vault <address> [--vault …] [--profile interim-sdk] [--url ws://…]
                                 [--db path] [--from-block N] [--jsonl evidence/alerts.jsonl] [--once] [--max-alerts N]
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any, Callable, Iterable

from .alerts import (NOT_YOURS, Alert, JsonlNotifier, Notifier, StdoutNotifier, TelegramError, format_text,
                     telegram_from_config)
from .callhash import same_account
from .chain_profile import Chain, ChainUnreachable, ProfileMismatch, UnknownChain, connect, event_field
from .store import GenesisChanged, Store

MAX_ATTEMPTS = 20  # an undelivered alert is retried once per new block, this many times at most

try:  # the websocket client substrate-interface uses; its errors mean "connection lost", not a bug
    from websocket import WebSocketException
except ImportError:  # pragma: no cover
    WebSocketException = ConnectionError
CONNECTION_ERRORS = (ChainUnreachable, WebSocketException, ConnectionError, TimeoutError, OSError)


@dataclass(frozen=True)
class Announced:
    """One decoded Proxy.Announced event."""

    real: str
    delegate: str
    call_hash: str
    extrinsic_index: int | None


def _hex(value: Any) -> str:
    if isinstance(value, (bytes, bytearray)):
        return "0x" + bytes(value).hex()
    return str(value).lower()


def decode_announced(events: Iterable[Any]) -> list[Announced]:
    """Every Proxy.Announced in a block's events, whatever the decoder's attribute shape (named or positional)."""
    found = []
    for e in events:
        v = getattr(e, "value", e)
        if (v.get("module_id"), v.get("event_id")) != ("Proxy", "Announced"):
            continue
        attrs = v["attributes"]
        found.append(Announced(real=event_field(attrs, "real", 0), delegate=event_field(attrs, "proxy", 1),
                               call_hash=_hex(event_field(attrs, "call_hash", 2)),
                               extrinsic_index=v.get("extrinsic_idx")))
    return found


def delegate_delay(proxies_value: Any, delegate: str) -> int | None:
    """The delay of `delegate` in a Proxy.Proxies(vault) value ((definitions, deposit) or a bare list)."""
    if not proxies_value:
        return None
    definitions = proxies_value[0] if (isinstance(proxies_value, (list, tuple)) and len(proxies_value) == 2
                                       and isinstance(proxies_value[1], int)) else proxies_value
    delays = []
    for d in definitions:
        who = d["delegate"] if isinstance(d, dict) else d[0]
        if same_account(who, delegate):
            delays.append(int(d["delay"] if isinstance(d, dict) else d[2]))
    # a delegate can hold several proxy types; announcements are executable after the shortest of its delays
    return min(delays) if delays else None


def classify(announced: Announced) -> str:
    """Owner intent registry: not built yet, so nothing can vouch for an announcement — every one is NOT YOURS."""
    return NOT_YOURS


class Watcher:
    def __init__(self, chain: Chain, store: Store, notifiers: list[Notifier], log: Callable[[str], None] | None = None,
                 clock: Callable[[], float] = time.time):
        self.chain = chain
        self.store = store
        self.notifiers = notifiers
        self.log = log or (lambda m: print(m, file=sys.stderr, flush=True))
        self.clock = clock
        self.alerts_sent = 0
        store.bind_chain(chain.genesis_hash, chain.profile.id)

    @property
    def s(self):
        return self.chain.substrate

    def watched(self) -> dict[str, str | None]:
        return self.store.vaults(self.chain.profile.id)

    def start_from(self, head: int, from_block: int | None) -> int:
        """First block to process: resume after the last processed one, else --from-block, else the head."""
        last = self.store.last_block
        if last is not None:
            return last + 1
        return head if from_block is None else from_block

    def process_block(self, number: int, head: int) -> list[Alert]:
        block_hash = self.s.get_block_hash(number)
        if block_hash is None:
            raise LookupError(f"no block #{number} on this node")
        vaults = self.watched()
        alerts = []
        for ann in decode_announced(self.s.get_events(block_hash)):
            vault = next((v for v in vaults if same_account(v, ann.real)), None)
            if vault is None:
                continue
            alerts.append(self._alert(ann, vault, vaults[vault], number, block_hash, head))
        self.store.set_last_block(number)
        return alerts

    def _alert(self, ann: Announced, vault: str, chat_id: str | None, number: int, block_hash: str, head: int) -> Alert:
        proxies = self.s.query("Proxy", "Proxies", [vault], block_hash=block_hash).value
        delay = delegate_delay(proxies, ann.delegate)
        risk = self.chain.at_risk(vault)
        alert = Alert(
            verdict=classify(ann), profile_id=self.chain.profile.id, profile_label=self.chain.profile.label,
            is_interim=self.chain.profile.is_interim, vault=vault, delegate=ann.delegate, call_hash=ann.call_hash,
            announced_block=number, block_hash=block_hash,
            release_block=None if delay is None else number + delay, head=head,
            block_time_ms=self.chain.block_time_ms, at_risk=self.chain.fmt(risk.transferable),
            locked=self.chain.fmt(risk.frozen) if risk.frozen else None, late_by=head - number, chat_id=chat_id,
            extra={"delay_blocks": delay, "extrinsic_index": ann.extrinsic_index})
        text = format_text(alert)
        stored = self.store.record(vault=vault, delegate=ann.delegate, call_hash=ann.call_hash, announced_block=number,
                                   block_hash=block_hash, extrinsic_index=ann.extrinsic_index,
                                   release_block=alert.release_block, verdict=alert.verdict, at_risk=alert.at_risk,
                                   alert_text=text, alert_json=json.dumps(alert.as_json()))
        if stored.alert_sent_at is None:
            self.deliver(stored, alert, text)
        else:
            self.log(f"already alerted: {vault} {ann.call_hash} at #{number}")
        return alert

    def retry_unsent(self, head: int) -> int:
        """Re-send undelivered alerts to the channels that missed them, countdown recomputed for the current head."""
        resent = 0
        for stored, raw in self.store.unsent(MAX_ATTEMPTS):
            data = {k: v for k, v in json.loads(raw).items() if k not in ("blocks_left", "veto_command")}
            alert = replace(Alert(**data), head=head, late_by=head - stored.announced_block)
            if self.deliver(stored, alert, format_text(alert)):
                resent += 1
        return resent

    def deliver(self, stored, alert: Alert, text: str) -> list[str]:
        """Send through every notifier that does not have this alert yet. It is delivered once every owner-facing
        channel (all but the JSON-lines log) has it; until then the watcher retries the missing ones each block."""
        self.store.note_attempt(stored)
        have = self.store.delivered_via(stored)
        sent = []
        for n in self.notifiers:
            if n.name in have:
                continue
            try:
                n.send(alert, text)
                sent.append(n.name)
            except (TelegramError, OSError) as e:
                self.log(f"notifier {n.name} failed: {e}")
        have |= set(sent)
        complete = all(n.name in have for n in self.notifiers if n.name != "jsonl")
        self.store.mark_delivery(stored, sorted(have), complete, self.clock())
        if complete:
            self.alerts_sent += 1
        return sent

    def catch_up(self, head: int, from_block: int | None = None) -> list[Alert]:
        """Process every block from where the watcher stopped up to `head`."""
        start = self.start_from(head, from_block)
        if head - start >= 1:
            self.log(f"catching up {head - start + 1} blocks: #{start}..#{head}")
        self.retry_unsent(head)  # earlier failures first, before this pass adds new rows
        alerts = []
        for n in range(start, head + 1):
            alerts += self.process_block(n, head)
        return alerts


def default_db(profile_id: str) -> Path:
    env = os.environ.get("AIRLOCK_DB")
    return Path(env) if env else Path.home() / ".local" / "share" / "airlock" / f"watcher-{profile_id}.db"


def run(args, notifiers: list[Notifier], log=None) -> int:
    log = log or (lambda m: print(m, file=sys.stderr, flush=True))
    reads = connect(args.url, args.profile)
    store = Store(args.db or default_db(reads.profile.id))
    watcher = None
    try:
        watcher = Watcher(reads, store, notifiers, log=log)
        for v in args.vault:
            store.watch(v, reads.profile.id)
        vaults = watcher.watched()
        if not vaults:
            log("no vaults to watch: pass --vault <address>")
            return 2
        log(f"watching {len(vaults)} vault(s) on {reads.profile.label} · genesis {reads.genesis_hash} · {reads.url} · "
            f"block {reads.block_time_ms} ms · notifiers: {', '.join(n.name for n in notifiers)} · db {store.path}")
        head = reads.substrate.get_block_number(None)
        watcher.catch_up(head, args.from_block)
        if args.once or _done(watcher, args):
            return 0
        return follow(watcher, args, log)
    except GenesisChanged as e:
        log(f"STOPPED: {e}")
        return 3
    finally:
        reads.substrate.close()
        store.close()


def _done(watcher: Watcher, args) -> bool:
    return args.max_alerts is not None and watcher.alerts_sent >= args.max_alerts


def follow(watcher: Watcher, args, log) -> int:
    """Header subscription with reconnect: any dropped connection -> reconnect, re-identify, rescan from the cursor."""
    backoff = 1.0
    while True:
        sub = None
        try:
            sub = connect(args.url, args.profile)
            if sub.genesis_hash != watcher.chain.genesis_hash:
                raise GenesisChanged(f"chain at {sub.url} now has genesis {sub.genesis_hash}, "
                                     f"was {watcher.chain.genesis_hash}")

            def on_header(obj, update_nr, subscription_id):
                head = int(obj["header"]["number"])
                if watcher.store.last_block is None or head > watcher.store.last_block:
                    watcher.catch_up(head)
                if _done(watcher, args):
                    return head  # a non-None return ends the subscription
                return None

            sub.substrate.subscribe_block_headers(on_header, finalized_only=args.finalized)
            return 0
        except CONNECTION_ERRORS as e:
            log(f"connection lost ({type(e).__name__}: {e}); reconnecting in {backoff:.0f} s")
            time.sleep(backoff)
            backoff = min(backoff * 2, 30)
            try:
                watcher.chain = _reconnect(watcher, args)
                backoff = 1.0
                log(f"reconnected to {watcher.chain.url} (same genesis); resuming after #{watcher.store.last_block}")
            except CONNECTION_ERRORS as e2:  # still down: loop and retry
                log(f"reconnect failed: {type(e2).__name__}: {e2}")
        finally:
            if sub is not None:
                sub.substrate.close()


def _reconnect(watcher: Watcher, args) -> Chain:
    try:
        watcher.chain.substrate.close()
    except Exception:
        pass
    chain = connect(args.url, args.profile)
    if chain.genesis_hash != watcher.chain.genesis_hash:
        chain.substrate.close()
        raise GenesisChanged(f"chain now has genesis {chain.genesis_hash}, was {watcher.chain.genesis_hash}")
    return chain


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="python -m airlock.watcher", description=__doc__.split("\n\n")[0])
    ap.add_argument("--vault", action="append", default=[], help="vault address to watch (repeatable; persisted)")
    ap.add_argument("--profile", default=None, help="expected chain profile (default $AIRLOCK_PROFILE, else auto)")
    ap.add_argument("--url", default=None, help="node endpoint (default $AIRLOCK_WSS, else the profile default)")
    ap.add_argument("--db", type=Path, default=None, help="SQLite file (default $AIRLOCK_DB, else ~/.local/share/airlock)")
    ap.add_argument("--from-block", type=int, default=None, help="first run only: scan from this block, not the head")
    ap.add_argument("--finalized", action="store_true", help="follow finalized heads (slower, reorg-proof)")
    ap.add_argument("--jsonl", type=Path, default=None, help="also append every alert to this JSON-lines file")
    ap.add_argument("--no-telegram", action="store_true", help="do not send to Telegram even if configured")
    ap.add_argument("--once", action="store_true", help="catch up to the current head, then exit")
    ap.add_argument("--max-alerts", type=int, default=None, help="exit after this many delivered alerts")
    args = ap.parse_args(argv)

    notifiers: list[Notifier] = [StdoutNotifier()]
    telegram = None if args.no_telegram else telegram_from_config()
    if telegram:
        notifiers.append(telegram)
    else:
        print("telegram: not configured (no token) — alerts go to stdout"
              + (" and the JSON-lines log" if args.jsonl else ""), file=sys.stderr)
    if args.jsonl:
        notifiers.append(JsonlNotifier(args.jsonl))
    try:
        return run(args, notifiers)
    except (UnknownChain, ProfileMismatch) as e:
        print(f"WATCHER FAILED: {type(e).__name__}: {e}", file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        return 130


if __name__ == "__main__":
    sys.exit(main())
