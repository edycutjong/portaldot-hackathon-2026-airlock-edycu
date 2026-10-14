import pytest

from airlock.chain_profile import (
    INTERIM_1002,
    INTERIM_SDK,
    V3,
    UnknownChain,
    identify,
    missing_proxy_calls,
    pure_address,
    transferable,
)


def test_identify_interim_sdk():
    assert identify(INTERIM_SDK.genesis_hash, INTERIM_SDK.code_hashes[0]) is INTERIM_SDK


def test_identify_interim_1002_is_case_insensitive():
    assert identify(INTERIM_1002.genesis_hash.upper().replace("0X", "0x"), INTERIM_1002.code_hashes[0]) is INTERIM_1002


def test_unknown_genesis_fails_loudly():
    with pytest.raises(UnknownChain, match="no Airlock profile"):
        identify("0x" + "11" * 32, "0x" + "22" * 32)


def test_known_genesis_with_unverified_runtime_fails_loudly():
    with pytest.raises(UnknownChain, match="not one Airlock has verified"):
        identify(INTERIM_SDK.genesis_hash, "0x" + "33" * 32)


def test_v3_stub_never_matches_until_filled():
    assert V3.genesis_hash is None and not V3.is_interim
    assert V3.label == "Portaldot V3 testnet"


def test_interim_labels_say_not_v3():
    for p in (INTERIM_SDK, INTERIM_1002):
        assert p.is_interim and "not V3" in p.label


def test_proxy_naming_per_profile():
    assert (INTERIM_SDK.create_pure_call, INTERIM_SDK.pure_created_event) == ("create_pure", "PureCreated")
    assert (INTERIM_1002.create_pure_call, INTERIM_1002.pure_created_event) == ("anonymous", "AnonymousCreated")
    # only the 1002 runtime needs decode overrides
    assert INTERIM_1002.type_registry_preset == "legacy" and INTERIM_SDK.type_registry_preset is None
    assert INTERIM_1002.polkadotjs_types == {"DispatchErrorModule": "DispatchErrorModuleU8"}
    assert not INTERIM_SDK.polkadotjs_types


def test_missing_proxy_calls_reports_gaps():
    modern = ["proxy", "add_proxy", "remove_proxy", "create_pure", "announce", "remove_announcement",
              "reject_announcement", "proxy_announced"]
    assert missing_proxy_calls(INTERIM_SDK, modern) == []
    # the 1002 profile needs `anonymous`, which a modern runtime does not have
    assert missing_proxy_calls(INTERIM_1002, modern) == ["anonymous"]
    assert missing_proxy_calls(INTERIM_SDK, ["proxy", "create_pure"]) == [
        "add_proxy", "remove_proxy", "announce", "remove_announcement", "reject_announcement", "proxy_announced"]


def test_transferable_modern_account_data():
    data = {"free": 1_000, "reserved": 100, "frozen": 300, "flags": 0}
    assert transferable(data) == 800  # frozen 300 is partly covered by the 100 reserved
    assert transferable(data, existential_deposit=250, keep_alive=True) == 750


def test_transferable_1002_account_data():
    data = {"free": 1_000, "reserved": 0, "misc_frozen": 200, "fee_frozen": 400}
    assert transferable(data) == 600


def test_transferable_never_negative():
    assert transferable({"free": 10, "reserved": 0, "frozen": 50, "flags": 0}) == 0


def test_transferable_rejects_unknown_layout():
    with pytest.raises(ValueError):
        transferable({"free": 1})


def test_pure_address_from_named_and_positional_attributes():
    assert pure_address(INTERIM_SDK, {"pure": "5Vault", "who": "5Cold"}) == "5Vault"
    assert pure_address(INTERIM_1002, {"anonymous": "5Vault", "who": "5Cold"}) == "5Vault"
    assert pure_address(INTERIM_1002, ["5Vault", "5Cold", "Any", 0]) == "5Vault"
