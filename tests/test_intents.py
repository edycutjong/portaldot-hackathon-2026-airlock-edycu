"""The owner intent registry's own logic: which announcement an intent vouches for (YOURS), and why everything else
stays NOT YOURS (I6) — a foreign or replayed preimage, a late registration, another vault; and who may enrol a token."""

import time

import pytest
from substrateinterface import Keypair

from airlock.callhash import blake2_256
from airlock.intents import (ENROLL_SKEW_S, Registry, RegistryError, enroll_message, load_token, new_token,
                             save_token, token_digest)
from airlock.store import INTENT_WINDOW_BLOCKS, Store

COLD = Keypair.create_from_uri("//Alice")
HOT = Keypair.create_from_uri("//Bob")
THIEF = Keypair.create_from_uri("//Eve")
VAULT = "5Hb37SqmVJSzREN7ziBw1ZQJWLgDS7DLaEjKisf8a6KUYsLv"
OTHER_VAULT = "5EYCAe5ijiYfyeZ2JM3THnX8Wxo1XxR1JQ7y3tsk7m5uNvEu"
GENESIS = "0x" + "0c" * 32
CALL = bytes.fromhex("0603008eaf04151687736326c9fea17e25fc5287613693c912909cb226aa4794f26a480b00a0724e1809")
H = blake2_256(CALL)


class Chain:
    """What the registry reads from the chain: the best block, who is a cold (delay-0) delegate, a call decoder."""

    def __init__(self):
        self.head = 100
        self.delegates = {VAULT: {COLD.ss58_address: 0, HOT.ss58_address: 5}, OTHER_VAULT: {THIEF.ss58_address: 0}}

    def delay_of(self, vault, account):
        return self.delegates.get(vault, {}).get(account)


@pytest.fixture
def env(tmp_path):
    store = Store(tmp_path / "r.db")
    chain = Chain()
    reg = Registry(store=store, genesis_hash=GENESIS, head=lambda: chain.head, delay_of_signer=chain.delay_of,
                   describe=lambda raw: "Balances.transfer_keep_alive(dest=…, value=1.0000 UNIT)")
    return store, chain, reg


def signed(token, vault=VAULT, signer=COLD, issued_at=None, genesis=GENESIS):
    issued_at = int(time.time()) if issued_at is None else issued_at
    digest = token_digest(token)
    sig = signer.sign(enroll_message(genesis, vault, digest, issued_at)).hex()
    return {"cold": signer.ss58_address, "token_sha256": digest, "issued_at": issued_at, "signature": "0x" + sig}


def enrolled(reg, vault=VAULT):
    token = new_token()
    reg.enroll(vault, signed(token, vault))
    return token


# -- enrolment: only the vault's cold key can bind a token --------------------------------------------------------

def test_cold_key_enrols_and_only_the_digest_is_stored(env):
    store, _, reg = env
    token = enrolled(reg)
    owner = store.owner(VAULT)
    assert owner.token_sha256 == token_digest(token) and owner.cold == COLD.ss58_address
    assert token not in str(store.db.execute("SELECT * FROM owner").fetchall())


def test_hot_key_cannot_enrol_it_is_a_delayed_delegate(env):
    _, _, reg = env
    with pytest.raises(RegistryError) as e:
        reg.enroll(VAULT, signed(new_token(), signer=HOT))
    assert e.value.status == 401 and "delay-0" in str(e.value)


def test_cold_key_of_another_vault_cannot_enrol_this_one(env):
    _, _, reg = env
    with pytest.raises(RegistryError) as e:
        reg.enroll(VAULT, signed(new_token(), signer=THIEF))
    assert e.value.status == 401


@pytest.mark.parametrize("tamper", ["signature", "digest", "vault", "genesis"])
def test_a_signature_over_anything_else_does_not_verify(env, tamper):
    _, _, reg = env
    body = signed(new_token(), genesis="0x" + "99" * 32 if tamper == "genesis" else GENESIS,
                  vault=OTHER_VAULT if tamper == "vault" else VAULT)
    if tamper == "signature":
        body["signature"] = "0x" + "00" * 64
    if tamper == "digest":
        body["token_sha256"] = token_digest("something else")
    with pytest.raises(RegistryError) as e:
        reg.enroll(VAULT, body)
    assert e.value.status == 401


