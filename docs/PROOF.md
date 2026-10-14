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
is the control for step 7: the same delegate and delay, without a veto, and the payment executes. Steps 11–13
(`--vesting`) send the vault a vested payout and claim it through the vault; the vesting-locked part is not counted as
at risk (checked after steps 11 and 12), and step 13 is the on-chain check of that figure: moving one token more than
the at-risk amount out of the vault fails on the lock (`Token.Frozen`; `Balances.LiquidityRestrictions` on 1002). The
`interim-1002` run uses smaller amounts (fund 300, spend 50, vest 400 POT) because its dev `//Alice` has little
unlocked balance left.

**Re-fetch.** Each run is then looked up again by extrinsic id alone (`scripts/refetch.py`), from a fresh process and
over the node's HTTP endpoint instead of the websocket the run used; block hash, tx hash and outcome must all match.
Both endpoints are the same local node, so this is an independent process and transport, not an independent node.

## Runs

### spike_proxy — 2026-10-14T11:53:42Z — interim — not V3 (upstream polkadot-sdk substrate-node dev chain)

Runtime `node/268` · genesis `0x0c4402ef4ccc34d28b16523e1cf26831716527c61a65d8e0840ab26e1636c8a2` · vault `5EQBNzp9pRUqTZTL5puByBeruHjG5Aa45dsEoKVRgyjFqbFj` · delay D = 5 blocks · code `b381a70`

