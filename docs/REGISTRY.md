# Owner intent registry — YOURS vs NOT YOURS

An announcement puts only a call hash on chain. A thief who phished the hot key can announce anything, and can even
publish the preimage of his own call. So Airlock never decides "yours" from the call itself: an announcement is
**YOURS** only when the owner registered that exact call **first**, with an owner token the hot-key seed cannot produce.
Everything else is **NOT YOURS — VETO**. Code: [airlock/intents.py](../airlock/intents.py) (rules),
[airlock/api.py](../airlock/api.py) (HTTP), [airlock/store.py](../airlock/store.py) (`owner`, `intent` tables),
[airlock/watcher.py](../airlock/watcher.py) (`classify`).

## Flow

| Step | Who | What |
|---|---|---|
| enrol | cold key, once per vault | `airlock enroll --vault V`: a random token; the cold key signs `airlock-enroll:v1:<genesis>:<vault public key>:<sha256(token)>:<issued_at>`; `POST /api/vaults/<V>/owner`. The registry checks the signature, that the signer is a **delay-0 delegate of V on chain**, that `issued_at` is within 10 minutes of its clock and newer than any earlier enrolment (no replay or roll-back), and stores the token's sha256 digest (with the signer, `issued_at` and signature) — never the token. |
| register | the CLI, before every announce | `POST /api/intents` with `Authorization: Bearer <token>` and `{vault, call_hex, call_hash}`. No or wrong token, or an unenrolled vault → **401** (the same answer for each). The registry re-hashes `call_hex` itself (`blake2_256`), rejects a mismatch (400), decodes it with the runtime (400 if it is not exactly one call), and records it with the current best block. |
| announce | hot key | `proxy.announce(V, hash)` — sent only after the registry's 200. Registry down, refusing, or silent for 10 s → nothing is announced. |
| classify | watcher | `Proxy.Announced(V, delegate, hash)` at block B is YOURS if an intent for (V, hash) was registered at a block < B, within 50 blocks of B, and has not vouched for another announcement. Otherwise NOT YOURS. |

There is no other way to put a preimage on the server, so a thief's call stays hash-only. Rules the tests pin down
(`tests/test_intents.py`, `tests/test_watcher_intents.py`, `tests/test_api.py`, `tests/test_cli_intents.py`):

- **Foreign preimage** — the same call bytes registered for another vault, or POSTed without the token (`thief.py
  --post-intent` → 401): NOT YOURS. This is the registry's core rule: an announcement whose call hash is not in the
  owner's intent registry is NOT YOURS even when its preimage is known.
- **Replay** — an intent vouches for one announcement only. The owner paid Bob 1.5 once; a thief re-announcing the very
  same call later is NOT YOURS.
- **Order** — an intent registered at or after the announcement's block does not count; nor does one older than 50
  blocks.
- **Fail closed** — if the watcher cannot read the registry, the verdict is NOT YOURS.
- **Rescan and reorg** — re-processing the same announcement (reconnect, reorg check) keeps its YOURS verdict and does
  not alert twice; if a reorg moves the owner's announcement to another block, the rewind releases the intent and the
  re-included announcement is YOURS again.

## Run it

```bash
python -m airlock.api --profile interim-sdk            # registry on http://127.0.0.1:8787, same db as the watcher
python -m airlock.watcher --profile interim-sdk --vault <V>
airlock enroll --vault <V>                             # cold key: $AIRLOCK_COLD_KEY
airlock announce --vault <V> --to <payee> --amount 1.5 # hot key: registers first, then announces -> YOURS
```

Both processes default to `~/.local/share/airlock/watcher-<profile>.db` (or `$AIRLOCK_DB` / `--db`); they must use the
same file. The registry listens on 127.0.0.1 by default, serves each request on its own thread, and never signs anything. The
CLI sends the owner token only over `https://`, or plain `http://` to this machine, and never follows a redirect.

## Evidence

`scripts/intent_evidence.sh <profile>` runs it all live with real processes on a local interim chain: enrol; an owner
spend alerted **YOURS** with the decoded call; the thief's preimage POST answered **401** and its announcement alerted
**NOT YOURS** and vetoed; the registry frozen mid-request (`SIGSTOP`) and then killed (`SIGKILL`) — `airlock announce`
exits 2 both times and nothing new is pending; exactly two alerts in total. Each log ends with its checks and
`INTENTS PASS` or `FAIL`; the extrinsics are re-fetched by id in `evidence/refetch-*`. Passing runs at the current
registry code (threaded server; token only over https or loopback http; no store lock across chain reads), commit
`d6f47d7`: `evidence/intents-interim-sdk-2026-10-14T170106Z.log`, `evidence/intents-interim-1002-2026-10-14T170232Z.log`.
Earlier passing runs at older commits (each log's first line names it): `…-sdk-2026-10-14T165014Z.log`,
`…-1002-2026-10-14T165141Z.log`, `…-sdk-2026-10-14T161239Z.log`, `…-1002-2026-10-14T161455Z.log`. The
first attempt, `evidence/intents-interim-sdk-2026-10-14T161108Z.log`, is kept: it stopped at the owner's registration
(HTTP 400 — the registry could not yet decode a call on a fresh connection); the bug it found was fixed in the next
commit.

## Honest scope

The token defends against **seed phishing**: the hot-key seed alone cannot register an intent. It is a file beside the
CLI config, so a file-level leak (malware, synced dotfiles, a backup) that exposes the token together with the hot key
lets a theft render YOURS. The YOURS label is the server's classification: a compromised server can mislabel or drop an
alert, but cannot move funds or shorten the delay — the chain enforces both, and the veto works whatever the label says.
A cold-key-certified intent key verified in the client would remove the server from that trust path; it is not built.
