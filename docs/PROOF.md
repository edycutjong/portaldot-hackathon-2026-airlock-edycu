# Airlock — on-chain proof

Every row is a real extrinsic, identified by extrinsic id (`block-index`), block hash and transaction hash, and
labelled with the chain profile it ran on (`airlock/chain_profile.py`). Rows are appended by the script that
produced them (`scripts/spike_proxy.py --proof docs/PROOF.md`), never typed by hand; each run names the commit
that produced it; its JSON is in `evidence/`.

**Profile labels.** The target is the Portaldot V3 testnet. At 2026-10-14 01:04 UTC its RPC host
`testnetv3-node.feso-apps.xyz` had no DNS record (`dig +short @1.1.1.1` returned nothing), so these rows come from
**interim chains — not V3**:

| Profile | Chain | Runtime | Genesis hash |
|---|---|---|---|
| `interim-sdk` | upstream polkadot-sdk `substrate-node` dev chain (`polkadot-stable2606-3`), run by `scripts/interim_node.sh` | `node/268` | `0x0c4402ef4ccc34d28b16523e1cf26831716527c61a65d8e0840ab26e1636c8a2` |
| `interim-1002` | Portaldot's own `portaldot_dev` dev node, `--tmp` | `portaldot/1002` | `0x53c36c0fc46f5bb2429d701b5ad7a0577b343aa53c4964333f1922611974dd22` |

The dev genesis is deterministic, so a fresh `interim-sdk` chain has the same genesis hash; a row can be re-fetched only
from the node that recorded it (`--base-path` keeps it across restarts, and both chains run as state archives so a
receipt's events stay readable). `interim-1002` runs with `--tmp`, so its rows are a run log, not re-fetchable proof
once that node stops. Signers are dev keys (`//Alice` cold, `//Bob` hot, `//Eve` thief's payee,
`//Ferdie` legitimate payee).

**Reading a run.** Steps 4 (I1), 7 (I2) and 9 are expected failures: `Proxy.Unannounced` is the pallet refusing the
call. Step 9 executes an announced payment before the delay has passed (the delay is enforced, not just stored); step 10
is the control for step 7: the same delegate and delay, without a veto, and the payment executes.

## Runs
