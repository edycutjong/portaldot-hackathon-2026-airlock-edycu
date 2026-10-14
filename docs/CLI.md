# The `airlock` CLI

The vault owner's side of the flow: create a vault, announce and execute spends with the hot key, veto and panic with
the cold key. Installed by `pip install -e .` as `airlock` (also `python -m airlock.cli`). Code:
[airlock/cli.py](../airlock/cli.py) (arguments, output) over [airlock/ops.py](../airlock/ops.py) (the operations).

| Command | Signer | Extrinsic(s) | Checked before signing | Checked after inclusion |
|---|---|---|---|---|
| `create --hot ADDR --delay N \| --delay-s S --fund X` | cold | `Proxy.create_pure` (`anonymous` on interim-1002), `Balances.transfer_keep_alive`, `Proxy.proxy(add_proxy(hot, Any, D))` | D ≥ 1, hot ≠ cold; deposits read from chain and printed; X covers the hot-key deposit + existential deposit; the cold key can pay X + the create deposit + fees | `Proxy.Proxies(vault)` = cold delay 0 + hot delay D; if a step after `create_pure` fails, the error names the new vault |
| `announce --vault V --to ADDR --amount X` | hot | `Proxy.announce(vault, blake2_256(call))` | hot is a delegate of the vault; the same call is not already pending (one execution clears every mature copy, so a second identical spend would be lost) | the hash is in `Proxy.Announcements(hot)` at the inclusion block (I3) |
| `execute --vault V --to ADDR --amount X` (or `--call 0x…`) | hot | `Proxy.proxy_announced(hot, vault, None, call)` | the hash is pending; the delay has passed (waits, or refuses with `--no-wait`); hot is still a delegate; the vault can move X (a call that fails inside `proxy_announced` still uses up its announcement) | no mature copy of the announcement is left |
| `veto --vault V --delegate HOT --hash H` | cold | `Proxy.proxy(vault, reject_announcement(hot, H))` | H is 32 bytes; the signer is a delay-0 delegate; H is pending for that delegate on that vault | H is no longer pending |
| `panic --vault V --delegate HOT` | cold | `Proxy.proxy(vault, remove_proxy(hot, type, D))` per definition | not the cold key itself; the signer is a delay-0 delegate; HOT is a delegate | HOT is no longer a delegate (its pending announcements stay stored and cannot execute while it is not a delegate; the CLI lists them — never re-add that key without vetoing them) |
| `status --vault V` | — | none (reads) | | delegates, at-risk balance, pending announcements |

"Checked after inclusion" matters because an included extrinsic is not proof of its effect: a `proxy.proxy` whose
inner call fails still succeeds as an extrinsic. The CLI reports failure in that case
([airlock/dispatch.py](../airlock/dispatch.py) reads `Proxy.ProxyExecuted`), and it also re-reads storage.

## Keys

`--cold` / `--hot` take a key spec, never a seed (a seed on the command line ends up in shell history):

- `file:<path>` — a file holding a mnemonic or secret URI, e.g. `~/.config/airlock/cold.seed` (`chmod 600`; the CLI
  warns otherwise);
- `env:<VAR>` — an environment variable holding it;
- `//Name` — a public dev derivation (`//Alice`, `//AirlockDemo//cold`), accepted **only on the interim dev chains**.
  A file or variable holding a dev derivation or the public dev phrase is refused on any other chain.

Defaults come from `$AIRLOCK_COLD_KEY` and `$AIRLOCK_HOT_KEY`, so the veto line of a watcher alert
(`airlock veto --vault … --delegate … --hash …`) runs as pasted. The chain is `$AIRLOCK_PROFILE` / `$AIRLOCK_WSS` (or
`--profile` / `--url`); a chain whose genesis and runtime code hash are not a known profile is refused before anything
is signed ([airlock/chain_profile.py](../airlock/chain_profile.py)).

## Output and exit status

Each extrinsic prints one line with the profile label, block, extrinsic id (`block-index`) and tx hash; `--json` prints
one JSON object per line instead (`{"tx": …}` per extrinsic, then `{"result": …}`).

| Exit | Meaning |
|---|---|
| 0 | done, and the effect was read back from chain state |
| 1 | an extrinsic failed on chain, or its effect is not visible afterwards; the node rejected a transaction; the connection was lost mid-command (outcome unknown — check with `status`) |
| 130 | interrupted (Ctrl-C) |
| 2 | refused before signing: bad key spec, unknown chain, nothing pending, delay not passed with `--no-wait`, … |

## Not built yet

The owner intent registry: an `announce` from this CLI is still shown by the watcher as NOT YOURS, because nothing
yet tells the watcher that the owner made it. Finality: a command reports success once its extrinsic is in a block and
the read-back at the best block shows the effect; it does not wait for finality (on the interim dev chains this is the
same thing in practice; on a public chain a reorg could still undo a just-included veto). "Veto all" for announcement spam (`utility.batch`), a web veto page and
`Revive.call` as a held call type are later steps.

## Tests

- Offline: `tests/test_ops.py` — the operations against a fake chain that applies the pallet's maturity rule:
  refusals (announce by a non-delegate or of an identical pending call; execute early, not pending, after panic, or
  more than the vault can move; veto with a bad hash, a delayed signer or nothing pending; panic of the cold key or a
  non-delegate; create unfunded, underfunded, over the cold key's balance, delay 0 or beyond u32) and read-backs
  (announce not stored at the inclusion block, veto still pending, a failure after `create_pure` naming the vault).
  `tests/test_cli.py` — the argument contract, the alert's veto line parsing as CLI input, and `main()`'s exit status
  for refusals, node rejection, a dropped connection and Ctrl-C. `tests/test_keys.py` — key specs.
- Live, on the current interim profile: `tests/integration/test_invariants_chain.py` — I1 (a delayed hot key cannot
  spend unannounced), I2 (a vetoed announcement never executes, also after the delay), the control (the same
  announcement without a veto executes after the delay and not before), and panic (a removed hot key gets
  `Proxy.NotProxy`). Skipped unless `AIRLOCK_IT=1`:

```bash
AIRLOCK_IT=1 AIRLOCK_PROFILE=interim-sdk pytest -q tests/integration
```
