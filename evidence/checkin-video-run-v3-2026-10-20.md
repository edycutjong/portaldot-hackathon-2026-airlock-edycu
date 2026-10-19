# Check-in video run on V3 (2026-10-20)

The check-in demo video shows one live run of the core flow on the Portaldot V3 testnet (`portaldot-v3/3002`, genesis
`0xd4f9acbcf7f52388d8b095abe3c198d19fc510e0343e643e87cfbccb155283bd`), vault `5F4rPFqS6PQEKUHacHtR5mLrDMVZ8RjCfJpYmfiktERhsmE1`
(delay 10 blocks). Every id below was re-fetched by extrinsic id from a fresh process (`scripts/refetch.py --id <id> --profile v3`,
outputs in the `refetch-v3-2026-10-19T18*.json` files next to this one).

| step | call | extrinsic id | outcome | tx hash |
|---|---|---|---|---|
| thief announces a sweep | `Proxy.announce` | `106211-1` | ok | `0x2587b2ca4ed0d7106edcf389efea1f8a7f5d99b4f4c369c3692addf3ae7ff927` |
| owner vetoes (pasted from the alert) | `Proxy.proxy(Proxy.reject_announcement)` | `106217-1` | ok | `0xa6ed0f556a0815c39a6bda849ad64a2825624a3b5d7a2b467cb0b844e675de69` |
| thief executes after the delay | `Proxy.proxy_announced(Balances.transfer_keep_alive)` | `106224-1` | `Proxy.Unannounced` | `0x6d8a790c5fd42f8cf8141ba16bb34b9b0303751b4f09a322da63f8e0cb8d66ce` |
| thief calls the proxy directly | `Proxy.proxy(Balances.transfer_keep_alive)` | `106230-1` | `Proxy.Unannounced` | `0xa118e3a941f508577149ae047af85d870c10a10929046b02f3a73c52ef224a4d` |

Watcher alert for this run: NOT YOURS, call hash `0x1d19e158d5e8f2277c08538cb22d55b9335f08e79c0180df7b32402f5eb40b59`, announced at block #106211, executable from
#106221, up to 11.4200 tPOT at risk. Vault `airlock status` before and after: at risk 11.4200 tPOT, free 11.5200 tPOT,
reserved 1.9800 tPOT, no pending announcements, unchanged.
