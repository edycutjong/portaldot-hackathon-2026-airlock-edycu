# Airlock — on-chain proof

Every row is a real extrinsic, identified by extrinsic id (`block-index`), block hash and transaction hash, and
labelled with the chain profile it ran on (`airlock/chain_profile.py`). Rows are appended by the script that
produced them (`scripts/spike_proxy.py --proof docs/PROOF.md`), never typed by hand; each run names the commit
that produced it. The JSON for every run, including earlier ones, is in `evidence/`.

**Profile labels.** The target is the Portaldot V3 testnet. At 2026-10-14 00:32 UTC its RPC host
`testnetv3-node.feso-apps.xyz` had no DNS record (`dig +short @1.1.1.1` returned nothing), so these rows come from
**interim chains — not V3**:

| Profile | Chain | Runtime | Genesis hash |
|---|---|---|---|
| `interim-sdk` | upstream polkadot-sdk `substrate-node` dev chain (`polkadot-stable2606-3`), run by `scripts/interim_node.sh` | `node/268` | `0x0c4402ef4ccc34d28b16523e1cf26831716527c61a65d8e0840ab26e1636c8a2` |
| `interim-1002` | Portaldot's own `portaldot_dev` dev node, `--tmp` | `portaldot/1002` | `0x53c36c0fc46f5bb2429d701b5ad7a0577b343aa53c4964333f1922611974dd22` |

The dev genesis is deterministic, so a fresh `interim-sdk` chain has the same genesis hash; a row can be re-fetched only
from the node that recorded it (`--base-path` keeps it across restarts). `interim-1002` runs with `--tmp`, so its rows
are a run log, not re-fetchable proof. Signers are dev keys (`//Alice` cold, `//Bob` hot, `//Eve` thief's payee,
`//Ferdie` legitimate payee).

**Reading a run.** Step 4 (I1) and step 7 (I2) are expected failures: `Proxy.Unannounced` is the pallet refusing the
thief. Step 9 is the control for step 7: the same delegate and delay, without a veto, and the payment executes.

## Runs

### spike_proxy — 2026-10-14T00:32:24Z — interim — not V3 (upstream polkadot-sdk substrate-node dev chain)

Runtime `node/268` · genesis `0x0c4402ef4ccc34d28b16523e1cf26831716527c61a65d8e0840ab26e1636c8a2` · vault `5CCGa8w5btggNvMQi6hLR4ZozRpcBVzqFptVPYn4EQQDPn4f` · delay D = 5 blocks · code `e5e4a0d`

| # | step | call | signer | expected | outcome | extrinsic id | block hash | tx hash | profile |
|---|---|---|---|---|---|---|---|---|---|
| 1 | create vault | `Proxy.create_pure` | cold | ok | ok | `591-2` | `0xf88028f8571707815c2bedb5a4d85782ed35756cd8b4b65c5e528a5d5a1dab58` | `0x99cf4374732fabf3698d97aa6ab7a7f1a6a12e315880a9febf5fde2aacbf0665` | interim-sdk — not V3 |
| 2 | fund vault | `Balances.transfer_keep_alive` | cold | ok | ok | `592-2` | `0x77d2c3482e12952256cdcec55d57e4a10f788abf2b5596d4ee458b99bd3789ae` | `0x3b25bc9cb8d8c381501ff514ce25ebac14baa13e7dcdb916ba57da4e43b32f3b` | interim-sdk — not V3 |
| 3 | add delayed hot key | `Proxy.proxy(Proxy.add_proxy)` | cold | ok | ok | `593-2` | `0x51306b0034d9b0e391f6615b8b9ac08672ec6fb98709bf58caa926c9154b7bcd` | `0x88cc01e938ce02fa2f1fefa169d3129fbb2428cd39cd4f95a9f21f91d8917e67` | interim-sdk — not V3 |
| 4 | hot spends unannounced | `Proxy.proxy(Balances.transfer_keep_alive)` | hot | Proxy.Unannounced | Proxy.Unannounced | `594-2` | `0x881e539ac6c4db2a02e690d47f0b82019688376f51456f78dc7933eacf39511a` | `0xd535ea1050e95ab3647e9ae7e2e95e142d5733747adf7d16714c75f79edcaa10` | interim-sdk — not V3 |
| 5 | hot announces theft | `Proxy.announce` | hot | ok | ok | `595-2` | `0x29ef132b58e061266a93f6520f3fa0cdb035f7e343702151734c4c8b8060b6df` | `0xc4f864ce8bba9109a3865b112ef11fb86eb584a50213e9ab1974b356a1b66d9c` | interim-sdk — not V3 |
| 6 | cold vetoes | `Proxy.proxy(Proxy.reject_announcement)` | cold | ok | ok | `596-2` | `0x943c6b6c24574173140f9b3a8960b1b3c7ecbde630b943ed9d7611ab58c833ef` | `0x20868ccce62dcb2b6f936f518c54b2d7ad2ab419d7ec3101e1680fc63bb0a30a` | interim-sdk — not V3 |
| 7 | vetoed theft after delay | `Proxy.proxy_announced(Balances.transfer_keep_alive)` | hot | Proxy.Unannounced | Proxy.Unannounced | `602-2` | `0x1fecf2a10844a485e156d95dd11715143d902702e21041f2d0dc94c3fad156bf` | `0xf9f6da751120028c327b05c37086eddce4d85cbd06c44881c06826a59a1b7333` | interim-sdk — not V3 |
| 8 | hot announces payment | `Proxy.announce` | hot | ok | ok | `603-2` | `0xa9e4f0e3f8c4ea245d09aa940b6d9283d37552ae5a659b45235335be672a9e7e` | `0x2f8e3cea0aeab83d0a3d1d9405cd6c441b4c0f74ad196ba6af6c24f37e20fe1e` | interim-sdk — not V3 |
| 9 | payment executes after delay | `Proxy.proxy_announced(Balances.transfer_keep_alive)` | hot | ok | ok | `610-2` | `0x4849d65441ed9e82cfd20b92e56520be0eab615ead33d07149fc4621ca640bf7` | `0x9fe70760f58eac57323ef77a01cfd0a4ec67e839d7ef6f2125b705eadfcd0cdf` | interim-sdk — not V3 |

