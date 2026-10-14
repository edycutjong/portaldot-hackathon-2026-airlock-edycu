# Airlock — on-chain proof

Every row is a real extrinsic, identified by extrinsic id (`block-index`), block hash and transaction hash, and
labelled with the chain profile it ran on (`airlock/chain_profile.py`). Rows are appended by the script that
produced them (`scripts/spike_proxy.py --proof docs/PROOF.md`), never added by hand (a stale row may be deleted by hand); each run names the commit
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

### spike_proxy — 2026-10-14T01:07:45Z — interim — not V3 (upstream polkadot-sdk substrate-node dev chain)

Runtime `node/268` · genesis `0x0c4402ef4ccc34d28b16523e1cf26831716527c61a65d8e0840ab26e1636c8a2` · vault `5FvNTGdCVaakQXmoYYgrS6XFgUbWBqfSYpAsyptdgRRKHWmF` · delay D = 5 blocks · code `48a84db`

| # | step | call | signer | expected | outcome | extrinsic id | block hash | tx hash | profile | note |
|---|---|---|---|---|---|---|---|---|---|---|
| 1 | create vault | `Proxy.create_pure` | cold | ok | ok | `73-2` | `0x7369035a0a6633f3d6ae053db61c57cefda562161e250dbae0cff36c4ab8c2f8` | `0x17db7e58feb4ba91b44026beeab1a637d0b49c388e2850d74e61da744d7f43f3` | interim-sdk — not V3 |  |
| 2 | fund vault | `Balances.transfer_keep_alive` | cold | ok | ok | `74-2` | `0x12d5444052aa6413771678b93ba8fee9830bf14cb1f8ae5c17cbf8b5a19cb2f7` | `0x610fc9cd89d61d36f08b18d8fb73e6583a9286f1768f586893d502fd39e6f425` | interim-sdk — not V3 |  |
| 3 | add delayed hot key | `Proxy.proxy(Proxy.add_proxy)` | cold | ok | ok | `75-2` | `0x62b6f3d59609c35c23498743143a68db89f9da4a1b604b70f880849923121a3e` | `0xca90003964090b25ff82f1488ac80c03f5010dfda1f3fbda45244959b12e95df` | interim-sdk — not V3 | delay 5 blocks |
| 4 | hot spends unannounced | `Proxy.proxy(Balances.transfer_keep_alive)` | hot | Proxy.Unannounced | Proxy.Unannounced | `76-2` | `0xd4633f417568b25f919eb2644f9f1b6dbec2164a76eafe28fe71ac8d6b4f53e1` | `0x5cc8193de1c908efed3c65dd1641c69ab0768e79d49b5c449df7cc400d397c71` | interim-sdk — not V3 | I1 |
| 5 | hot announces theft | `Proxy.announce` | hot | ok | ok | `77-2` | `0x652b032e303b65628090c5ffb45bbc4c708ead506484b03b87593c2dbcda1a0c` | `0x0a1113406f91e12340360c256aea969b4bba02f924f2877e0a26df5a0e041532` | interim-sdk — not V3 | `call hash 0xf4c3c3361dcfb6b8193c568bd42e6ab4d2731097b285f573e10e7d2f7e55478a` |
| 6 | cold vetoes | `Proxy.proxy(Proxy.reject_announcement)` | cold | ok | ok | `78-2` | `0xb990c07a9f5f29dd87f7f98e58cf868ea3e97c934591a6bd4a2940df31748498` | `0xf69a5bb03f4e84a2100beca375b62a08577308f0f60f6f7d08ce640403292a40` | interim-sdk — not V3 |  |
| 7 | vetoed theft after delay | `Proxy.proxy_announced(Balances.transfer_keep_alive)` | hot | Proxy.Unannounced | Proxy.Unannounced | `84-2` | `0x956a061303591a7a427b1f968e6c8d49b641b430be2922a5cab8bdbaf28efce2` | `0x0f5771372b43caa62d2d6da2a6760a9d4a53433ed4eb318d425d56fc3ec8ffd3` | interim-sdk — not V3 | I2 |
| 8 | hot announces payment | `Proxy.announce` | hot | ok | ok | `85-2` | `0x5be98211332043416589c2a4e3cd071774850728a942c520fbd1853f64c26834` | `0x08b1cf270ddfd50cfed6f9e39dafc030f93e730859647b60a7ee9001685212bf` | interim-sdk — not V3 | `call hash 0x33aaaddcdcaeb538d590ead70a635c99a4d5a6adf61773ea5558f5c96291a385` |
| 9 | payment before delay | `Proxy.proxy_announced(Balances.transfer_keep_alive)` | hot | Proxy.Unannounced | Proxy.Unannounced | `86-2` | `0x7308659ab4179eb4cd6adfbb2cc6f7a17db328c6f833d42fcdc54522707be035` | `0x6af702568178dcc873b1e3d561ab1416b51c01cdf486071e1a3c8c150aa5fce2` | interim-sdk — not V3 | delay enforced |
| 10 | payment executes after delay | `Proxy.proxy_announced(Balances.transfer_keep_alive)` | hot | ok | ok | `92-2` | `0x3e92320890b0f868ebe5a71267016ae1de72502267549d421931e30319243998` | `0xbce6d8b728916d7fae94ddb2c40ac9c3a3aa31c40f12e17f614990a7db8ca28c` | interim-sdk — not V3 | control for I2 |

