"""The Airlock watcher: follow new blocks, catch every `Proxy.Announced` on a watched vault, alert the owner.

  new header (subscription) -> process every block since the last one processed (also after downtime; a block the
                               watcher processed that is no longer canonical is re-scanned — reorg check by hash)
    -> System.Events -> Proxy.Announced { real, proxy, call_hash } with real ∈ watched vaults
    -> delay of that delegate from Proxy.Proxies(vault) -> executable-from block
    -> at-risk = what a transfer could move out of the vault (chain_profile, per AccountData layout)
    -> still pending? (Proxy.Announcements) -> verdict: YOURS only if the owner registered this call first (owner
       intent registry, same SQLite file — airlock/intents.py), else NOT YOURS
    -> SQLite -> notifiers
  at start, and after any block whose events could not be read: sweep Proxy.Announcements of every watched vault's
  delegates, so an announcement made before the first run (or inside a pruned range) is still alerted.

It never signs anything. Two connections: one carries the header subscription, the other the reads, so a read never
interleaves with subscription traffic. A watchdog thread closes the subscription when no header has arrived for a few
block times (node pings keep a socket timeout from ever firing, so the watchdog counts headers, not bytes). On a dropped
or stalled connection it reconnects, re-checks the genesis hash, and rescans from the last processed block; a different
genesis, or a chain whose processed blocks are gone (a reset dev chain), stops it.

usage: python -m airlock.watcher --vault <address> [--vault …] [--profile interim-sdk] [--url ws://…]
                                 [--db path] [--from-block N] [--jsonl evidence/alerts.jsonl] [--once] [--max-alerts N]
"""

from __future__ import annotations

import argparse
import json
import os
import socket
import sys
import threading
import time
from dataclasses import dataclass, fields, replace
from pathlib import Path
from typing import Any, Callable, Iterable

from .alerts import (NOT_YOURS, YOURS, Alert, JsonlNotifier, Notifier, StdoutNotifier, TelegramError, format_text,
                     telegram_from_config)
from .callhash import pending, same_account
from .chain_profile import Chain, ChainUnreachable, ProfileMismatch, UnknownChain, connect, event_field
from .store import ChainReset, GenesisChanged, Intent, Store

RETRY_WINDOW_S = 30 * 60  # an undelivered alert is retried on every new block for this long, then given up (logged)
RETRY_BUDGET_S = 5.0  # per pass, after the first retry no new one starts past this long: new blocks are never starved
READ_TIMEOUT_S = 30  # a read the node does not answer in this time counts as a lost connection
STALL_BLOCK_TIMES = 5  # no new header for this many block times (at least 30 s): reconnect

try:  # the websocket client substrate-interface uses; its errors mean "connection lost", not a bug
    from websocket import WebSocketException
except ImportError:  # pragma: no cover
    WebSocketException = ConnectionError
try:
    from substrateinterface.exceptions import SubstrateRequestException
except ImportError:  # pragma: no cover
    SubstrateRequestException = RuntimeError
# transport trouble -> reconnect and resume. A graceful websocket close makes the library parse an empty frame
# (JSONDecodeError); a node refusing a request (rate limit, overload) raises SubstrateRequestException.
CONNECTION_ERRORS = (ChainUnreachable, WebSocketException, ConnectionError, TimeoutError, OSError,
                     json.JSONDecodeError, SubstrateRequestException)


UNKNOWN_AT_RISK = "the whole balance (unreadable)"


EVENT_READ_ATTEMPTS = 3  # a refused get_events is retried on later headers this many times before it is a GAP


class BlockNotYetAvailable(Exception):
    """The subscription announced a block the reads connection cannot serve yet."""


@dataclass(frozen=True)
class Announced:
    """One decoded Proxy.Announced event (or a pending announcement found by the sweep)."""

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


def delegates(proxies_value: Any) -> list[tuple[str, int]]:
    """(delegate, delay) pairs from a Proxy.Proxies(vault) value ((definitions, deposit) or a bare list)."""
    if not proxies_value:
        return []
    definitions = proxies_value[0] if (isinstance(proxies_value, (list, tuple)) and len(proxies_value) == 2
                                       and isinstance(proxies_value[1], int)) else proxies_value
    return [((d["delegate"], int(d["delay"])) if isinstance(d, dict) else (d[0], int(d[2]))) for d in definitions]