| # | step | call | signer | expected | outcome | extrinsic id | block hash | tx hash | profile | note |
|---|---|---|---|---|---|---|---|---|---|---|
| 1 | create vault | `Proxy.create_pure` | cold | ok | ok | `12975-2` | `0x5bc61e3b9925cd8c42d3b1e74976ba1830190ddf319ff3badb7beff89bfda83b` | `0xf0da423c1f73908858d6eca73ca8836bff573ea9e5e3d4e8fb2b510fa76d84d1` | interim-sdk — not V3 |  |
| 2 | fund vault | `Balances.transfer_keep_alive` | cold | ok | ok | `12976-2` | `0x2f9276e58e00394d8c957f5d2c7352119e2fb9b97965f19d888cbb76762d3593` | `0x89419f360c2849c3b8eb7f18bd3af90e2be99682a7104effb7d503f2c47aadce` | interim-sdk — not V3 |  |
| 3 | add delayed hot key | `Proxy.proxy(Proxy.add_proxy)` | cold | ok | ok | `12977-2` | `0x692b4911285a7d4b5713054569a6a131d69f6655aa5ce72f709b0a606408503d` | `0x697cdb47c50e72ec26409bc1f971d5a688d94157b32937f7f767dcab783499e8` | interim-sdk — not V3 | delay 5 blocks |
| 4 | hot spends unannounced | `Proxy.proxy(Balances.transfer_keep_alive)` | hot | Proxy.Unannounced | Proxy.Unannounced | `12978-2` | `0x225145ddf22107ef6ee838ebdcdd2b87f9b1ae61fc7e11352e4a087ada3b2c28` | `0x67da523ab55ed7924414e320887bdeb4d87a4e21d70270fdd446dc2325a343a2` | interim-sdk — not V3 | I1 |
| 5 | hot announces theft | `Proxy.announce` | hot | ok | ok | `12979-2` | `0xdf23cf79bdc37109ca43a2904dc2a8e496d95bbaef49fb593e8ce8ebf37887d5` | `0x68d756437f8c06b4ffb42171fbe1af62810b22176a3ba6366c699837f227daf8` | interim-sdk — not V3 | `call hash 0xf4c3c3361dcfb6b8193c568bd42e6ab4d2731097b285f573e10e7d2f7e55478a` |
| 6 | cold vetoes | `Proxy.proxy(Proxy.reject_announcement)` | cold | ok | ok | `12980-2` | `0x311818dea4adffc823bf5bf2e36e1e294344353b1eb9c7870d70de6101d7df6d` | `0xa59b3c56efd051a61b4e7a9a3bf2606f787cd0d0ddbdd419a63e2e0f9a76ddfb` | interim-sdk — not V3 |  |
| 7 | vetoed theft after delay | `Proxy.proxy_announced(Balances.transfer_keep_alive)` | hot | Proxy.Unannounced | Proxy.Unannounced | `12986-2` | `0x13f1ba789123e0238d7717eea8e6c0d0c9dd673068573188bff8d793919ba76d` | `0xc3299d85226f45c18fa4113e04312f9f8e9896e11566a7d616ad99dd26f26a47` | interim-sdk — not V3 | I2 |
| 8 | hot announces payment | `Proxy.announce` | hot | ok | ok | `12987-2` | `0x9a2f92f3e5b03098fa9c33de93828f1d04fae7db9b72ef66e5097d48a8840855` | `0x342abb33d1537fd94eb20055a853d234371c8ec61c602cbab2a6b84e30777f88` | interim-sdk — not V3 | `call hash 0x33aaaddcdcaeb538d590ead70a635c99a4d5a6adf61773ea5558f5c96291a385` |
| 9 | payment before delay | `Proxy.proxy_announced(Balances.transfer_keep_alive)` | hot | Proxy.Unannounced | Proxy.Unannounced | `12988-2` | `0x85aa648f49435f8a1aa3c7cd28fa14675ba1888b64dafc9ea703a99e660803d9` | `0x859bb7000b2e6d577e9c151bf8679d6f27b39e5239e22c02151274880ee9bfee` | interim-sdk — not V3 | delay enforced |
| 10 | payment executes after delay | `Proxy.proxy_announced(Balances.transfer_keep_alive)` | hot | ok | ok | `12994-2` | `0xe7171d38123f555d44e9e8c9ff97a220bc1d08eb42858a752af4f6eed7b23c50` | `0x25784312ea6a2bda35d6dbcb3c133619d69388e0accb6c30d574784eb4b5b2b3` | interim-sdk — not V3 | control for I2 |
| 11 | vested payout to vault | `Vesting.vested_transfer` | cold | ok | ok | `12995-2` | `0x2f360c5c552190ba97ec8a493a57808581b572c21a0c3bcff734c31a9009085a` | `0x134ad93a435760ab152239a450c3d2d8db996cc98e5820ca108ce59ee5f47a1d` | interim-sdk — not V3 | 20000 locked, 20.0000 UNIT/block from #12994 |
| 12 | vault claims vested | `Proxy.proxy(Vesting.vest)` | cold | ok | ok | `12998-2` | `0x3565ed1ab1badec09497864dfacdb740f91abd4d2441d92d049997790d111168` | `0xacd32888c843b5ee838f614bbabcf6b91549e500034a4fff10c976fd43a68d25` | interim-sdk — not V3 | vest through the vault |
| 13 | move above at-risk | `Proxy.proxy(Balances.transfer_keep_alive)` | cold | inner:Token.Frozen | inner:Token.Frozen | `12999-2` | `0x37ffcd6fc2c0cece55ec916cb79674544bb3860a7d6715e9e1d4ee9b3bbe6b7b` | `0xa1fe7a67cb1bed502cd5c662e4181113ba9c5c3f1cbd5784cb02e40697dda2fd` | interim-sdk — not V3 | at-risk 980.0000 UNIT + 1 |

### spike_proxy — 2026-10-14T11:54:58Z — interim — not V3 (Portaldot dev node, runtime spec 1002)

Runtime `portaldot/1002` · genesis `0x53c36c0fc46f5bb2429d701b5ad7a0577b343aa53c4964333f1922611974dd22` · vault `5HLe29QVJDwg5aA7FZgRmiYqmEyFKJ8434u2pvn28nusy3Qh` · delay D = 5 blocks · code `b381a70`

