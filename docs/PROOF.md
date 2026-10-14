# Airlock — on-chain proof

Every row is a real extrinsic, identified by extrinsic id (`block-index`), block hash and transaction hash, and
labelled with the chain profile it ran on (`airlock/chain_profile.py`). Rows are appended by the scripts that
produced them (`scripts/spike_proxy.py --proof docs/PROOF.md`), never typed by hand.

**Profile labels.** The target is the Portaldot V3 testnet. It is offline while this is written, so these rows come from
**interim chains — not V3**:

| Profile | Chain | Runtime | Genesis hash |
|---|---|---|---|
| `interim-sdk` | upstream polkadot-sdk `substrate-node` dev chain (`polkadot-stable2606-3`), run by `scripts/interim_node.sh` | `node/268` | `0x0c4402ef4ccc34d28b16523e1cf26831716527c61a65d8e0840ab26e1636c8a2` |
| `interim-1002` | Portaldot's own `portaldot_dev` dev node, `--tmp` | `portaldot/1002` | `0x53c36c0fc46f5bb2429d701b5ad7a0577b343aa53c4964333f1922611974dd22` |

The dev genesis is deterministic, so a fresh `interim-sdk` chain has the same genesis hash; a row can be re-fetched only
from the node that recorded it (`--base-path` keeps it across restarts). `interim-1002` runs with `--tmp`, so its rows
are a run log, not re-fetchable proof. Signers are dev keys (`//Alice` cold, `//Bob` hot, `//Eve` payee).

## Runs

### spike_proxy — 2026-10-14T00:12:14Z — interim — not V3 (upstream polkadot-sdk substrate-node dev chain)

Profile `interim-sdk` · runtime `node/268` · genesis `0x0c4402ef4ccc34d28b16523e1cf26831716527c61a65d8e0840ab26e1636c8a2` · vault `5CBL5HHyGCz9MMVNsfzYP9Y14QbfAwQXe2ibKbJucFFhvLHt`

| # | step | call | signer | expected | outcome | extrinsic id | block hash | tx hash | profile |
|---|---|---|---|---|---|---|---|---|---|
| 1 | create vault | `Proxy.create_pure` | cold | ok | ok | `188-2` | `0x75ef0b45cea65b2dfbb5c25866b665380b1df8fb074d28b408784ef00e9c2dc0` | `0xe4d325416c5ea1089710714bd3b82da926f3f1b32a6757804dcd1fc19b660183` | interim-sdk |
| 2 | fund vault | `Balances.transfer_keep_alive` | cold | ok | ok | `189-2` | `0xc2048db07ca0c5645b3988a3a2ae1025d3d47089bccf9f151f431055c49778bc` | `0x79d1d70d161e1444a8831ba916586d5189b6b36a1080b09a7fda5b537ca40171` | interim-sdk |
| 3 | add delayed hot key | `Proxy.proxy(Proxy.add_proxy)` | cold | ok | ok | `190-2` | `0xcba000b52b68e68fb38634e21b4064d4efa3e89d406d57fdee1afb8004040804` | `0xc5e1f37fc60a4217a893682572c16323994bc5ca2de7314ea91b1079453233ae` | interim-sdk |
| 4 | hot spends unannounced | `Proxy.proxy(Balances.transfer_keep_alive)` | hot | Proxy.Unannounced | Proxy.Unannounced | `191-2` | `0x1729c6051abc58f7cf66b1fc7dc8e0d200712529b41d6d019b2292235fc2927a` | `0x3ff6d8f740b9a6b284223f73f7b53a15daa21ddac599945145e795e5d2ca262a` | interim-sdk |
| 5 | hot announces | `Proxy.announce` | hot | ok | ok | `192-2` | `0xa14f3f744ebda2b4424299531135aac5d0158452b04c84e0411251e69a17509f` | `0x88f7c4d1607e7f98551e6d28120329506915611e6b307ec9c60a7765b9d271ad` | interim-sdk |
| 6 | cold vetoes | `Proxy.proxy(Proxy.reject_announcement)` | cold | ok | ok | `193-2` | `0x40467a4629d64d4f8027ac9aa4bb5f4e32c6d42394cd12e0b3e3dadccf6a6f0b` | `0x0588a9f0123694d554c1690d44c471306820203cc2b5ce48c763804c9f975467` | interim-sdk |
| 7 | hot executes after delay | `Proxy.proxy_announced(Balances.transfer_keep_alive)` | hot | Proxy.Unannounced | Proxy.Unannounced | `200-2` | `0xc4d0985229755cc93044f6c24bd573cd34849a54bbc8047faccfa8ea1eb8a9db` | `0x0699d92065e6cfa192206ab1bdc3e7213bd6f79a29a6e19270cd12d148d6676c` | interim-sdk |

### spike_proxy — 2026-10-14T00:12:55Z — interim — not V3 (Portaldot dev node, runtime spec 1002)

