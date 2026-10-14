# Airlock — on-chain proof

Every row is a real extrinsic, identified by extrinsic id (`block-index`), block hash and transaction hash, and
labelled with the chain profile it ran on (`airlock/chain_profile.py`). Rows are appended by the script that
produced them (`scripts/spike_proxy.py --proof docs/PROOF.md`), never typed by hand; each run names the commit
that produced it. The JSON for every run is in `evidence/`; files not listed below are superseded runs of earlier commits
the same day (each names its commit, except the first four, which predate that field).

**Profile labels.** The target is the Portaldot V3 testnet. At 2026-10-14 00:47 UTC its RPC host
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

**Reading a run.** Steps 4 (I1), 7 (I2) and 9 are expected failures: `Proxy.Unannounced` is the pallet refusing the
call. Step 9 executes an announced payment before the delay has passed (the delay is enforced, not just stored); step 10
is the control for step 7: the same delegate and delay, without a veto, and the payment executes.

## Runs

### spike_proxy — 2026-10-14T00:47:25Z — interim — not V3 (upstream polkadot-sdk substrate-node dev chain)

Runtime `node/268` · genesis `0x0c4402ef4ccc34d28b16523e1cf26831716527c61a65d8e0840ab26e1636c8a2` · vault `5DWxiPjqFG2KaDpVU2n1SJTwPNkJtdPkbXYEDGfyFzrkDzSe` · delay D = 5 blocks · code `df77299`

| # | step | call | signer | expected | outcome | extrinsic id | block hash | tx hash | profile | note |
|---|---|---|---|---|---|---|---|---|---|---|
| 1 | create vault | `Proxy.create_pure` | cold | ok | ok | `891-2` | `0x1d67e9fcba9639b7933bad4781ed73aad053272518cfd1cdea865e8ccf40970b` | `0xff4e10e55fb0bccb26c31501f794e3b7585699637b1676b2a37314788d4c496e` | interim-sdk — not V3 |  |
| 2 | fund vault | `Balances.transfer_keep_alive` | cold | ok | ok | `892-2` | `0xa04c821a843d836543692de2c217206fc5611f117c0dc93a8550857600a82530` | `0xe8c220af2ce3ea6fd82783e5821b0451cfa674e234fea04cbc2b742cc9961464` | interim-sdk — not V3 |  |
| 3 | add delayed hot key | `Proxy.proxy(Proxy.add_proxy)` | cold | ok | ok | `893-2` | `0x809f22224e05edbafcac915238d4fb4dd3d56fe5d94f781b761869b0941c6211` | `0xfbf294d539a6e3f6c4f94a97f7a4d133f85d5ef602a9d357ad264b3e13418d53` | interim-sdk — not V3 | delay 5 blocks |
| 4 | hot spends unannounced | `Proxy.proxy(Balances.transfer_keep_alive)` | hot | Proxy.Unannounced | Proxy.Unannounced | `894-2` | `0x9b2dcd65caf762a81b47467fa219bdd9a4b590d7e6f9eb89f382476495b1283a` | `0x726e577530a3c25d09a7cf5f9b4aee374634db690f43b162377ef31651e28792` | interim-sdk — not V3 | I1 |
| 5 | hot announces theft | `Proxy.announce` | hot | ok | ok | `895-2` | `0x4888c9c53714adb3cf37849d01668a4408cb8e5e7c717ec35a6c82fb36b62c3e` | `0x5c28e1a73d712dee6f96f6e9990b6774fd9bd932272b720656fc61ec63cdd9e5` | interim-sdk — not V3 | `call hash 0xf4c3c3361dcfb6b8193c568bd42e6ab4d2731097b285f573e10e7d2f7e55478a` |
| 6 | cold vetoes | `Proxy.proxy(Proxy.reject_announcement)` | cold | ok | ok | `896-2` | `0xf69ad0a66a0d8b810ae9f7edfaaab373726428023c0918abd302c8c643546ccb` | `0xaff69a2b7bb64d89fd6115ba0f4c494c67e8685314f9b0e9c3e9955297608be5` | interim-sdk — not V3 |  |
| 7 | vetoed theft after delay | `Proxy.proxy_announced(Balances.transfer_keep_alive)` | hot | Proxy.Unannounced | Proxy.Unannounced | `902-2` | `0x1569f42aaf088f3244c90487fa802922f2275383e3464fb143948f171fdeea42` | `0xc5764379ca2a079f7baf0a8e4ad9d24b2c92aecdda8a6c38ed64b3f69e8b8cc3` | interim-sdk — not V3 | I2 |
| 8 | hot announces payment | `Proxy.announce` | hot | ok | ok | `903-2` | `0x6b881a645e553bb2cb453a8f8e7be8922d6678b238828ada0a92b844273a1e8d` | `0x87fe647dc547b53bb0a65a07d0f6fcc69846e14b0cc873a4daeae570c182603b` | interim-sdk — not V3 | `call hash 0x33aaaddcdcaeb538d590ead70a635c99a4d5a6adf61773ea5558f5c96291a385` |
| 9 | payment before delay | `Proxy.proxy_announced(Balances.transfer_keep_alive)` | hot | Proxy.Unannounced | Proxy.Unannounced | `904-2` | `0x8aecbdfe996566cf5bc712bbe9e0525d9300c7e3259f21f3b5a1dd19903781dc` | `0x3ce371a4dad3b29d821c0210577b19098bdbde9f879b670dd01222501521a2ce` | interim-sdk — not V3 | delay enforced |
| 10 | payment executes after delay | `Proxy.proxy_announced(Balances.transfer_keep_alive)` | hot | ok | ok | `910-2` | `0x6584a1ebf71998611601a121f2060226938ea532792bda3dee380ed229890bf7` | `0xf099724029be5c0b856160790a4b06d236d705463e033b0d52c59b1cfb7632a2` | interim-sdk — not V3 | control for I2 |