| # | step | call | signer | expected | outcome | extrinsic id | block hash | tx hash | profile | note |
|---|---|---|---|---|---|---|---|---|---|---|
| 1 | create vault | `Proxy.anonymous` | cold | ok | ok | `6504-1` | `0xb9c7b4cf1d35d235d967febe91f95b2bc1b7fa03eceee3c7cac4d650c3a3f994` | `0x04bb8689fade2bc73887645a4c69e56ace76bdca19fb55edc65562b40c5bc064` | interim-1002 — not V3 |  |
| 2 | fund vault | `Balances.transfer_keep_alive` | cold | ok | ok | `6505-1` | `0x5479bafc9af66b4ac261b746c2faabe8fb8024e4379f92c2eb914a57f2c73de0` | `0x6af80798dcb3dd14a9e35d3619a29efce8ee0ccdcc84918bb3cb83cfa14532e4` | interim-1002 — not V3 |  |
| 3 | add delayed hot key | `Proxy.proxy(Proxy.add_proxy)` | cold | ok | ok | `6506-1` | `0xccd8a88a43119c3fae292233baa8d493463872e1d225e225b149766dc4b4c05a` | `0x5701a84a8691d3fb5ef1bce2bb7483a86f17599b773d287ed10f66b7144725a5` | interim-1002 — not V3 | delay 5 blocks |
| 4 | hot spends unannounced | `Proxy.proxy(Balances.transfer_keep_alive)` | hot | Proxy.Unannounced | Proxy.Unannounced | `6507-1` | `0x6e87f114ab49d08c843b2537e00243a572a33e990fbab59e07d87160505f9ffe` | `0x5c5ebb7a42af724bb59bb72f747482b1f93f22361caa188ef1aeb0388812ffc6` | interim-1002 — not V3 | I1 |
| 5 | hot announces theft | `Proxy.announce` | hot | ok | ok | `6508-1` | `0xc21b51dd027e7ba5fabfe309808c770ae24138b4a5d677dc8f55862a01d0507e` | `0xaf0b74da45f3146a4be720b78a940f971893b918241cdbe5cdabfd6d2a0b73e5` | interim-1002 — not V3 | `call hash 0xbc2bf7a3d174cc486bd6da760525b7274a57b027fd400804325bb0c590d73a7f` |
| 6 | cold vetoes | `Proxy.proxy(Proxy.reject_announcement)` | cold | ok | ok | `6509-1` | `0x80e92dffb0227eeaac98d74f1baccf4514d673a968d62c4bec721ffd04d98784` | `0xecbfa57e4650e6a7c5833f029ab40a2a8e20687d4b3ce7e8a87cb21ed3b7c8bc` | interim-1002 — not V3 |  |
| 7 | vetoed theft after delay | `Proxy.proxy_announced(Balances.transfer_keep_alive)` | hot | Proxy.Unannounced | Proxy.Unannounced | `6515-1` | `0x004ae072fcd7eeff0c11e8db96c970e08c113e4581b8e4e3001c1997595896f5` | `0x5d29e88025c66d9f85628abcc247f3cbb05b9db80260dbe1c1dd11b0e302b954` | interim-1002 — not V3 | I2 |
| 8 | hot announces payment | `Proxy.announce` | hot | ok | ok | `6516-1` | `0xce58ec25b5c5cba404e75d7eb0712cefa306c097399dcdc5836f6663bd34405b` | `0xd300a89628dc4e535d7ac0293a93e96cef84ec61204e1d4a23bdfd9b8dda733f` | interim-1002 — not V3 | `call hash 0x899f4616ad308c3d59fde7a85dc38edb9832607f70f87f80a6e8e9b3e5c73440` |
| 9 | payment before delay | `Proxy.proxy_announced(Balances.transfer_keep_alive)` | hot | Proxy.Unannounced | Proxy.Unannounced | `6517-1` | `0x0dd490cf630a10eb83cc1cb376fc0f919ea70df59e921afd480f86763d95d4e0` | `0x85beb7acf65ceac4f66ab651f0019878b2fbf3d8f8962e2bd07a43f50a31f4d2` | interim-1002 — not V3 | delay enforced |
| 10 | payment executes after delay | `Proxy.proxy_announced(Balances.transfer_keep_alive)` | hot | ok | ok | `6523-1` | `0x22b01f4be1bc93f93de6933de9abb16d1acd6d72480c1a08bccfb1932104c9e2` | `0x0fe9501dec5f0627f23ae8bf566d9db5dcd24822dc6e97fb6b9769c355f9560f` | interim-1002 — not V3 | control for I2 |
| 11 | vested payout to vault | `Vesting.vested_transfer` | cold | ok | ok | `6524-1` | `0x6598059bab9ff74d79a9916d05b7516f02023b88a545357b3619afa445646aeb` | `0x545913689ce13245396447417c9415c34b62a8ad6b0191acbb6aa906bd85b427` | interim-1002 — not V3 | 400 locked, 0.4000 POT/block from #6523 |
| 12 | vault claims vested | `Proxy.proxy(Vesting.vest)` | cold | ok | ok | `6527-1` | `0x219dffba067aacc3bf57a391b083f5f2c671421028ba3ba0cdcd2b90a593e656` | `0x486f6850c634950bda8251de8b2966a95c445bc60a3a58347523cc4972c5977b` | interim-1002 — not V3 | vest through the vault |
| 13 | move above at-risk | `Proxy.proxy(Balances.transfer_keep_alive)` | cold | inner:Balances.LiquidityRestrictions | inner:Balances.LiquidityRestrictions | `6528-1` | `0xecdaad58d9e17d3d7825a189327e41e23ed753cf866d020f8535320a1d44152b` | `0x3261e06e97fce7012c7f7e41e838a79955636393f0af154669b5bf581d19503e` | interim-1002 — not V3 | at-risk 249.6200 POT + 1 |

