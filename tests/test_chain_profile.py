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
    big = "123456789012345678901234.5"  # 36 digits in planck: past a default 28-digit context, inside u128
    assert to_planck(big, 12) == 1234567890123456789012345 * 10**11
    assert to_planck(2**128 - 1, 0) == 2**128 - 1


@pytest.mark.parametrize("bad", ["1e-20", "-1", "inf", "nan", "abc", "1_000", "1e1000000", "1e400"])
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
        if url == "ws://down" or (url == "ws://flaky" and kwargs.get("type_registry_preset")):
            raise ConnectionRefusedError("refused")
        self.url, self.closed = url, False
        _FakeSubstrate.instances.append(self)

    def rpc_request(self, method, params):
        result = {
            "chain_getBlockHash": {"ws://other": "0x" + "ab" * 32,
                                   "ws://flaky": INTERIM_1002.genesis_hash}.get(self.url, INTERIM_SDK.genesis_hash),
            "state_getStorageHash": INTERIM_1002.code_hashes[0] if self.url == "ws://flaky" else INTERIM_SDK.code_hashes[0],
            "state_getRuntimeVersion": {"specName": "node", "specVersion": 999},  # not the verified 268
        }[method]
        return {"result": result}

    def init_runtime(self):
        pass

    def close(self):
        self.closed = True


def test_connect_clears_the_handshake_timeout(monkeypatch):
    """Once connected, neither the live socket nor a reconnect may keep the handshake timeout."""
    import substrateinterface

    from airlock.chain_profile import CONNECT_TIMEOUT_S, Chain, connect

    seen = {}

    class Socket:
        def settimeout(self, value):
            seen["socket"] = value

    class Connected(_FakeSubstrate):
        def __init__(self, url, **kwargs):
            super().__init__(url, **kwargs)
            self.ws_options = dict(kwargs.get("ws_options") or {})
            seen["opened_with"] = self.ws_options.get("timeout")
            self.websocket = Socket()

    import airlock.chain_profile as cp

    monkeypatch.setattr(substrateinterface, "SubstrateInterface", Connected)
    monkeypatch.setattr(cp, "_resolve", lambda substrate, *a: Chain(substrate, cp.INTERIM_SDK, "ws://sdk", "", "", "node",
                                                                     268, 12, "UNIT", 3000, "test", True, 1))
    chain = connect("ws://sdk", "interim-sdk")
    assert seen == {"opened_with": CONNECT_TIMEOUT_S, "socket": None}
    assert "timeout" not in chain.substrate.ws_options


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


def test_connect_1002_reconnect_failure_is_typed_and_probe_closed(fake_substrate):
    # interim-1002 reconnects with the legacy preset after identification; that second connection can fail too
    from airlock.chain_profile import ChainUnreachable, connect

    with pytest.raises(ChainUnreachable):
        connect("ws://flaky", "interim-1002")
    assert all(s.closed for s in fake_substrate.instances)


def test_connect_times_out_on_a_silent_endpoint(monkeypatch):
    """A port that accepts TCP but never answers the websocket handshake must not hang forever."""
    import socket
    import threading
    import time

    import airlock.chain_profile as cp

    monkeypatch.setattr(cp, "CONNECT_TIMEOUT_S", 1)
    srv = socket.socket()
    srv.bind(("127.0.0.1", 0))
    srv.listen(1)
    held = []
    threading.Thread(target=lambda: held.append(srv.accept()), daemon=True).start()
    started = time.monotonic()
    with pytest.raises(cp.ChainUnreachable):
        cp.connect(f"ws://127.0.0.1:{srv.getsockname()[1]}")
    assert time.monotonic() - started < 10
    srv.close()


def test_fmt_amount_is_exact_for_large_balances():
    from airlock.chain_profile import fmt_amount
    assert fmt_amount(999_996_197_339_160_444_122, 12, "UNIT") == "999,996,197.3391 UNIT"
    assert fmt_amount(2**128 - 1, 14, "POT").startswith("3,402,823,669,209,384,634,633,746")
    assert fmt_amount(5, 0, "X") == "5.0000 X"
    assert fmt_amount(1, 12, "UNIT") == "0.0000 UNIT"


def test_at_risk_excludes_locks_for_both_layouts():
    from airlock.chain_profile import at_risk, frozen
    modern = {"free": 1_000, "reserved": 100, "frozen": 300, "flags": 0}
    assert at_risk(modern).transferable == 800 and at_risk(modern).frozen == 300
    legacy = {"free": 1_000, "reserved": 0, "misc_frozen": 400, "fee_frozen": 250}
    assert frozen(legacy) == 400 and at_risk(legacy).transferable == 600
    assert at_risk(modern).held_back == 200  # frozen 300, of which reserved 100 already covers part


def test_at_risk_1002_ignores_the_fee_lock():
    """A transfer from the vault is checked against misc_frozen only; the thief's fees come from the delegate."""
    from airlock.chain_profile import at_risk
    data = {"free": 1_000, "reserved": 0, "misc_frozen": 100, "fee_frozen": 700}
    assert at_risk(data).transferable == 900 and at_risk(data).held_back == 100


def test_frozen_refuses_unknown_layouts():
    from airlock.chain_profile import frozen
    with pytest.raises(ValueError):
        frozen({"free": 1})
