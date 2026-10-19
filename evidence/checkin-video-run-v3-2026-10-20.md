# Check-in video run on V3 (2026-10-20)

The check-in demo video shows one live run of the core flow on the Portaldot V3 testnet (`portaldot-v3/3002`, genesis
`0xd4f9acbcf7f52388d8b095abe3c198d19fc510e0343e643e87cfbccb155283bd`), vault `5F4rPFqS6PQEKUHacHtR5mLrDMVZ8RjCfJpYmfiktERhsmE1`
(delay 10 blocks). Every id below was re-fetched by extrinsic id from a fresh process (`scripts/refetch.py --id <id> --profile v3`,
outputs in the `refetch-v3-2026-10-19T18*.json` files next to this one). The video is the second take; the first take's run is in
the same vault history and is not shown.

| step | call | extrinsic id | outcome | tx hash |
|---|---|---|---|---|
| thief announces a sweep | `Proxy.announce` | `106296-1` | ok | `0x549257319246b08f6fa3ca322ee1f9ffc98a0cc132d3214cca8118d78d7649b2` |
| owner vetoes (pasted from the alert) | `Proxy.proxy(Proxy.reject_announcement)` | `106302-1` | ok | `0x91954e771503cd50aec05ee4a1d87d79d7a995eaf9e6c3f16019d2780e8c2135` |
| thief executes after the delay | `Proxy.proxy_announced(Balances.transfer_keep_alive)` | `106310-1` | Proxy.Unannounced | `0x2a9cba831f51e0ce2e37980ce6d27b95489a8f39b525d3fc3c31165431ddae09` |
| thief calls the proxy directly | `Proxy.proxy(Balances.transfer_keep_alive)` | `106316-1` | Proxy.Unannounced | `0xa39cce8d2d36b7799a9913e629c7bd07533346193ba24f92dd5088c187907048` |

Watcher alert for this run: NOT YOURS, call hash `0x1d19e158d5e8f2277c08538cb22d55b9335f08e79c0180df7b32402f5eb40b59`, announced at block #106296, executable from
#106306, up to 11.4200 tPOT at risk. Vault `airlock status` before and after: at risk 11.4200 tPOT, free 11.5200 tPOT,
reserved 1.9800 tPOT, no pending announcements, unchanged.