def delegate_delay(proxies_value: Any, delegate: str) -> int | None:
    """The delay of `delegate` in a Proxy.Proxies(vault) value."""
    delays = [delay for who, delay in delegates(proxies_value) if same_account(who, delegate)]
    # a delegate can hold several proxy types and the caller of proxy_announced may force one (`force_proxy_type`), so
    # an announcement is executable after the shortest of its delays: alerting early is the safe side
    return min(delays) if delays else None


def classify(store: Store, ann: Announced, vault: str, announced_block: int) -> tuple[str, Intent | None]:
    """YOURS only when the owner registered this exact call (owner token) before it was announced and no other
    announcement has used that intent; every other announcement is NOT YOURS — a known preimage alone never counts
    (I6). Fails closed: an unreadable registry means NOT YOURS."""
    try:
        intent = store.match_intent(vault, ann.call_hash, ann.delegate, announced_block)
    except Exception:
        intent = None
    return (YOURS, intent) if intent is not None else (NOT_YOURS, None)


class Watchdog:
    """Fires `on_stall` once when `beat()` has not been called for `stall_s` seconds (checked by a daemon thread)."""

    def __init__(self, stall_s: float, on_stall: Callable[[], None], clock: Callable[[], float] = time.monotonic,
                 tick_s: float = 1.0):
        self.stall_s, self.on_stall, self.clock, self.tick_s = stall_s, on_stall, clock, tick_s
        self.last = clock()
        self.fired = False
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    def beat(self) -> None:
        self.last = self.clock()

    def stalled(self) -> bool:
        return self.clock() - self.last > self.stall_s

    def _run(self) -> None:
        while not self._stop.wait(self.tick_s):
            if self.stalled():
                self.fired = True
                self.on_stall()
                return

    def start(self) -> "Watchdog":
        self._thread = threading.Thread(target=self._run, name="airlock-watchdog", daemon=True)
        self._thread.start()
        return self

    def stop(self) -> None:
        self._stop.set()


