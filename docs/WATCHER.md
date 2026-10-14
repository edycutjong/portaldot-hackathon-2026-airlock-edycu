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
3. **Verdict.** The owner intent registry does not exist yet, so nothing can vouch for an announcement: every one is
   **NOT YOURS**. An announcement carries only the call hash; the alert does not need the call to be decoded.
4. **Countdown.** The delegate's delay is read from `Proxy.Proxies(vault)` at the announcement's block. The pallet lets
   `proxy_announced` through once `now − announced ≥ delay`, so the call can be included in block `announced + delay`;
   the alert therefore names `announced + delay − 1` as the last block a veto can land in and still be sure to win,
   and counts the blocks left until then.
5. **At risk.** What a transfer could move out of the vault right now, per AccountData layout
   (`airlock/chain_profile.py`); the part of the free balance held back by locks (e.g. vesting) is shown apart.
   `scripts/spike_proxy.py --vesting` checks this figure on chain: one token more than it fails on the lock.
6. **SQLite** (`airlock/store.py`): the chain's genesis hash, the block cursor and recent block hashes, watched vaults,
   and one row per announcement. Re-scanning a block does not alert again; each channel's delivery is recorded the
   moment it succeeds, so a crash can repeat at most the one channel that was mid-send (at-least-once). A channel that
   failed is retried on every new block, with the countdown recomputed, for 30 minutes; then the watcher logs
   `GAVE UP … never reached <channel>` instead of dropping it silently.
7. **Notifiers:** stdout, a JSON-lines log (`--jsonl`), and Telegram `sendMessage` (plain text, no image). The text
   carries the copy-paste veto command; a URL button is added only when `AIRLOCK_VETO_URL_BASE` (an `https://` URL) is
   set — no web veto page exists yet. A Telegram failure is reported and retried, never raised into the block loop, and
   the bot token never appears in an error message.
8. **Resilience:** a dropped connection, or no new header for 5 block times (at least 30 s), or a read unanswered for
   30 s → reconnect with backoff (reset only once a header is processed again), re-check the genesis hash, re-scan from
   the cursor. A different genesis, or a chain on which none of the processed blocks exists any more (a dev chain
   restarted from genesis has the same genesis hash), stops the watcher with exit code 3 instead of trusting the cursor.

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

## Evidence (2026-10-14, interim chains — not V3, code `880d148`)

Each alert below was triggered by a `Proxy.announce` sent from a separate process: a plain `substrate-interface`
script with no Airlock imports (not part of this repository), signed by the dev key `//Charlie`, which had been added
as a delayed delegate of a fresh vault. Telegram was not configured, so alerts went to stdout and the JSON-lines log.

| Case | Chain | Announce (extrinsic id) | What the watcher did | Log |
|---|---|---|---|---|
| live | interim-sdk | `13293-2` | NOT YOURS at the announcement's block; veto must land by #13312, 19 blocks (≈ 57 s) | `evidence/watcher-interim-sdk-2026-10-14T120922Z.log` |
| announced while the watcher was stopped | interim-sdk | `13295-2` | on restart caught up #13294..#13299, alerted "Seen 4 blocks late", 15 blocks left to veto | `…120922Z-restart.log` |
| node stopped mid-watch, then restarted | interim-sdk | `13306-2` | logged the lost connection and failed reconnects, reconnected (same genesis), resumed after #13301, alerted | `…120922Z-reconnect.log` |
| live, legacy decoding | interim-1002 | `6729-1` | NOT YOURS, 9 blocks (≈ 54 s) left to veto | `evidence/watcher-interim-1002-2026-10-14T121710Z.log` |

The restart case resumed after the last processed block, so the earlier announcement (`13293-2`) was not re-scanned;
not alerting twice on a re-scanned block, the retry path and the reorg/reset handling are covered by unit tests
(`tests/test_watcher.py`), not by these runs. The announce transactions are in `evidence/raw-announce-*.jsonl`, and
each was re-fetched by extrinsic id over HTTP from a fresh process (`evidence/refetch-interim-sdk-2026-10-14T121738Z.json`,
`evidence/refetch-interim-1002-2026-10-14T121731Z.json`). The alerts as JSON, with the head at alert time, are in
`evidence/watcher-*.jsonl`.
