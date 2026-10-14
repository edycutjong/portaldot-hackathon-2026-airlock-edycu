# Airlock — on-chain proof

Every row is a real extrinsic, identified by extrinsic id (`block-index`), block hash and transaction hash, and
labelled with the chain profile it ran on (`airlock/chain_profile.py`). Rows are appended by the script that
produced them (`scripts/spike_proxy.py --proof docs/PROOF.md`, `scripts/refetch.py --proof docs/PROOF.md`), never added
by hand (a stale row may be deleted by hand); each run names the commit that produced it; its JSON is in `evidence/`.

**Profile labels.** The target is the Portaldot V3 testnet. At 2026-10-14 01:04 UTC and again at 11:30 UTC its RPC host
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
is the control for step 7: the same delegate and delay, without a veto, and the payment executes. Steps 11–12
(`--vesting`) send the vault a vested payout and claim it through the vault: the vesting-locked part is not counted as
at risk, and the at-risk figure equals the transferable balance (checked after each step).

**Re-fetch.** Each run is then looked up again by extrinsic id alone (`scripts/refetch.py`), from a fresh process and
over the node's HTTP endpoint instead of the websocket the run used; block hash, tx hash and outcome must all match.
Both endpoints are the same local node, so this is an independent process and transport, not an independent node.

## Runs

### spike_proxy — 2026-10-14T11:15:53Z — interim — not V3 (upstream polkadot-sdk substrate-node dev chain)

Runtime `node/268` · genesis `0x0c4402ef4ccc34d28b16523e1cf26831716527c61a65d8e0840ab26e1636c8a2` · vault `5CqcTxWjvApFo7dcavxfPj2oRu3PmjRp51PDcBjWBwxte8r5` · delay D = 5 blocks · code `88027bf`

