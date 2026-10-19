#!/usr/bin/env python3
"""Seed the demo vault and its history on a chain, through the CLI's own operations, so the demo moment is reproducible.

What it writes (fixtures/ holds the inputs; every amount is in the chain's own token units):
  setup     funder -> cold, hot, payee-rent, payee-coffee        (interim dev chains only; elsewhere pre-fund them)
  vault     cold   create_pure, fund, add_proxy(hot, Any, D)      -> the demo vault (hot = delayed delegate)
  spends    hot    5 x announce, then after D 5 x proxy_announced  (fixtures/spends.csv: legitimate, not vetoed)
  drains    hot    2 x announce a drain to attacker-N             (fixtures/attacks.csv rows 1-2)
            cold   2 x veto (reject_announcement through the vault)
            hot    proxy_announced of drain 1 after D             -> must FAIL Proxy.Unannounced (I2)
  withdraw  hot    announce + remove_announcement                 (the hot key takes back its own announcement)
  panic     cold   throwaway vault: create, fund, add hot, remove_proxy(hot)
            hot    announce on it after the panic                 -> must FAIL Proxy.NotProxy
  checks    fixtures/expected.json: vault total = fund - sum(spends) exactly, payees received exactly their spends,
            attackers hold 0, no announcement pending on the vault, delegates as expected on both vaults

Keys are derived from one root + fixed paths (fixtures/accounts.json). On interim dev chains the root is the public dev
phrase (paths //AirlockDemo//cold …) and //Alice (//Dave on interim-1002) funds the demo accounts. On any other chain
pass --root file:<path> (mnemonic, mode 600, kept in ~/.config/airlock/) and fund cold + hot first; --plan prints
what they need. Re-running on a chain where the recorded vault still exists only re-checks it (--fresh: a new vault).

usage: python scripts/seed.py [--profile interim-sdk] [--url ws://…] [--delay-s 60 | --delay N] [--proof docs/PROOF.md]
"""

from __future__ import annotations

import argparse
import csv
import json
import os
import sys
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from substrateinterface import Keypair  # noqa: E402

from airlock.chain_profile import Chain, ChainUnreachable, ProfileMismatch, UnknownChain, connect  # noqa: E402
from airlock.keys import KeyRefused, secret_of  # noqa: E402
from airlock.ops import (NotConfirmed, Refused, Sender, TxFailed, announce, blocks_for,  # noqa: E402
                         code_version, create_vault, delay_of, deposits, execute, panic, pending_for, transfer, veto,
                         withdraw)

FIXTURES = ROOT / "fixtures"
FUNDER = {"interim-sdk": "//Alice", "interim-1002": "//Dave"}  # 1002's //Alice is a validator with little unlocked
VAULT_FUND = Decimal("5000")
STATE_DIR = Path(os.environ.get("AIRLOCK_STATE_DIR", Path.home() / ".local" / "share" / "airlock"))


class SeedFailed(RuntimeError):
    pass


def load_fixtures() -> tuple[dict, list[dict], list[dict], dict]:
    accounts = json.loads((FIXTURES / "accounts.json").read_text())
    with (FIXTURES / "spends.csv").open() as f:
        spends = list(csv.DictReader(f))
    with (FIXTURES / "attacks.csv").open() as f:
        attacks = list(csv.DictReader(f))
    expected = json.loads((FIXTURES / "expected.json").read_text())
    keys = {(s["payee"], s["amount"]) for s in spends}
    if len(keys) != len(spends):  # same payee + amount = same call hash: one execution would clear both
        raise SeedFailed("fixtures/spends.csv: two spends with the same payee and amount have the same call hash")
    return accounts, spends, attacks, expected


def derive(root: str, path: str) -> Keypair:
    return Keypair.create_from_uri(root + path)


def amounts(chain: Chain, spends: list[dict], fee: int, vault_fund: Decimal = VAULT_FUND) -> dict[str, int]:
    """What each demo account needs, from the chain's constants (deposits) and a measured transfer fee."""
    d = deposits(chain)
    ed = chain.existential_deposit
    proxy_create = d["ProxyDepositBase"] + d["ProxyDepositFactor"]  # reserved from cold per vault created
    hot_slot = d["ProxyDepositFactor"]  # reserved from the vault when it adds the hot key
    announce_pending = d["AnnouncementDepositBase"] + (len(spends) + 1) * d["AnnouncementDepositFactor"]
    vault = chain.units(vault_fund)
    throwaway = 2 * ed + 2 * hot_slot
    return {
        "vault": vault,
        "throwaway": throwaway,
        "cold": 2 * proxy_create + vault + throwaway + 2 * ed + 200 * fee,
        "hot": announce_pending + 2 * ed + 200 * fee,
        "payee": ed,
    }