class Watcher:
    def __init__(self, chain: Chain, store: Store, notifiers: list[Notifier], log: Callable[[str], None] | None = None,
                 clock: Callable[[], float] = time.time):
        self.chain = chain
        self.store = store
        self.notifiers = notifiers
        self.log = log or (lambda m: print(m, file=sys.stderr, flush=True))
        self.clock = clock
        self.alerts_sent = 0
        self.on_progress: Callable[[], None] = lambda: None  # called per processed block (feeds the watchdog)
        self._attempted: set[tuple] = set()  # alerts attempted in the current pass
        self.sweep_owed = False  # a sweep that could not read storage is repeated on the next pass
        self._event_refusals: dict[int, int] = {}  # block number -> times the node refused its events
        store.bind_chain(chain.genesis_hash, chain.profile.id)

    @property
    def s(self):
        return self.chain.substrate

    def watched(self) -> dict[str, str | None]:
        return self.store.vaults(self.chain.profile.id)

    def start_from(self, head: int, from_block: int | None) -> int:
        """First block to process. Resuming: the block after the newest processed block that is still canonical
        (a reorg rewinds the cursor); none still canonical -> ChainReset. First run: --from-block, else the head."""
        last = self.store.last_block
        if last is None:
            return head if from_block is None else from_block
        kept = self.store.processed_hashes()
        if not kept:  # a cursor without hashes: trust it unless the chain is now shorter
            if last > head:
                raise ChainReset(f"processed up to #{last}, but the chain's head is #{head}")
            return last + 1
        for number, block_hash in kept:
            if number <= head and self.s.get_block_hash(number) == block_hash:
                if number != last:
                    self.log(f"reorg: processed blocks after #{number} are no longer canonical; re-scanning")
                    self.store.rewind(number)
                return number + 1
        raise ChainReset(f"none of the last {len(kept)} processed blocks (up to #{last}) is on this chain any more "
                         f"(head #{head}): reset or replaced chain")

    def process_block(self, number: int, head: int) -> tuple[list[Alert], bool]:
        """Alerts for one block, and whether its events were readable (False = a gap the sweep must cover)."""
        block_hash = self.s.get_block_hash(number)
        if block_hash is None:
            raise BlockNotYetAvailable(f"no block #{number} on the reads node yet")
        try:
            events = self.s.get_events(block_hash)
        except (WebSocketException, ConnectionError, TimeoutError, OSError, json.JSONDecodeError):
            raise  # transport trouble: reconnect and retry this block
        except SubstrateRequestException as e:  # a node refusing the read (rate limit, overload) or pruned state
            refusals = self._event_refusals[number] = self._event_refusals.get(number, 0) + 1
            if refusals < EVENT_READ_ATTEMPTS:  # try again on a later header before the block is given up as a gap
                raise BlockNotYetAvailable(f"events of #{number} refused ({e}); attempt {refusals} of "
                                           f"{EVENT_READ_ATTEMPTS}") from e
            self._event_refusals.pop(number, None)
            self.log(f"GAP: events of #{number} refused {refusals} times ({type(e).__name__}: {e}); "
                     f"pending announcements will be swept instead")
            self.store.set_last_block(number, block_hash)
            return [], False
        except Exception as e:  # an event this decoder cannot read: never wedge on one block
            self.log(f"GAP: events of #{number} unreadable ({type(e).__name__}: {e}); "
                     f"pending announcements will be swept instead")
            self.store.set_last_block(number, block_hash)
            return [], False
        try:
            announced = decode_announced(events)
        except Exception as e:  # a Proxy.Announced shape this decoder does not know: the sweep reads storage instead
            self.log(f"GAP: Proxy.Announced in #{number} undecodable ({type(e).__name__}: {e}); "
                     f"pending announcements will be swept instead")
            self.store.set_last_block(number, block_hash)
            return [], False
        vaults = self.watched()
        alerts = []
        for ann in announced:
            vault = next((v for v in vaults if same_account(v, ann.real)), None)
            if vault is None:
                continue
            alerts.append(self._alert(ann, vault, vaults[vault], number, block_hash, head))
        self.store.set_last_block(number, block_hash)
        self._event_refusals.pop(number, None)
        return alerts, True

    def _proxies(self, vault: str, block_hash: str | None) -> Any:
        try:
            return self.s.query("Proxy", "Proxies", [vault], block_hash=block_hash or None).value
        except SubstrateRequestException:  # state at that block pruned: the current delegates are the best guess
            return self.s.query("Proxy", "Proxies", [vault]).value

    def is_pending(self, vault: str, delegate: str, call_hash: str, height: int) -> bool:
        """This very announcement (same hash AND block) is still in Proxy.Announcements."""
        return any(a.call_hash == call_hash.lower() and a.height == height and same_account(a.real, vault)
                   for a in pending(self.s, delegate))

    def _figure(self, what: str, read: Callable[[], Any], fallback: Any) -> Any:
        """One secondary alert figure (delay, at-risk, pending). Transport errors still reconnect; anything else (a
        storage or account layout this decoder does not know) is logged and replaced, so the alert still goes out."""
        try:
            return read()
        except CONNECTION_ERRORS:
            raise
        except Exception as e:
            self.log(f"alert figure '{what}' unreadable ({type(e).__name__}: {e}); alerting without it")
            return fallback

    def _alert(self, ann: Announced, vault: str, chat_id: str | None, number: int, block_hash: str, head: int,
               source: str = "block scan") -> Alert:
        delay = self._figure("delay", lambda: delegate_delay(self._proxies(vault, block_hash), ann.delegate), None)
        risk = self._figure("at-risk", lambda: self.chain.at_risk(vault), None)
        verdict, intent = classify(self.store, ann, vault, number)
        alert = Alert(
            verdict=verdict, profile_id=self.chain.profile.id, profile_label=self.chain.profile.label,
            is_interim=self.chain.profile.is_interim, vault=vault, delegate=ann.delegate, call_hash=ann.call_hash,
            announced_block=number, block_hash=block_hash,
            release_block=None if delay is None else number + delay, head=head,
            block_time_ms=self.chain.block_time_ms,
            at_risk=UNKNOWN_AT_RISK if risk is None else self.chain.fmt(risk.transferable),
            locked=self.chain.fmt(risk.held_back) if risk is not None and risk.held_back else None,
            late_by=head - number, chat_id=chat_id,
            pending=self._figure("pending", lambda: self.is_pending(vault, ann.delegate, ann.call_hash, number), None),
            extra={"delay_blocks": delay, "extrinsic_index": ann.extrinsic_index, "source": source,
                   **({"intent_id": intent.id, "call": intent.summary, "registered_head": intent.registered_head}
                      if intent else {})})
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

    def sweep_pending(self, head: int) -> list[Alert]:
        """Alert every announcement still pending on a watched vault that no block scan has recorded: the first run
        starts at the head, and a gap (unreadable events) must not hide a live announcement."""
        alerts, incomplete = [], False
        for vault, chat_id in self.watched().items():
            found = self._figure(f"delegates of {vault}",
                                 lambda: [d for d, _ in delegates(self._proxies(vault, None))], None)
            incomplete |= found is None
            for delegate in found or []:
                entries = self._figure(f"announcements of {delegate}", lambda: pending(self.s, delegate), None)
                incomplete |= entries is None
                for a in entries or []:
                    if not same_account(a.real, vault) or self.store.has(vault, delegate, a.call_hash, a.height):
                        continue
                    ann = Announced(real=vault, delegate=delegate, call_hash=a.call_hash, extrinsic_index=None)
                    block_hash = self.s.get_block_hash(a.height) or ""
                    alerts.append(self._alert(ann, vault, chat_id, a.height, block_hash, head, source="pending sweep"))
                    self.on_progress()
        if incomplete:  # "could not read" is not "nothing pending": try the whole sweep again on the next pass
            self.sweep_owed = True
            self.log("pending sweep incomplete (storage unreadable); owed to the next pass")
        return alerts

    def retry_unsent(self, head: int, budget_s: float = RETRY_BUDGET_S) -> int:
        """Re-send undelivered alerts to the channels that missed them, countdown and pending state recomputed.
        Rows go least-recently-attempted first, so a slow channel cannot starve the newer alerts behind the oldest one;
        rows already attempted in this pass are skipped (tracked in memory, so a wall-clock step cannot reorder them).
        Once RETRY_WINDOW_S has passed, one last attempt is made; if that fails too the alert is given up on — loudly,
        never silently."""
        resent = tried = 0
        names = {f.name for f in fields(Alert)}
        started = time.monotonic()
        for stored, raw, first in self.store.unsent():
            if (stored.vault, stored.delegate, stored.call_hash, stored.announced_block) in self._attempted:
                continue
            if tried and time.monotonic() - started > budget_s:
                break  # at least one retry per pass; then no new ones once the budget is spent (next header goes on)
            tried += 1
            data = {k: v for k, v in json.loads(raw).items() if k in names}
            alert = replace(Alert(**data), head=head, late_by=head - stored.announced_block,
                            pending=self._figure("pending", lambda: self.is_pending(
                                stored.vault, stored.delegate, stored.call_hash, stored.announced_block), None))
            if self.deliver(stored, alert, format_text(alert)):
                resent += 1
            self.on_progress()
            expired = first is not None and self.clock() - first > RETRY_WINDOW_S
            if expired and self.store.get(stored.vault, stored.delegate, stored.call_hash,
                                          stored.announced_block).alert_sent_at is None:
                missing = sorted({n.name for n in self.notifiers} - self.store.delivered_via(stored) - {"jsonl"})
                self.log(f"GAVE UP after {RETRY_WINDOW_S // 60} min: alert for {stored.vault} {stored.call_hash} "
                         f"(#{stored.announced_block}) never reached {', '.join(missing)}")
                self.store.give_up(stored)
        return resent

    def deliver(self, stored, alert: Alert, text: str) -> list[str]:
        """Send through every notifier that does not have this alert yet, recording each success at once. It is
        delivered once every owner-facing channel (all but the JSON-lines log) has it; until then it is retried."""
        self.store.note_attempt(stored, self.clock())
        self._attempted.add((stored.vault, stored.delegate, stored.call_hash, stored.announced_block))
        have = self.store.delivered_via(stored)
        sent = []
        for n in self.notifiers:
            if n.name in have:
                continue
            try:
                n.send(alert, text)
            except (TelegramError, OSError) as e:
                self.log(f"notifier {n.name} failed: {e}")
                continue
            self.store.add_delivery(stored, n.name)
            have.add(n.name)
            sent.append(n.name)
        if all(n.name in have for n in self.notifiers if n.name != "jsonl"):
            self.store.mark_sent(stored, self.clock())
            self.alerts_sent += 1
        return sent

    def catch_up(self, head: int, from_block: int | None = None, sweep: bool = False) -> list[Alert]:
        """Process every block from where the watcher stopped (after a reorg check) up to `head`; then sweep pending
        announcements if asked or if a block's events were unreadable; then retry undelivered alerts (new first)."""
        self._attempted = set()
        start = self.start_from(head, from_block)
        if head - start >= 1:
            self.log(f"catching up {head - start + 1} blocks: #{start}..#{head}")
        alerts = []
        for n in range(start, head + 1):
            try:
                block_alerts, readable = self.process_block(n, head)
            except BlockNotYetAvailable as e:  # the reads node lags the subscription: pick it up on the next header
                self.log(f"{e}; retrying on the next header")
                break
            alerts += block_alerts
            sweep = sweep or not readable
            self.on_progress()
        if sweep or self.sweep_owed:
            self.sweep_owed = False
            alerts += self.sweep_pending(head)
        self.retry_unsent(head)  # never re-tries what this pass just attempted
        return alerts