| # | step | call | signer | expected | outcome | extrinsic id | block hash | tx hash | profile | note |
|---|---|---|---|---|---|---|---|---|---|---|
| 1 | create vault | `Proxy.create_pure` | cold | ok | ok | `12224-2` | `0x12e3884da1b857304cea58030225e956743caffc1c1cda8f5e7942ab59a1a383` | `0x7becd3cb275a3a27b00d8a5c70abbe33eb140b1dfe85f4e735cfb38e57686bbf` | interim-sdk — not V3 |  |
| 2 | fund vault | `Balances.transfer_keep_alive` | cold | ok | ok | `12225-2` | `0xe9885167eb97e24796e45864031f80c3edbba07047f3b6d904b88c9dd24002d7` | `0xe499a8ff99987cd494eb803739814e987c54f2cf41baba73a8862ce62de7f5bb` | interim-sdk — not V3 |  |
| 3 | add delayed hot key | `Proxy.proxy(Proxy.add_proxy)` | cold | ok | ok | `12226-2` | `0xd2a3ef68c3fac6f33f77dcb82fb5f9681169742d8740dd77e362679fe4fc3807` | `0xb5e21b96c29d8a3c48e1935f93994d13dc6174d5a60b1bf7139b188e61dee079` | interim-sdk — not V3 | delay 5 blocks |
| 4 | hot spends unannounced | `Proxy.proxy(Balances.transfer_keep_alive)` | hot | Proxy.Unannounced | Proxy.Unannounced | `12227-2` | `0xea646bee88d64db9909047a91b4344ad409b328822d8865061b475900fcd53ec` | `0xc4fd4ce8ed47306cd7d69e5d7d7438482b767e5fdccec68a01121ed3d593c684` | interim-sdk — not V3 | I1 |
| 5 | hot announces theft | `Proxy.announce` | hot | ok | ok | `12228-2` | `0x9208f6dc829c1f131f8625427022c7c594b4f184af00c53c5ccbe64dc008fdb7` | `0x2bec071a25eb80bd0a7b0cc7bab8dbd479c48eee54ae4096607607eebb8ab93a` | interim-sdk — not V3 | `call hash 0xf4c3c3361dcfb6b8193c568bd42e6ab4d2731097b285f573e10e7d2f7e55478a` |
| 6 | cold vetoes | `Proxy.proxy(Proxy.reject_announcement)` | cold | ok | ok | `12229-2` | `0x9b835663140d777ea2c9fad8bb5ba26c40a082afa78801939419e98b7080749f` | `0xbdf4f6b82e7f55313368f35a7602ece73591aad471f2de29844fb7c998a2258c` | interim-sdk — not V3 |  |
| 7 | vetoed theft after delay | `Proxy.proxy_announced(Balances.transfer_keep_alive)` | hot | Proxy.Unannounced | Proxy.Unannounced | `12235-2` | `0xeb93a429c35325895650cc5a3ce992f00d42e5211383ff979fa2764c367ef73f` | `0xb6412bca78cb51cb7d0dba7a9b773f7b47646e36cba92cb6f644073945af8668` | interim-sdk — not V3 | I2 |
| 8 | hot announces payment | `Proxy.announce` | hot | ok | ok | `12236-2` | `0xbc5c3b63a71f06cc00c087b75c6cc9962fc3cc1dd17ebfd5ac3c0141baf06095` | `0xd4536eb7f0b9c4f073568d4cabdb42965e957818afa5bfd4d1f51ee52241c818` | interim-sdk — not V3 | `call hash 0x33aaaddcdcaeb538d590ead70a635c99a4d5a6adf61773ea5558f5c96291a385` |
| 9 | payment before delay | `Proxy.proxy_announced(Balances.transfer_keep_alive)` | hot | Proxy.Unannounced | Proxy.Unannounced | `12237-2` | `0xdd97d07c4399152cd499771d2ff19dca450d81425eeee229e4bb81a5081274c1` | `0x6f384ca3ca913357c1dc9293664f90e9942b0a902d414b2305057e59f9affb15` | interim-sdk — not V3 | delay enforced |
| 10 | payment executes after delay | `Proxy.proxy_announced(Balances.transfer_keep_alive)` | hot | ok | ok | `12243-2` | `0x5c97820d68d10f8b65494b70cc5d5e045cfe16942056b6597ce45f153eff25f2` | `0x13e3c57df7c9ec3754e11a01d8c42b58f8f4d27921da31a1b07bd9c02794567f` | interim-sdk — not V3 | control for I2 |
| 11 | vested payout to vault | `Vesting.vested_transfer` | cold | ok | ok | `12244-2` | `0x59cb29818d2d095b0fa2723983c01bfcfbd302ca698a76d72779ffe1a1e48068` | `0x530aa03e3b401e9664269f50234c32f1c8810791d908c16fa58cb87359fe7983` | interim-sdk — not V3 | 20000 locked, 20.0000 UNIT/block from #12243 |
| 12 | vault claims vested | `Proxy.proxy(Vesting.vest)` | cold | ok | ok | `12247-2` | `0xf319462cad4389012aecb52cc35d099d99d8c7a38b7dd426d5ae6296b6e6a67a` | `0xeef154c655938056180f8a2a216eb19216e173c84c6897df3e2a4d4f8c34d32d` | interim-sdk — not V3 | vest through the vault |

### spike_proxy — 2026-10-14T11:27:41Z — interim — not V3 (Portaldot dev node, runtime spec 1002)

Runtime `portaldot/1002` · genesis `0x53c36c0fc46f5bb2429d701b5ad7a0577b343aa53c4964333f1922611974dd22` · vault `5CUGWWuajLKhsVsQi6TTbDQQQ4XtGZbfUJZaegHkw8SprFpD` · delay D = 5 blocks · code `7d11b22`

