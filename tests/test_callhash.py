"""Call-hash tests against recorded runtime metadata (tests/fixtures), no node needed.

The two I3 vectors are the hashes `Proxy.Announcements(//Bob)` actually stored on-chain by the spike runs (step
"hot announces theft"; the hash is in that row's note in docs/PROOF.md and evidence/*.json): recomputing them offline
from the recorded metadata must give the same hash.
"""

import pytest
from substrateinterface import Keypair

from airlock.callhash import (
    Announcement,
    ReviveUnavailable,
    blake2_256,
    call_bytes,
    call_hash,
    is_announced,
    normalize_announcements,
    revive_call,
    same_account,
)
from airlock.offline import load

EVE = Keypair.create_from_uri("//Eve").ss58_address
VAULT = "5DsWFnKeSyDzjFrduzfd5SsxRc9VtEcmECnBorTRyuKoK3AF"
TOKEN = "0x" + "11" * 20
WEIGHT = {"ref_time": 1_000_000_000, "proof_size": 100_000}
ERC20_TRANSFER = "0xa9059cbb" + "00" * 12 + "22" * 20 + "%064x" % 5


@pytest.fixture(scope="module")
def sdk():
    return load("interim-sdk")


@pytest.fixture(scope="module")
def p1002():
    return load("interim-1002")


def theft(rt, decimals, dest=EVE, amount=100):
    return rt.compose_call("Balances", "transfer_keep_alive", {"dest": dest, "value": amount * 10**decimals})


def test_tampered_fixture_is_refused(tmp_path):
    import shutil

    from airlock.offline import FIXTURES

    for name in ("interim-sdk.json", "interim-sdk.metadata.scale.gz"):
        shutil.copy(FIXTURES / name, tmp_path / name)
    info = (tmp_path / "interim-sdk.json").read_text().replace('"metadata_sha256": "', '"metadata_sha256": "00')
    (tmp_path / "interim-sdk.json").write_text(info)
    with pytest.raises(ValueError, match="sha256"):
        load("interim-sdk", tmp_path)


def test_blake2_256_known_vector():
    assert blake2_256(b"") == "0x0e5751c026e543b2e8ab2eb06099daa1d1e5df47778f7787faab45cdf12fe3a8"


def test_i3_interim_sdk_matches_on_chain_announcement(sdk):
    assert call_hash(theft(sdk, 12)) == "0xf4c3c3361dcfb6b8193c568bd42e6ab4d2731097b285f573e10e7d2f7e55478a"


def test_i3_interim_1002_matches_on_chain_announcement(p1002):
    assert call_hash(theft(p1002, 14)) == "0x5b11231612a3699c8009e8abb7b35314a73a8f28088221aa1a5d1a92bd2222fa"


def test_call_hash_same_for_object_bytes_and_hex(sdk):
    call = theft(sdk, 12)
    raw = call_bytes(call)
    assert call_hash(call) == call_hash(raw) == call_hash("0x" + raw.hex())


def test_call_hash_of_a_decoded_call_ignores_trailing_bytes(sdk):
    from scalecodec.base import ScaleBytes

    raw = call_bytes(theft(sdk, 12))
    decoded = sdk.runtime_config.create_scale_object("Call", data=ScaleBytes(raw + b"\x01\x02\x03"), metadata=sdk.metadata)
    decoded.decode(check_remaining=False)  # as when a call is read out of a block or a batch
    assert call_bytes(decoded) == raw and call_hash(decoded) == call_hash(raw)


def test_call_hash_rejects_non_calls():
    with pytest.raises(TypeError):
        call_hash(42)
    with pytest.raises(ValueError):
        call_hash("deadbeef")


def test_amount_and_destination_change_the_hash(sdk):
    base = call_hash(theft(sdk, 12))
    assert call_hash(theft(sdk, 12, amount=101)) != base
    assert call_hash(theft(sdk, 12, dest=Keypair.create_from_uri("//Ferdie").ss58_address)) != base


def test_wrapper_hash_differs_from_inner_call(sdk):
    inner = theft(sdk, 12)
    wrapped = sdk.compose_call("Proxy", "proxy", {"real": VAULT, "force_proxy_type": None, "call": inner})
    assert call_hash(wrapped) != call_hash(inner)  # announce the inner call, never the wrapper


def test_revive_call_encodes_on_interim_sdk(sdk):
    call = revive_call(sdk, TOKEN, 0, WEIGHT, 10**12, ERC20_TRANSFER)
    raw = call_bytes(call)
    revive_index = next(p["index"] for p in sdk.metadata.pallets if p.name == "Revive").value
    assert raw[0] == revive_index and raw[1] == 1  # Revive pallet, call index 1 (`call`)
    assert raw[2:22] == bytes.fromhex("11" * 20)  # dest H160 follows directly
    assert raw.endswith(bytes.fromhex(ERC20_TRANSFER[2:]))  # calldata is the tail
    assert call_hash(call) == call_hash(revive_call(sdk, TOKEN, 0, WEIGHT, 10**12, bytes.fromhex(ERC20_TRANSFER[2:])))


def test_revive_gas_limits_are_part_of_the_hash(sdk):
    base = call_hash(revive_call(sdk, TOKEN, 0, WEIGHT, 10**12, ERC20_TRANSFER))
    more_gas = dict(WEIGHT, ref_time=WEIGHT["ref_time"] + 1)
    assert call_hash(revive_call(sdk, TOKEN, 0, more_gas, 10**12, ERC20_TRANSFER)) != base
    assert call_hash(revive_call(sdk, TOKEN, 0, WEIGHT, 10**12 + 1, ERC20_TRANSFER)) != base


def test_revive_unavailable_on_1002(p1002):
    assert not p1002.has_call("Revive", "call")
    with pytest.raises(ReviveUnavailable):
        revive_call(p1002, TOKEN, 0, WEIGHT, 10**12, ERC20_TRANSFER)


def test_normalize_announcements_storage_shapes():
    entry = {"real": VAULT, "call_hash": "0xABCD", "height": 7}
    expected = [Announcement(VAULT, "0xabcd", 7)]
    assert normalize_announcements(([entry], 459)) == expected
    assert normalize_announcements([entry]) == expected
    wrapped = [{"type": "AccountId", "value": VAULT}, {"type": "Hash", "value": "0xabcd"}, {"type": "u32", "value": 7}]
    assert normalize_announcements(([wrapped], 459)) == expected
    assert normalize_announcements(None) == [] and normalize_announcements(([], 0)) == []
    # a bare list of exactly two positional entries is two announcements, not (announcements, deposit)
    two = [[VAULT, "0x01", 1], [VAULT, "0x02", 2]]
    assert [a.call_hash for a in normalize_announcements(two)] == ["0x01", "0x02"]


def test_is_announced_matches_vault_and_exact_call(sdk):
    call = theft(sdk, 12)
    pending = [Announcement(VAULT, call_hash(call), 106)]
    assert is_announced(pending, VAULT, call)
    assert not is_announced(pending, "5Other", call)
    assert not is_announced(pending, VAULT, theft(sdk, 12, amount=99))


def test_same_vault_under_another_ss58_prefix_still_matches(sdk):
    from substrateinterface.utils.ss58 import ss58_decode, ss58_encode

    call = theft(sdk, 12)
    as_polkadot = ss58_encode(ss58_decode(VAULT), ss58_format=0)
    assert as_polkadot != VAULT and same_account(as_polkadot, VAULT)
    assert is_announced([Announcement(as_polkadot, call_hash(call), 1)], VAULT, call)
    assert same_account("0x" + ss58_decode(VAULT), VAULT)