### re-fetch — 2026-10-14T11:57:29Z — the 2026-10-14T11:53:42Z interim-sdk run, by extrinsic id

Fresh process over http (`http://127.0.0.1:29944`; the run itself was sent over ws) at head #13049 · `evidence/refetch-interim-sdk-2026-10-14T115729Z.json` · every row: same block hash, tx hash and outcome as recorded.

| extrinsic id | call | outcome | tx hash | profile |
|---|---|---|---|---|
| `12975-2` | `Proxy.create_pure` | ok | `0xf0da423c1f73908858d6eca73ca8836bff573ea9e5e3d4e8fb2b510fa76d84d1` | interim-sdk — not V3 |
| `12976-2` | `Balances.transfer_keep_alive` | ok | `0x89419f360c2849c3b8eb7f18bd3af90e2be99682a7104effb7d503f2c47aadce` | interim-sdk — not V3 |
| `12977-2` | `Proxy.proxy(Proxy.add_proxy)` | ok | `0x697cdb47c50e72ec26409bc1f971d5a688d94157b32937f7f767dcab783499e8` | interim-sdk — not V3 |
| `12978-2` | `Proxy.proxy(Balances.transfer_keep_alive)` | Proxy.Unannounced | `0x67da523ab55ed7924414e320887bdeb4d87a4e21d70270fdd446dc2325a343a2` | interim-sdk — not V3 |
| `12979-2` | `Proxy.announce` | ok | `0x68d756437f8c06b4ffb42171fbe1af62810b22176a3ba6366c699837f227daf8` | interim-sdk — not V3 |
| `12980-2` | `Proxy.proxy(Proxy.reject_announcement)` | ok | `0xa59b3c56efd051a61b4e7a9a3bf2606f787cd0d0ddbdd419a63e2e0f9a76ddfb` | interim-sdk — not V3 |
| `12986-2` | `Proxy.proxy_announced(Balances.transfer_keep_alive)` | Proxy.Unannounced | `0xc3299d85226f45c18fa4113e04312f9f8e9896e11566a7d616ad99dd26f26a47` | interim-sdk — not V3 |
| `12987-2` | `Proxy.announce` | ok | `0x342abb33d1537fd94eb20055a853d234371c8ec61c602cbab2a6b84e30777f88` | interim-sdk — not V3 |
| `12988-2` | `Proxy.proxy_announced(Balances.transfer_keep_alive)` | Proxy.Unannounced | `0x859bb7000b2e6d577e9c151bf8679d6f27b39e5239e22c02151274880ee9bfee` | interim-sdk — not V3 |
| `12994-2` | `Proxy.proxy_announced(Balances.transfer_keep_alive)` | ok | `0x25784312ea6a2bda35d6dbcb3c133619d69388e0accb6c30d574784eb4b5b2b3` | interim-sdk — not V3 |
| `12995-2` | `Vesting.vested_transfer` | ok | `0x134ad93a435760ab152239a450c3d2d8db996cc98e5820ca108ce59ee5f47a1d` | interim-sdk — not V3 |
| `12998-2` | `Proxy.proxy(Vesting.vest)` | ok | `0xacd32888c843b5ee838f614bbabcf6b91549e500034a4fff10c976fd43a68d25` | interim-sdk — not V3 |
| `12999-2` | `Proxy.proxy(Balances.transfer_keep_alive)` | inner:Token.Frozen | `0xa1fe7a67cb1bed502cd5c662e4181113ba9c5c3f1cbd5784cb02e40697dda2fd` | interim-sdk — not V3 |

