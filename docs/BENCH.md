# Airlock — benchmark

`scripts/bench.py` runs N thief drains (default 20) against a fresh Airlock vault on a local interim chain and writes
every measured value to `evidence/bench-<profile>-<time>.json`; the tables below are appended by the script itself
(`--md docs/BENCH.md`, passing runs only), never typed by hand.

**What one drain is.** `scripts/thief.py` — which imports no Airlock code — announces a `transfer_keep_alive` sweep of
the vault's sweepable balance with the stolen hot key. The watcher (`python -m airlock.watcher`, its own process,
stdout + JSON-lines notifiers; Telegram is not configured, so its delivery time is not part of these numbers) raises a
NOT YOURS alert; the bench then sends the alert's veto with the cold key immediately — an automated veto, so the veto
numbers measure the chain, not a person's reaction time. After every drain's delay has passed, the thief executes each
announced sweep; each must fail `Proxy.Unannounced`.

**Metrics.**
- *Alert latency*: from the announce block's on-chain timestamp (`Timestamp.Now`) to the moment the watcher wrote the
  alert (wall clock). Node, watcher and bench run on the same machine, so this is the watcher's own processing time
  after the block is authored, not a network figure. In blocks: the head when the alert was built minus the announce
  block.
- *Veto inclusion*: from sending the veto to seeing it included (client-observed seconds); in blocks, the veto's block
  minus the head when it was sent.
- *Announce → veto*: the veto block's on-chain timestamp minus the announce block's, and the block difference, against
  the vault's delay D.
- Percentiles are nearest-rank: p95 of 20 values is the 19th smallest.

**Ablation (`--ablate`).** One control drain: the same thief script with `--direct` (a plain `proxy.proxy`, no
announcement) against a control vault whose hot key has delay 0 — what a vault without Airlock's delay looks like. It
runs once per invocation and empties the control vault's sweepable balance.

**Scope.** Local interim chains only — **not V3** (the V3 testnet RPC did not resolve; see `PROOF.md`). Dev keys
`//AirlockBench//…`, funded by `//Alice` (`//Dave` on `interim-1002`).

## Runs

### bench — 2026-10-14T16:23:26Z — interim — not V3 (upstream polkadot-sdk substrate-node dev chain)

`evidence/bench-interim-sdk-2026-10-14T162326Z.json` · code `8199612` · genesis `0x0c4402ef4ccc34d28b16523e1cf26831716527c61a65d8e0840ab26e1636c8a2` · block time 3000 ms · D = 20 blocks (60 s) · vault `5FbMHnS5e4o4oo8184zvPKsxTWoL2p3YbKiPF5vbR7Qiaz9U`

**20 thief drains** (scripts/thief.py, no Airlock code): 20/20 alerted NOT YOURS, 20/20 vetoed and then failed `Proxy.Unannounced` when executed after the delay; moved to the thief: 0 planck; vault total 1000000000000000 planck before, 1000000000000000 after. Fewest blocks to spare before a drain could have executed: 19 of 20.

| metric (nearest-rank) | p50 | p95 | range |
|---|---|---|---|
| alert latency: announce block timestamp → alert written | 0.031 s | 0.039 s | 0.016–0.041 s |
| alert latency in blocks (head at alert − announce block) | 0 blocks | 0 blocks | 0–0 blocks |
| veto inclusion: sent → included (automated, no human) | 2.067 s | 2.155 s | 1.89–2.171 s |
| veto inclusion in blocks (veto block − head when sent) | 1 block | 1 block | 1–1 block |
| announce → veto, on-chain timestamps | 3.0 s | 3.013 s | 2.989–3.019 s |
| announce → veto in blocks | 1 block | 1 block | 1–1 block |

**Ablation (`--ablate`, one control drain):** the same thief script with `--direct` against a control vault whose hot key has delay 0 moved 100.0000 UNIT (100000000000000 planck) of the control vault's 398.0000 UNIT in 1 extrinsic (`18400-2`, outcome ok), 1 block after the head it was sent at.
