# Final demo video run on V3 (2026-10-24)

The final demo video shows one live run of the core flow on the Portaldot V3 testnet (`portaldot-v3/3002`, genesis
`0xd4f9acbcf7f52388d8b095abe3c198d19fc510e0343e643e87cfbccb155283bd`), recorded 2026-10-23 23:24–23:27 UTC (2026-10-24 06:24–06:27 WIB),
vault `5F4rPFqS6PQEKUHacHtR5mLrDMVZ8RjCfJpYmfiktERhsmE1` (hot key delay 10 blocks, cold key delay 0). Three terminals were
recorded at the same time (owner, thief, watcher). Every id below was re-fetched by extrinsic id from a fresh process on
camera (`scripts/refetch.py --profile v3 --id 166811-1 --id 166816-1 --id 166825-1 --id 166831-1`: 4/4 found,
[refetch-v3-2026-10-23T232725Z.json](refetch-v3-2026-10-23T232725Z.json)).

| step | call | extrinsic id | outcome | tx hash |
|---|---|---|---|---|
| thief announces a sweep of the whole vault | `Proxy.announce` | `166811-1` | ok | `0x313aac6fb9eea989d9f00caf3cab96b204d916a787616125db73832fd8eb954b` |
| owner vetoes (the line pasted from the alert) | `Proxy.proxy(Proxy.reject_announcement)` | `166816-1` | ok | `0x3b57e68dcdcce72a86c7742be63cb8ac0d4fee938647d5c811f582cb4bc63f04` |
| thief executes after the delay | `Proxy.proxy_announced(Balances.transfer_keep_alive)` | `166825-1` | Proxy.Unannounced | `0x5a13e624e46e29d01ca59d2f7b1117802ada5898628f88fe0f091ed7dca1146f` |
| thief calls the proxy directly | `Proxy.proxy(Balances.transfer_keep_alive)` | `166831-1` | Proxy.Unannounced | `0x4d498305bc77c7f1f907ae2d64233353513f300f154554291aedfac572a9f0df` |

Watcher alert for this run: NOT YOURS, call hash `0x1d19e158d5e8f2277c08538cb22d55b9335f08e79c0180df7b32402f5eb40b59`,
announced by delegate `5Cwitu8fsmPcnpashjvrCsAvTVXp67oQLAKq8BnchwpLZULN` at block #166811, executable from #166821,
"Veto must land by #166820: 9 blocks (≈ 54 s)", up to 11.4200 tPOT at risk. The veto was included in block #166816, four
blocks before that deadline. The watcher printed the alert before `thief.py` returned (on V3 the thief script waits for
finality before it prints its receipt).

Vault `airlock status` before (head #166806) and after (head #166835): at risk 11.4200 tPOT, free 11.5200 tPOT, reserved
1.9800 tPOT, no pending announcements, unchanged. The two outputs as recorded (terminal line wrapping removed):

```
$ airlock status --vault 5F4rPFqS6PQEKUHacHtR5mLrDMVZ8RjCfJpYmfiktERhsmE1
chain: Portaldot V3 testnet · portaldot-v3/3002 · genesis 0xd4f9acbcf7f52388d8b095abe3c198d19fc510e0343e643e87cfbccb155283bd · wss://test-node-v3.portaldot.dev
vault 5F4rPFqS6PQEKUHacHtR5mLrDMVZ8RjCfJpYmfiktERhsmE1 on Portaldot V3 testnet · head #166806
  at risk 11.4200 tPOT (free 11.5200 tPOT, reserved 1.9800 tPOT, locked 0.0000 tPOT)
  delegate 5Cwitu8fsmPcnpashjvrCsAvTVXp67oQLAKq8BnchwpLZULN Any delay 10
  delegate 5EnJxVLrjdznoeEqGyz8Hf1T5MwK1ZTGTBnVnvsuYWu1UjHw Any delay 0  (cold: veto/panic)
  no pending announcements

$ airlock status --vault 5F4rPFqS6PQEKUHacHtR5mLrDMVZ8RjCfJpYmfiktERhsmE1
chain: Portaldot V3 testnet · portaldot-v3/3002 · genesis 0xd4f9acbcf7f52388d8b095abe3c198d19fc510e0343e643e87cfbccb155283bd · wss://test-node-v3.portaldot.dev
vault 5F4rPFqS6PQEKUHacHtR5mLrDMVZ8RjCfJpYmfiktERhsmE1 on Portaldot V3 testnet · head #166835
  at risk 11.4200 tPOT (free 11.5200 tPOT, reserved 1.9800 tPOT, locked 0.0000 tPOT)
  delegate 5Cwitu8fsmPcnpashjvrCsAvTVXp67oQLAKq8BnchwpLZULN Any delay 10
  delegate 5EnJxVLrjdznoeEqGyz8Hf1T5MwK1ZTGTBnVnvsuYWu1UjHw Any delay 0  (cold: veto/panic)
  no pending announcements
```

The veto's block on the V3 testnet's public explorer (signed by the cold key `5EnJxVLrjdznoeEqGyz8Hf1T5MwK1ZTGTBnVnvsuYWu1UjHw`,
call `proxy.rejectAnnouncement`, callHash `0x1d19e158…eb40b59`):
https://node-console.portaldot.dev/?rpc=wss%3A%2F%2Ftest-node-v3.portaldot.dev#/explorer/query/0xa8f95309e37549e56809b7af7d53818eb17e66be6044b4dfaed91c129c6468eb