def state_path(chain: Chain) -> Path:
    return STATE_DIR / f"seed-{chain.profile.id}-{chain.genesis_hash[2:10]}.json"


def check(sp: Sender, chain: Chain, st: dict, spends: list[dict], who: dict[str, str], expected: dict) -> list[str]:
    """fixtures/expected.json, evaluated against the chain. Returns the failures (empty = all hold)."""
    s, vault = chain.substrate, st["vault"]
    fails = []

    def total(addr: str) -> int:
        data = s.query("System", "Account", [addr]).value["data"]
        return int(data["free"]) + int(data["reserved"])

    spent = sum(chain.units(x["amount"]) for x in spends)
    if expected["vault_total"] == "fund - sum(spends)" and total(vault) != st["fund"] - spent:
        fails.append(f"vault total {chain.fmt(total(vault))} != fund {chain.fmt(st['fund'])} - spends {chain.fmt(spent)}")
    if expected["payees_received"] == "sum of their spends":
        for payee in sorted({x["payee"] for x in spends}):
            want = sum(chain.units(x["amount"]) for x in spends if x["payee"] == payee)
            got = sp.free(who[payee]) - st["payee_start"][payee]
            if got != want:
                fails.append(f"{payee} received {chain.fmt(got)}, expected {chain.fmt(want)}")
    for name in expected["attackers_hold_zero"]:
        if total(who[name]) != 0:
            fails.append(f"{name} holds {chain.fmt(total(who[name]))}, expected 0")
    if expected["pending_on_vault"] == 0 and pending_for(sp, vault):
        fails.append(f"announcements still pending on the vault: {pending_for(sp, vault)}")
    defs = sp.delegates(vault)
    if (delay_of(defs, who["cold"]), delay_of(defs, who["hot"])) != (0, st["delay"]) or len(defs) != 2:
        fails.append(f"demo vault delegates {defs}, expected cold delay 0 + hot delay {st['delay']}")
    if st.get("throwaway"):
        defs = sp.delegates(st["throwaway"])
        if delay_of(defs, who["hot"]) is not None or delay_of(defs, who["cold"]) != 0:
            fails.append(f"throwaway vault delegates {defs}, expected cold only (hot removed by panic)")
    return fails


def seed(sp: Sender, chain: Chain, args, who_keys: dict[str, Keypair], spends, attacks, expected) -> dict:
    s = chain.substrate
    who = {k: v.ss58_address for k, v in who_keys.items()}
    cold, hot = who_keys["cold"], who_keys["hot"]
    delay = args.delay if args.delay is not None else blocks_for(args.delay_s, chain.block_time_ms)
    fee = int(s.get_payment_info(transfer(chain, who["hot"], chain.existential_deposit), cold)["partialFee"])
    need = amounts(chain, spends, fee, args.vault_fund)
    floor = sum(chain.units(x["amount"]) for x in spends) + deposits(chain)["ProxyDepositFactor"] + chain.existential_deposit
    if need["vault"] < floor:  # the vault pays the hot key's proxy deposit, then every spend, and must stay alive
        raise SeedFailed(f"--vault-fund {args.vault_fund} is below what the spends need ({chain.fmt(floor)})")
    print(f"plan: delay D = {delay} blocks; cold needs {chain.fmt(need['cold'])}, hot {chain.fmt(need['hot'])} "
          f"(deposits read from chain, transfer fee {chain.fmt(fee)})")
    if args.plan:
        return {}

    # setup: fund the demo accounts (dev funder on interim chains; elsewhere they must already hold enough)
    if chain.profile.is_interim:
        funder = Keypair.create_from_uri(FUNDER[chain.profile.id])
        for name, want in (("cold", need["cold"]), ("hot", need["hot"]), ("payee-rent", need["payee"]),
                           ("payee-coffee", need["payee"])):
            short = want - sp.free(who[name])
            if short > 0:
                sp.send(f"fund {name}", "funder", funder, transfer(chain, who[name], short), note=chain.fmt(short))
    for name in ("cold", "hot"):
        if sp.free(who[name]) < need[name]:
            raise SeedFailed(f"{name} {who[name]} holds {chain.fmt(sp.free(who[name]))}, needs {chain.fmt(need[name])}")
    st = {"genesis_hash": chain.genesis_hash, "profile": chain.profile.id, "delay": delay, "fund": need["vault"],
          "payee_start": {p: sp.free(who[p]) for p in ("payee-rent", "payee-coffee")}}

    # the demo vault
    st["vault"] = vault = create_vault(sp, cold, who["hot"], delay, index=args.index, fund=need["vault"])
    print(f"demo vault {vault}")

    # 5 legitimate spends: announce all, wait D once, execute all
    calls = [(x, transfer(chain, who[x["payee"]], chain.units(x["amount"]))) for x in spends]
    for x, call in calls:
        announce(sp, hot, vault, call, step=f"announce {x['label']} {x['amount']}")
    for x, call in calls:
        execute(sp, hot, vault, call, step=f"execute {x['label']} {x['amount']}")

    # 2 drains, vetoed; the first is then tried after its delay and must fail Unannounced
    drains = []
    for a in attacks[:2]:
        planck = chain.at_risk(vault).transferable if a["amount"] == "all" else chain.units(a["amount"])
        call = transfer(chain, who[a["destination"]], planck)
        p = announce(sp, hot, vault, call, step=f"drain {a['attempt']} announce → {a['destination']}")
        veto(sp, cold, vault, who["hot"], p.call_hash, step=f"drain {a['attempt']} veto")
        drains.append((call, p))
    sp.wait_until_block(max(p.release_block for _, p in drains))
    sp.send("drain 1 execute after veto", "hot", hot,
            s.compose_call("Proxy", "proxy_announced", {"delegate": who["hot"], "real": vault,
                                                         "force_proxy_type": None, "call": drains[0][0]}),
            "Proxy.Unannounced", note="I2: vetoed, delay passed")

    # the hot key withdraws one of its own announcements
    second_thoughts = transfer(chain, who["payee-coffee"], chain.units("9.9"))
    p = announce(sp, hot, vault, second_thoughts, step="announce (to withdraw)")
    withdraw(sp, hot, vault, p.call_hash, step="withdraw own announcement")

    # panic on a throwaway vault, so the demo vault keeps its hot key for the live demo
    st["throwaway"] = vault2 = create_vault(sp, cold, who["hot"], delay, index=args.index + 1, fund=need["throwaway"],
                                          label="throwaway: ")
    panic(sp, cold, vault2, who["hot"], step="throwaway: panic")
    sp.send("throwaway: hot announces after panic", "hot", hot,
            s.compose_call("Proxy", "announce", {"real": vault2, "call_hash": p.call_hash}), "Proxy.NotProxy")
    return st