| # | step | call | signer | expected | outcome | extrinsic id | block hash | tx hash | profile | note |
|---|---|---|---|---|---|---|---|---|---|---|
| 1 | create vault | `Proxy.anonymous` | cold | ok | ok | `6231-1` | `0xeb8f22a4359a17919b33574e45755b4fc8f9219a54c36ad6a10a98d9b222269c` | `0xd93e1081dda5321f03fec308971d67a909fc2e6ba7e6456f7924f20a0f008e96` | interim-1002 — not V3 |  |
| 2 | fund vault | `Balances.transfer_keep_alive` | cold | ok | ok | `6232-1` | `0x8b13e482a2e96405aa3d7d6181034aaa92ebcf225a05dc3cde1235f0a312ac60` | `0x9a2351ad0c0499552018a629a0ada0f4faf5fb20c5ffaf6f4477e7c3a87149ed` | interim-1002 — not V3 |  |
| 3 | add delayed hot key | `Proxy.proxy(Proxy.add_proxy)` | cold | ok | ok | `6233-1` | `0xcee4d73f1d45099e0ba6a9c55829cbf6964410283ff0d53739eee7e7cf939311` | `0x854d6e4b005a9225b67843695761e9bcc2b1e84080b264c22c371554d6c999a4` | interim-1002 — not V3 | delay 5 blocks |
| 4 | hot spends unannounced | `Proxy.proxy(Balances.transfer_keep_alive)` | hot | Proxy.Unannounced | Proxy.Unannounced | `6234-1` | `0x0a3621aff82f9eab1c06820829a3026bb05094f5bd303ca3bfc9d06b7767e702` | `0x258d676ecf2ca957f6acae84782200cf6a95dce284801dc09192083cb94c97fc` | interim-1002 — not V3 | I1 |
| 5 | hot announces theft | `Proxy.announce` | hot | ok | ok | `6235-1` | `0x3037293ab29d64f795eb61c8acec478f98ddb4d3f24e00d1f54ef2ce8d06640a` | `0x584346748c18dd9ad4533de46ae28b54cf1766f26ffb00157c4d66d4348d40f8` | interim-1002 — not V3 | `call hash 0x5b11231612a3699c8009e8abb7b35314a73a8f28088221aa1a5d1a92bd2222fa` |
| 6 | cold vetoes | `Proxy.proxy(Proxy.reject_announcement)` | cold | ok | ok | `6236-1` | `0x8c57061bf9032aa70ea3dfbb342a2df1eafa55c336a8e14f304972c35bf7c954` | `0x62aa4100dbf0801ed18743010f0fe59e8b462db7203782b39f5456f4d5b81b52` | interim-1002 — not V3 |  |
| 7 | vetoed theft after delay | `Proxy.proxy_announced(Balances.transfer_keep_alive)` | hot | Proxy.Unannounced | Proxy.Unannounced | `6242-1` | `0x09389cc321ecda0ee33b6b194eb8d539fd5adb8e8538ce0b9e21f36a0da00b64` | `0xc1b7a423634284aa5a3855165ca08c23cdea16c4e46075b9fe11bed68744a7f7` | interim-1002 — not V3 | I2 |
| 8 | hot announces payment | `Proxy.announce` | hot | ok | ok | `6243-1` | `0x6255af40411e7096dbb9dc74f8a8c4fc2a542743763b3dfab4350163c38188f0` | `0x86a779268f5a5eed2f1f7ab008e28c15757574707d9c0bb34533e817b1b84565` | interim-1002 — not V3 | `call hash 0xa4369adc5768a0261ef223e7fa5da14b9ca90da9f10717df38bfae9768ec2748` |
| 9 | payment before delay | `Proxy.proxy_announced(Balances.transfer_keep_alive)` | hot | Proxy.Unannounced | Proxy.Unannounced | `6244-1` | `0xe283949557b9302bfbedec20457b213a13f55db88717b60c94f8260a245e7182` | `0x9e1da84a43f1bc0916315eb1b4e46de8320f5f6408ffa54b703b2f6d293c319d` | interim-1002 — not V3 | delay enforced |
| 10 | payment executes after delay | `Proxy.proxy_announced(Balances.transfer_keep_alive)` | hot | ok | ok | `6250-1` | `0x3f903dd62816ce820ff11caf7f7e7b185ca0a44344b925d6c01d5ffbd3e877e2` | `0xed114bbe9377bd3a430bc195608f837ef9166149258407d364d4e0d7627da26c` | interim-1002 — not V3 | control for I2 |
| 11 | vested payout to vault | `Vesting.vested_transfer` | cold | ok | ok | `6251-1` | `0xeaed06785c0af6ed6833cb3ecab9705c56a5278ae4fb62a79b2804fea282e9b5` | `0xf279fcb90625f0e984268687d3973e9c76143c28b35a8599087bc4bc8b439613` | interim-1002 — not V3 | 1000 locked, 1.0000 POT/block from #6250 |
| 12 | vault claims vested | `Proxy.proxy(Vesting.vest)` | cold | ok | ok | `6254-1` | `0x8382c1915980ab8317a6da5ad24bf1553cff96a7f5220d9ec5aea5aee67e1d6d` | `0xd49bd0aee21b2e45da0f52be416a982d7884ba1d7a44cdab434b9cc6752db7df` | interim-1002 — not V3 | vest through the vault |