### spike_proxy — 2026-10-14T00:48:25Z — interim — not V3 (Portaldot dev node, runtime spec 1002)

Runtime `portaldot/1002` · genesis `0x53c36c0fc46f5bb2429d701b5ad7a0577b343aa53c4964333f1922611974dd22` · vault `5FgjWjK2hFS3c5hDVcYfXjoPHhs2weh1VGHSr199F8WoqjNd` · delay D = 5 blocks · code `df77299`

| # | step | call | signer | expected | outcome | extrinsic id | block hash | tx hash | profile | note |
|---|---|---|---|---|---|---|---|---|---|---|
| 1 | create vault | `Proxy.anonymous` | cold | ok | ok | `398-1` | `0x01fc2ce0d230ca0cf0266650299f3253936ccca9512d03166be48f81a7a31f32` | `0x30b4ef45adfc7e79b1ea23e6cd943a43d940df87dd582d380dcba424e27229d8` | interim-1002 — not V3 |  |
| 2 | fund vault | `Balances.transfer_keep_alive` | cold | ok | ok | `399-1` | `0x392e3891f68ae4e052c79b6eb3a7228123944e1e441c44557fb8f0f79f436f7a` | `0x360abf932071a069c19a55a2d68bf09563af8f6c18ea627d876d97ab97fc4ed6` | interim-1002 — not V3 |  |
| 3 | add delayed hot key | `Proxy.proxy(Proxy.add_proxy)` | cold | ok | ok | `400-1` | `0xecc4c111d4641a5d7bc67e7e277ce1225480614b5c1b19ec155396a4aaf0f257` | `0x10e6da819648d85361e6949f9a6f18ac7797dde3361134226dafcd4cd74a2f6b` | interim-1002 — not V3 | delay 5 blocks |
| 4 | hot spends unannounced | `Proxy.proxy(Balances.transfer_keep_alive)` | hot | Proxy.Unannounced | Proxy.Unannounced | `401-1` | `0x907c9c0acb56414267eee033ecc7a616fb1b6bd3fe90b177f17f822857d530b2` | `0xe05f3b08d9f1eaa3ac22ea96838decbed0a2c226e554da130e6b2b890239c940` | interim-1002 — not V3 | I1 |
| 5 | hot announces theft | `Proxy.announce` | hot | ok | ok | `402-1` | `0x809ad5b2fd80b856a32f4469275c5b5778beccf34bdba92434ce257852992804` | `0x21a1eb2af7f090b399731413b9c976f16b795691dabbcbc069fd2c7d8a17388c` | interim-1002 — not V3 | `call hash 0x5b11231612a3699c8009e8abb7b35314a73a8f28088221aa1a5d1a92bd2222fa` |
| 6 | cold vetoes | `Proxy.proxy(Proxy.reject_announcement)` | cold | ok | ok | `403-1` | `0xc3b1b37f05ff697f9f1bf52c75d39a67dcd59b25e6e3754bac293de8154423a9` | `0xb856d00eefb241af0325e4783a021fdcabb2f107929139942538b86bbf1016e1` | interim-1002 — not V3 |  |
| 7 | vetoed theft after delay | `Proxy.proxy_announced(Balances.transfer_keep_alive)` | hot | Proxy.Unannounced | Proxy.Unannounced | `409-1` | `0x487639ea0669edcda70e89b47177fa587b83ba6d5547c530c7f8db3f4a086776` | `0x8a6065d8b127b5430c0297abb58a1466f4ea0c961e6a3c781facbfe6f9dda783` | interim-1002 — not V3 | I2 |
| 8 | hot announces payment | `Proxy.announce` | hot | ok | ok | `410-1` | `0xd34cfef5d6525c3f6e0f880bdd20f560e1acaefecbcafd119585412c572a5c73` | `0x5f9df3bb3492f0d9e24efac2119fbd7bc4a6ee4b90d8a9a17084c2adab759a8f` | interim-1002 — not V3 | `call hash 0xa4369adc5768a0261ef223e7fa5da14b9ca90da9f10717df38bfae9768ec2748` |
| 9 | payment before delay | `Proxy.proxy_announced(Balances.transfer_keep_alive)` | hot | Proxy.Unannounced | Proxy.Unannounced | `411-1` | `0xbc7793fed9394c0e746933f9bdafa96fd71e672a339af08d914fc00d1e643ee4` | `0x4ba6fc3e52b610881ec1a63c295550b55f26eea58b6bf92c1e7f66634f1d3381` | interim-1002 — not V3 | delay enforced |
| 10 | payment executes after delay | `Proxy.proxy_announced(Balances.transfer_keep_alive)` | hot | ok | ok | `417-1` | `0x5fd817419486da626b151dac2fee741eb2dd5bcc719645b07fbe5efb2a380293` | `0xbebfdfe81b26cfb2eb5a8f5518806042c0a6bc96552b6265cf8d0aea059fb10a` | interim-1002 — not V3 | control for I2 |