Profile `interim-1002` · runtime `portaldot/1002` · genesis `0x53c36c0fc46f5bb2429d701b5ad7a0577b343aa53c4964333f1922611974dd22` · vault `5FxZ8BvR7yQbBQ2bKVbfDqRZTjCSBCEBqHqjyGZu1gerq2xT`

| # | step | call | signer | expected | outcome | extrinsic id | block hash | tx hash | profile |
|---|---|---|---|---|---|---|---|---|---|
| 1 | create vault | `Proxy.anonymous` | cold | ok | ok | `43-1` | `0x304890b4151dfa42a7b97cc89f74d4e49ebc5f91ebed3334312f31a76ee29c88` | `0x77a7c92e1d48c7887c437451c03f54c642edc4dacfa40a4c25a997dcf915f243` | interim-1002 |
| 2 | fund vault | `Balances.transfer_keep_alive` | cold | ok | ok | `44-1` | `0x274451e6c0f1a2c6d39a48790e5d5e5c41173b3ec838398928b723291e1032c5` | `0xb5ce8e1a52d838a1ab4a84fa2a117c72daa48b1a01b90a35d14a1a87ceba4787` | interim-1002 |
| 3 | add delayed hot key | `Proxy.proxy(Proxy.add_proxy)` | cold | ok | ok | `45-1` | `0xcaf70e0c8a6d11413d8b189a4ae80e0c595463ff55a60094882f84a43e5de2cf` | `0x0cf7a2e8ac462405c8b7061e47fb64b9373633ca904b5a7d40843e4784ba89d7` | interim-1002 |
| 4 | hot spends unannounced | `Proxy.proxy(Balances.transfer_keep_alive)` | hot | Proxy.Unannounced | Proxy.Unannounced | `46-1` | `0x54057c33c58cbf89312f491d3b9275a7df1300a2f5db75bba040d801d7006742` | `0x69bab140a5d96dbb8d699f23fe534895116bbccb273d7481436975da9e3095c1` | interim-1002 |
| 5 | hot announces | `Proxy.announce` | hot | ok | ok | `47-1` | `0xd9d7bd691fb34d552b32a891b682d803d0c421caf982456259a68fc5f10feb1f` | `0x03d240b299ca4515fff7278d1f874e3aaaa7bd5f05538a7f6b3819676e2cc4f3` | interim-1002 |
| 6 | cold vetoes | `Proxy.proxy(Proxy.reject_announcement)` | cold | ok | ok | `48-1` | `0xf90a13e00a46b3e550d24e8894f79f2fc1b9cf7331bbbbd8fb88b2a28421e957` | `0x8559e9fcae68f82859cdc5594fa429ed7ddeb4eb21f93148ca12382de179e516` | interim-1002 |
| 7 | hot executes after delay | `Proxy.proxy_announced(Balances.transfer_keep_alive)` | hot | Proxy.Unannounced | Proxy.Unannounced | `54-1` | `0x6dc44d6fb64532a9c29a98d42474c16f18da03bd17a0911ef097525c31580e6a` | `0x9dbc86597afff4907a97b407629c08fdc92ae7bf671410018500efea3c7dd80f` | interim-1002 |

### spike_proxy — 2026-10-14T00:16:27Z — interim — not V3 (upstream polkadot-sdk substrate-node dev chain)

Profile `interim-sdk` · runtime `node/268` · genesis `0x0c4402ef4ccc34d28b16523e1cf26831716527c61a65d8e0840ab26e1636c8a2` · vault `5Fe5MVGFazAbD2Gg8YVVKR3ELTpL8HkMTdJe9Y7SAryX2pp7`