### spike_proxy — 2026-10-14T00:33:25Z — interim — not V3 (Portaldot dev node, runtime spec 1002)

Runtime `portaldot/1002` · genesis `0x53c36c0fc46f5bb2429d701b5ad7a0577b343aa53c4964333f1922611974dd22` · vault `5GfhoE1bDLj7tMVgyVB6HmHY6DCAmYX77dxmTSNEW3W85YQc` · delay D = 5 blocks · code `e5e4a0d`

| # | step | call | signer | expected | outcome | extrinsic id | block hash | tx hash | profile |
|---|---|---|---|---|---|---|---|---|---|
| 1 | create vault | `Proxy.anonymous` | cold | ok | ok | `248-1` | `0x0c4ff0ee917b2cf96a3c1061079c417be29256d752b4e5eaff355ad1a2cf6cd9` | `0x875ebcbdcaa1795f6a188538d59b243c080135db750fdacb692bd93841427699` | interim-1002 — not V3 |
| 2 | fund vault | `Balances.transfer_keep_alive` | cold | ok | ok | `249-1` | `0xf4d46928dcd21549cdd58c8c061ba21755fc679270ab44cfacae406cb8531f16` | `0xe4d27f4f184f9ca50566c6c23bcde7662974bdfd53e7f7379e40be779724f4f0` | interim-1002 — not V3 |
| 3 | add delayed hot key | `Proxy.proxy(Proxy.add_proxy)` | cold | ok | ok | `250-1` | `0x902e8afdc553360c01272416c7afbd4cd493f042b69630782ae4ad162ccf199b` | `0xf129bab29ea7ca6a177b28dd2a1e2b856965d25c25659f71d34831cce53f917d` | interim-1002 — not V3 |
| 4 | hot spends unannounced | `Proxy.proxy(Balances.transfer_keep_alive)` | hot | Proxy.Unannounced | Proxy.Unannounced | `251-1` | `0x16616977a73324ad8d8087c20d825f5e8c053864c498a14fbf3080eecc5062fe` | `0xae40bf29d856060e4273d141df5e1d7a9414b63c108534a843419493aa4b68e7` | interim-1002 — not V3 |
| 5 | hot announces theft | `Proxy.announce` | hot | ok | ok | `252-1` | `0xd567046edfb96e4a9b4c0bec407e1f6951592b3a56392f6f81faf01b0ee30bf7` | `0x86e261be67c69e2b48468392b1de1f2ae6d981abbf1f9e3ce9ce2289cb519bbc` | interim-1002 — not V3 |
| 6 | cold vetoes | `Proxy.proxy(Proxy.reject_announcement)` | cold | ok | ok | `253-1` | `0x56797d1a73f441da156dd8234fcf04c9a28c3033697c5b6237a081bc6834c7b5` | `0x457aabf62ad2a810115da401b3d0a955b99f9712ee1510917d8191cb8a90952d` | interim-1002 — not V3 |
| 7 | vetoed theft after delay | `Proxy.proxy_announced(Balances.transfer_keep_alive)` | hot | Proxy.Unannounced | Proxy.Unannounced | `259-1` | `0xb988c7a01054ed604723c090be084cf8630c292263a62a2ad42455a0251318a2` | `0xdf821ab660db05faa553e6d2d340bb1275f21e431dfc0fcfbc67286369c47231` | interim-1002 — not V3 |
| 8 | hot announces payment | `Proxy.announce` | hot | ok | ok | `260-1` | `0xde5d958c04ab8dd7b2d1e478839e7dbb43b9798b3c3eae93685d823339c75b43` | `0x13f3c5f1672f6c50ab64727738bb3658723798e2f05305fc6d0932b5b3ff04fc` | interim-1002 — not V3 |
| 9 | payment executes after delay | `Proxy.proxy_announced(Balances.transfer_keep_alive)` | hot | ok | ok | `267-1` | `0xb71e41a227a2d6b35e3a368f297507c3690423f13228fb420752730b69ada250` | `0x163ace1d5abf5c52a37dbe6fe54d191d09b1d362d20ca92a08aa58dd70669d68` | interim-1002 — not V3 |
