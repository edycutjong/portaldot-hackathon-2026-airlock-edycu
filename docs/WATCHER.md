# Airlock watcher

`python -m airlock.watcher` follows a chain's new blocks and alerts the owner the moment a delegate announces a spend
from a watched vault. It never signs anything.

## What it does

1. **Header subscription** on one connection; reads (events, storage) on a second, so a read never interleaves with
   subscription traffic.
2. For every block since the last one it processed, it reads `System.Events` and keeps each
   `Proxy.Announced { real, proxy, call_hash }` whose `real` is a watched vault. Named (modern metadata) and positional
   (runtime 1002 under the `legacy` preset) event layouts are both decoded. It follows best heads (fast); the hashes of
   the last 512 processed blocks are kept, and each pass first checks that the newest one is still canonical — after a
   reorg it rewinds to the newest block that is and re-scans from there (`--finalized` follows finalized heads instead).
3. **Pending sweep.** At start, after a reconnect, and after any block whose events could not be read (a pruned node,
   logged as `GAP`), it also reads `Proxy.Announcements` of every delegate of every watched vault and alerts any
   pending announcement it has not recorded — so an announcement made before the watcher's first run is not missed.
4. **Verdict.** The owner intent registry does not exist yet, so nothing can vouch for an announcement: every one is
   **NOT YOURS**. An announcement carries only the call hash; the alert does not need the call to be decoded. Each alert
   also checks that the announcement is still pending; one that was already vetoed, removed or executed (seen late,
   after downtime) is sent as **NOT YOURS — NO LONGER PENDING**, without a veto command, telling the owner to check the
   vault.
5. **Countdown.** The delegate's delay is read from `Proxy.Proxies(vault)` at the announcement's block. The pallet lets
   `proxy_announced` through once `now − announced ≥ delay`, so the call can be included in block `announced + delay`;
   the alert therefore names `announced + delay − 1` as the last block a veto can land in and still be sure to win,
   and counts the blocks left until then.
6. **At risk.** What a transfer could move out of the vault right now, per AccountData layout
   (`airlock/chain_profile.py`): locks are excluded, and so is the existential deposit when the pallet keeps it (modern
   runtimes: the vault holds deposits and has a single provider; on 1002 the reserved deposit counts toward it). The
   part of the free balance that cannot move is shown apart. `scripts/spike_proxy.py --vesting` checks the figure
   against the chain where a lock is the limit (one planck more fails on the lock, exactly it succeeds); the
   existential-deposit floor is covered by unit tests.
7. **SQLite** (`airlock/store.py`): the chain's genesis hash, the block cursor and recent block hashes, watched vaults,
   and one row per announcement. Re-scanning a block does not alert again; each channel's delivery is recorded the
   moment it succeeds, so a crash can repeat at most the one channel that was mid-send (at-least-once). A channel that
   failed is retried after each pass's new blocks, least recently attempted first (at least one retry per pass; after
   5 s no new one is started, so a pass runs at most one send timeout longer and new blocks come first), with the
   countdown and pending state recomputed, for 30 minutes; after one last attempt the watcher logs
   `GAVE UP … never reached <channel>` instead of dropping it silently. The JSON-lines log is evidence, not an owner
   channel: a failed write is logged once and not retried.
8. **Notifiers:** stdout, a JSON-lines log (`--jsonl`), and Telegram `sendMessage` (plain text, no image). The text
   carries the copy-paste veto command; a URL button is added only when `AIRLOCK_VETO_URL_BASE` (an `https://` URL) is
   set — no web veto page exists yet. A Telegram failure is reported and retried, never raised into the block loop, and
   the bot token never appears in an error message. A malformed token, a token without a chat id, or a non-`https`
   button URL stops the watcher at start (exit 2); `--no-telegram` runs it on stdout only.
9. **Resilience:** a watchdog thread counts headers — no new header for 5 block times (at least 30 s) closes the
   subscription (node pings would keep a plain socket timeout from firing); a dropped connection or a read unanswered
   for 30 s, a graceful close, or a refused request does the same. It then reconnects with backoff (reset only once a
   header is processed again), re-checks the genesis hash, re-scans from the cursor and sweeps pending announcements.
   A block whose events cannot be read or decoded is logged as a `GAP` and covered by the sweep, never retried forever.
   If one figure of an alert cannot be read (a storage or account layout the decoder does not know — the V3 layouts are
   unverified), the alert still goes out: at risk becomes "the whole balance (unreadable)", an unreadable delay becomes
   "veto now", and the log names the figure. A different genesis, or a chain on which none of the processed blocks
   exists any more (a dev chain restarted from genesis has the same genesis hash), stops the watcher with exit code 3.
   A vault address that does not decode is refused (exit 2); a vault with no delegates is watched with a warning. A
   node that is unreachable at start, or a chain whose runtime changed (new code hash) when the watcher reconnects,
   stops it with exit code 1 (`WATCHER FAILED`): the profile's facts may no longer hold, so it does not guess.