def default_db(profile_id: str) -> Path:
    env = os.environ.get("AIRLOCK_DB")
    return Path(env) if env else Path.home() / ".local" / "share" / "airlock" / f"watcher-{profile_id}.db"


def _arm_timeout(chain: Chain, seconds: float) -> None:
    """A socket timeout on a websocket connection (no-op over HTTP): a silent node raises instead of hanging."""
    websocket = getattr(chain.substrate, "websocket", None)
    if websocket is not None:
        websocket.settimeout(seconds)


def stall_seconds(block_time_ms: int | None) -> float:
    return max(STALL_BLOCK_TIMES * (block_time_ms or 6000) / 1000, 30.0)


def _open_reads(args) -> Chain:
    chain = connect(args.url, args.profile, auto_reconnect=False)
    _arm_timeout(chain, READ_TIMEOUT_S)
    return chain


def _head(chain: Chain, finalized: bool) -> int:
    s = chain.substrate
    return s.get_block_number(s.get_chain_finalised_head()) if finalized else s.get_block_number(None)


def validate_vaults(chain: Chain, addresses: list[str], log) -> list[str]:
    """Refuse an address that does not decode (a typo would otherwise be 'watched' forever and never alert); warn when
    a vault has no delegates yet (nothing can announce for it)."""
    from substrateinterface.utils.ss58 import ss58_decode

    bad = []
    for a in addresses:
        try:
            ss58_decode(a)
        except Exception:
            bad.append(a)
            continue
        if not delegates(chain.substrate.query("Proxy", "Proxies", [a]).value):
            log(f"warning: {a} has no proxy delegates on this chain yet — nothing can announce for it")
    return bad