### spike_proxy — 2026-10-14T01:08:46Z — interim — not V3 (Portaldot dev node, runtime spec 1002)

Runtime `portaldot/1002` · genesis `0x53c36c0fc46f5bb2429d701b5ad7a0577b343aa53c4964333f1922611974dd22` · vault `5Hb37SqmVJSzREN7ziBw1ZQJWLgDS7DLaEjKisf8a6KUYsLv` · delay D = 5 blocks · code `48a84db`

| # | step | call | signer | expected | outcome | extrinsic id | block hash | tx hash | profile | note |
|---|---|---|---|---|---|---|---|---|---|---|
| 1 | create vault | `Proxy.anonymous` | cold | ok | ok | `48-1` | `0x664de95ff69b9850001189ffd023f0e90ee98ce47af63b776ef6dafa39f82e01` | `0xec1e63b6c81f92218669165946350eeaeaceee1fe9718351d250370eae1453bd` | interim-1002 — not V3 |  |
| 2 | fund vault | `Balances.transfer_keep_alive` | cold | ok | ok | `49-1` | `0xf470df6111bb591db31594bc66949da92f2b449b0889e56da0c3fd5e137994af` | `0xaeac6c2bace2736c4e4b5d2c8bb23806c020027a12326e4106b4ee7a8e596efd` | interim-1002 — not V3 |  |
| 3 | add delayed hot key | `Proxy.proxy(Proxy.add_proxy)` | cold | ok | ok | `50-1` | `0x2290c08323cb4502cac4c3e98a1d526f401d4b5d8a2d372e23359707158c0bfb` | `0xded42f2515eb7fa46fe4f5ed3d0aa2a6ed80a1360a44a1647733c670b69e4eb4` | interim-1002 — not V3 | delay 5 blocks |
| 4 | hot spends unannounced | `Proxy.proxy(Balances.transfer_keep_alive)` | hot | Proxy.Unannounced | Proxy.Unannounced | `51-1` | `0xe5d60afa814c15a9ea9bfb3d719ff8b14d8ff56529b002fea5016d277f9f478d` | `0x4616fd3eec52d2539edd74fd3fb494a0f2cefbe994a815e410110a0b51419e61` | interim-1002 — not V3 | I1 |
| 5 | hot announces theft | `Proxy.announce` | hot | ok | ok | `52-1` | `0xd9d89f5814e9f4966a3112f042a5f0c2dd406d423ac900e6763de18532599b74` | `0x87d74d15452cffa1b67907b15cc0877f8a88215f32d9c174991cab3d484f387b` | interim-1002 — not V3 | `call hash 0x5b11231612a3699c8009e8abb7b35314a73a8f28088221aa1a5d1a92bd2222fa` |
| 6 | cold vetoes | `Proxy.proxy(Proxy.reject_announcement)` | cold | ok | ok | `53-1` | `0xdebb771095da04be4bb8e69d5cb975f1ef2a9e0e18415516ba93c70165d6020a` | `0xf5f815288ea6ebd1c18fcd519cc0322d4c7543456eefb48119f541854c8a768a` | interim-1002 — not V3 |  |
| 7 | vetoed theft after delay | `Proxy.proxy_announced(Balances.transfer_keep_alive)` | hot | Proxy.Unannounced | Proxy.Unannounced | `59-1` | `0x871e2d4d8da8ccf35ecac263cdc364a7814c0dc64b14e1b9bd1b5f98400aadef` | `0x2bf21ff0b80d5ae9d93fdc017123f3e9e98e278bdb62f3e8596f4118b12ab652` | interim-1002 — not V3 | I2 |
| 8 | hot announces payment | `Proxy.announce` | hot | ok | ok | `60-1` | `0xae3a5889aeb4aa8df78652e8baa81186ca9e7f435e286bd87064612af6e0a542` | `0xd15fb7677cf623667c2550cdc0a970b83df1ba4ef9a416a2b99a4c13a4c46e33` | interim-1002 — not V3 | `call hash 0xa4369adc5768a0261ef223e7fa5da14b9ca90da9f10717df38bfae9768ec2748` |
| 9 | payment before delay | `Proxy.proxy_announced(Balances.transfer_keep_alive)` | hot | Proxy.Unannounced | Proxy.Unannounced | `61-1` | `0xf5a8526f1d0e80f31447b4d5cefb5fa60dae41cd95a9c12f76fc922544b41238` | `0x33a1b4d57d3b4f9cb2221c692e91851a8badfe47a2d269d39ca5cae752167158` | interim-1002 — not V3 | delay enforced |
| 10 | payment executes after delay | `Proxy.proxy_announced(Balances.transfer_keep_alive)` | hot | ok | ok | `67-1` | `0x5d366827e2889f631e1659f6461f14c72d4d1d4620f77bd818230995bc8f5f15` | `0x658cb5034d1de0907ebd4c3c3429d71de3fddba39ea429543379ec4825b34e14` | interim-1002 — not V3 | control for I2 |