### re-fetch — 2026-10-14T11:57:31Z — the 2026-10-14T11:54:58Z interim-1002 run, by extrinsic id

Fresh process over http (`http://127.0.0.1:19933`; the run itself was sent over ws) at head #6529 · `evidence/refetch-interim-1002-2026-10-14T115731Z.json` · every row: same block hash, tx hash and outcome as recorded.

| extrinsic id | call | outcome | tx hash | profile |
|---|---|---|---|---|
| `6504-1` | `Proxy.anonymous` | ok | `0x04bb8689fade2bc73887645a4c69e56ace76bdca19fb55edc65562b40c5bc064` | interim-1002 — not V3 |
| `6505-1` | `Balances.transfer_keep_alive` | ok | `0x6af80798dcb3dd14a9e35d3619a29efce8ee0ccdcc84918bb3cb83cfa14532e4` | interim-1002 — not V3 |
| `6506-1` | `Proxy.proxy(Proxy.add_proxy)` | ok | `0x5701a84a8691d3fb5ef1bce2bb7483a86f17599b773d287ed10f66b7144725a5` | interim-1002 — not V3 |
| `6507-1` | `Proxy.proxy(Balances.transfer_keep_alive)` | Proxy.Unannounced | `0x5c5ebb7a42af724bb59bb72f747482b1f93f22361caa188ef1aeb0388812ffc6` | interim-1002 — not V3 |
| `6508-1` | `Proxy.announce` | ok | `0xaf0b74da45f3146a4be720b78a940f971893b918241cdbe5cdabfd6d2a0b73e5` | interim-1002 — not V3 |
| `6509-1` | `Proxy.proxy(Proxy.reject_announcement)` | ok | `0xecbfa57e4650e6a7c5833f029ab40a2a8e20687d4b3ce7e8a87cb21ed3b7c8bc` | interim-1002 — not V3 |
| `6515-1` | `Proxy.proxy_announced(Balances.transfer_keep_alive)` | Proxy.Unannounced | `0x5d29e88025c66d9f85628abcc247f3cbb05b9db80260dbe1c1dd11b0e302b954` | interim-1002 — not V3 |
| `6516-1` | `Proxy.announce` | ok | `0xd300a89628dc4e535d7ac0293a93e96cef84ec61204e1d4a23bdfd9b8dda733f` | interim-1002 — not V3 |
| `6517-1` | `Proxy.proxy_announced(Balances.transfer_keep_alive)` | Proxy.Unannounced | `0x85beb7acf65ceac4f66ab651f0019878b2fbf3d8f8962e2bd07a43f50a31f4d2` | interim-1002 — not V3 |
| `6523-1` | `Proxy.proxy_announced(Balances.transfer_keep_alive)` | ok | `0x0fe9501dec5f0627f23ae8bf566d9db5dcd24822dc6e97fb6b9769c355f9560f` | interim-1002 — not V3 |
| `6524-1` | `Vesting.vested_transfer` | ok | `0x545913689ce13245396447417c9415c34b62a8ad6b0191acbb6aa906bd85b427` | interim-1002 — not V3 |
| `6527-1` | `Proxy.proxy(Vesting.vest)` | ok | `0x486f6850c634950bda8251de8b2966a95c445bc60a3a58347523cc4972c5977b` | interim-1002 — not V3 |
| `6528-1` | `Proxy.proxy(Balances.transfer_keep_alive)` | inner:Balances.LiquidityRestrictions | `0x3261e06e97fce7012c7f7e41e838a79955636393f0af154669b5bf581d19503e` | interim-1002 — not V3 |
