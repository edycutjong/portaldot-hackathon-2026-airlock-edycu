# Airlock — on-chain proof

Every row is a real extrinsic, identified by extrinsic id (`block-index`), block hash and transaction hash, and
labelled with the chain profile it ran on (`airlock/chain_profile.py`). Rows are appended by the script that
produced them (`scripts/spike_proxy.py`, `scripts/seed.py` and `scripts/refetch.py`, each with `--proof docs/PROOF.md`),
never added by hand (a stale row may be deleted by hand); each spike and seed run names the commit that produced it,
each end-to-end log (`evidence/e2e-*.log`) names it on its first line, and every run's JSON or log is in `evidence/`.

**Profile labels.** The target is the Portaldot V3 testnet. Its RPC host `testnetv3-node.feso-apps.xyz` did not
resolve in the checks on 2026-10-14 logged in `evidence/v3-dns.txt` (at 12:44 UTC the
resolver answered `NXDOMAIN`), so these rows come from
**interim chains — not V3**:

| Profile | Chain | Runtime | Genesis hash |
|---|---|---|---|
| `interim-sdk` | upstream polkadot-sdk `substrate-node` dev chain (`polkadot-stable2606-3`), run by `scripts/interim_node.sh` | `node/268` | `0x0c4402ef4ccc34d28b16523e1cf26831716527c61a65d8e0840ab26e1636c8a2` |
| `interim-1002` | the `portaldot_dev` dev node from the Portaldot node repository (`portaldotVolunteer/Portaldot-node`), `--tmp` | `portaldot/1002` | `0x53c36c0fc46f5bb2429d701b5ad7a0577b343aa53c4964333f1922611974dd22` |