| # | step | call | signer | expected | outcome | extrinsic id | block hash | tx hash | profile |
|---|---|---|---|---|---|---|---|---|---|
| 1 | create vault | `Proxy.create_pure` | cold | ok | ok | `272-2` | `0x0fe9e5aa06974d1a801d6f3c927593c517280232f854f827783d3de5b321f94c` | `0x630e3a9987c98901b02dbaf2dd47f1ebb2c295eeddd7436e818b07f1726220c0` | interim-sdk |
| 2 | fund vault | `Balances.transfer_keep_alive` | cold | ok | ok | `273-2` | `0x1ae6687a101431427ac0ce00cde748bc8728f7758d52a714b198bcf120080d4f` | `0xe81a661200e0a6718698a3af266f7f21aba76b18236e3e592b07402acaac59f5` | interim-sdk |
| 3 | add delayed hot key | `Proxy.proxy(Proxy.add_proxy)` | cold | ok | ok | `274-2` | `0xbee04413af940306130cd4e40fffa8280b70858eeef7a49e2fa64f3f3535141c` | `0x3907473a4602b8e867da9616b796fc2d2a1e8472818931691863f471de98f95f` | interim-sdk |
| 4 | hot spends unannounced | `Proxy.proxy(Balances.transfer_keep_alive)` | hot | Proxy.Unannounced | Proxy.Unannounced | `275-2` | `0x3953ad30521e480e40a1780f7802c4c9a1bafa8ae0606f1614a4d0e962fc2d97` | `0x7be406822ecb055ce7f9fbc65266071537169707bfcc9826f1ce7962dc4ea92f` | interim-sdk |
| 5 | hot announces | `Proxy.announce` | hot | ok | ok | `276-2` | `0x8aa2345dc646d44fdf5dced6a3ef295d745de535f50eed9232c835e6ebba5882` | `0x42f78a5488c513ca845070b65f0b84e98e408153b2f9d3aefb6738787f361b94` | interim-sdk |
| 6 | cold vetoes | `Proxy.proxy(Proxy.reject_announcement)` | cold | ok | ok | `277-2` | `0x673fb6c0b5aabc7706b787e6c867dc0ee1dd81f36b3a7ea6563d797abdd5e09f` | `0x243bebc35668ad0b418820d9c2f79f958989e04284d99664adfd5ab1de80ef7f` | interim-sdk |
| 7 | hot executes after delay | `Proxy.proxy_announced(Balances.transfer_keep_alive)` | hot | Proxy.Unannounced | Proxy.Unannounced | `283-2` | `0x43da1502729c091b18a963071666f8ad4c2c94ad07ae124a8decebfe8096dc9a` | `0xc4a22d574c4765c253b7484dc17167cbd61e2f70475bfed406d12b80f77278ce` | interim-sdk |

### spike_proxy — 2026-10-14T00:17:04Z — interim — not V3 (Portaldot dev node, runtime spec 1002)

Profile `interim-1002` · runtime `portaldot/1002` · genesis `0x53c36c0fc46f5bb2429d701b5ad7a0577b343aa53c4964333f1922611974dd22` · vault `5FQ9Ld4TshxD5PvecCpVQfcRS9KWsqcLdjyqK1VeePDKu2gf`

| # | step | call | signer | expected | outcome | extrinsic id | block hash | tx hash | profile |
|---|---|---|---|---|---|---|---|---|---|
| 1 | create vault | `Proxy.anonymous` | cold | ok | ok | `84-1` | `0xe9d55067701cc2217d03ec7ad294ad8e084502509f65ee36bfd2f376af7b8b0a` | `0xc33d04702f4cdbd3769aaa4b25838894815dc17b070bfbe29aea9f142866c083` | interim-1002 |
| 2 | fund vault | `Balances.transfer_keep_alive` | cold | ok | ok | `85-1` | `0xd870b79c81de840aa000f00ea6e12f2bb914abc350a43dbba7f128f2f3477296` | `0xd4ed44f1554b87efad38393986733f51da56429ee0480d45a0885aae5e48f3c3` | interim-1002 |
| 3 | add delayed hot key | `Proxy.proxy(Proxy.add_proxy)` | cold | ok | ok | `86-1` | `0xce2dea92b9d2b8b777eca46e3bb6d20bab2f967d6c7536c63b859b4966a3dbd2` | `0x3bbe79c325871dc4514a8e4117737d2ed5170bd12ebd1f0377b32bc8878e1966` | interim-1002 |
| 4 | hot spends unannounced | `Proxy.proxy(Balances.transfer_keep_alive)` | hot | Proxy.Unannounced | Proxy.Unannounced | `87-1` | `0x26e40b7d11907b33576167903e1173dc98825bc65745b6ce836e1b8527409253` | `0x3d5275fe57d458a6be583c77b10f1b5d24d8cb918d33a4326d58506c35652e65` | interim-1002 |
| 5 | hot announces | `Proxy.announce` | hot | ok | ok | `88-1` | `0xe88c380a38811c1368967f1f5926a2f6a3b7508570bcbcbedb1a3a1eca87a8e9` | `0xc0197d05c2fbc4e1797362127e58ce6b5e8a42788530493c7edd37691255bdd0` | interim-1002 |
| 6 | cold vetoes | `Proxy.proxy(Proxy.reject_announcement)` | cold | ok | ok | `89-1` | `0xbeaf6838bf218ab1ec8bbf9889ff50bf5c8fded692f2fceb32e7479895b2433d` | `0xd12db51a02a58a216dcd2581b34d49fbf20dfe9ee2171d7577168cafa420ec82` | interim-1002 |
| 7 | hot executes after delay | `Proxy.proxy_announced(Balances.transfer_keep_alive)` | hot | Proxy.Unannounced | Proxy.Unannounced | `95-1` | `0xc47cdc1425d73299f2b80a697e67f00c8189d977adf55b80fcaec5fa0d9b2a4c` | `0x11919371b91239576cd1e9a2a997629cf23752c3147f7d23287e0857d13dc74f` | interim-1002 |
