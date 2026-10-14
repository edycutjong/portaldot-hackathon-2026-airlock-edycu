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