def test_stale_enrolment_is_refused(env):
    _, _, reg = env
    with pytest.raises(RegistryError) as e:
        reg.enroll(VAULT, signed(new_token(), issued_at=int(time.time()) - ENROLL_SKEW_S - 5))
    assert e.value.status == 401


def test_replayed_older_enrolment_cannot_roll_the_token_back(env):
    store, _, reg = env
    old_token, new = new_token(), new_token()
    old_body = signed(old_token, issued_at=int(time.time()) - 10)
    reg.enroll(VAULT, old_body)
    reg.enroll(VAULT, signed(new, issued_at=int(time.time())))
    with pytest.raises(RegistryError) as e:
        reg.enroll(VAULT, old_body)
    assert e.value.status == 409 and store.owner(VAULT).token_sha256 == token_digest(new)


@pytest.mark.parametrize("body", [{}, {"cold": "x"}, {"cold": COLD.ss58_address, "token_sha256": "ab",
                                                      "issued_at": 1, "signature": "0x"}])
def test_malformed_enrolment_is_400_or_401(env, body):
    with pytest.raises(RegistryError) as e:
        env[2].enroll(VAULT, body)
    assert e.value.status in (400, 401)


# -- registration: the owner token, and the hash the server computes itself -----------------------------------------

def test_register_needs_the_owner_token(env):
    _, _, reg = env
    enrolled(reg)
    for token in (None, "", "guess", new_token()):
        with pytest.raises(RegistryError) as e:
            reg.register(token, {"vault": VAULT, "call_hex": "0x" + CALL.hex()})
        assert e.value.status == 401


def test_register_on_an_unenrolled_vault_is_401_not_404(env):
    _, _, reg = env
    token = enrolled(reg)
    with pytest.raises(RegistryError) as e:
        reg.register(token, {"vault": OTHER_VAULT, "call_hex": "0x" + CALL.hex()})
    assert e.value.status == 401


def test_server_recomputes_the_hash_and_rejects_a_mismatch(env):
    _, _, reg = env
    token = enrolled(reg)
    with pytest.raises(RegistryError) as e:
        reg.register(token, {"vault": VAULT, "call_hex": "0x" + CALL.hex(), "call_hash": "0x" + "ab" * 32})
    assert e.value.status == 400
    got = reg.register(token, {"vault": VAULT, "call_hex": "0x" + CALL.hex(), "call_hash": H.upper().replace("0X", "0x")})
    assert got["call_hash"] == H and got["registered_head"] == 100 and got["summary"].startswith("Balances.")


@pytest.mark.parametrize("call_hex", ["", "abcd", "0xzz", "0x", 7, None, "0x" + "00" * 20000])
def test_bad_call_hex_is_400(env, call_hex):
    _, _, reg = env
    token = enrolled(reg)
    with pytest.raises(RegistryError) as e:
        reg.register(token, {"vault": VAULT, "call_hex": call_hex})
    assert e.value.status == 400


def test_undecodable_call_is_400(env):
    store, chain, _ = env
    reg = Registry(store=store, genesis_hash=GENESIS, head=lambda: chain.head, delay_of_signer=chain.delay_of,
                   describe=lambda raw: (_ for _ in ()).throw(ValueError("no such call")))
    token = enrolled(reg)
    with pytest.raises(RegistryError) as e:
        reg.register(token, {"vault": VAULT, "call_hex": "0xffff"})
    assert e.value.status == 400 and store.intents(VAULT) == []


# -- intent match: which announcement is YOURS ---------------------------------------------------------------------

def registered(env, head=100):
    store, chain, reg = env
    chain.head = head
    if not hasattr(reg, "token"):
        reg.token = enrolled(reg)
    return reg.register(reg.token, {"vault": VAULT, "call_hex": "0x" + CALL.hex()})


def test_registered_then_announced_is_yours(env):
    registered(env)
    intent = env[0].match_intent(VAULT, H, HOT.ss58_address, 101)
    assert intent is not None and intent.call_hex == "0x" + CALL.hex() and intent.summary.startswith("Balances.")