def proof_markdown(chain: Chain, rows: list[dict], st: dict, started: str, version: str, checks: list[str]) -> str:
    lines = [f"### seed — {started} — {chain.profile.label}", "",
             f"Runtime `{chain.spec_name}/{chain.spec_version}` · genesis `{chain.genesis_hash}` · endpoint "
             f"`{chain.url}` · demo vault `{st['vault']}` · throwaway vault `{st['throwaway']}` · delay D = "
             f"{st['delay']} blocks · code `{version}` · checks (fixtures/expected.json): "
             f"{'all hold' if not checks else '; '.join(checks)}", "",
             "| # | step | call | signer | expected | outcome | extrinsic id | block hash | tx hash | profile |",
             "|---|---|---|---|---|---|---|---|---|---|"]
    for i, r in enumerate(rows, 1):
        lines.append(f"| {i} | {r['step']} | `{r['call']}` | {r['signer']} | {r['expected']} | {r['outcome']} | "
                     f"`{r['extrinsic_id']}` | `{r['block_hash']}` | `{r['extrinsic_hash']}` | {r['profile']} |")
    return "\n".join(lines) + "\n"


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--profile", default=None, help="expected chain profile (default $AIRLOCK_PROFILE, else auto)")
    ap.add_argument("--url", default=None, help="node websocket (default $AIRLOCK_WSS, else the profile default)")
    g = ap.add_mutually_exclusive_group()
    g.add_argument("--delay", type=int, default=None, help="hot-key delay D in blocks")
    g.add_argument("--delay-s", type=float, default=60, help="hot-key delay D in seconds (default 60)")
    ap.add_argument("--root", default=None, help="key root: file:<path> / env:<VAR> (default: dev phrase, interim only)")
    ap.add_argument("--index", type=int, default=0, help="create_pure index for the demo vault (throwaway: +1)")
    ap.add_argument("--fresh", action="store_true", help="seed a new demo vault even if one is recorded")
    ap.add_argument("--vault-fund", type=Decimal, default=VAULT_FUND,
                    help="tokens the demo vault is funded with (default %(default)s; must exceed the spends)")
    ap.add_argument("--plan", action="store_true", help="print what the accounts need and exit")
    ap.add_argument("--proof", type=Path, default=None, help="append the rows to this PROOF.md (passing runs only)")
    args = ap.parse_args(argv)
    if args.delay is not None and args.delay < 1:
        ap.error("--delay must be >= 1")
    if not args.vault_fund.is_finite() or args.vault_fund <= 0:
        ap.error("--vault-fund must be a positive amount")

    try:
        accounts, spends, attacks, expected = load_fixtures()
        chain = connect(args.url, args.profile)
    except (UnknownChain, ProfileMismatch, ChainUnreachable, SeedFailed) as e:
        print(f"SEED REFUSED: {e}", file=sys.stderr)
        return 2
    try:
        try:
            root = secret_of(args.root, chain.profile) if args.root else None
        except KeyRefused as e:
            print(f"SEED REFUSED: {e}", file=sys.stderr)
            return 2
        if root is None:
            if not chain.profile.is_interim:
                print(f"SEED REFUSED: {chain.profile.label} is not an interim dev chain: pass --root file:<path>",
                      file=sys.stderr)
                return 2
            root = ""  # the public dev phrase: "//AirlockDemo//cold" derives from it
        keys = {name: derive(root, a["path"]) for name, a in accounts["accounts"].items()}
        if not root:  # the committed addresses are the dev-root ones: catch a drifted fixture
            drift = [n for n, a in accounts["accounts"].items() if keys[n].ss58_address != a["address_dev_root"]]
            if drift:
                print(f"SEED REFUSED: fixtures/accounts.json addresses drifted for {drift}", file=sys.stderr)
                return 2
        return run(chain, args, keys, spends, attacks, expected)
    finally:
        chain.substrate.close()


