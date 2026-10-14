# Airlock watcher

`python -m airlock.watcher` follows a chain's new blocks and alerts the owner the moment a delegate announces a spend
from a watched vault. It never signs anything.

## What it does

1. **Header subscription** on one connection; reads (events, storage) on a second, so a read never interleaves with
   subscription traffic.
2. For every block since the last one it processed (no gaps after downtime), it reads `System.Events` and keeps each
   `Proxy.Announced { real, proxy, call_hash }` whose `real` is a watched vault. Named (modern metadata) and positional
   (runtime 1002 under the `legacy` preset) event layouts are both decoded.
3. **Verdict.** The owner intent registry does not exist yet, so nothing can vouch for an announcement: every one is
   **NOT YOURS**. An announcement carries only the call hash; the alert does not need the call to be decoded.
4. **Countdown.** The delegate's delay is read from `Proxy.Proxies(vault)` at the announcement's block; the call is
   executable from `announced block + delay`.
5. **At risk.** The vault's transferable balance, computed per AccountData layout (`airlock/chain_profile.py`):
   locked funds (e.g. vesting) are shown apart and not counted.
6. **SQLite** (`airlock/store.py`): the chain's genesis hash, the block cursor, watched vaults, and one row per
   announcement. Re-processing a block (crash, reconnect) never alerts twice; a channel that failed is retried each new
   block (up to 20 attempts) with the countdown recomputed.
7. **Notifiers:** stdout, a JSON-lines log (`--jsonl`), and Telegram `sendMessage` (plain text, no image). The text
   carries the copy-paste veto command; a URL button is added only when `AIRLOCK_VETO_URL_BASE` is set (no web veto page
   exists yet).
8. **Resilience:** a dropped connection → reconnect with backoff, re-check the genesis hash, rescan from the cursor. A
   different genesis stops the watcher (exit 3) instead of rescanning another chain.

The `airlock veto` command in the alert is the CLI's syntax; the CLI itself is the next build step. The same veto as
a raw extrinsic is printed under it: `Proxy.proxy(vault, Proxy.reject_announcement(delegate, call_hash))`, signed by
the cold key.

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

## Evidence (2026-10-14, interim chains — not V3)

Each alert below was triggered by a `Proxy.announce` sent by a raw `substrate-interface` script with no Airlock
imports, signed by a dev key (`//Charlie`) registered as a delayed delegate of a fresh vault. Telegram was not
configured, so alerts went to stdout and the JSON-lines log.

| Case | Chain | Announce (extrinsic id) | Alert | Files |
|---|---|---|---|---|
| live | interim-sdk | `12544-2` | NOT YOURS, 20 blocks left | `evidence/watcher-interim-sdk-2026-10-14T113142Z.log` |
| watcher stopped, announce, restart | interim-sdk | `12549-2` | NOT YOURS, "Seen 4 blocks late", 16 blocks left; `12544-2` not re-alerted | `…-restart.log` |
| node stopped mid-watch, restarted | interim-sdk | `12589-2` | reconnected (same genesis), resumed from the cursor, NOT YOURS | `…-reconnect.log` |
| live, legacy decoding | interim-1002 | `6285-1` | NOT YOURS, 10 blocks left | `evidence/watcher-interim-1002-2026-10-14T113253Z.log` |

The announce transactions are recorded in `evidence/raw-announce-*.jsonl`, and each was re-fetched by extrinsic id over
HTTP from a fresh process (`evidence/refetch-interim-sdk-2026-10-14T113440Z.json`,
`evidence/refetch-interim-1002-2026-10-14T113442Z.json`). The alerts themselves, with the head at alert time, are in
`evidence/watcher-*.jsonl`.
