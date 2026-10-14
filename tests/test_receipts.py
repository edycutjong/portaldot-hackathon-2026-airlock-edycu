"""Re-fetch by extrinsic id: id parsing, call naming, and the comparison that decides MATCH vs DIFF."""

from types import SimpleNamespace

import pytest

from airlock.receipts import Refetched, _call_name, compare, extrinsic_hash, parse_extrinsic_id

ROW = {"extrinsic_id": "85-2", "block_hash": "0xAB", "extrinsic_hash": "0xcd", "outcome": "Proxy.Unannounced"}


def got(**over):
    base = dict(extrinsic_id="85-2", block_number=85, block_hash="0xab", extrinsic_hash="0xcd",
                call="Proxy.announce", outcome="Proxy.Unannounced")
    return Refetched(**{**base, **over})


def test_parse_extrinsic_id():
    assert parse_extrinsic_id("85-2") == (85, 2)


@pytest.mark.parametrize("bad", ["85", "85-", "-2", "85-2-1", "0x55-2", "a-b", ""])
def test_parse_extrinsic_id_refuses_malformed(bad):
    with pytest.raises(ValueError):
        parse_extrinsic_id(bad)


def test_compare_matches_case_insensitively():
    assert compare(ROW, got()) == []


@pytest.mark.parametrize("field,value", [("block_hash", "0xff"), ("extrinsic_hash", "0x00"), ("outcome", "ok")])
def test_compare_reports_each_difference(field, value):
    diffs = compare(ROW, got(**{field: value}))
    assert len(diffs) == 1 and diffs[0].startswith(field)


def test_compare_missing_recorded_field_is_a_difference():
    assert compare({"extrinsic_id": "85-2"}, got())


def test_extrinsic_hash_is_blake2_256():
    assert extrinsic_hash(b"") == "0x0e5751c026e543b2e8ab2eb06099daa1d1e5df47778f7787faab45cdf12fe3a8"


def test_call_name_includes_the_proxied_call():
    xt = SimpleNamespace(value={"call": {"call_module": "Proxy", "call_function": "proxy", "call_args": [
        {"name": "real", "value": "5F..."},
        {"name": "call", "value": {"call_module": "Balances", "call_function": "transfer_keep_alive"}}]}})
    assert _call_name(xt) == "Proxy.proxy(Balances.transfer_keep_alive)"


def test_call_name_plain_and_unknown():
    plain = SimpleNamespace(value={"call": {"call_module": "Proxy", "call_function": "announce", "call_args": []}})
    assert _call_name(plain) == "Proxy.announce"
    assert _call_name(SimpleNamespace(value=None)) == "?.?"


def test_refetch_reads_raw_announce_jsonl(tmp_path):
    import importlib.util
    import json
    from pathlib import Path

    spec = importlib.util.spec_from_file_location("refetch", Path(__file__).parent.parent / "scripts" / "refetch.py")
    refetch_mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(refetch_mod)
    f = tmp_path / "raw-announce-x.jsonl"
    f.write_text("\n".join(json.dumps(r) for r in (
        {"step": "setup", "vault": "v", "genesis_hash": "0xg"},
        {"step": "announce", "extrinsic_id": "7-1", "block_hash": "0xb", "extrinsic_hash": "0xt", "outcome": "ok",
         "genesis_hash": "0xg"})) + "\n")
    got = refetch_mod.load(f)
    assert got["status"] == "passed" and got["genesis_hash"] == "0xg"
    assert [r["extrinsic_id"] for r in got["rows"]] == ["7-1"]
    f.write_text(json.dumps({"extrinsic_id": "7-1", "genesis_hash": "0xa"}) + "\n"
                 + json.dumps({"extrinsic_id": "8-1", "genesis_hash": "0xb"}) + "\n")
    with pytest.raises(SystemExit):
        refetch_mod.load(f)
