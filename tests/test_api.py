"""The registry over real HTTP on a local port: 401 without the owner token, the hash recomputed server-side, request
limits, and the client helpers the CLI uses (enrol, register-before-announce)."""

import http.client
import json
import threading

import pytest

from airlock.api import serve
from airlock.intents import Registry, RegistryError, enroll, new_token, post, register_intent
from airlock.store import Store
from tests.test_intents import CALL, COLD, GENESIS, H, HOT, VAULT, Chain


@pytest.fixture
def api(tmp_path):
    store = Store(tmp_path / "api.db")
    chain = Chain()
    reg = Registry(store=store, genesis_hash=GENESIS, head=lambda: chain.head, delay_of_signer=chain.delay_of,
                   describe=lambda raw: "Balances.transfer_keep_alive(…)")
    server = serve(reg, {"profile": "test", "genesis_hash": GENESIS}, "127.0.0.1", 0, log=lambda m: None)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    url = f"http://127.0.0.1:{server.server_port}"
    yield url, store, server
    server.shutdown()
    server.server_close()


def raw(url, method, path, body=None, headers=None):
    host, port = url.removeprefix("http://").split(":")
    c = http.client.HTTPConnection(host, int(port), timeout=5)
    c.request(method, path, body=body, headers=headers or {})
    r = c.getresponse()
    data = r.read()
    c.close()
    return r.status, json.loads(data or b"{}")


def owner_token(url):
    token = new_token()
    enroll(url, COLD, VAULT, GENESIS, token)
    return token


def test_health(api):
    status, body = raw(api[0], "GET", "/api/health")
    assert status == 200 and body["best_block"] == 100 and body["genesis_hash"] == GENESIS


def test_intent_without_token_is_401(api):
    url = api[0]
    owner_token(url)
    body = json.dumps({"vault": VAULT, "call_hex": "0x" + CALL.hex()})
    assert raw(url, "POST", "/api/intents", body, {"Content-Type": "application/json"})[0] == 401
    assert raw(url, "POST", "/api/intents", body, {"Authorization": "Bearer wrong"})[0] == 401
    assert raw(url, "POST", "/api/intents", body, {"Authorization": "Basic abc"})[0] == 401
    assert api[1].intents(VAULT) == []


def test_thief_posting_the_preimage_cannot_make_it_yours(api):
    """The thief knows his own call's bytes; posting them without the owner token stores nothing."""
    url, store, _ = api
    owner_token(url)
    with pytest.raises(RegistryError) as e:
        post(url, "/api/intents", {"vault": VAULT, "call_hex": "0x" + CALL.hex(), "call_hash": H})
    assert e.value.status == 401 and store.intents(VAULT) == []
    assert store.match_intent(VAULT, H, HOT.ss58_address, 101) is None


def test_owner_registers_and_the_intent_is_stored(api):
    url, store, _ = api
    token = owner_token(url)
    got = register_intent(url, token, VAULT, CALL)
    assert got["call_hash"] == H and got["registered_head"] == 100
    assert [i["call_hash"] for i in store.intents(VAULT)] == [H]


def test_mismatched_hash_is_400(api):
    url = api[0]
    token = owner_token(url)
    with pytest.raises(RegistryError) as e:
        post(url, "/api/intents", {"vault": VAULT, "call_hex": "0x" + CALL.hex(), "call_hash": "0x" + "00" * 32}, token)
    assert e.value.status == 400


@pytest.mark.parametrize("body, want", [(b"not json", 400), (b"[1,2]", 400), (b"x" * (64 * 1024 + 1), 413)])
def test_bad_bodies(api, body, want):
    url = api[0]
    token = owner_token(url)
    status, reply = raw(url, "POST", "/api/intents", body, {"Authorization": f"Bearer {token}"})
    assert status == want and "error" in reply


def test_enrolment_by_the_hot_key_is_401_over_http(api):
    url = api[0]
    with pytest.raises(RegistryError) as e:
        enroll(url, HOT, VAULT, GENESIS, new_token())
    assert e.value.status == 401 and api[1].owner(VAULT) is None


def test_unknown_paths_are_404(api):
    assert raw(api[0], "GET", "/api/intents")[0] == 404
    assert raw(api[0], "POST", "/api/vaults/not-an-address/owner", b"{}")[0] == 404


def test_registry_down_is_status_0(api):
    url, _, server = api
    token = owner_token(url)
    server.shutdown()
    server.server_close()
    with pytest.raises(RegistryError) as e:
        register_intent(url, token, VAULT, CALL, timeout_s=2)
    assert e.value.status == 0


def test_registry_answering_for_another_hash_is_not_trusted(monkeypatch):
    import airlock.intents as intents

    monkeypatch.setattr(intents, "post", lambda *a, **k: {"call_hash": "0x" + "ab" * 32})
    with pytest.raises(RegistryError):
        register_intent("http://x", "t", VAULT, CALL)


def test_thief_script_posting_its_preimage_gets_401(api):
    from tests.test_seed_fixtures import load_script

    url, store, _ = api
    owner_token(url)
    thief = load_script("thief")
    assert thief.post_intent(url, VAULT, CALL, H) == 401
    assert store.intents(VAULT) == [] and store.match_intent(VAULT, H, HOT.ss58_address, 101) is None


@pytest.mark.parametrize("profile_id", ["interim-sdk", "interim-1002"])
def test_describe_call_against_recorded_metadata(profile_id):
    """The decoder the registry uses, on both interim runtimes (decoded args arrive as a list of name/value)."""
    from airlock import offline
    from airlock.api import describe_call
    from airlock.callhash import call_bytes, same_account
    from airlock.chain_profile import PROFILES, Chain

    rt = offline.load(profile_id)
    chain = Chain(substrate=rt, profile=PROFILES[profile_id], url="offline", genesis_hash="0x", code_hash="0x",
                  spec_name="x", spec_version=rt.spec_version, decimals=12, symbol="UNIT", block_time_ms=3000,
                  block_time_source="test", has_revive=False, existential_deposit=1)
    raw = call_bytes(rt.compose_call("Balances", "transfer_keep_alive",
                                     {"dest": "5C5vdpsPjL7KwZySdTNBxVRJ5os7qQBwrkU7mu4fA61jCryJ", "value": 1_500_000_000_000}))
    text = describe_call(chain, raw)
    dest = text.split("dest=")[1].split(",")[0]  # SS58 from a live node; the offline registry has no SS58 format
    assert same_account(dest, "5C5vdpsPjL7KwZySdTNBxVRJ5os7qQBwrkU7mu4fA61jCryJ")
    assert text.startswith("Balances.transfer_keep_alive(dest=") and text.endswith(", value=1.5000 UNIT)")
    with pytest.raises(Exception):  # trailing bytes: the decoder itself refuses (RemainingScaleBytesNotEmpty)
        describe_call(chain, raw + b"\x00")
    with pytest.raises(Exception):
        describe_call(chain, b"\xff\xff")