def run(args, notifiers: list[Notifier], log=None) -> int:
    log = log or (lambda m: print(m, file=sys.stderr, flush=True))
    reads = _open_reads(args)
    store = Store(args.db or default_db(reads.profile.id))
    watcher = None
    try:
        watcher = Watcher(reads, store, notifiers, log=log)
        bad = validate_vaults(reads, args.vault, log)
        if bad:
            log(f"not a valid address: {', '.join(bad)}")
            return 2
        for v in args.vault:
            store.watch(v, reads.profile.id)
        vaults = watcher.watched()
        if not vaults:
            log("no vaults to watch: pass --vault <address>")
            return 2
        log(f"watching {len(vaults)} vault(s) on {reads.profile.label} · genesis {reads.genesis_hash} · {reads.url} · "
            f"block {reads.block_time_ms} ms · notifiers: {', '.join(n.name for n in notifiers)} · db {store.path}")
        sweep_owed = False
        try:
            watcher.catch_up(_head(reads, args.finalized), args.from_block, sweep=True)
        except CONNECTION_ERRORS as e:  # the follow loop reconnects, resumes from the cursor, and still sweeps
            log(f"initial catch-up interrupted ({type(e).__name__}: {e})")
            if args.once:
                return 1
            sweep_owed = True
        if args.once or _done(watcher, args):
            return 0
        return follow(watcher, args, log, sweep_first=sweep_owed)
    except GenesisChanged as e:
        log(f"STOPPED: {e}")
        return 3
    finally:
        (watcher.chain if watcher else reads).substrate.close()
        store.close()


def _done(watcher: Watcher, args) -> bool:
    return args.max_alerts is not None and watcher.alerts_sent >= args.max_alerts


def _shutdown(chain: Chain) -> None:
    """Called from the watchdog thread: break the subscription's blocking recv(). A plain close() does not wake a recv
    blocked in another thread on Linux; shutting the raw socket down (SHUT_RDWR) does, on Linux and macOS."""
    websocket = getattr(chain.substrate, "websocket", None)
    raw = getattr(websocket, "sock", None)
    try:
        if raw is not None:
            raw.shutdown(socket.SHUT_RDWR)
    except OSError:
        pass
    try:
        (getattr(websocket, "shutdown", None) or websocket.close)()
    except Exception:
        pass


