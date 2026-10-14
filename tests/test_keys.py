"""Key specs: dev URIs only on interim chains, secrets never from argv, never echoed in an error."""

import os

import pytest
from substrateinterface import Keypair

from airlock.chain_profile import INTERIM_SDK, V3
from airlock.keys import KeyRefused, load_key, secret_of

ALICE = "5GrwvaEF5zXb26Fz9rcQpDWS57CtERHpNehXCPcNoHGKutQY"
# the public Substrate dev phrase: every //Alice-style dev key derives from it, so it guards nothing
MNEMONIC = "bottom drive obey lake curtain smoke basket hold race lonely fit walk"


def test_dev_uri_accepted_on_interim():
    assert load_key("//Alice", INTERIM_SDK, "cold").ss58_address == ALICE


def test_dev_uri_refused_off_interim():
    with pytest.raises(KeyRefused, match="public dev key"):
        load_key("//Alice", V3, "cold")


def fresh_phrase():
    return Keypair.generate_mnemonic()  # generated per run, never stored


def test_file_key(tmp_path):
    phrase = fresh_phrase()
    f = tmp_path / "cold.seed"
    f.write_text(phrase + "//cold\n")
    os.chmod(f, 0o600)
    assert load_key(f"file:{f}", V3, "cold").ss58_address == Keypair.create_from_uri(phrase + "//cold").ss58_address


def test_dev_phrase_in_a_file_refused_off_interim(tmp_path):
    f = tmp_path / "k"
    f.write_text(MNEMONIC + "//cold")
    os.chmod(f, 0o600)
    with pytest.raises(KeyRefused, match="dev derivation"):
        load_key(f"file:{f}", V3, "cold")
    assert load_key(f"file:{f}", INTERIM_SDK, "cold").ss58_address == load_key("//cold", INTERIM_SDK, "x").ss58_address


def test_file_readable_by_others_warns(tmp_path, capsys):
    f = tmp_path / "cold.seed"
    f.write_text(fresh_phrase())
    os.chmod(f, 0o644)
    load_key(f"file:{f}", V3, "cold")
    assert "chmod 600" in capsys.readouterr().err


def test_file_holding_dev_uri_refused_off_interim(tmp_path):
    f = tmp_path / "k"
    f.write_text("//Alice")
    os.chmod(f, 0o600)
    with pytest.raises(KeyRefused, match="dev derivation"):
        secret_of(f"file:{f}", V3)


def test_missing_file_and_empty_env(tmp_path, monkeypatch):
    with pytest.raises(KeyRefused, match="cannot read key file"):
        load_key(f"file:{tmp_path / 'nope'}", V3, "cold")
    monkeypatch.delenv("AIRLOCK_TEST_SEED", raising=False)
    with pytest.raises(KeyRefused, match="empty or unset"):
        load_key("env:AIRLOCK_TEST_SEED", V3, "cold")


def test_env_key(monkeypatch):
    monkeypatch.setenv("AIRLOCK_TEST_SEED", fresh_phrase())
    assert load_key("env:AIRLOCK_TEST_SEED", V3, "hot").ss58_address.startswith("5")


def test_bare_mnemonic_refused_without_echo():
    with pytest.raises(KeyRefused) as e:
        load_key(MNEMONIC, V3, "cold")
    assert "shell history" in str(e.value)
    assert "lake" not in str(e.value)


def test_invalid_secret_not_echoed(monkeypatch):
    monkeypatch.setenv("AIRLOCK_TEST_SEED", "not a real phrase sesame")
    with pytest.raises(KeyRefused) as e:
        load_key("env:AIRLOCK_TEST_SEED", V3, "cold")
    assert "sesame" not in str(e.value)


def test_no_spec_names_the_env_var():
    with pytest.raises(KeyRefused, match=r"\$AIRLOCK_COLD_KEY"):
        load_key(None, INTERIM_SDK, "cold")
