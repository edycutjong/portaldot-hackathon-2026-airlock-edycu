import pytest

from airlock.chain_profile import (
    INTERIM_1002,
    INTERIM_SDK,
    V3,
    UnknownChain,
    identify,
    median_block_time_ms,
    missing_proxy_calls,
    event_field,
    pure_address,
    transferable,
)


def test_identify_interim_sdk():
    assert identify(INTERIM_SDK.genesis_hash, INTERIM_SDK.code_hashes[0]) is INTERIM_SDK


def test_identify_interim_1002_is_case_insensitive():
    assert identify(INTERIM_1002.genesis_hash.upper().replace("0X", "0x"), INTERIM_1002.code_hashes[0]) is INTERIM_1002


def test_missing_hashes_fail_loudly():
    with pytest.raises(UnknownChain, match="did not report"):
        identify(None, INTERIM_SDK.code_hashes[0])
    with pytest.raises(UnknownChain, match="did not report"):
        identify(INTERIM_SDK.genesis_hash, None)


def test_median_block_time():
    assert median_block_time_ms([0, 3000, 6000, 9500, 12000]) == 3000
    assert median_block_time_ms([5]) is None


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
    # keep-alive: ED is covered by the 400 already locked, so nothing more is held back
    assert transferable(data, existential_deposit=10, keep_alive=True) == 600


def test_transferable_1002_keep_alive_counts_reserved_toward_ed():
    # 2021 pallet_balances: must_remain = ED - (total - liquid); reserved 50 already exceeds ED 10
    assert transferable({"free": 100, "reserved": 50, "misc_frozen": 0, "fee_frozen": 0},
                        existential_deposit=10, keep_alive=True) == 100
    assert transferable({"free": 100, "reserved": 0, "misc_frozen": 0, "fee_frozen": 0},
                        existential_deposit=10, keep_alive=True) == 90


def test_transferable_never_negative():
    assert transferable({"free": 10, "reserved": 0, "frozen": 50, "flags": 0}) == 0


def test_transferable_rejects_unknown_layout():
    with pytest.raises(ValueError):
        transferable({"free": 1})


def test_pure_address_from_named_and_positional_attributes():
    assert pure_address(INTERIM_SDK, {"pure": "5Vault", "who": "5Cold"}) == "5Vault"
    assert pure_address(INTERIM_1002, {"anonymous": "5Vault", "who": "5Cold"}) == "5Vault"
    assert pure_address(INTERIM_1002, ["5Vault", "5Cold", "Any", 0]) == "5Vault"
    # what the 1002 runtime actually decodes to under the legacy preset
    assert pure_address(INTERIM_1002, [{"type": "AccountId", "value": "5Vault"}, {"type": "AccountId", "value": "5Cold"}]) == "5Vault"


def test_event_field_unwraps_legacy_and_reports_missing():
    assert event_field({"result": {"Ok": None}}, "result", 0) == {"Ok": None}
    assert event_field([{"type": "DispatchResult", "value": {"Ok": None}}], "result", 0) == {"Ok": None}
    with pytest.raises(ValueError):
        event_field([], "result", 0)
