# Airlock — benchmark

`scripts/bench.py` runs N thief drains (default 20) against a fresh Airlock vault on a local interim chain and writes
every measured value to `evidence/bench-<profile>-<time>.json`; the tables below are appended by the script itself
(`--md docs/BENCH.md`, passing runs only), never typed by hand.

**What one drain is.** `scripts/thief.py` — which imports no Airlock code — announces a `transfer_keep_alive` sweep of
the vault's sweepable balance with the stolen hot key. The watcher (`python -m airlock.watcher`, its own process,
stdout + JSON-lines notifiers; Telegram is not configured, so its delivery time is not part of these numbers) raises a
NOT YOURS alert; the bench then sends the veto that alert carries (its vault, delegate and call hash) with the cold key
immediately — an automated veto, so the veto numbers measure the chain, not a person's reaction time. After every drain's delay has passed, the thief executes each
announced sweep; each must fail `Proxy.Unannounced`.

**Metrics.**
- *Alert latency*: from the announce block's on-chain timestamp (`Timestamp.Now`, the block's slot time) to the moment
  the watcher wrote the alert (wall clock). Node, watcher and bench run on the same machine, so this covers block
  authoring, import, header delivery and the watcher's reads — not a network figure, and not Telegram delivery. In
  blocks: the head when the alert was built minus the announce block.
- *Veto send*: the veto extrinsic's send, from just before signing until its receipt has been read — submission,
  inclusion, decoding the block's events to judge the outcome, and the block-number lookup for the result row (the CLI
  operation's pre-checks and its read-back afterwards are outside the timer; the timed span is the same in both runs
  below); in blocks, the veto's block minus the head read just before signing.
- *Announce → veto*: the veto block's on-chain timestamp minus the announce block's, and the block difference, against
  the vault's delay D.
- Percentiles are nearest-rank: p95 of 20 values is the 19th smallest.

**Ablation (`--ablate`).** One control drain: the same thief script with `--direct` (a plain `proxy.proxy`, no
announcement) against a control vault whose hot key has delay 0 — what a vault without Airlock's delay looks like. It
runs once per invocation and empties the control vault's sweepable balance (the existential deposit and the reserved
proxy deposit stay: a keep-alive transfer cannot take them).

**Scope.** Local interim chains only — **not V3** (the V3 testnet RPC did not resolve when these runs were made, 2026-10-14; V3 came back on 2026-10-19 and this benchmark has not been run on it, see `PROOF.md`). Dev keys
`//AirlockBench//…`, funded by `//Alice` (`//Dave` on `interim-1002`).

## Runs

### bench — 2026-10-14T16:45:09Z — interim — not V3 (upstream polkadot-sdk substrate-node dev chain)

`evidence/bench-interim-sdk-2026-10-14T164509Z.json` · code `ea49971` · genesis `0x0c4402ef4ccc34d28b16523e1cf26831716527c61a65d8e0840ab26e1636c8a2` · block time 3000 ms · D = 20 blocks (60 s) · vault `5H2DZ46VA4vHDitBoDosw1HRxCTFEQZpt5cUQPtkU54t1eFd`

**20 thief drains** (scripts/thief.py, no Airlock code): 20/20 alerted NOT YOURS, 20/20 vetoed and then failed `Proxy.Unannounced` when executed after the delay; moved to the thief: 0 planck; vault total 1000000000000000 planck before, 1000000000000000 after. Fewest blocks to spare before a drain could have executed: 19 of 20.

| metric (nearest-rank) | p50 | p95 | range |
|---|---|---|---|
| alert latency: announce block timestamp → alert written (incl. block import + delivery, one machine) | 0.023 s | 0.03 s | 0.013–0.032 s |
| alert latency in blocks (head at alert − announce block) | 0 blocks | 0 blocks | 0–0 blocks |
| veto send: signing → included receipt read (automated, no human) | 2.116 s | 2.171 s | 1.92–2.178 s |
| veto inclusion in blocks (veto block − head just before signing) | 1 block | 1 block | 1–1 block |
| announce → veto, on-chain timestamps | 2.999 s | 3.01 s | 2.945–3.01 s |
| announce → veto in blocks | 1 block | 1 block | 1–1 block |

**Ablation (`--ablate`, one control drain):** the same thief script with `--direct` against a control vault whose hot key has delay 0 moved 100.0000 UNIT (100000000000000 planck) of the control vault's 398.0000 UNIT in 1 extrinsic (`18734-2`, outcome ok), 1 block after the head it was sent at. The rest stays because a keep-alive transfer cannot take it: the existential deposit and the reserved proxy deposit.

### bench — 2026-10-14T16:36:23Z — interim — not V3 (Portaldot dev node, runtime spec 1002)

`evidence/bench-interim-1002-2026-10-14T163623Z.json` · code `d4f4be0` · genesis `0x53c36c0fc46f5bb2429d701b5ad7a0577b343aa53c4964333f1922611974dd22` · block time 6000 ms · D = 10 blocks (60 s) · vault `5CnNrBEc9AQpLzD84TLHRnTBMW8SDH2xRqa9udz9sJzhwHvy`

**20 thief drains** (scripts/thief.py, no Airlock code): 20/20 alerted NOT YOURS, 20/20 vetoed and then failed `Proxy.Unannounced` when executed after the delay; moved to the thief: 0 planck; vault total 100000000000000000 planck before, 100000000000000000 after. Fewest blocks to spare before a drain could have executed: 9 of 10.

| metric (nearest-rank) | p50 | p95 | range |
|---|---|---|---|
| alert latency: announce block timestamp → alert written (incl. block import + delivery, one machine) | 0.033 s | 0.157 s | 0.018–0.214 s |
| alert latency in blocks (head at alert − announce block) | 0 blocks | 0 blocks | 0–0 blocks |
| veto send: signing → included receipt read (automated, no human) | 5.918 s | 7.581 s | 5.395–8.236 s |
| veto inclusion in blocks (veto block − head just before signing) | 1 block | 1 block | 1–1 block |
| announce → veto, on-chain timestamps | 5.997 s | 6.007 s | 5.513–6.843 s |
| announce → veto in blocks | 1 block | 1 block | 1–1 block |

**Ablation (`--ablate`, one control drain):** the same thief script with `--direct` against a control vault whose hot key has delay 0 moved 100.0000 POT (10000000000000000 planck) of the control vault's 102.9800 POT in 1 extrinsic (`9397-1`, outcome ok), 1 block after the head it was sent at. The rest stays because a keep-alive transfer cannot take it: the existential deposit and the reserved proxy deposit.
