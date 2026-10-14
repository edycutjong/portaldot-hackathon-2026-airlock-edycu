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


# --- exact token units -------------------------------------------------------------------------------------------

def test_to_planck_is_exact():
    from airlock.chain_profile import to_planck

    assert to_planck(100, 12) == 100 * 10**12
    assert to_planck("0.5", 14) == 5 * 10**13
    big = "123456789012345678901234567890.5"
    assert to_planck(big, 12) == 1234567890123456789012345678905 * 10**11


@pytest.mark.parametrize("bad", ["1e-20", "-1", "inf", "nan", "abc"])
def test_to_planck_refuses_bad_amounts(bad):
    from airlock.chain_profile import to_planck

    with pytest.raises(ValueError):
        to_planck(bad, 14)


# --- block time: measured fallback when the runtime has no consensus constant ------------------------------------

class _NoConstantChain:
    """Fake substrate: no Babe/Aura constant, blocks 3 s apart with one 9 s gap."""

    stamps = [0, 3000, 6000, 15000, 18000, 21000]

    def get_constant(self, pallet, const):
        return None

    def get_block_number(self, _):
        return len(self.stamps)

    def get_block_hash(self, n):
        return n

    def query(self, pallet, item, block_hash):
        from types import SimpleNamespace

        return SimpleNamespace(value=self.stamps[block_hash - 1])


def test_block_time_measured_when_no_constant():
    from airlock.chain_profile import _block_time

    assert _block_time(_NoConstantChain()) == (3000, "measured (median Timestamp.Now delta)")


# --- connect(): failures are loud, typed, and never leak the socket ---------------------------------------------

class _FakeSubstrate:
    instances: list = []

    def __init__(self, url, **kwargs):
        if url == "ws://down":
            raise ConnectionRefusedError("refused")
        self.url, self.closed = url, False
        _FakeSubstrate.instances.append(self)

    def rpc_request(self, method, params):
        result = {
            "chain_getBlockHash": INTERIM_SDK.genesis_hash if self.url != "ws://other" else "0x" + "ab" * 32,
            "state_getStorageHash": INTERIM_SDK.code_hashes[0],
            "state_getRuntimeVersion": {"specName": "node", "specVersion": 999},  # not the verified 268
        }[method]
        return {"result": result}

    def init_runtime(self):
        pass

    def close(self):
        self.closed = True


@pytest.fixture
def fake_substrate(monkeypatch):
    import substrateinterface

    _FakeSubstrate.instances = []
    monkeypatch.setattr(substrateinterface, "SubstrateInterface", _FakeSubstrate)
    monkeypatch.delenv("AIRLOCK_PROFILE", raising=False)
    monkeypatch.delenv("AIRLOCK_WSS", raising=False)
    return _FakeSubstrate


def test_connect_unreachable_is_typed(fake_substrate):
    from airlock.chain_profile import ChainUnreachable, connect

    with pytest.raises(ChainUnreachable):
        connect("ws://down")


def test_connect_unknown_chain_closes_probe(fake_substrate):
    from airlock.chain_profile import connect

    with pytest.raises(UnknownChain):
        connect("ws://other")
    assert all(s.closed for s in fake_substrate.instances)


def test_connect_wrong_profile_closes_probe(fake_substrate):
    from airlock.chain_profile import ProfileMismatch, connect

    with pytest.raises(ProfileMismatch, match="not the requested interim-1002"):
        connect("ws://sdk", "interim-1002")
    assert all(s.closed for s in fake_substrate.instances)


def test_connect_runtime_spec_mismatch_closes_connection(fake_substrate):
    from airlock.chain_profile import connect

    with pytest.raises(UnknownChain, match="expects runtime"):
        connect("ws://sdk", "interim-sdk")
    assert fake_substrate.instances and all(s.closed for s in fake_substrate.instances)