The `airlock veto` command in the alert is the CLI's syntax; the CLI itself is the next build step, and the alert says
so. Until then the alert also gives the same veto as a polkadot.js Apps extrinsic signed by the cold key:
`proxy.proxy(real=<vault>, forceProxyType=None, call=proxy.rejectAnnouncement(delegate, callHash))`.

## Run it

```bash
scripts/interim_node.sh interim-sdk          # terminal 1
python -m airlock.watcher --profile interim-sdk --vault <vault address> --jsonl evidence/alerts.jsonl   # terminal 2
```

Vaults passed with `--vault` are stored, so a restart needs no flags beyond the profile. `--from-block N` scans history
on a first run; `--once` catches up and exits; `--max-alerts N` exits after N delivered alerts; `--finalized` follows
finalized heads instead of the best head. The database defaults to `~/.local/share/airlock/watcher-<profile>.db`
(`--db` or `$AIRLOCK_DB` to change).

**Telegram.** Put `TELEGRAM_BOT_TOKEN=…` and `TELEGRAM_CHAT_ID=…` in `~/.config/airlock/telegram.env` (or set
`AIRLOCK_TELEGRAM_TOKEN` / `AIRLOCK_TELEGRAM_CHAT_ID`). Without a token the watcher says so at start and alerts on
stdout (and the JSON-lines log) only. The Telegram sender is unit-tested against a local mock of the Bot API
(`tests/test_alerts.py`); **no alert has been sent to a real phone yet** — no bot token is configured.

## Evidence (2026-10-14, interim chains — not V3, code `5ea95f2`)

Each alert below was triggered by a `Proxy.announce` from [`scripts/raw_announce.py`](../scripts/raw_announce.py),
which imports nothing from Airlock, signed by the dev key `//Charlie` after it was added as a delayed delegate of a
fresh vault (500 tokens funded; one vault per chain, created by `//Alice` on interim-sdk and `//Dave` on interim-1002 —
the four interim-sdk cases share it). Telegram was not configured, so alerts went to stdout and the JSON-lines log. On
`interim-sdk` the alert shows 202 UNIT at risk and 100 UNIT that cannot move: the vault holds proxy deposits, so it
keeps the 100 UNIT existential deposit. Lines starting `[driver]` in the logs were written by
[`scripts/watcher_evidence.sh`](../scripts/watcher_evidence.sh), which ran all five cases, stopped, froze and restarted
the node, and re-fetched the announces; the logs show when each happened (a driver line can land just after the
watcher's own line for the same moment).

| Case | Chain | Announce (extrinsic id) | What the watcher did | Log |
|---|---|---|---|---|
| live | interim-sdk | `16255-2` | NOT YOURS at the announcement's block; veto must land by #16274, 19 blocks (≈ 57 s) | `evidence/watcher-interim-sdk-2026-10-14T143952Z.log` |
| announced while the watcher was stopped | interim-sdk | `16257-2` | on restart caught up #16256..#16261, alerted "Seen 4 blocks late", 15 blocks (≈ 45 s) left to veto | `evidence/watcher-interim-sdk-2026-10-14T143952Z-restart.log` |
| node stopped (SIGTERM) mid-watch, then restarted | interim-sdk | `16269-2` | logged the lost connection and each refused reconnect (backoff 1, 2, 4, 8 s), reconnected after the restart (same genesis), resumed after #16264, alerted | `evidence/watcher-interim-sdk-2026-10-14T143952Z-reconnect.log` |
| node frozen mid-watch (`SIGSTOP`: socket open, no headers), then resumed | interim-sdk | `16276-2` | "connection lost (no new header for 30 s)", reconnected after `SIGCONT`, resumed after #16272, alerted | `evidence/watcher-interim-sdk-2026-10-14T143952Z-stall.log` |
| live, legacy decoding | interim-1002 | `8177-1` | NOT YOURS, 9 blocks (≈ 54 s) left to veto, 498.0200 POT at risk | `evidence/watcher-interim-1002-2026-10-14T144153Z.log` |

The restart case resumed after the last processed block, so the earlier announcement (`16255-2`) was not re-scanned.
Not alerting twice on a re-scanned block, the pending sweep, NO LONGER PENDING alerts, retries (rotation) and give-up,
reorg re-scan, reset-chain stop, the pruned-state and undecodable-event gaps and the unreadable-figure fallbacks are
covered by unit tests (`tests/test_watcher.py`), not by these
runs. The announce transactions are in `evidence/raw-announce-*.jsonl`; each was re-read by extrinsic id over HTTP
from a fresh process and compared with the recorded block hash, tx hash and outcome — 4/4 and 1/1 matched
(`evidence/refetch-interim-sdk-2026-10-14T144220Z.json`, `evidence/refetch-interim-1002-2026-10-14T144222Z.json`). The alerts as JSON, with the head at alert time, are in
`evidence/watcher-*.jsonl`.