### re-fetch — 2026-10-14T11:30:21Z — the 2026-10-14T11:15:53Z interim-sdk run, by extrinsic id

Fresh process over http (`http://127.0.0.1:29944`; the run itself was sent over ws) at head #12513 · `evidence/refetch-interim-sdk-2026-10-14T113021Z.json` · every row: same block hash, tx hash and outcome as recorded.

| extrinsic id | call | outcome | tx hash | profile |
|---|---|---|---|---|
| `12224-2` | `Proxy.create_pure` | ok | `0x7becd3cb275a3a27b00d8a5c70abbe33eb140b1dfe85f4e735cfb38e57686bbf` | interim-sdk — not V3 |
| `12225-2` | `Balances.transfer_keep_alive` | ok | `0xe499a8ff99987cd494eb803739814e987c54f2cf41baba73a8862ce62de7f5bb` | interim-sdk — not V3 |
| `12226-2` | `Proxy.proxy(Proxy.add_proxy)` | ok | `0xb5e21b96c29d8a3c48e1935f93994d13dc6174d5a60b1bf7139b188e61dee079` | interim-sdk — not V3 |
| `12227-2` | `Proxy.proxy(Balances.transfer_keep_alive)` | Proxy.Unannounced | `0xc4fd4ce8ed47306cd7d69e5d7d7438482b767e5fdccec68a01121ed3d593c684` | interim-sdk — not V3 |
| `12228-2` | `Proxy.announce` | ok | `0x2bec071a25eb80bd0a7b0cc7bab8dbd479c48eee54ae4096607607eebb8ab93a` | interim-sdk — not V3 |
| `12229-2` | `Proxy.proxy(Proxy.reject_announcement)` | ok | `0xbdf4f6b82e7f55313368f35a7602ece73591aad471f2de29844fb7c998a2258c` | interim-sdk — not V3 |
| `12235-2` | `Proxy.proxy_announced(Balances.transfer_keep_alive)` | Proxy.Unannounced | `0xb6412bca78cb51cb7d0dba7a9b773f7b47646e36cba92cb6f644073945af8668` | interim-sdk — not V3 |
| `12236-2` | `Proxy.announce` | ok | `0xd4536eb7f0b9c4f073568d4cabdb42965e957818afa5bfd4d1f51ee52241c818` | interim-sdk — not V3 |
| `12237-2` | `Proxy.proxy_announced(Balances.transfer_keep_alive)` | Proxy.Unannounced | `0x6f384ca3ca913357c1dc9293664f90e9942b0a902d414b2305057e59f9affb15` | interim-sdk — not V3 |
| `12243-2` | `Proxy.proxy_announced(Balances.transfer_keep_alive)` | ok | `0x13e3c57df7c9ec3754e11a01d8c42b58f8f4d27921da31a1b07bd9c02794567f` | interim-sdk — not V3 |
| `12244-2` | `Vesting.vested_transfer` | ok | `0x530aa03e3b401e9664269f50234c32f1c8810791d908c16fa58cb87359fe7983` | interim-sdk — not V3 |
| `12247-2` | `Proxy.proxy(Vesting.vest)` | ok | `0xeef154c655938056180f8a2a216eb19216e173c84c6897df3e2a4d4f8c34d32d` | interim-sdk — not V3 |