The dev genesis is deterministic (`scripts/interim_node.sh --fresh` restarts the chain under the same genesis hash); a row can be re-fetched only
from the node that recorded it (`--base-path` keeps it across restarts, and both chains run as state archives so a
receipt's events stay readable). `interim-1002` runs with `--tmp`, so its rows are a run log, not re-fetchable proof
once that node stops. Signers are public dev keys: in the spike runs `//Alice` cold (`//Dave` on `interim-1002`),
`//Bob` hot, `//Eve` thief's payee, `//Ferdie` legitimate payee; in the seed and end-to-end runs the demo accounts
`//AirlockDemo//cold`, `//AirlockDemo//hot`, `//AirlockDemo//payee-rent`, … (addresses in `fixtures/accounts.json`),
funded by `//Alice` (`//Dave` on `interim-1002`).

**Reading a run.** Steps 4 (I1), 7 (I2) and 9 are expected failures: `Proxy.Unannounced` is the pallet refusing the
call. Step 9 executes an announced payment before the delay has passed (the delay is enforced, not just stored); step 10
is the control for step 7: the same delegate and delay, without a veto, and the payment executes. Steps 11–14
(`--vesting`) send the vault a vested payout and claim it through the vault; the vesting-locked part is not counted as
at risk (checked after steps 11 and 12). Steps 13–14 check the at-risk figure against the chain itself: moving one
planck more than it out of the vault fails on the lock (`Token.Frozen`; `Balances.LiquidityRestrictions` on 1002), and
moving exactly it succeeds, after which the vault's at-risk is 0. On `interim-1002` the cold key is `//Dave` (`--cold`),
because that dev chain's `//Alice` is a staking validator with little unlocked balance left.

**Reading a seed run** (`scripts/seed.py`, inputs in `fixtures/`). It builds the demo vault through the CLI's own
operations (`airlock/ops.py`) with D = 60 s in blocks (20 on `interim-sdk`, 10 on `interim-1002`): 5 legitimate spends
announced and executed after the delay, 2 drains announced and vetoed, then the first drain executed after its delay
(expected `Proxy.Unannounced`), one announcement withdrawn by the hot key, and a panic on a throwaway vault after which
the hot key's announcement fails `Proxy.NotProxy`. The header line names the checks from `fixtures/expected.json` that
held at the end (vault total = fund − spends exactly, payees received exactly their spends, attackers hold 0, nothing
pending, delegates as expected). The first `fund …` rows appear only when an account was short.

**Reading an end-to-end run** (`scripts/e2e_evidence.sh`, logs `evidence/e2e-*.log`). The thief's three rows come from
`scripts/thief.py`, which imports no Airlock code: announce a `transfer_keep_alive` sweep of the
demo vault's free balance minus locks and one existential deposit (the watcher's at-risk figure can be one existential
deposit higher: on `interim-1002` the vault's reserved deposit keeps it alive, so its whole free balance can move), then
(after the watcher's NOT YOURS alert and the veto) execute it after the delay, and try `proxy.proxy` with no
announcement. Both fail `Proxy.Unannounced`. The veto row is the alert's own `airlock veto …` line, run as printed; it
has no recorded row to compare with, so it is listed as found by id.

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

### seed — 2026-10-14T15:15:39Z — interim — not V3 (upstream polkadot-sdk substrate-node dev chain)

Runtime `node/268` · genesis `0x0c4402ef4ccc34d28b16523e1cf26831716527c61a65d8e0840ab26e1636c8a2` · endpoint `ws://127.0.0.1:29944` · demo vault `5DMivfmqPLLaJHrkckLMK3dnPpvqUuuCdwo4AnsbcqF4czTG` · throwaway vault `5CKL7fBisw3MdLYNFskgBJv782odB49edyoCQhp41A8nm8wL` · delay D = 20 blocks · code `1f13091` · checks (fixtures/expected.json): all hold

| # | step | call | signer | expected | outcome | extrinsic id | block hash | tx hash | profile |
|---|---|---|---|---|---|---|---|---|---|
| 1 | fund cold | `Balances.transfer_keep_alive` | funder | ok | ok | `16953-2` | `0x2a57ade3bc30b81d6027c47cfb2c03e11e553bdd7731c082f76eb53363f96ba7` | `0x8e8181c4db10e843383a92e7228b615fe0caf186506d417803432209bae66b55` | interim-sdk — not V3 |
| 2 | fund hot | `Balances.transfer_keep_alive` | funder | ok | ok | `16954-2` | `0x5621b62f77ece32f93de38bd31318c2127209121a8d8d12d326e3eb611631b39` | `0x48ec85ca55f5bf0df67479b7ec554650f98be0faee3681fddcbdc80c365e904f` | interim-sdk — not V3 |
| 3 | create vault | `Proxy.create_pure` | cold | ok | ok | `16955-2` | `0x4c20693aa794a3aa7637d92ccac296f0041309d862b1385d0b3c7f9a6db5b4f8` | `0x9b6913236f255aef3ee9fbe72324ce56842e981e31701a64aa5ff5f788442bde` | interim-sdk — not V3 |
| 4 | fund vault | `Balances.transfer_keep_alive` | cold | ok | ok | `16956-2` | `0xa2c49c2679e7811ac5055444b8d171a2ba5e9d354a87934bbfd21cab2a72f134` | `0xf79bbb5d6044ac1a8bdeed9f6fc2ec8e424b2c5700b1dd434e44616fa0000670` | interim-sdk — not V3 |
| 5 | add delayed hot key | `Proxy.proxy(Proxy.add_proxy)` | cold | ok | ok | `16957-2` | `0xbc14900ca9b417537c2fd536927dae99ad7d6f48a04b8bb6cf5eb9183c91fb9b` | `0x3275c711990a2e5d0b537c2c2052d7ddc5ee0df3b01077363c99a63ea74d8993` | interim-sdk — not V3 |
| 6 | announce rent 12.5 | `Proxy.announce` | hot | ok | ok | `16958-2` | `0x9cb26757184767f837738a281470eed3bcd670375aec7fbdd979203199ff2d3e` | `0xfd62486a67d4ef224a83ad1e710e2bb949bbdd37ea2dbb2ad25edeebf6b74c5a` | interim-sdk — not V3 |
| 7 | announce coffee 3.2 | `Proxy.announce` | hot | ok | ok | `16959-2` | `0xa252c328a1d845c0bb76046684ad52277453be9bceb9df45ec575a8d7c6c83b2` | `0xe9046d62bdc49f428c522ea481bd40a4380d918b006825043c48a651ec9e1b92` | interim-sdk — not V3 |
| 8 | announce internet 4.75 | `Proxy.announce` | hot | ok | ok | `16960-2` | `0xd0fae1127bbbe609402d9c624ce62d5aecbdf8674a25376c25fccfb2c15ffb89` | `0x0790b73c83b1af26a7ff49d31d6ae3f500667b0caf228327ff94bcab1b71ba07` | interim-sdk — not V3 |
| 9 | announce coffee 3.4 | `Proxy.announce` | hot | ok | ok | `16961-2` | `0x42d46cf54f831fb2bedf00f3c71148da2bddf9b7f583c02ef9d2d87497355268` | `0x0fef93342110b5c13efa854dd94ce6d761b376ea6c0e463f4b64d43dd5b1c55f` | interim-sdk — not V3 |
| 10 | announce books 8 | `Proxy.announce` | hot | ok | ok | `16962-2` | `0x1deff685e2b7526754e9f8a68357c25bd649ad00aec6c9e6058a64b4775b4e23` | `0x03f6876e61fbbb2491fa1ab2b3050445fe07ab050208867b656292f603dc361b` | interim-sdk — not V3 |
| 11 | execute rent 12.5 | `Proxy.proxy_announced(Balances.transfer_keep_alive)` | hot | ok | ok | `16979-2` | `0x72085d1c1248d1b61c2854b8003f63938d9da41cdfc14be04ae7edb4cc510d8d` | `0xe7d56af21d0114254f914d87d2d226467dfaf629c1605c32232d7eff6d898d6e` | interim-sdk — not V3 |
| 12 | execute coffee 3.2 | `Proxy.proxy_announced(Balances.transfer_keep_alive)` | hot | ok | ok | `16980-2` | `0x5be7374f927e30f89f60e46baf2cf8ff1aad03698c7e4db3826eef65c31799da` | `0x3103a4ec5fe0cc3c9d4652656433c678f0bbe5d37bb47dca62b61e4000b93ede` | interim-sdk — not V3 |
| 13 | execute internet 4.75 | `Proxy.proxy_announced(Balances.transfer_keep_alive)` | hot | ok | ok | `16981-2` | `0xd3426344a08d7c7776c33518cc7cb6c514f5f651f2f05cd0490ea62c658e9871` | `0x453db8a6b1879f2fc72ea08c3f90d09b647b1aece281ad81b6e72f1878dcb563` | interim-sdk — not V3 |
| 14 | execute coffee 3.4 | `Proxy.proxy_announced(Balances.transfer_keep_alive)` | hot | ok | ok | `16982-2` | `0xef68bebf4144e5758a5d0edd6a36348127539f79d6c3daa61ed7e5682c5df469` | `0x5b5d324b22fe1b604a31a295f4bfa5c7c0f651788a9713f55d23d03644b78980` | interim-sdk — not V3 |
| 15 | execute books 8 | `Proxy.proxy_announced(Balances.transfer_keep_alive)` | hot | ok | ok | `16983-2` | `0x02b705a63e76abf533b7759b22ec3f66eb5526ae95e64d927f06b3d8c5577f76` | `0xca8bc9784a59936ebcdbc6765d330aa689edba8d3898be4993f01fc55fba1243` | interim-sdk — not V3 |
| 16 | drain 1 announce → attacker-1 | `Proxy.announce` | hot | ok | ok | `16984-2` | `0x00335a13bf0a26772e036800efd82396f7a17ab22518cc1a4322c206acd8f74a` | `0x4e0b22982abe8f7c8d1eeb7f0cb0038be2e4818fed334be380a167df309a27a8` | interim-sdk — not V3 |
| 17 | drain 1 veto | `Proxy.proxy(Proxy.reject_announcement)` | cold | ok | ok | `16985-2` | `0xde85a873089eb82312719440109a4af2ba08b2ca1bc0caf7cad37392253162cc` | `0x7643fea1ff1ee69bc6b3926ae4311ce17ee7e3457ced37981a1f85d7091794ac` | interim-sdk — not V3 |
| 18 | drain 2 announce → attacker-2 | `Proxy.announce` | hot | ok | ok | `16986-2` | `0x0ea06fe1c66d5fcbb400c9024c941b66a2ce911976548164d27a5bad0d492a10` | `0xba230cbd13081e6888ed19f2f1b3c12c3fe17a0901e46f06bb3cbf103972b9f3` | interim-sdk — not V3 |
| 19 | drain 2 veto | `Proxy.proxy(Proxy.reject_announcement)` | cold | ok | ok | `16987-2` | `0x991b195c1946816df7d3f19e5d5d371ef467e0dbe41d42663d7e170ef963b326` | `0x986ba03c64fa449b27084f7b03f46037be824f9c58dd1f15fa1676d77634d31e` | interim-sdk — not V3 |
| 20 | drain 1 execute after veto | `Proxy.proxy_announced(Balances.transfer_keep_alive)` | hot | Proxy.Unannounced | Proxy.Unannounced | `17007-2` | `0xcaaac611ec7fbc70f8e09cd09140e888abc475bb9803ca10600ce7ce50fffce7` | `0xc46a09f4a47823d8b9f918666ddfb32496657a3b8940757805fbbd9916ccf2ad` | interim-sdk — not V3 |
| 21 | announce (to withdraw) | `Proxy.announce` | hot | ok | ok | `17008-2` | `0x740f59754bfd77662e5916f346bb9af32312b9bb83dfa8ba0b56aeb9617cb842` | `0xab3e2e4649c1b1fe1e0b2db55469a38217b3ae1400020c29c132345e235e7ad7` | interim-sdk — not V3 |
| 22 | withdraw own announcement | `Proxy.remove_announcement` | hot | ok | ok | `17009-2` | `0x77e2ffdc009acdd4e2cf9b7e6b900cb039e106ba5e293e1834ba776eb24f4180` | `0x62570bc962a97feeef7c16bb9b415fbf4253b2af3f1ccf44b46c69bae096ba2f` | interim-sdk — not V3 |
| 23 | throwaway: create vault | `Proxy.create_pure` | cold | ok | ok | `17010-2` | `0x3807701ff14147590186921898b7f5e3af4fec1b072e5fcaa219541b51deadad` | `0x54b3f5990de51c50da248c5e56f47107f0b7cedac7f63d348eb00852d90b3856` | interim-sdk — not V3 |
| 24 | throwaway: fund vault | `Balances.transfer_keep_alive` | cold | ok | ok | `17011-2` | `0xaa07bde90765945e48bb406c8ea2ecf3e7e52bd357682ebacf0165a42de2c57f` | `0xdb9910d99c2961c22fc28d393c50aa2c6dacb3f1b0ae44c8551c6327c0c23c93` | interim-sdk — not V3 |
| 25 | throwaway: add delayed hot key | `Proxy.proxy(Proxy.add_proxy)` | cold | ok | ok | `17012-2` | `0x99a0a4edb94da57d1adfddd1af0456a61a35af31c3aa0e5886875163a0ef97d3` | `0xcdf64540f0f6e6a42ad8f93d3991626278bb231c34b3e584f1b3d438f991e46e` | interim-sdk — not V3 |
| 26 | throwaway: panic | `Proxy.proxy(Proxy.remove_proxy)` | cold | ok | ok | `17013-2` | `0x3e627c47752ffa6b1ceef3c7b2259f60bbf59a3a1a3da1814ca9cb2621b3160b` | `0x477475b90c31ae6a26fb9abd81b237dbb564e838db93b63e992f24b14479eb1e` | interim-sdk — not V3 |
| 27 | throwaway: hot announces after panic | `Proxy.announce` | hot | Proxy.NotProxy | Proxy.NotProxy | `17014-2` | `0x9ce5e93fe6a2e5104bb7e1aa3ae7f341ccbf83fe66a16489f1ca40c03e52d849` | `0x5708783476a98f9bf12dc3f5ebf158238344ede1278b7e88de60c06fae706553` | interim-sdk — not V3 |

### seed — 2026-10-14T15:15:31Z — interim — not V3 (Portaldot dev node, runtime spec 1002)

Runtime `portaldot/1002` · genesis `0x53c36c0fc46f5bb2429d701b5ad7a0577b343aa53c4964333f1922611974dd22` · endpoint `ws://127.0.0.1:19944` · demo vault `5EC8isS2KprLKrekHS9tXATwLYuzaVYcZJjDjrjGnX42y7Lj` · throwaway vault `5EZFr7ABEbjJY61JPEZsisPXJPHsnVs9tWZmXXLAnVwhg1eN` · delay D = 10 blocks · code `1f13091` · checks (fixtures/expected.json): all hold

| # | step | call | signer | expected | outcome | extrinsic id | block hash | tx hash | profile |
|---|---|---|---|---|---|---|---|---|---|
| 1 | fund cold | `Balances.transfer_keep_alive` | funder | ok | ok | `8510-1` | `0x045b15620cf9c6eaefeb6303e68f1e30b4a7d4851f343a50e58eb8fb18598a45` | `0xcb5f9381e8ebe5e0326a4a2642fe97f3d714a35f88695820b56c28cd2f9bacec` | interim-1002 — not V3 |
| 2 | fund hot | `Balances.transfer_keep_alive` | funder | ok | ok | `8511-1` | `0x93883d5eaedac0b753dcd4aa06a5ecf0b1f0b90e9aa4694fed87b1388285f451` | `0xb723342f2b2c239d121c6dbc6ad9c495a884e6bba8e1d1fa0243c3c96468b4e3` | interim-1002 — not V3 |
| 3 | fund payee-rent | `Balances.transfer_keep_alive` | funder | ok | ok | `8512-1` | `0x3fa644f746c0f1b94aec1b2c60919d12f689a5242b0e4e5e432840d3a387c13e` | `0x6b5bdbd1ef958e5778d95f23d830172e08faee2559d646a1b63b51b24ee3cb3d` | interim-1002 — not V3 |
| 4 | fund payee-coffee | `Balances.transfer_keep_alive` | funder | ok | ok | `8513-1` | `0xbfa04b7da6e6d6264b719d57e3b7391fcd57e2b817423d2bc36cf8750371709e` | `0x3d106f17515abddaf30ec03a8d43b1a538a4bb77c2a47ee9fc62a2c9b30cc9d5` | interim-1002 — not V3 |
| 5 | create vault | `Proxy.anonymous` | cold | ok | ok | `8514-1` | `0x64d3d76a186c156459baf7681ff97a6a8173ebcf2430c7ed78ffd5440d4368cc` | `0x9c540fec1825c07dbe393e5e4d81511a5dc396a01d33e71ae3ceb86cd50c42d9` | interim-1002 — not V3 |
| 6 | fund vault | `Balances.transfer_keep_alive` | cold | ok | ok | `8515-1` | `0x975d9c20a08f8823e1322c551fb7fca47e0b8c9938911ca968ef933ff74123f5` | `0x9125c36c1f5260018fe5a4361c52df13ac6e1ee5deb673d97a2ec331a56763b7` | interim-1002 — not V3 |
| 7 | add delayed hot key | `Proxy.proxy(Proxy.add_proxy)` | cold | ok | ok | `8516-1` | `0x6e770dfb09b6cdecf06dd8954c4f07ab340ba31bb84730c26b9e4d7bc5f54a0b` | `0x662e7df9f7fa0195c4581d62bc088a10756e6c9e8c7cff4d4f81f772758e5b13` | interim-1002 — not V3 |
| 8 | announce rent 12.5 | `Proxy.announce` | hot | ok | ok | `8517-1` | `0x40e8303695bf0b19c934c5ccc9cee60a52304464386e8c7b2dbfc8830868c8fe` | `0xeaf639aaa279fb4f95e07eedc674cbf6d5be2e1f66ddd3a48f0a5f3af52462f1` | interim-1002 — not V3 |
| 9 | announce coffee 3.2 | `Proxy.announce` | hot | ok | ok | `8518-1` | `0x3a85b67f51e8cee5defbf4cc10c4afbf42a09744248a5f8bdef1a0eb04ab0b3f` | `0x469886899a2c5d09fb6e5e2616fc059212c1659a2e2b7e6e391872fc872765e0` | interim-1002 — not V3 |
| 10 | announce internet 4.75 | `Proxy.announce` | hot | ok | ok | `8519-1` | `0xceff2267c310b88ad861c4fd024b6ea3240899d2467c0425e980dbdb5d4f6ab2` | `0x69fb96bb9a4b01bca82ea4e39ab8236e736ebc4b55c0fa8cb022b5371d8bab20` | interim-1002 — not V3 |
| 11 | announce coffee 3.4 | `Proxy.announce` | hot | ok | ok | `8520-1` | `0x22bef4e250a9ccc4d984e2172817e06b625099639d65598f29f740e9ccc76453` | `0x0a4a5f313571c633198987b33cc21441fdf6a438716ecb2fce8726e8ac14cae1` | interim-1002 — not V3 |
| 12 | announce books 8 | `Proxy.announce` | hot | ok | ok | `8521-1` | `0x76cec0f71cfd6a6ce983149a2882adac05b3c26a772e46173971523a396d0808` | `0x36ca9900758a1917dad06ed273b74cfcfc2f15a0aac6fa0d1fafe1da98ab2ed7` | interim-1002 — not V3 |
| 13 | execute rent 12.5 | `Proxy.proxy_announced(Balances.transfer_keep_alive)` | hot | ok | ok | `8528-1` | `0xc8a05a15b89df22bb37cdf9a8e1d622d7a70a7a44ca2d329eb0b183449abb970` | `0xeb86309ec52c000de299ad24c37e904ea9069e86cb52c94d2b3cd649f6b83593` | interim-1002 — not V3 |
| 14 | execute coffee 3.2 | `Proxy.proxy_announced(Balances.transfer_keep_alive)` | hot | ok | ok | `8529-1` | `0xa44733a436d6d95bed78a217ba04799ffc3bb780f05b13d7c8d5d3ff88b48d45` | `0x6a577e93aaeabe9b40ca40bdd4bf98e47875ff66d424ff33b9f08f5a4c64bf16` | interim-1002 — not V3 |
| 15 | execute internet 4.75 | `Proxy.proxy_announced(Balances.transfer_keep_alive)` | hot | ok | ok | `8530-1` | `0xf51638d413b973718b1091e9d2c59e71f9aaf3b1424ef801a508f2ef7c2e4625` | `0x868407305be566cb8515d4fa8df9bf2c4120a06ba5b750158f339eb180d2b2d8` | interim-1002 — not V3 |
| 16 | execute coffee 3.4 | `Proxy.proxy_announced(Balances.transfer_keep_alive)` | hot | ok | ok | `8531-1` | `0x6b83b8341a32eeff64b70f0278ce75faf774dcf7b5bc639a76ce78e16d23a826` | `0xd2cfb3138c7597326f6c14d46153caba96848c8bc46e83d6632c2189ca6c7991` | interim-1002 — not V3 |
| 17 | execute books 8 | `Proxy.proxy_announced(Balances.transfer_keep_alive)` | hot | ok | ok | `8532-1` | `0x482bebe3474b68ac7153ad33ab1cfa65ed620b2be61acb8d3c5555ba447a7088` | `0xd71515bab0a6ac1acda50c1b2ac00c7555c16ed0f091f184ba3a2d7220868983` | interim-1002 — not V3 |
| 18 | drain 1 announce → attacker-1 | `Proxy.announce` | hot | ok | ok | `8533-1` | `0x32d9de54e9e6f4e34c36a20e9324e832e13a1ca76917deb2b7a8e94bdd7faa48` | `0x8eed4c0849bf5b068481c19aa13392502bd79a63561d197ed446915755751822` | interim-1002 — not V3 |
| 19 | drain 1 veto | `Proxy.proxy(Proxy.reject_announcement)` | cold | ok | ok | `8534-1` | `0x3964307d16375f6629ba3cf292c67c9650ebd10eaf3d6f3398e47c7205f74f3a` | `0x6db5ec532781c0de5c28f48030f8eb9a7bac5161a162a37feca460993b7d8974` | interim-1002 — not V3 |
| 20 | drain 2 announce → attacker-2 | `Proxy.announce` | hot | ok | ok | `8535-1` | `0xae966e980ec14fb4efc9ff2b72d51a6d6909e49b513daef9cb4bfa269123e699` | `0x18e83ffa8371d546c886c6f3ffd5af6df5ceb938b7c221ed17f58f3b115f9fee` | interim-1002 — not V3 |
| 21 | drain 2 veto | `Proxy.proxy(Proxy.reject_announcement)` | cold | ok | ok | `8536-1` | `0xe4001241d70322d89c6503817f965e8d6d9003003ac7320b0a4c34d6badb67b7` | `0x49c3387c08477b2fba5144751b74bca9ac75142002f926ee52e4d60720a9ebcc` | interim-1002 — not V3 |
| 22 | drain 1 execute after veto | `Proxy.proxy_announced(Balances.transfer_keep_alive)` | hot | Proxy.Unannounced | Proxy.Unannounced | `8546-1` | `0x3b4fbaef59817acb0a2f9dd1d7694aab452f350c4da94a749315f944dadc3f24` | `0x8799fd07612d01c8875cb880dd47bd841b5948b0fe7b4b92c2e5a62a5d758d98` | interim-1002 — not V3 |
| 23 | announce (to withdraw) | `Proxy.announce` | hot | ok | ok | `8547-1` | `0x0732275d72d268e8d2efb52dd28fa4f25ed84c3e52f5688da9c999af3a2ca7cc` | `0x90d0b3f14d16f0c8486f674eef9e062bd6cc718cb70ceca224b3d09108ad1c63` | interim-1002 — not V3 |
| 24 | withdraw own announcement | `Proxy.remove_announcement` | hot | ok | ok | `8548-1` | `0x268846cddcc7e853d0ffb45d48f2e46dbf47c54536065a33657bfe962cf25ade` | `0x073d67ee36a785576430e7ad43f3cafeb7d899c93a9421bafb40c63d8024b521` | interim-1002 — not V3 |
| 25 | throwaway: create vault | `Proxy.anonymous` | cold | ok | ok | `8549-1` | `0x9bd306c4ef346e9020ff1fe49153043274647451b5af95e89327e527efb56c93` | `0x72f85658ca0b7d58094223d50f5c7477753b7dddc67da8f8b9c61ca6a510a286` | interim-1002 — not V3 |
| 26 | throwaway: fund vault | `Balances.transfer_keep_alive` | cold | ok | ok | `8550-1` | `0x8159d5384d606e77bb57ba069d68623c97e9b41876e701da2cb4aa8b3c888789` | `0x6c8cd8fcfe21989457c9c5693deae98be0ed9e39982d52467ffce19d9438d0f7` | interim-1002 — not V3 |
| 27 | throwaway: add delayed hot key | `Proxy.proxy(Proxy.add_proxy)` | cold | ok | ok | `8551-1` | `0x276b59ed9723b18b4c8d0e22d57e1d7ead1fc064a9f720a95f2cf0a4e9c33967` | `0x9e29f39a874b73c52a5a2061e4526c7d1e9288f761cc125484b12b2b86695313` | interim-1002 — not V3 |
| 28 | throwaway: panic | `Proxy.proxy(Proxy.remove_proxy)` | cold | ok | ok | `8552-1` | `0x6ee85f87a101156407a09dacd188f76a8f68da115ce42eb2dec0bfbe88b1c32e` | `0x27db1c2dc9be8772327ad62345d2d7008fb8b97edc37697c7296833377a6c6dc` | interim-1002 — not V3 |
| 29 | throwaway: hot announces after panic | `Proxy.announce` | hot | Proxy.NotProxy | Proxy.NotProxy | `8553-1` | `0x83b56a605eb6ddcbd33d848c92bdcd7a5bef2666f235121a477850b24d0f19f7` | `0x6c8a81cde997420ed6c19031064452c16a6b43528e8d2195ac02f10cf00eb281` | interim-1002 — not V3 |

### re-fetch — 2026-10-14T15:25:49Z — the 2026-10-14T15:15:39Z interim-sdk run, by extrinsic id

Fresh process over http (`http://127.0.0.1:29944`; the run itself was sent over ws) at head #17155 · `evidence/refetch-interim-sdk-2026-10-14T152549Z.json` · every row: same block hash, tx hash and outcome as recorded.

| extrinsic id | call | outcome | tx hash | profile |
|---|---|---|---|---|
| `16953-2` | `Balances.transfer_keep_alive` | ok | `0x8e8181c4db10e843383a92e7228b615fe0caf186506d417803432209bae66b55` | interim-sdk — not V3 |
| `16954-2` | `Balances.transfer_keep_alive` | ok | `0x48ec85ca55f5bf0df67479b7ec554650f98be0faee3681fddcbdc80c365e904f` | interim-sdk — not V3 |
| `16955-2` | `Proxy.create_pure` | ok | `0x9b6913236f255aef3ee9fbe72324ce56842e981e31701a64aa5ff5f788442bde` | interim-sdk — not V3 |
| `16956-2` | `Balances.transfer_keep_alive` | ok | `0xf79bbb5d6044ac1a8bdeed9f6fc2ec8e424b2c5700b1dd434e44616fa0000670` | interim-sdk — not V3 |
| `16957-2` | `Proxy.proxy(Proxy.add_proxy)` | ok | `0x3275c711990a2e5d0b537c2c2052d7ddc5ee0df3b01077363c99a63ea74d8993` | interim-sdk — not V3 |
| `16958-2` | `Proxy.announce` | ok | `0xfd62486a67d4ef224a83ad1e710e2bb949bbdd37ea2dbb2ad25edeebf6b74c5a` | interim-sdk — not V3 |
| `16959-2` | `Proxy.announce` | ok | `0xe9046d62bdc49f428c522ea481bd40a4380d918b006825043c48a651ec9e1b92` | interim-sdk — not V3 |
| `16960-2` | `Proxy.announce` | ok | `0x0790b73c83b1af26a7ff49d31d6ae3f500667b0caf228327ff94bcab1b71ba07` | interim-sdk — not V3 |
| `16961-2` | `Proxy.announce` | ok | `0x0fef93342110b5c13efa854dd94ce6d761b376ea6c0e463f4b64d43dd5b1c55f` | interim-sdk — not V3 |
| `16962-2` | `Proxy.announce` | ok | `0x03f6876e61fbbb2491fa1ab2b3050445fe07ab050208867b656292f603dc361b` | interim-sdk — not V3 |
| `16979-2` | `Proxy.proxy_announced(Balances.transfer_keep_alive)` | ok | `0xe7d56af21d0114254f914d87d2d226467dfaf629c1605c32232d7eff6d898d6e` | interim-sdk — not V3 |
| `16980-2` | `Proxy.proxy_announced(Balances.transfer_keep_alive)` | ok | `0x3103a4ec5fe0cc3c9d4652656433c678f0bbe5d37bb47dca62b61e4000b93ede` | interim-sdk — not V3 |
| `16981-2` | `Proxy.proxy_announced(Balances.transfer_keep_alive)` | ok | `0x453db8a6b1879f2fc72ea08c3f90d09b647b1aece281ad81b6e72f1878dcb563` | interim-sdk — not V3 |
| `16982-2` | `Proxy.proxy_announced(Balances.transfer_keep_alive)` | ok | `0x5b5d324b22fe1b604a31a295f4bfa5c7c0f651788a9713f55d23d03644b78980` | interim-sdk — not V3 |
| `16983-2` | `Proxy.proxy_announced(Balances.transfer_keep_alive)` | ok | `0xca8bc9784a59936ebcdbc6765d330aa689edba8d3898be4993f01fc55fba1243` | interim-sdk — not V3 |
| `16984-2` | `Proxy.announce` | ok | `0x4e0b22982abe8f7c8d1eeb7f0cb0038be2e4818fed334be380a167df309a27a8` | interim-sdk — not V3 |
| `16985-2` | `Proxy.proxy(Proxy.reject_announcement)` | ok | `0x7643fea1ff1ee69bc6b3926ae4311ce17ee7e3457ced37981a1f85d7091794ac` | interim-sdk — not V3 |
| `16986-2` | `Proxy.announce` | ok | `0xba230cbd13081e6888ed19f2f1b3c12c3fe17a0901e46f06bb3cbf103972b9f3` | interim-sdk — not V3 |
| `16987-2` | `Proxy.proxy(Proxy.reject_announcement)` | ok | `0x986ba03c64fa449b27084f7b03f46037be824f9c58dd1f15fa1676d77634d31e` | interim-sdk — not V3 |
| `17007-2` | `Proxy.proxy_announced(Balances.transfer_keep_alive)` | Proxy.Unannounced | `0xc46a09f4a47823d8b9f918666ddfb32496657a3b8940757805fbbd9916ccf2ad` | interim-sdk — not V3 |
| `17008-2` | `Proxy.announce` | ok | `0xab3e2e4649c1b1fe1e0b2db55469a38217b3ae1400020c29c132345e235e7ad7` | interim-sdk — not V3 |
| `17009-2` | `Proxy.remove_announcement` | ok | `0x62570bc962a97feeef7c16bb9b415fbf4253b2af3f1ccf44b46c69bae096ba2f` | interim-sdk — not V3 |
| `17010-2` | `Proxy.create_pure` | ok | `0x54b3f5990de51c50da248c5e56f47107f0b7cedac7f63d348eb00852d90b3856` | interim-sdk — not V3 |
| `17011-2` | `Balances.transfer_keep_alive` | ok | `0xdb9910d99c2961c22fc28d393c50aa2c6dacb3f1b0ae44c8551c6327c0c23c93` | interim-sdk — not V3 |
| `17012-2` | `Proxy.proxy(Proxy.add_proxy)` | ok | `0xcdf64540f0f6e6a42ad8f93d3991626278bb231c34b3e584f1b3d438f991e46e` | interim-sdk — not V3 |
| `17013-2` | `Proxy.proxy(Proxy.remove_proxy)` | ok | `0x477475b90c31ae6a26fb9abd81b237dbb564e838db93b63e992f24b14479eb1e` | interim-sdk — not V3 |
| `17014-2` | `Proxy.announce` | Proxy.NotProxy | `0x5708783476a98f9bf12dc3f5ebf158238344ede1278b7e88de60c06fae706553` | interim-sdk — not V3 |

### re-fetch — 2026-10-14T15:25:50Z — the 2026-10-14T15:15:31Z interim-1002 run, by extrinsic id

Fresh process over http (`http://127.0.0.1:19933`; the run itself was sent over ws) at head #8612 · `evidence/refetch-interim-1002-2026-10-14T152550Z.json` · every row: same block hash, tx hash and outcome as recorded.

| extrinsic id | call | outcome | tx hash | profile |
|---|---|---|---|---|
| `8510-1` | `Balances.transfer_keep_alive` | ok | `0xcb5f9381e8ebe5e0326a4a2642fe97f3d714a35f88695820b56c28cd2f9bacec` | interim-1002 — not V3 |
| `8511-1` | `Balances.transfer_keep_alive` | ok | `0xb723342f2b2c239d121c6dbc6ad9c495a884e6bba8e1d1fa0243c3c96468b4e3` | interim-1002 — not V3 |
| `8512-1` | `Balances.transfer_keep_alive` | ok | `0x6b5bdbd1ef958e5778d95f23d830172e08faee2559d646a1b63b51b24ee3cb3d` | interim-1002 — not V3 |
| `8513-1` | `Balances.transfer_keep_alive` | ok | `0x3d106f17515abddaf30ec03a8d43b1a538a4bb77c2a47ee9fc62a2c9b30cc9d5` | interim-1002 — not V3 |
| `8514-1` | `Proxy.anonymous` | ok | `0x9c540fec1825c07dbe393e5e4d81511a5dc396a01d33e71ae3ceb86cd50c42d9` | interim-1002 — not V3 |
| `8515-1` | `Balances.transfer_keep_alive` | ok | `0x9125c36c1f5260018fe5a4361c52df13ac6e1ee5deb673d97a2ec331a56763b7` | interim-1002 — not V3 |
| `8516-1` | `Proxy.proxy(Proxy.add_proxy)` | ok | `0x662e7df9f7fa0195c4581d62bc088a10756e6c9e8c7cff4d4f81f772758e5b13` | interim-1002 — not V3 |
| `8517-1` | `Proxy.announce` | ok | `0xeaf639aaa279fb4f95e07eedc674cbf6d5be2e1f66ddd3a48f0a5f3af52462f1` | interim-1002 — not V3 |
| `8518-1` | `Proxy.announce` | ok | `0x469886899a2c5d09fb6e5e2616fc059212c1659a2e2b7e6e391872fc872765e0` | interim-1002 — not V3 |
| `8519-1` | `Proxy.announce` | ok | `0x69fb96bb9a4b01bca82ea4e39ab8236e736ebc4b55c0fa8cb022b5371d8bab20` | interim-1002 — not V3 |
| `8520-1` | `Proxy.announce` | ok | `0x0a4a5f313571c633198987b33cc21441fdf6a438716ecb2fce8726e8ac14cae1` | interim-1002 — not V3 |
| `8521-1` | `Proxy.announce` | ok | `0x36ca9900758a1917dad06ed273b74cfcfc2f15a0aac6fa0d1fafe1da98ab2ed7` | interim-1002 — not V3 |
| `8528-1` | `Proxy.proxy_announced(Balances.transfer_keep_alive)` | ok | `0xeb86309ec52c000de299ad24c37e904ea9069e86cb52c94d2b3cd649f6b83593` | interim-1002 — not V3 |
| `8529-1` | `Proxy.proxy_announced(Balances.transfer_keep_alive)` | ok | `0x6a577e93aaeabe9b40ca40bdd4bf98e47875ff66d424ff33b9f08f5a4c64bf16` | interim-1002 — not V3 |
| `8530-1` | `Proxy.proxy_announced(Balances.transfer_keep_alive)` | ok | `0x868407305be566cb8515d4fa8df9bf2c4120a06ba5b750158f339eb180d2b2d8` | interim-1002 — not V3 |
| `8531-1` | `Proxy.proxy_announced(Balances.transfer_keep_alive)` | ok | `0xd2cfb3138c7597326f6c14d46153caba96848c8bc46e83d6632c2189ca6c7991` | interim-1002 — not V3 |
| `8532-1` | `Proxy.proxy_announced(Balances.transfer_keep_alive)` | ok | `0xd71515bab0a6ac1acda50c1b2ac00c7555c16ed0f091f184ba3a2d7220868983` | interim-1002 — not V3 |
| `8533-1` | `Proxy.announce` | ok | `0x8eed4c0849bf5b068481c19aa13392502bd79a63561d197ed446915755751822` | interim-1002 — not V3 |
| `8534-1` | `Proxy.proxy(Proxy.reject_announcement)` | ok | `0x6db5ec532781c0de5c28f48030f8eb9a7bac5161a162a37feca460993b7d8974` | interim-1002 — not V3 |
| `8535-1` | `Proxy.announce` | ok | `0x18e83ffa8371d546c886c6f3ffd5af6df5ceb938b7c221ed17f58f3b115f9fee` | interim-1002 — not V3 |
| `8536-1` | `Proxy.proxy(Proxy.reject_announcement)` | ok | `0x49c3387c08477b2fba5144751b74bca9ac75142002f926ee52e4d60720a9ebcc` | interim-1002 — not V3 |
| `8546-1` | `Proxy.proxy_announced(Balances.transfer_keep_alive)` | Proxy.Unannounced | `0x8799fd07612d01c8875cb880dd47bd841b5948b0fe7b4b92c2e5a62a5d758d98` | interim-1002 — not V3 |
| `8547-1` | `Proxy.announce` | ok | `0x90d0b3f14d16f0c8486f674eef9e062bd6cc718cb70ceca224b3d09108ad1c63` | interim-1002 — not V3 |
| `8548-1` | `Proxy.remove_announcement` | ok | `0x073d67ee36a785576430e7ad43f3cafeb7d899c93a9421bafb40c63d8024b521` | interim-1002 — not V3 |
| `8549-1` | `Proxy.anonymous` | ok | `0x72f85658ca0b7d58094223d50f5c7477753b7dddc67da8f8b9c61ca6a510a286` | interim-1002 — not V3 |
| `8550-1` | `Balances.transfer_keep_alive` | ok | `0x6c8cd8fcfe21989457c9c5693deae98be0ed9e39982d52467ffce19d9438d0f7` | interim-1002 — not V3 |
| `8551-1` | `Proxy.proxy(Proxy.add_proxy)` | ok | `0x9e29f39a874b73c52a5a2061e4526c7d1e9288f761cc125484b12b2b86695313` | interim-1002 — not V3 |
| `8552-1` | `Proxy.proxy(Proxy.remove_proxy)` | ok | `0x27db1c2dc9be8772327ad62345d2d7008fb8b97edc37697c7296833377a6c6dc` | interim-1002 — not V3 |
| `8553-1` | `Proxy.announce` | Proxy.NotProxy | `0x6c8a81cde997420ed6c19031064452c16a6b43528e8d2195ac02f10cf00eb281` | interim-1002 — not V3 |

### re-fetch — 2026-10-14T15:26:38Z — the thief-interim-sdk-2026-10-14T152055Z interim-sdk run, by extrinsic id

Fresh process over http (`http://127.0.0.1:29944`; the run itself was sent over ws) at head #17171 · `evidence/refetch-interim-sdk-2026-10-14T152638Z.json` · every row: same block hash, tx hash and outcome as recorded.

| extrinsic id | call | outcome | block hash | tx hash | profile |
|---|---|---|---|---|---|
| `17061-2` | `Proxy.announce` | ok | `0x4917b5c6c9bdb0f58d0e7258ca89912aebda7ac62d04e78d9ca30602f0639258` | `0x7721ae29aa2f40099ddb5f146c367723ea82518f0636ca5d166653f71efd084f` | interim-sdk — not V3 |
| `17084-2` | `Proxy.proxy_announced(Balances.transfer_keep_alive)` | Proxy.Unannounced | `0x7ced02ca2dde2f284f2aa3b77d31ae34d041f13b9e3fe7cb22ddce1d8ca17efa` | `0x991bcad46ae3564e01529def14b0a0984285bbe4738c41db246030ec157622c8` | interim-sdk — not V3 |
| `17086-2` | `Proxy.proxy(Balances.transfer_keep_alive)` | Proxy.Unannounced | `0xa42f8b12a9da80810f6dc8de314b82a817b2784895c3dc3380669aa6ad67b262` | `0x681c4371379ab1e4bb3bee9749d4ae7b68d2a8ca40d7ff2ffa49d1904e4f1569` | interim-sdk — not V3 |

### re-fetch — 2026-10-14T15:26:41Z — 17063-2 on interim-sdk, found by extrinsic id

Fresh process over http (`http://127.0.0.1:29944`) at head #17172 · `evidence/refetch-interim-sdk-2026-10-14T152641Z.json` · looked up by id alone: no recorded row to compare with — the block hash, call, outcome and tx hash below are what the chain returns.

| extrinsic id | call | outcome | block hash | tx hash | profile |
|---|---|---|---|---|---|
| `17063-2` | `Proxy.proxy(Proxy.reject_announcement)` | ok | `0x8ed064b3624e5cf16c7b898aa6f631e6a22ef28d66f4c49f6764627d0d7cdc85` | `0x4985d2190f14de7fb4474a2389aebd3e2d95e771f459c557716e18e9f11c79eb` | interim-sdk — not V3 |

### re-fetch — 2026-10-14T15:26:42Z — the thief-interim-1002-2026-10-14T152051Z interim-1002 run, by extrinsic id

Fresh process over http (`http://127.0.0.1:19933`; the run itself was sent over ws) at head #8621 · `evidence/refetch-interim-1002-2026-10-14T152642Z.json` · every row: same block hash, tx hash and outcome as recorded.

| extrinsic id | call | outcome | block hash | tx hash | profile |
|---|---|---|---|---|---|
| `8564-1` | `Proxy.announce` | ok | `0xe453f5b475d061c5e74daf184aff3a9378622890019d6e21eb0d8f0618fc8382` | `0xa80d747cbab7719cfb9c3041c87124b2681a67df979b708becaffc5e47a5febe` | interim-1002 — not V3 |
| `8576-1` | `Proxy.proxy_announced(Balances.transfer_keep_alive)` | Proxy.Unannounced | `0x6af8c7ddec9e5f72718dcad8883524b32ba664db1df2f889fba8b72054e3b858` | `0x3b73fae7808572c750ce2c59148e3ac68d8ef825c7737946b1c9e53325e5b66b` | interim-1002 — not V3 |
| `8577-1` | `Proxy.proxy(Balances.transfer_keep_alive)` | Proxy.Unannounced | `0xe23c51f7cbd4880c9f71457db5a8796dd6b88dffd1a024a13e1b635d4d108465` | `0x6dc3d1866c46105135d3ffe63ea8a4ff2594ba8c3b1559cd72979bfafb0fada7` | interim-1002 — not V3 |

### re-fetch — 2026-10-14T15:26:43Z — 8565-1 on interim-1002, found by extrinsic id

Fresh process over http (`http://127.0.0.1:19933`) at head #8621 · `evidence/refetch-interim-1002-2026-10-14T152643Z.json` · looked up by id alone: no recorded row to compare with — the block hash, call, outcome and tx hash below are what the chain returns.

| extrinsic id | call | outcome | block hash | tx hash | profile |
|---|---|---|---|---|---|
| `8565-1` | `Proxy.proxy(Proxy.reject_announcement)` | ok | `0x78a572b29bc67aaa3e4bbe334bf59153dd174121a2a33dfffb5179a86a8089a0` | `0x85e91e1b8c9f492747d72bb0f76947379c17854373a8006a8f7d5f4da35cf0fc` | interim-1002 — not V3 |

### re-fetch — 2026-10-14T15:43:52Z — the thief-interim-sdk-2026-10-14T153911Z interim-sdk run, by extrinsic id

Fresh process over http (`http://127.0.0.1:29944`; the run itself was sent over ws) at head #17516 · `evidence/refetch-interim-sdk-2026-10-14T154352Z.json` · every row: same block hash, tx hash and outcome as recorded.

| extrinsic id | call | outcome | block hash | tx hash | profile |
|---|---|---|---|---|---|
| `17426-2` | `Proxy.announce` | ok | `0xae932b3e76d4726f83a6748f127a64703f10f52e46f93d1224a8e3d60e92a2e6` | `0xe33d46654cda42d25c49bca2d5f5a1aa8a7682d46514361a539a6a0576f634c2` | interim-sdk — not V3 |
| `17449-2` | `Proxy.proxy_announced(Balances.transfer_keep_alive)` | Proxy.Unannounced | `0x774ad52161040d333c247c5e3f10ffcdc737ef0a32e1f7c0819099355de4d27e` | `0xb8bb582d40d4a9d21d62013127fdfb8b102fbf138e5d9544652fe60c63476790` | interim-sdk — not V3 |
| `17451-2` | `Proxy.proxy(Balances.transfer_keep_alive)` | Proxy.Unannounced | `0xef9829701796d1640fba39af059f66498df4c715f5fdc23921937c547e920368` | `0xc1ac18266e9b09aa9625d27702e034f42aa947b0badb910a69e8cb69cb0c81a4` | interim-sdk — not V3 |

### re-fetch — 2026-10-14T15:43:54Z — 17428-2 on interim-sdk, found by extrinsic id

Fresh process over http (`http://127.0.0.1:29944`) at head #17517 · `evidence/refetch-interim-sdk-2026-10-14T154354Z.json` · looked up by id alone: no recorded row to compare with — the block hash, call, outcome and tx hash below are what the chain returns.

| extrinsic id | call | outcome | block hash | tx hash | profile |
|---|---|---|---|---|---|
| `17428-2` | `Proxy.proxy(Proxy.reject_announcement)` | ok | `0xa7cfad34ea7e1afe8ff483222b1650429c3f725f150a77f97894e6d250cd8cd3` | `0x49aaa7bf0eaa6db42cfa48273f78f661068d7bc9d7dfa16e4d5ac6a17589ddd7` | interim-sdk — not V3 |

### re-fetch — 2026-10-14T15:43:56Z — 8747-1 on interim-1002, found by extrinsic id

Fresh process over http (`http://127.0.0.1:19933`) at head #8793 · `evidence/refetch-interim-1002-2026-10-14T154356Z.json` · looked up by id alone: no recorded row to compare with — the block hash, call, outcome and tx hash below are what the chain returns.

| extrinsic id | call | outcome | block hash | tx hash | profile |
|---|---|---|---|---|---|
| `8747-1` | `Proxy.proxy(Proxy.reject_announcement)` | ok | `0x08bffc747a1a1f56349fdf7b41467c8faa55115c4716eefc1187c37f2ccabf0f` | `0x1c8858eb209ef626edea210fa737b1d2a13a4e9c69ab332b54cd1ddfbf825bc0` | interim-1002 — not V3 |

### re-fetch — 2026-10-14T15:49:56Z — the thief-interim-1002-2026-10-14T153904Z interim-1002 run, by extrinsic id

Fresh process over http (`http://127.0.0.1:19933`; the run itself was sent over ws) at head #8853 · `evidence/refetch-interim-1002-2026-10-14T154956Z.json` · every row: same block hash, tx hash and outcome as recorded.

| extrinsic id | call | outcome | block hash | tx hash | profile |
|---|---|---|---|---|---|
| `8746-1` | `Proxy.announce` | ok | `0x372a8fcce8a1c9c3886cda2180a716ea2e023a5c0651b0db33f517071b4548bc` | `0x24468ffc95cc4d6dcfe15e3eefde994d460e35ae8555d3fdccd13a0a7bd74c11` | interim-1002 — not V3 |
| `8758-1` | `Proxy.proxy_announced(Balances.transfer_keep_alive)` | Proxy.Unannounced | `0x267eb9a6425bce50518f3b4378686fae04a4faea3645840f7b9de28832e5c015` | `0xdab01170656306e2f18ed44588c451f33b0000f106611e7d143905cea7585935` | interim-1002 — not V3 |
| `8759-1` | `Proxy.proxy(Balances.transfer_keep_alive)` | Proxy.Unannounced | `0x33c52d4ca6bdec17a8a00e600013660f96f7348a606aa8a06f62cc5a9d1d4d20` | `0x5215ff1d80df506ba11c604f8908fbb19fcd42e3fcf801157f0806b9bfb87fc3` | interim-1002 — not V3 |

### re-fetch — 2026-10-14T15:54:24Z — the thief-interim-sdk-2026-10-14T155007Z interim-sdk run, by extrinsic id

Fresh process over http (`http://127.0.0.1:29944`; the run itself was sent over ws) at head #17727 · `evidence/refetch-interim-sdk-2026-10-14T155424Z.json` · every row: same block hash, tx hash and outcome as recorded.

| extrinsic id | call | outcome | block hash | tx hash | profile |
|---|---|---|---|---|---|
| `17645-2` | `Proxy.announce` | ok | `0x82de8d25f6a496414a67735e193f8b1325a1522827c9bc46fca4b24a0e2c38bc` | `0xdb7f2df54ce84cb203c465d3f86e25e6b3eef9435849c636a14cd54a22916371` | interim-sdk — not V3 |
| `17668-2` | `Proxy.proxy_announced(Balances.transfer_keep_alive)` | Proxy.Unannounced | `0x44e36aff855b91f6c2ce84f386e1b0f79ab04ab3da14f2953903b36d9de6ef1f` | `0x7dcc24790a6560436a3b98dd7d41a74545333ab9343607a905cfce8396cd72a1` | interim-sdk — not V3 |
| `17670-2` | `Proxy.proxy(Balances.transfer_keep_alive)` | Proxy.Unannounced | `0x038069e6451c2a15c45f677424621fe30b9a8f7d2ca09945dcd278bea703cf45` | `0x24e0e721b068ce3c6b465b53fa313998cf56fa00a69031ce8394615fd083e5a8` | interim-sdk — not V3 |

### re-fetch — 2026-10-14T15:54:27Z — 17647-2 on interim-sdk, found by extrinsic id

Fresh process over http (`http://127.0.0.1:29944`) at head #17728 · `evidence/refetch-interim-sdk-2026-10-14T155427Z.json` · looked up by id alone: no recorded row to compare with — the block hash, call, outcome and tx hash below are what the chain returns.

| extrinsic id | call | outcome | block hash | tx hash | profile |
|---|---|---|---|---|---|
| `17647-2` | `Proxy.proxy(Proxy.reject_announcement)` | ok | `0xed598f5dc314d400f8ef0d3e7a4f47e7a446cc26a754b567f6e118e7a02bacd3` | `0x4cd52589a19024fa43e334d94f72dca2bd8daca90ba3b3f32933d05aaf9bc890` | interim-sdk — not V3 |

### re-fetch — 2026-10-14T15:54:29Z — the thief-interim-1002-2026-10-14T154956Z interim-1002 run, by extrinsic id

Fresh process over http (`http://127.0.0.1:19933`; the run itself was sent over ws) at head #8898 · `evidence/refetch-interim-1002-2026-10-14T155429Z.json` · every row: same block hash, tx hash and outcome as recorded.

| extrinsic id | call | outcome | block hash | tx hash | profile |
|---|---|---|---|---|---|
| `8854-1` | `Proxy.announce` | ok | `0x11fbf8e55d160d7cd110e935435e68cbd3533fe9c7a124d2ae8d653b988c414c` | `0x4717188726f8efd9af6b9dea96c5cfa13da6835f0a24895b05bacf079b7381da` | interim-1002 — not V3 |
| `8866-1` | `Proxy.proxy_announced(Balances.transfer_keep_alive)` | Proxy.Unannounced | `0x7816a87e40281f1f5ae5d2438a630605e55a26c450cf13e0648b5e823b9247c7` | `0x6b36e75940ec9df17fd053e2042d34d6daccf2eca2be05979b3cf52ec5449373` | interim-1002 — not V3 |
| `8867-1` | `Proxy.proxy(Balances.transfer_keep_alive)` | Proxy.Unannounced | `0x55ce1eaaa14384b86621d4a1c3a9ac1321d747c0d735ed4aebfa3f1b3a2b7e29` | `0xa137e7c43e8f96ffe3102c84955ae9773831443855a5c98bfc710d45b4d6382f` | interim-1002 — not V3 |

### re-fetch — 2026-10-14T15:54:29Z — 8855-1 on interim-1002, found by extrinsic id

Fresh process over http (`http://127.0.0.1:19933`) at head #8898 · `evidence/refetch-interim-1002-2026-10-14T155429Z-2.json` · looked up by id alone: no recorded row to compare with — the block hash, call, outcome and tx hash below are what the chain returns.

| extrinsic id | call | outcome | block hash | tx hash | profile |
|---|---|---|---|---|---|
| `8855-1` | `Proxy.proxy(Proxy.reject_announcement)` | ok | `0x493f5379c6139d2b78ac2aec7f6229e4384dcacd2b05a90cad154050119e0004` | `0x3850632e3be73ae3335cca65c47fe6300fda90963aa3ca6392ee2aebf039e66b` | interim-1002 — not V3 |

### seed — 2026-10-19T16:10:53Z — Portaldot V3 testnet

Runtime `portaldot-v3/3002` · genesis `0xd4f9acbcf7f52388d8b095abe3c198d19fc510e0343e643e87cfbccb155283bd` · endpoint `wss://test-node-v3.portaldot.dev` · demo vault `5G1jCu2ZvTkjsrGpNajJgExfAfthseLdb5EKhNjr4HA7Bhc9` · throwaway vault `5HFX8AFaLxULRMj9oz8owUT9uvs49vwXyBHwtTjBg5nQRJkx` · delay D = 10 blocks · code `489674a` · checks (fixtures/expected.json): all hold

| # | step | call | signer | expected | outcome | extrinsic id | block hash | tx hash | profile | explorer |
|---|---|---|---|---|---|---|---|---|---|---|
| 1 | create vault | `Proxy.create_pure` | cold | ok | ok | `105141-1` | `0x386ee92af1f2c82888a2f18dd8015565e1e23d1f7660b48d0e33b8a742e068e1` | `0xb2fff24331c48d8e0603dee4f38b46e8da09d00b97d80dfc127a5d2e698b6acc` | v3 | [block](https://node-console.portaldot.dev/?rpc=wss%3A%2F%2Ftest-node-v3.portaldot.dev#/explorer/query/0x386ee92af1f2c82888a2f18dd8015565e1e23d1f7660b48d0e33b8a742e068e1) |
| 2 | fund vault | `Balances.transfer_keep_alive` | cold | ok | ok | `105142-1` | `0x49438875da2f047face57288373a7b460e171a6e18c2785b2266f3e46551dbff` | `0x23b5678faf307a57e9bcdccd34b3d7e8b140476e85fc3b8c1af9b182173707fe` | v3 | [block](https://node-console.portaldot.dev/?rpc=wss%3A%2F%2Ftest-node-v3.portaldot.dev#/explorer/query/0x49438875da2f047face57288373a7b460e171a6e18c2785b2266f3e46551dbff) |
| 3 | add delayed hot key | `Proxy.proxy(Proxy.add_proxy)` | cold | ok | ok | `105143-1` | `0x5810a0e3aa342baf4cc3d3b80503ae8db9a238d3cb06ad86d8a42e2a5e43a3a6` | `0xde20361dd7c1c6bfd8ee68f4e90c8bcb44f18c51ef73711f6f8683a078503aa6` | v3 | [block](https://node-console.portaldot.dev/?rpc=wss%3A%2F%2Ftest-node-v3.portaldot.dev#/explorer/query/0x5810a0e3aa342baf4cc3d3b80503ae8db9a238d3cb06ad86d8a42e2a5e43a3a6) |
| 4 | announce rent 12.5 | `Proxy.announce` | hot | ok | ok | `105145-1` | `0xcb038cd2c3ff98a21b83157f41000f4a40ed066917e40355b7873a78041f7f6d` | `0x97bbd79e1566140debb0c40fb45fa590382ea3e481edc5f969e35e0650e2e2e1` | v3 | [block](https://node-console.portaldot.dev/?rpc=wss%3A%2F%2Ftest-node-v3.portaldot.dev#/explorer/query/0xcb038cd2c3ff98a21b83157f41000f4a40ed066917e40355b7873a78041f7f6d) |
| 5 | announce coffee 3.2 | `Proxy.announce` | hot | ok | ok | `105146-1` | `0xbb477dd95ca1bc229062a730df8e5ce445670b87439ff9e696975f46ebfe4f1c` | `0x2ca10425a4ae7d8b7824e5f090f3cc809247434d4c08a906093fede296a552b5` | v3 | [block](https://node-console.portaldot.dev/?rpc=wss%3A%2F%2Ftest-node-v3.portaldot.dev#/explorer/query/0xbb477dd95ca1bc229062a730df8e5ce445670b87439ff9e696975f46ebfe4f1c) |
| 6 | announce internet 4.75 | `Proxy.announce` | hot | ok | ok | `105147-1` | `0xf908a42db5ae54ad909aee5cd6b1f8a6a022f4ef424538689c276fb5d68dac8c` | `0x50e0de5e482c22214e21b94926b4db8f0f5af4a2ca8d52946e849d37608ff754` | v3 | [block](https://node-console.portaldot.dev/?rpc=wss%3A%2F%2Ftest-node-v3.portaldot.dev#/explorer/query/0xf908a42db5ae54ad909aee5cd6b1f8a6a022f4ef424538689c276fb5d68dac8c) |
| 7 | announce coffee 3.4 | `Proxy.announce` | hot | ok | ok | `105148-1` | `0xd0f52799d3028a4a9d6e65aee38b3fc6f6794ba75b01d1877bdeae903dfb6a45` | `0x7475f4a6689914341166d30ae62e578054e075270bb658f78325e321aa15b6bb` | v3 | [block](https://node-console.portaldot.dev/?rpc=wss%3A%2F%2Ftest-node-v3.portaldot.dev#/explorer/query/0xd0f52799d3028a4a9d6e65aee38b3fc6f6794ba75b01d1877bdeae903dfb6a45) |
| 8 | announce books 8 | `Proxy.announce` | hot | ok | ok | `105149-1` | `0xecee3e27616b07b5a0c40c0a526f1f61bd57385394332d6d03dfb73d88529f19` | `0xcae1bab03a2fcdd7dd2cc4140724e6d113ed48ae30a57f0db1ef75a32e4d309d` | v3 | [block](https://node-console.portaldot.dev/?rpc=wss%3A%2F%2Ftest-node-v3.portaldot.dev#/explorer/query/0xecee3e27616b07b5a0c40c0a526f1f61bd57385394332d6d03dfb73d88529f19) |
| 9 | execute rent 12.5 | `Proxy.proxy_announced(Balances.transfer_keep_alive)` | hot | ok | ok | `105156-1` | `0xe5113d3b8d838b6e948a2d925e1f5f44651f56c64769a442596b42a8e92b0f2d` | `0xb4ab2f099f4788890da5de5523fc186b4fc886d215e1770180dee1ac7f5b21fe` | v3 | [block](https://node-console.portaldot.dev/?rpc=wss%3A%2F%2Ftest-node-v3.portaldot.dev#/explorer/query/0xe5113d3b8d838b6e948a2d925e1f5f44651f56c64769a442596b42a8e92b0f2d) |
| 10 | execute coffee 3.2 | `Proxy.proxy_announced(Balances.transfer_keep_alive)` | hot | ok | ok | `105157-1` | `0x0ea2df3c9d45db05e0d6a31cba2a343dfb2357c2ab3a6934bfd76e18c77f70e5` | `0x1396c637e47a4ea4663fe9658e6830a5fc1d490655d71cf5180e4988a4465290` | v3 | [block](https://node-console.portaldot.dev/?rpc=wss%3A%2F%2Ftest-node-v3.portaldot.dev#/explorer/query/0x0ea2df3c9d45db05e0d6a31cba2a343dfb2357c2ab3a6934bfd76e18c77f70e5) |
| 11 | execute internet 4.75 | `Proxy.proxy_announced(Balances.transfer_keep_alive)` | hot | ok | ok | `105158-1` | `0xe603fbcef1fcbffc7496ae410c2acd21b939772e7864352e079e9d22bab7b8ca` | `0x793d6ca9e68ad9aa09bea69402ade60a6ece346c573ba79e3a22731c2408cbe1` | v3 | [block](https://node-console.portaldot.dev/?rpc=wss%3A%2F%2Ftest-node-v3.portaldot.dev#/explorer/query/0xe603fbcef1fcbffc7496ae410c2acd21b939772e7864352e079e9d22bab7b8ca) |
| 12 | execute coffee 3.4 | `Proxy.proxy_announced(Balances.transfer_keep_alive)` | hot | ok | ok | `105159-1` | `0x4e9caa1678a99c1300b546ebafced3256e4a5430140c4f885ace8527f9b75973` | `0x3f6df8b03267681353dfa42ea22aa83dae9f88841d61dba2b33226838f4d7899` | v3 | [block](https://node-console.portaldot.dev/?rpc=wss%3A%2F%2Ftest-node-v3.portaldot.dev#/explorer/query/0x4e9caa1678a99c1300b546ebafced3256e4a5430140c4f885ace8527f9b75973) |
| 13 | execute books 8 | `Proxy.proxy_announced(Balances.transfer_keep_alive)` | hot | ok | ok | `105161-1` | `0xc953983d75f78ae526c1d9a1533d2d26e7e5286be870056a9b56532180ec664f` | `0xf5d8198ae7d29550d17f1a911c2354b0e6942e8816c84f0f8075fe94eef04425` | v3 | [block](https://node-console.portaldot.dev/?rpc=wss%3A%2F%2Ftest-node-v3.portaldot.dev#/explorer/query/0xc953983d75f78ae526c1d9a1533d2d26e7e5286be870056a9b56532180ec664f) |
| 14 | drain 1 announce → attacker-1 | `Proxy.announce` | hot | ok | ok | `105162-1` | `0xfa7474595be641e748f3f95fb79978806166b65e2b0adc11d60a35712aa97802` | `0x5becc36ac3d9ded8435fab1e33e7301488d227173781829d3e8cc34c8e2398af` | v3 | [block](https://node-console.portaldot.dev/?rpc=wss%3A%2F%2Ftest-node-v3.portaldot.dev#/explorer/query/0xfa7474595be641e748f3f95fb79978806166b65e2b0adc11d60a35712aa97802) |
| 15 | drain 1 veto | `Proxy.proxy(Proxy.reject_announcement)` | cold | ok | ok | `105163-1` | `0x046b1aa5f782e0dac7859f4d20f7495f42cf63aec5b2983366720dba3b880f61` | `0x9e711c1368f617869f864a1e4515dba482c4a37be37aabcc73fa2af003bf8d38` | v3 | [block](https://node-console.portaldot.dev/?rpc=wss%3A%2F%2Ftest-node-v3.portaldot.dev#/explorer/query/0x046b1aa5f782e0dac7859f4d20f7495f42cf63aec5b2983366720dba3b880f61) |
| 16 | drain 2 announce → attacker-2 | `Proxy.announce` | hot | ok | ok | `105164-1` | `0xf588d5dba6d790166ff45206232ee10c700ead6420bcca6614c3770211fc2ab4` | `0x66c67a07ca9599ba2b31494cb1c5223ec5c77271f20948b032a877ce58cc154a` | v3 | [block](https://node-console.portaldot.dev/?rpc=wss%3A%2F%2Ftest-node-v3.portaldot.dev#/explorer/query/0xf588d5dba6d790166ff45206232ee10c700ead6420bcca6614c3770211fc2ab4) |
| 17 | drain 2 veto | `Proxy.proxy(Proxy.reject_announcement)` | cold | ok | ok | `105165-1` | `0xe3cad6b3b554e135c95a9f18bf3293692955cbc33d801d1f8ac9c816c7e632e2` | `0xa9661953ae7981b013ba1e52645caf37762be025aed544450109107ef5a9db7d` | v3 | [block](https://node-console.portaldot.dev/?rpc=wss%3A%2F%2Ftest-node-v3.portaldot.dev#/explorer/query/0xe3cad6b3b554e135c95a9f18bf3293692955cbc33d801d1f8ac9c816c7e632e2) |
| 18 | drain 1 execute after veto | `Proxy.proxy_announced(Balances.transfer_keep_alive)` | hot | Proxy.Unannounced | Proxy.Unannounced | `105175-1` | `0x8fd0eb8c90100bdc2a8c7ce596f5b253ece9a5c07949639942bae8b787fd1816` | `0x368f4f1778225fd5b5815e8c1718a94c4fd19468d522213ef3b7a319b57aa663` | v3 | [block](https://node-console.portaldot.dev/?rpc=wss%3A%2F%2Ftest-node-v3.portaldot.dev#/explorer/query/0x8fd0eb8c90100bdc2a8c7ce596f5b253ece9a5c07949639942bae8b787fd1816) |
| 19 | announce (to withdraw) | `Proxy.announce` | hot | ok | ok | `105176-1` | `0x9a06450d598571fb2d30cdca439192c31b280cb0dbc22e3240c89728beeaec1a` | `0x3330c4b8e5356da64bc4204ee848e76aaea551a842451e9941b227b53d890194` | v3 | [block](https://node-console.portaldot.dev/?rpc=wss%3A%2F%2Ftest-node-v3.portaldot.dev#/explorer/query/0x9a06450d598571fb2d30cdca439192c31b280cb0dbc22e3240c89728beeaec1a) |
| 20 | withdraw own announcement | `Proxy.remove_announcement` | hot | ok | ok | `105177-1` | `0x5231b915075499289229f201bdc5920602316af7398c20a53d8624f4952cea73` | `0xc1ac50d424fa2728336b34e2928b228bce27946981a282e8171b2dcd2d2f439d` | v3 | [block](https://node-console.portaldot.dev/?rpc=wss%3A%2F%2Ftest-node-v3.portaldot.dev#/explorer/query/0x5231b915075499289229f201bdc5920602316af7398c20a53d8624f4952cea73) |
| 21 | throwaway: create vault | `Proxy.create_pure` | cold | ok | ok | `105180-1` | `0x210b0efcc48808feed0c92a4ddc927e3917d8fbe2a185eef61983da3e53a34ff` | `0xc1e27c33366e32f9f5a4b763cf5183a0eb4eae672b770baac5d921f66a91b706` | v3 | [block](https://node-console.portaldot.dev/?rpc=wss%3A%2F%2Ftest-node-v3.portaldot.dev#/explorer/query/0x210b0efcc48808feed0c92a4ddc927e3917d8fbe2a185eef61983da3e53a34ff) |
| 22 | throwaway: fund vault | `Balances.transfer_keep_alive` | cold | ok | ok | `105181-1` | `0x22a7b74b2cfaafe913e995333d8b3978962767b3a41afc1880556be8b9fc3088` | `0xd955be6701ea5d9d9de34c88a3a33c686d83ed33ffca93e6e6686a580baf3a4d` | v3 | [block](https://node-console.portaldot.dev/?rpc=wss%3A%2F%2Ftest-node-v3.portaldot.dev#/explorer/query/0x22a7b74b2cfaafe913e995333d8b3978962767b3a41afc1880556be8b9fc3088) |
| 23 | throwaway: add delayed hot key | `Proxy.proxy(Proxy.add_proxy)` | cold | ok | ok | `105182-1` | `0x84bb6b7e98c6d583cf908c1dbba5c3b978ffb3f758b7a782c36ede39423e767b` | `0xacb57af24a278a92ec50db9c19fd0a05ef4568521ae67f468dc0c27df8e05bce` | v3 | [block](https://node-console.portaldot.dev/?rpc=wss%3A%2F%2Ftest-node-v3.portaldot.dev#/explorer/query/0x84bb6b7e98c6d583cf908c1dbba5c3b978ffb3f758b7a782c36ede39423e767b) |
| 24 | throwaway: panic | `Proxy.proxy(Proxy.remove_proxy)` | cold | ok | ok | `105183-1` | `0xe0a9d65703f4358a2c5066c9ecd9e9b1ac8496565cc537e0e6ac0b47ed1260e9` | `0x4e5cc55749a7aacc89d156c1a8916cc37c51c00dac6eee60a5af3acebdce22df` | v3 | [block](https://node-console.portaldot.dev/?rpc=wss%3A%2F%2Ftest-node-v3.portaldot.dev#/explorer/query/0xe0a9d65703f4358a2c5066c9ecd9e9b1ac8496565cc537e0e6ac0b47ed1260e9) |
| 25 | throwaway: hot announces after panic | `Proxy.announce` | hot | Proxy.NotProxy | Proxy.NotProxy | `105184-1` | `0x7fa304ff4e2d231b81665d155bcbe8b2c12728bc7c4419333a28e9924a4ebe50` | `0x4683509b1e3696e7fb9ab4f09abdd5b9ab51b0ac689995ca3d64d1bfe6baf177` | v3 | [block](https://node-console.portaldot.dev/?rpc=wss%3A%2F%2Ftest-node-v3.portaldot.dev#/explorer/query/0x7fa304ff4e2d231b81665d155bcbe8b2c12728bc7c4419333a28e9924a4ebe50) |