def test_same_announcement_rescanned_is_still_yours(env):
    registered(env)
    first = env[0].match_intent(VAULT, H, HOT.ss58_address, 101)
    assert env[0].match_intent(VAULT, H, HOT.ss58_address, 101) == first


def test_replay_of_a_used_intent_is_not_yours(env):
    """The owner paid Bob once; a thief re-announcing the very same call later gets no vouching."""
    registered(env)
    assert env[0].match_intent(VAULT, H, HOT.ss58_address, 101) is not None
    assert env[0].match_intent(VAULT, H, HOT.ss58_address, 140) is None


def test_two_registrations_vouch_for_two_announcements(env):
    registered(env)
    registered(env, head=101)  # the same call registered again (a second payment of the same amount)
    assert env[0].match_intent(VAULT, H, HOT.ss58_address, 102) is not None
    assert env[0].match_intent(VAULT, H, HOT.ss58_address, 103) is not None
    assert env[0].match_intent(VAULT, H, HOT.ss58_address, 104) is None


def test_registered_after_the_announcement_is_not_yours(env):
    registered(env, head=120)
    assert env[0].match_intent(VAULT, H, HOT.ss58_address, 120) is None
    assert env[0].match_intent(VAULT, H, HOT.ss58_address, 110) is None


def test_intent_expires_after_the_window(env):
    registered(env)
    assert env[0].match_intent(VAULT, H, HOT.ss58_address, 100 + INTENT_WINDOW_BLOCKS + 1) is None
    assert env[0].match_intent(VAULT, H, HOT.ss58_address, 100 + INTENT_WINDOW_BLOCKS) is not None


def test_foreign_preimage_on_another_vault_is_not_yours(env):
    """Same call bytes, same hash — but registered for a different vault."""
    registered(env)
    assert env[0].match_intent(OTHER_VAULT, H, HOT.ss58_address, 101) is None


def test_unregistered_hash_is_not_yours(env):
    registered(env)
    assert env[0].match_intent(VAULT, "0x" + "ab" * 32, HOT.ss58_address, 101) is None


def test_vault_spelled_with_another_ss58_prefix_matches(env):
    from substrateinterface.utils.ss58 import ss58_decode, ss58_encode

    registered(env)
    generic = ss58_encode(ss58_decode(VAULT), 0)
    assert generic != VAULT and env[0].match_intent(generic, H, HOT.ss58_address, 101) is not None


# -- the CLI's token file ----------------------------------------------------------------------------------------

def test_token_file_is_owner_only_and_loads(tmp_path):
    path = tmp_path / "t" / "v.token"
    save_token(path, "secret-token")
    assert oct(path.stat().st_mode & 0o777) == "0o600"
    assert load_token(f"file:{path}", VAULT) == "secret-token"


def test_token_on_the_command_line_is_refused(monkeypatch):
    with pytest.raises(RegistryError):
        load_token("raw-token-value", VAULT)
    monkeypatch.setenv("TOK", "from-env")
    assert load_token("env:TOK", VAULT) == "from-env"


def test_missing_default_token_names_the_enrol_command(monkeypatch, tmp_path):
    import airlock.intents as intents

    monkeypatch.setattr(intents, "TOKEN_DIR", tmp_path)
    with pytest.raises(RegistryError) as e:
        load_token(None, VAULT)
    assert "airlock enroll" in str(e.value)


def test_save_token_replaces_a_world_readable_file_without_writing_through_it(tmp_path):
    import os

    path = tmp_path / "v.token"
    path.write_text("old")
    os.chmod(path, 0o644)
    target = tmp_path / "elsewhere"
    target.write_text("untouched")
    link = tmp_path / "l.token"
    link.symlink_to(target)
    save_token(path, "new-token")
    save_token(link, "new-token")
    assert oct(path.stat().st_mode & 0o777) == "0o600" and path.read_text().strip() == "new-token"
    assert target.read_text() == "untouched" and not link.is_symlink()
    assert not list(tmp_path.glob(".*.tmp"))


def test_loading_a_wide_open_token_warns(tmp_path, capsys):
    import os

    path = tmp_path / "w.token"
    path.write_text("tok\n")
    os.chmod(path, 0o644)
    assert load_token(f"file:{path}", VAULT) == "tok" and "chmod 600" in capsys.readouterr().err
