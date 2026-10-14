"""Seed fixtures and scripts: consistent inputs, funding derived from chain constants, the thief stays Airlock-free."""

import ast
import importlib.util
from pathlib import Path
from types import SimpleNamespace

from substrateinterface import Keypair

from airlock.chain_profile import INTERIM_SDK, Chain

ROOT = Path(__file__).resolve().parent.parent


def load_script(name):
    spec = importlib.util.spec_from_file_location(name, ROOT / "scripts" / f"{name}.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


seed = load_script("seed")


def test_fixtures_load_and_are_consistent():
    accounts, spends, attacks, expected = seed.load_fixtures()
    roles = set(accounts["accounts"])
    assert {"cold", "hot", "payee-rent", "payee-coffee"} <= roles
    assert len(spends) == 5 and {s["payee"] for s in spends} <= roles
    assert len(attacks) == 20 and {a["destination"] for a in attacks} <= roles
    assert set(expected["attackers_hold_zero"]) <= roles


def test_committed_addresses_are_the_dev_root_derivations():
    accounts, *_ = seed.load_fixtures()
    for name, a in accounts["accounts"].items():
        assert Keypair.create_from_uri(a["path"]).ss58_address == a["address_dev_root"], name


def test_duplicate_spend_rejected(tmp_path, monkeypatch):
    for f in ("accounts.json", "attacks.csv", "expected.json"):
        (tmp_path / f).write_text((ROOT / "fixtures" / f).read_text())
    (tmp_path / "spends.csv").write_text("label,amount,payee\na,1,payee-rent\nb,1,payee-rent\n")
    monkeypatch.setattr(seed, "FIXTURES", tmp_path)
    try:
        seed.load_fixtures()
    except seed.SeedFailed as e:
        assert "same call hash" in str(e)
    else:
        raise AssertionError("duplicate spend accepted")


def test_funding_covers_deposits_from_chain_constants():
    constants = {"ProxyDepositBase": 63, "ProxyDepositFactor": 198, "AnnouncementDepositBase": 63,
                 "AnnouncementDepositFactor": 396, "MaxPending": 32, "MaxProxies": 32}
    s = SimpleNamespace(get_constant=lambda pallet, name: SimpleNamespace(value=constants[name]))
    chain = Chain(substrate=s, profile=INTERIM_SDK, url="", genesis_hash="", code_hash="", spec_name="", spec_version=0,
                  decimals=0, symbol="UNIT", block_time_ms=3000, block_time_source="", has_revive=False,
                  existential_deposit=100)
    need = seed.amounts(chain, [{}] * 5, fee=1)
    assert need["vault"] == 5000
    assert need["hot"] >= 63 + 6 * 396 + 100  # every legitimate spend pending at once, plus one more, plus ED
    assert need["cold"] >= 2 * (63 + 198) + need["vault"] + need["throwaway"]


def test_thief_and_raw_announce_import_no_airlock_code():
    for name in ("thief", "raw_announce"):
        tree = ast.parse((ROOT / "scripts" / f"{name}.py").read_text())
        modules = {n.module for n in ast.walk(tree) if isinstance(n, ast.ImportFrom)}
        modules |= {a.name for n in ast.walk(tree) if isinstance(n, ast.Import) for a in n.names}
        assert not any(m and m.split(".")[0] == "airlock" for m in modules), name


def test_thief_refuses_dev_key_off_interim():
    thief = load_script("thief")
    try:
        thief.hot_key("//AirlockDemo//hot", "0x" + "00" * 32)
    except SystemExit as e:
        assert "not a local interim dev chain" in str(e)
    else:
        raise AssertionError("dev key accepted on an unknown chain")
    assert thief.hot_key("//AirlockDemo//hot", thief.DEV_SDK_GENESIS).ss58_address == \
        Keypair.create_from_uri("//AirlockDemo//hot").ss58_address
