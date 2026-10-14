# Airlock — on-chain proof

Every row is a real extrinsic, identified by extrinsic id (`block-index`), block hash and transaction hash, and
labelled with the chain profile it ran on (`airlock/chain_profile.py`). Rows are appended by the script that
produced them (`scripts/spike_proxy.py --proof docs/PROOF.md`, `scripts/refetch.py --proof docs/PROOF.md`), never added
by hand (a stale row may be deleted by hand); each run names the commit that produced it; its JSON is in `evidence/`.

**Profile labels.** The target is the Portaldot V3 testnet. Its RPC host `testnetv3-node.feso-apps.xyz` did not
resolve in the checks on 2026-10-14 logged in `evidence/v3-dns.txt` (at 12:44 UTC the
resolver answered `NXDOMAIN`), so these rows come from
**interim chains — not V3**:

| Profile | Chain | Runtime | Genesis hash |
|---|---|---|---|
| `interim-sdk` | upstream polkadot-sdk `substrate-node` dev chain (`polkadot-stable2606-3`), run by `scripts/interim_node.sh` | `node/268` | `0x0c4402ef4ccc34d28b16523e1cf26831716527c61a65d8e0840ab26e1636c8a2` |
| `interim-1002` | the `portaldot_dev` dev node from the Portaldot node repository (`portaldotVolunteer/Portaldot-node`), `--tmp` | `portaldot/1002` | `0x53c36c0fc46f5bb2429d701b5ad7a0577b343aa53c4964333f1922611974dd22` |