### re-fetch — 2026-10-14T11:30:23Z — the 2026-10-14T11:27:41Z interim-1002 run, by extrinsic id

Fresh process over http (`http://127.0.0.1:19933`; the run itself was sent over ws) at head #6257 · `evidence/refetch-interim-1002-2026-10-14T113023Z.json` · every row: same block hash, tx hash and outcome as recorded.

| extrinsic id | call | outcome | tx hash | profile |
|---|---|---|---|---|
| `6231-1` | `Proxy.anonymous` | ok | `0xd93e1081dda5321f03fec308971d67a909fc2e6ba7e6456f7924f20a0f008e96` | interim-1002 — not V3 |
| `6232-1` | `Balances.transfer_keep_alive` | ok | `0x9a2351ad0c0499552018a629a0ada0f4faf5fb20c5ffaf6f4477e7c3a87149ed` | interim-1002 — not V3 |
| `6233-1` | `Proxy.proxy(Proxy.add_proxy)` | ok | `0x854d6e4b005a9225b67843695761e9bcc2b1e84080b264c22c371554d6c999a4` | interim-1002 — not V3 |
| `6234-1` | `Proxy.proxy(Balances.transfer_keep_alive)` | Proxy.Unannounced | `0x258d676ecf2ca957f6acae84782200cf6a95dce284801dc09192083cb94c97fc` | interim-1002 — not V3 |
| `6235-1` | `Proxy.announce` | ok | `0x584346748c18dd9ad4533de46ae28b54cf1766f26ffb00157c4d66d4348d40f8` | interim-1002 — not V3 |
| `6236-1` | `Proxy.proxy(Proxy.reject_announcement)` | ok | `0x62aa4100dbf0801ed18743010f0fe59e8b462db7203782b39f5456f4d5b81b52` | interim-1002 — not V3 |
| `6242-1` | `Proxy.proxy_announced(Balances.transfer_keep_alive)` | Proxy.Unannounced | `0xc1b7a423634284aa5a3855165ca08c23cdea16c4e46075b9fe11bed68744a7f7` | interim-1002 — not V3 |
| `6243-1` | `Proxy.announce` | ok | `0x86a779268f5a5eed2f1f7ab008e28c15757574707d9c0bb34533e817b1b84565` | interim-1002 — not V3 |
| `6244-1` | `Proxy.proxy_announced(Balances.transfer_keep_alive)` | Proxy.Unannounced | `0x9e1da84a43f1bc0916315eb1b4e46de8320f5f6408ffa54b703b2f6d293c319d` | interim-1002 — not V3 |
| `6250-1` | `Proxy.proxy_announced(Balances.transfer_keep_alive)` | ok | `0xed114bbe9377bd3a430bc195608f837ef9166149258407d364d4e0d7627da26c` | interim-1002 — not V3 |
| `6251-1` | `Vesting.vested_transfer` | ok | `0xf279fcb90625f0e984268687d3973e9c76143c28b35a8599087bc4bc8b439613` | interim-1002 — not V3 |
| `6254-1` | `Proxy.proxy(Vesting.vest)` | ok | `0xd49bd0aee21b2e45da0f52be416a982d7884ba1d7a44cdab434b9cc6752db7df` | interim-1002 — not V3 |