def follow(watcher: Watcher, args, log, sweep_first: bool = False) -> int:
    """Header subscription with reconnect: a dropped or stalled connection -> reconnect, re-identify, rescan from the
    cursor. The backoff resets only once a header has been processed again, so a flapping node is not hammered.
    `sweep_first`: the start-up sweep did not complete, so the first header pass owes one."""
    state = {"backoff": 1.0, "first": not sweep_first}
    stall = stall_seconds(watcher.chain.block_time_ms)
    while True:
        sub, dog = None, None
        try:
            sub = connect(args.url, args.profile, auto_reconnect=False)
            if sub.genesis_hash != watcher.chain.genesis_hash:
                raise GenesisChanged(f"chain at {sub.url} now has genesis {sub.genesis_hash}, "
                                     f"was {watcher.chain.genesis_hash}")
            dog = Watchdog(stall, lambda: _shutdown(sub)).start()
            watcher.on_progress = dog.beat
            sweep_next = not state["first"]  # after a reconnect, sweep once: covers anything a gap could hide
            state["first"] = False

            def on_header(obj, update_nr, subscription_id):
                nonlocal sweep_next
                dog.beat()
                watcher.catch_up(int(obj["header"]["number"]), sweep=sweep_next)  # also re-checks the tip's hash
                sweep_next = False
                state["backoff"] = 1.0
                if _done(watcher, args):
                    return True  # a non-None return ends the subscription
                return None

            sub.substrate.subscribe_block_headers(on_header, finalized_only=args.finalized)
            return 0
        except CONNECTION_ERRORS as e:
            reason = f"no new header for {stall:.0f} s" if dog is not None and dog.fired else f"{type(e).__name__}: {e}"
            backoff = state["backoff"]
            log(f"connection lost ({reason}); reconnecting in {backoff:.0f} s")
            time.sleep(backoff)
            state["backoff"] = min(backoff * 2, 30)
            try:
                watcher.chain = _reconnect(watcher, args)
                log(f"reconnected to {watcher.chain.url} (same genesis); resuming after #{watcher.store.last_block}")
            except CONNECTION_ERRORS as e2:  # still down: loop and retry
                log(f"reconnect failed: {type(e2).__name__}: {e2}")
        finally:
            if dog is not None:
                dog.stop()
            watcher.on_progress = lambda: None
            if sub is not None:
                try:
                    sub.substrate.close()
                except Exception:
                    pass


def _reconnect(watcher: Watcher, args) -> Chain:
    try:
        watcher.chain.substrate.close()
    except Exception:
        pass
    chain = _open_reads(args)
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
    ap.add_argument("--finalized", action="store_true",
                    help="follow finalized heads (slower; best heads are the default, with a reorg re-scan by hash)")
    ap.add_argument("--jsonl", type=Path, default=None, help="also append every alert to this JSON-lines file")
    ap.add_argument("--no-telegram", action="store_true", help="do not send to Telegram even if configured")
    ap.add_argument("--once", action="store_true", help="catch up to the current head, then exit")
    ap.add_argument("--max-alerts", type=int, default=None, help="exit after this many delivered alerts")
    args = ap.parse_args(argv)

    notifiers: list[Notifier] = [StdoutNotifier()]
    try:
        telegram = None if args.no_telegram else telegram_from_config()
    except ValueError as e:  # malformed token, missing chat id, bad button URL: refuse rather than alert half-blind
        print(f"telegram: MISCONFIGURED — {e}. Fix ~/.config/airlock/telegram.env, or run with --no-telegram to alert "
              f"on stdout only.", file=sys.stderr)
        return 2
    if telegram:
        notifiers.append(telegram)
    else:
        why = "disabled (--no-telegram)" if args.no_telegram else "not configured (no token)"
        print(f"telegram: {why} — alerts go to stdout" + (" and the JSON-lines log" if args.jsonl else ""),
              file=sys.stderr)
    if args.jsonl:
        notifiers.append(JsonlNotifier(args.jsonl))
    try:
        return run(args, notifiers)
    except (UnknownChain, ProfileMismatch) as e:  # incl. ChainUnreachable at start, and a runtime upgrade seen on a
        print(f"WATCHER FAILED: {type(e).__name__}: {e}", file=sys.stderr)  # reconnect: profile facts may have changed
        return 1
    except CONNECTION_ERRORS as e:  # the node went away during start-up (before the follow loop could reconnect)
        print(f"WATCHER FAILED: node unreachable or I/O error during start-up ({type(e).__name__}: {e})",
              file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        return 130


if __name__ == "__main__":
    sys.exit(main())