The dev genesis is deterministic, so a fresh `interim-sdk` chain has the same genesis hash; a row can be re-fetched only
from the node that recorded it (`--base-path` keeps it across restarts, and both chains run as state archives so a
receipt's events stay readable). `interim-1002` runs with `--tmp`, so its rows are a run log, not re-fetchable proof
once that node stops. Signers are dev keys (`//Alice` cold — `//Dave` on `interim-1002`, `//Bob` hot, `//Eve`
thief's payee, `//Ferdie` legitimate payee).

**Reading a run.** Steps 4 (I1), 7 (I2) and 9 are expected failures: `Proxy.Unannounced` is the pallet refusing the
call. Step 9 executes an announced payment before the delay has passed (the delay is enforced, not just stored); step 10
is the control for step 7: the same delegate and delay, without a veto, and the payment executes. Steps 11–14
(`--vesting`) send the vault a vested payout and claim it through the vault; the vesting-locked part is not counted as
at risk (checked after steps 11 and 12). Steps 13–14 check the at-risk figure against the chain itself: moving one
planck more than it out of the vault fails on the lock (`Token.Frozen`; `Balances.LiquidityRestrictions` on 1002), and
moving exactly it succeeds, after which the vault's at-risk is 0. On `interim-1002` the cold key is `//Dave` (`--cold`),
because that dev chain's `//Alice` is a staking validator with little unlocked balance left.

**Re-fetch.** Each run is then looked up again by extrinsic id alone (`scripts/refetch.py`), from a fresh process and
over the node's HTTP endpoint instead of the websocket the run used; block hash, tx hash and outcome must all match.
Both endpoints are the same local node, so this is an independent process and transport, not an independent node.

## Runs

### spike_proxy — 2026-10-14T12:40:31Z — interim — not V3 (upstream polkadot-sdk substrate-node dev chain)

Runtime `node/268` · genesis `0x0c4402ef4ccc34d28b16523e1cf26831716527c61a65d8e0840ab26e1636c8a2` · vault `5DrJnHgeiXefWZRD493NfvM7Fms454EDRCFExSvYuBiLyge4` · delay D = 5 blocks · code `d5712c3`

| # | step | call | signer | expected | outcome | extrinsic id | block hash | tx hash | profile | note |
|---|---|---|---|---|---|---|---|---|---|---|
| 1 | create vault | `Proxy.create_pure` | cold | ok | ok | `13907-2` | `0x10d50f2a98c84c697302f05278efe52dd3d433813fb778cb090753991ea6ef21` | `0x17b08bc3fbd990c020295c91a40566209774d2dec890c1c5e2016f2e28c9c2e2` | interim-sdk — not V3 |  |
| 2 | fund vault | `Balances.transfer_keep_alive` | cold | ok | ok | `13908-2` | `0x5e2fffa55d67971f54da48f95a371fb29f866aab7e5bde2a8c14a4e596cd028b` | `0xed71081647c138d9759da6f21e9dd80db0aae98d2fc8e8919e88cd075571f234` | interim-sdk — not V3 |  |
| 3 | add delayed hot key | `Proxy.proxy(Proxy.add_proxy)` | cold | ok | ok | `13909-2` | `0xa8bde4d0e1dabfe99a32f9172455728db9de9e1ff8798a267cd2b53f8fd0b70a` | `0x3c6e59964f75046a197d0bdf4c748d893c72a175eb5ed214de4542c7fbe4c0ba` | interim-sdk — not V3 | delay 5 blocks |
| 4 | hot spends unannounced | `Proxy.proxy(Balances.transfer_keep_alive)` | hot | Proxy.Unannounced | Proxy.Unannounced | `13910-2` | `0x5c9f905e4885ec4b24b376f460950cf96d909f4f7f91ec8c22c21dc64c2afda0` | `0x1b70c807cb4ffa76d006805ceb5ba7ef015ad0c3840209468a858f98ae129a3b` | interim-sdk — not V3 | I1 |
| 5 | hot announces theft | `Proxy.announce` | hot | ok | ok | `13911-2` | `0x9a30cb124d90d40d8308be4500739585e9b5416e31b8d46576070f497a489042` | `0x46f654765737335680de48a00437b111e8a5f18118da842b2bf73b3ed0331253` | interim-sdk — not V3 | `call hash 0xf4c3c3361dcfb6b8193c568bd42e6ab4d2731097b285f573e10e7d2f7e55478a` |
| 6 | cold vetoes | `Proxy.proxy(Proxy.reject_announcement)` | cold | ok | ok | `13912-2` | `0x282fb82d9319b4519f62e332967fb4120db430b9d9a55052b4c95affb3a8a48c` | `0xb632cbb5f46b26d78a4a6545eb0b544d80a85e866ebaff071ca00d6bdba7d41c` | interim-sdk — not V3 |  |
| 7 | vetoed theft after delay | `Proxy.proxy_announced(Balances.transfer_keep_alive)` | hot | Proxy.Unannounced | Proxy.Unannounced | `13918-2` | `0xfade7646c5328f5713873830c1ae409adef58d1029374922566cc877840feae3` | `0xb0fd0945235cf133574761dc03f2f1dd6f08295238335d78a12b92e15b70b816` | interim-sdk — not V3 | I2 |
| 8 | hot announces payment | `Proxy.announce` | hot | ok | ok | `13919-2` | `0xd0db0d2d32c07f5eecfea16d67f67f99bbb66cecfb290c9771b59be23fa44d5c` | `0xabd8de55c07e91c413dbb9827764bb01dcd7d93fca49c82e1204495da91e9baf` | interim-sdk — not V3 | `call hash 0x33aaaddcdcaeb538d590ead70a635c99a4d5a6adf61773ea5558f5c96291a385` |
| 9 | payment before delay | `Proxy.proxy_announced(Balances.transfer_keep_alive)` | hot | Proxy.Unannounced | Proxy.Unannounced | `13920-2` | `0x7ac1d6b30b97c92b6e96cf0512bd534bad80533a92fa0fd14e1a44034c90b168` | `0x3ee1943d599af5cb1f2eba7f3d80b166e0547feda0fef304ee2d40279ad6efeb` | interim-sdk — not V3 | delay enforced |
| 10 | payment executes after delay | `Proxy.proxy_announced(Balances.transfer_keep_alive)` | hot | ok | ok | `13926-2` | `0x884ced0a617231b716c9e27a0d02bea3119095a81099abe1db62759750828927` | `0x1a5ded8b1535303dc74db900d63e8b84691b774bc963c7acc4f5835fc566b3e4` | interim-sdk — not V3 | control for I2 |
| 11 | vested payout to vault | `Vesting.vested_transfer` | cold | ok | ok | `13927-2` | `0x581d70dcc72e3d30c1f53e345d3445dc02161c570c029d736f297054ae445fb7` | `0xac822b38484996551f169daea4f8aee5ddc2b4a205f795829185007e6f1ac117` | interim-sdk — not V3 | 20000 locked, 20.0000 UNIT/block from #13926 |
| 12 | vault claims vested | `Proxy.proxy(Vesting.vest)` | cold | ok | ok | `13930-2` | `0x3de389de8548d8a1cc021e95b731a3db91534e80f5a11dc128df3651ec6b6e9b` | `0x19df6cab994777c058a9aeb3a2bb1b48689145f361749b9cf0430dbca2991e4d` | interim-sdk — not V3 | vest through the vault |
| 13 | move at-risk + 1 planck | `Proxy.proxy(Balances.transfer_allow_death)` | cold | inner:Token.Frozen | inner:Token.Frozen | `13931-2` | `0x54c98c18d94bfcd2c2377ba63569d4d893216c15fdcfd87faf783caad34b7b4b` | `0x04e07e88df51d4a9f634d80bfd868b4b8212a64b143810e53deba0aac8611108` | interim-sdk — not V3 | at-risk 980.0000 UNIT + 1 planck |
| 14 | move exactly at-risk | `Proxy.proxy(Balances.transfer_allow_death)` | cold | ok | ok | `13932-2` | `0xcab8ef757e93923dcce44a02e037fecf7779e4799869fe65791822815a251e86` | `0x211bacfad74551de4c1d0631e14d28b4d391ba4d1f2a6c8331f757b45f0c3962` | interim-sdk — not V3 | 980000000000000 planck |

### spike_proxy — 2026-10-14T12:41:49Z — interim — not V3 (Portaldot dev node, runtime spec 1002)

Runtime `portaldot/1002` · genesis `0x53c36c0fc46f5bb2429d701b5ad7a0577b343aa53c4964333f1922611974dd22` · vault `5FYeHKThqoYya1Pqi49pcddNLxhUK9NwSZxJ1FXCvRVFYRoy` · delay D = 5 blocks · code `d5712c3`

| # | step | call | signer | expected | outcome | extrinsic id | block hash | tx hash | profile | note |
|---|---|---|---|---|---|---|---|---|---|---|
| 1 | create vault | `Proxy.anonymous` | cold | ok | ok | `6973-1` | `0xf90613edfea57f604653dbafd876b6e912537f0b4e587b24e97ab0d6effdb5ba` | `0x8d67d8839c9a2d683d6059a7308319def1d70529afe6b7575188b6b028e40e39` | interim-1002 — not V3 |  |
| 2 | fund vault | `Balances.transfer_keep_alive` | cold | ok | ok | `6974-1` | `0xb507236cdd7aff9eb21f636152002bb6aed0623ebf1c7f464c5ab09bc32554cd` | `0xba3fbdce4d1817b741c10e0cb84c533646990d7d5b03e69b6ea25d1c161c57b7` | interim-1002 — not V3 |  |
| 3 | add delayed hot key | `Proxy.proxy(Proxy.add_proxy)` | cold | ok | ok | `6975-1` | `0x0743aa84ad180ed1573fbb95b5af9b9a6c6e097027aa0621341ddc4efb2d6480` | `0x84d272ba519675fb45416d40f8e96a2ecbf39443bb3d0eddedbf9eec2a9aab3f` | interim-1002 — not V3 | delay 5 blocks |
| 4 | hot spends unannounced | `Proxy.proxy(Balances.transfer_keep_alive)` | hot | Proxy.Unannounced | Proxy.Unannounced | `6976-1` | `0x7b5b8e26bc6aa34041de6b5b6a382ee23d81e99351696c7886011497a2f8a12d` | `0x8836c688cb6c361de8759a043a00da070b2808c1d5dc3c21d5032404d55faf5b` | interim-1002 — not V3 | I1 |
| 5 | hot announces theft | `Proxy.announce` | hot | ok | ok | `6977-1` | `0x8d9f91c403c90bb965dd0f31d92f434bfbc0060a45970c5725d8a8433dd89225` | `0xdc1b388ba397b770abd7c81357ca18c7ad23af11023a1347c753e50253671dc9` | interim-1002 — not V3 | `call hash 0x5b11231612a3699c8009e8abb7b35314a73a8f28088221aa1a5d1a92bd2222fa` |
| 6 | cold vetoes | `Proxy.proxy(Proxy.reject_announcement)` | cold | ok | ok | `6978-1` | `0xb3e192d94a003ee6d03bb16743a0fd1aa03328005b067479028eb4b0114f3609` | `0x34bca803f41e3f9159f769ff2b6b835a08d543a6cd4ffd5fbd321f67b16cba41` | interim-1002 — not V3 |  |
| 7 | vetoed theft after delay | `Proxy.proxy_announced(Balances.transfer_keep_alive)` | hot | Proxy.Unannounced | Proxy.Unannounced | `6984-1` | `0x6004967a0c651a81a0cf65e74df967e382c53e2c210a41283af9c57c7e4c67b1` | `0x127e1058bece0a8ff5c09a65e38c959b6ad4ae35f4f53902b78995528148f40d` | interim-1002 — not V3 | I2 |
| 8 | hot announces payment | `Proxy.announce` | hot | ok | ok | `6985-1` | `0x082f29a90633f3ea731892212b8ea7584dc8504e1ac46a15d0ce3a81da2507e0` | `0xf67e5d237a8ec3c2783fd48b9f9933ab9db1b67fc3a6fd5d71215eb918499024` | interim-1002 — not V3 | `call hash 0xa4369adc5768a0261ef223e7fa5da14b9ca90da9f10717df38bfae9768ec2748` |
| 9 | payment before delay | `Proxy.proxy_announced(Balances.transfer_keep_alive)` | hot | Proxy.Unannounced | Proxy.Unannounced | `6986-1` | `0x1ab449aeee54d71c16d2e4724f1e74e6bdb5a30d066603057437bcab737897f9` | `0x4250576f8c5d601dc68be64dcd393e9e2276d20fb1f34edc543df6c466676e9d` | interim-1002 — not V3 | delay enforced |
| 10 | payment executes after delay | `Proxy.proxy_announced(Balances.transfer_keep_alive)` | hot | ok | ok | `6992-1` | `0x4e414816da9412c88529943fd4572dbe12dfc13af1c1c8c70bc1003aabcab0ba` | `0xd3c3e2f421e1a5c3acf5d6ee04c5469d3344078fc532695c3e4a0522c251ba4b` | interim-1002 — not V3 | control for I2 |
| 11 | vested payout to vault | `Vesting.vested_transfer` | cold | ok | ok | `6993-1` | `0x4a37fd74165a39db9dce2ca35eba124b60059aec4d094d19bf48c6948081730b` | `0x3026ea535bedb6c3e758eab67bcb02267e3aa950679e14b2c12ba9270657c692` | interim-1002 — not V3 | 1000 locked, 1.0000 POT/block from #6992 |
| 12 | vault claims vested | `Proxy.proxy(Vesting.vest)` | cold | ok | ok | `6996-1` | `0x64b2d1c8115be4e14eefb81cfe0170b668858cebf6f8306e8895d3db84aa1ffc` | `0x364df8a0f90ff0203eb743d0e6ee15fde9a63ab10f10c11cff5e5b2e8b08b408` | interim-1002 — not V3 | vest through the vault |
| 13 | move at-risk + 1 planck | `Proxy.proxy(Balances.transfer)` | cold | inner:Balances.LiquidityRestrictions | inner:Balances.LiquidityRestrictions | `6997-1` | `0xb5f8c60ace0f6b84c4686afba3753f762cb11341b26b5b2d2723e2e4111fe276` | `0xc73dfbfb5215adb05debd51dce310295ec30f7eff85a55c4f62e30cea1e6ecfc` | interim-1002 — not V3 | at-risk 902.0200 POT + 1 planck |
| 14 | move exactly at-risk | `Proxy.proxy(Balances.transfer)` | cold | ok | ok | `6998-1` | `0x47d9feade73158228a79458fc48cfd392033c7cedf19573fd98dda814577fab9` | `0x42e8eb4597fa2978fd4baec9467afadc62a6d43ea711aa75596327bfc1b5dd4f` | interim-1002 — not V3 | 90202000000000000 planck |

### re-fetch — 2026-10-14T12:44:30Z — the 2026-10-14T12:40:31Z interim-sdk run, by extrinsic id

Fresh process over http (`http://127.0.0.1:29944`; the run itself was sent over ws) at head #13986 · `evidence/refetch-interim-sdk-2026-10-14T124430Z.json` · every row: same block hash, tx hash and outcome as recorded.

| extrinsic id | call | outcome | tx hash | profile |
|---|---|---|---|---|
| `13907-2` | `Proxy.create_pure` | ok | `0x17b08bc3fbd990c020295c91a40566209774d2dec890c1c5e2016f2e28c9c2e2` | interim-sdk — not V3 |
| `13908-2` | `Balances.transfer_keep_alive` | ok | `0xed71081647c138d9759da6f21e9dd80db0aae98d2fc8e8919e88cd075571f234` | interim-sdk — not V3 |
| `13909-2` | `Proxy.proxy(Proxy.add_proxy)` | ok | `0x3c6e59964f75046a197d0bdf4c748d893c72a175eb5ed214de4542c7fbe4c0ba` | interim-sdk — not V3 |
| `13910-2` | `Proxy.proxy(Balances.transfer_keep_alive)` | Proxy.Unannounced | `0x1b70c807cb4ffa76d006805ceb5ba7ef015ad0c3840209468a858f98ae129a3b` | interim-sdk — not V3 |
| `13911-2` | `Proxy.announce` | ok | `0x46f654765737335680de48a00437b111e8a5f18118da842b2bf73b3ed0331253` | interim-sdk — not V3 |
| `13912-2` | `Proxy.proxy(Proxy.reject_announcement)` | ok | `0xb632cbb5f46b26d78a4a6545eb0b544d80a85e866ebaff071ca00d6bdba7d41c` | interim-sdk — not V3 |
| `13918-2` | `Proxy.proxy_announced(Balances.transfer_keep_alive)` | Proxy.Unannounced | `0xb0fd0945235cf133574761dc03f2f1dd6f08295238335d78a12b92e15b70b816` | interim-sdk — not V3 |
| `13919-2` | `Proxy.announce` | ok | `0xabd8de55c07e91c413dbb9827764bb01dcd7d93fca49c82e1204495da91e9baf` | interim-sdk — not V3 |
| `13920-2` | `Proxy.proxy_announced(Balances.transfer_keep_alive)` | Proxy.Unannounced | `0x3ee1943d599af5cb1f2eba7f3d80b166e0547feda0fef304ee2d40279ad6efeb` | interim-sdk — not V3 |
| `13926-2` | `Proxy.proxy_announced(Balances.transfer_keep_alive)` | ok | `0x1a5ded8b1535303dc74db900d63e8b84691b774bc963c7acc4f5835fc566b3e4` | interim-sdk — not V3 |
| `13927-2` | `Vesting.vested_transfer` | ok | `0xac822b38484996551f169daea4f8aee5ddc2b4a205f795829185007e6f1ac117` | interim-sdk — not V3 |
| `13930-2` | `Proxy.proxy(Vesting.vest)` | ok | `0x19df6cab994777c058a9aeb3a2bb1b48689145f361749b9cf0430dbca2991e4d` | interim-sdk — not V3 |
| `13931-2` | `Proxy.proxy(Balances.transfer_allow_death)` | inner:Token.Frozen | `0x04e07e88df51d4a9f634d80bfd868b4b8212a64b143810e53deba0aac8611108` | interim-sdk — not V3 |
| `13932-2` | `Proxy.proxy(Balances.transfer_allow_death)` | ok | `0x211bacfad74551de4c1d0631e14d28b4d391ba4d1f2a6c8331f757b45f0c3962` | interim-sdk — not V3 |

### re-fetch — 2026-10-14T12:44:32Z — the 2026-10-14T12:41:49Z interim-1002 run, by extrinsic id

Fresh process over http (`http://127.0.0.1:19933`; the run itself was sent over ws) at head #6999 · `evidence/refetch-interim-1002-2026-10-14T124432Z.json` · every row: same block hash, tx hash and outcome as recorded.

| extrinsic id | call | outcome | tx hash | profile |
|---|---|---|---|---|
| `6973-1` | `Proxy.anonymous` | ok | `0x8d67d8839c9a2d683d6059a7308319def1d70529afe6b7575188b6b028e40e39` | interim-1002 — not V3 |
| `6974-1` | `Balances.transfer_keep_alive` | ok | `0xba3fbdce4d1817b741c10e0cb84c533646990d7d5b03e69b6ea25d1c161c57b7` | interim-1002 — not V3 |
| `6975-1` | `Proxy.proxy(Proxy.add_proxy)` | ok | `0x84d272ba519675fb45416d40f8e96a2ecbf39443bb3d0eddedbf9eec2a9aab3f` | interim-1002 — not V3 |
| `6976-1` | `Proxy.proxy(Balances.transfer_keep_alive)` | Proxy.Unannounced | `0x8836c688cb6c361de8759a043a00da070b2808c1d5dc3c21d5032404d55faf5b` | interim-1002 — not V3 |
| `6977-1` | `Proxy.announce` | ok | `0xdc1b388ba397b770abd7c81357ca18c7ad23af11023a1347c753e50253671dc9` | interim-1002 — not V3 |
| `6978-1` | `Proxy.proxy(Proxy.reject_announcement)` | ok | `0x34bca803f41e3f9159f769ff2b6b835a08d543a6cd4ffd5fbd321f67b16cba41` | interim-1002 — not V3 |
| `6984-1` | `Proxy.proxy_announced(Balances.transfer_keep_alive)` | Proxy.Unannounced | `0x127e1058bece0a8ff5c09a65e38c959b6ad4ae35f4f53902b78995528148f40d` | interim-1002 — not V3 |
| `6985-1` | `Proxy.announce` | ok | `0xf67e5d237a8ec3c2783fd48b9f9933ab9db1b67fc3a6fd5d71215eb918499024` | interim-1002 — not V3 |
| `6986-1` | `Proxy.proxy_announced(Balances.transfer_keep_alive)` | Proxy.Unannounced | `0x4250576f8c5d601dc68be64dcd393e9e2276d20fb1f34edc543df6c466676e9d` | interim-1002 — not V3 |
| `6992-1` | `Proxy.proxy_announced(Balances.transfer_keep_alive)` | ok | `0xd3c3e2f421e1a5c3acf5d6ee04c5469d3344078fc532695c3e4a0522c251ba4b` | interim-1002 — not V3 |
| `6993-1` | `Vesting.vested_transfer` | ok | `0x3026ea535bedb6c3e758eab67bcb02267e3aa950679e14b2c12ba9270657c692` | interim-1002 — not V3 |
| `6996-1` | `Proxy.proxy(Vesting.vest)` | ok | `0x364df8a0f90ff0203eb743d0e6ee15fde9a63ab10f10c11cff5e5b2e8b08b408` | interim-1002 — not V3 |
| `6997-1` | `Proxy.proxy(Balances.transfer)` | inner:Balances.LiquidityRestrictions | `0xc73dfbfb5215adb05debd51dce310295ec30f7eff85a55c4f62e30cea1e6ecfc` | interim-1002 — not V3 |
| `6998-1` | `Proxy.proxy(Balances.transfer)` | ok | `0x42e8eb4597fa2978fd4baec9467afadc62a6d43ea711aa75596327bfc1b5dd4f` | interim-1002 — not V3 |