def run(chain: Chain, args, keys, spends, attacks, expected) -> int:
    started = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    version = code_version()
    who = {k: v.ss58_address for k, v in keys.items()}
    print(f"chain: {chain.profile.label} · {chain.spec_name}/{chain.spec_version} · genesis {chain.genesis_hash} · "
          f"{chain.url} · {chain.decimals} decimals · block {chain.block_time_ms} ms · code {version}")

    def echo(r):
        mark = "PASS" if r["outcome"] == r["expected"] else "FAIL"
        print(f"[{r['profile']}] {mark} {r['step']:<34} {r['call']:<52} {r['signer']:<6} #{r['block']:<6} "
              f"id={r['extrinsic_id']} tx={r['extrinsic_hash']}" + ("" if r["outcome"] == "ok" else f" ({r['outcome']})"),
              flush=True)

    sp = Sender(chain, echo=echo)
    state_file = state_path(chain)
    if state_file.exists() and not args.fresh and not args.plan:
        st = json.loads(state_file.read_text())
        if st.get("genesis_hash") == chain.genesis_hash and sp.delegates(st["vault"]):
            fails = check(sp, chain, st, spends, who, expected)
            print(f"already seeded: demo vault {st['vault']} (state {state_file}); checks: "
                  f"{'all hold' if not fails else fails}")
            return 1 if fails else 0
        print(f"recorded vault {st.get('vault')} is gone from this chain (reset dev chain?): seeding again")

    failure, st = None, {}
    try:
        st = seed(sp, chain, args, keys, spends, attacks, expected)
        if args.plan:
            return 0
        fails = check(sp, chain, st, spends, who, expected)
        if fails:
            failure = "checks failed: " + "; ".join(fails)
        else:
            print("checks PASS: " + "; ".join(expected["description"]))
    except (TxFailed, NotConfirmed, Refused, SeedFailed) as e:
        failure = f"{type(e).__name__}: {e}"
    except Exception as e:  # keep the evidence of what was sent before an unexpected error
        failure = f"{type(e).__name__}: {e}"
    if failure:
        print(f"SEED FAILED: {failure}", file=sys.stderr)

    types = sorted({r["call"] for r in sp.rows})
    status = "failed" if failure else "passed"
    out = ROOT / "evidence" / f"seed-{chain.profile.id}-{started.replace(':', '')}{'-FAILED' if failure else ''}.json"
    out.parent.mkdir(exist_ok=True)
    out.write_text(json.dumps({"started": started, "status": status, "failure": failure, "code": version,
                               "profile": chain.profile.id, "label": chain.profile.label, "endpoint": chain.url,
                               "genesis_hash": chain.genesis_hash, "code_hash": chain.code_hash,
                               "runtime": f"{chain.spec_name}/{chain.spec_version}", "delay_blocks": st.get("delay"),
                               "vault": st.get("vault"), "throwaway_vault": st.get("throwaway"), "accounts": who,
                               "tx_count": len(sp.rows), "call_types": types, "rows": sp.rows}, indent=2) + "\n")
    print(f"{len(sp.rows)} extrinsics, {len(types)} call types, {status} -> {out.relative_to(ROOT)}")
    if failure:
        return 1
    state_file.parent.mkdir(parents=True, exist_ok=True)
    state_file.write_text(json.dumps(st, indent=2) + "\n")
    print(f"demo vault {st['vault']} · hot {who['hot']} (delay {st['delay']} blocks) · cold {who['cold']}")
    print(f"thief:  python scripts/thief.py --vault {st['vault']} --to <address> --amount all")
    if args.proof:
        with args.proof.open("a") as f:
            f.write("\n" + proof_markdown(chain, sp.rows, st, started, version, []))
        print(f"rows appended to {args.proof}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
