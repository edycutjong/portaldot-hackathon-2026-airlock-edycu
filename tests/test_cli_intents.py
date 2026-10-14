"""Register-before-announce through the CLI: the intent reaches the registry before proxy.announce is signed, and when
the registry is down, refuses, dies mid-request or hangs, NOTHING is announced (exit 2) — so the owner's own spend can
never alert NOT YOURS. Plus `airlock enroll`."""

import socket
import threading

import pytest

from airlock import cli
from airlock.api import serve
from airlock.intents import Registry, enroll, new_token, save_token
from airlock.store import Store
from tests.test_cli import fake_connect
from airlock.chain_profile import INTERIM_SDK
from tests.test_intents import Chain
from tests.test_ops import COLD, HOT, VAULT

PAYEE = "5FLSigC9HGRKVhB9FiEo4Y3koPsNmBmLJbpXg2mp1hXcS59Y"  # //Charlie


GENESIS = INTERIM_SDK.genesis_hash  # the fake chain the CLI talks to (tests.test_ops.make)


@pytest.fixture
def registry(tmp_path):
    store = Store(tmp_path / "reg.db")
    chain = Chain()
    reg = Registry(store=store, genesis_hash=GENESIS, head=lambda: chain.head, delay_of_signer=chain.delay_of,
                   describe=lambda raw: "Balances.transfer_keep_alive(…)")
    server = serve(reg, {}, "127.0.0.1", 0, log=lambda m: None)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    url = f"http://127.0.0.1:{server.server_port}"
    token = new_token()
    enroll(url, COLD, VAULT, GENESIS, token)
    path = tmp_path / "owner.token"
    save_token(path, token)
    yield url, store, path
    server.shutdown()
    server.server_close()


def announce_argv(api, token_path=None, extra=()):
    argv = ["announce", "--hot", "//Bob", "--vault", VAULT, "--to", PAYEE, "--amount", "0.000000000005",
            "--api", api]
    if token_path:
        argv += ["--token", f"file:{token_path}"]
    return argv + list(extra)


def sent_names(s):
    return [c.call_function["name"] for c in s.sent]


def test_intent_is_registered_before_the_announce_is_signed(monkeypatch, registry, capsys):
    api, store, path = registry
    s = fake_connect(monkeypatch)
    seen = []
    submit = s.submit_extrinsic

    def spy(xt, wait_for_inclusion):
        seen.append((xt[0].call_function["name"], len(store.intents(VAULT))))
        return submit(xt, wait_for_inclusion)

    s.submit_extrinsic = spy
    assert cli.main(announce_argv(api, path)) == 0
    assert seen == [("announce", 1)]  # the intent was already stored when the announce went out
    out = capsys.readouterr().out
    assert "intent registered" in out and "YOURS" in out
    assert store.intents(VAULT)[0]["call_hash"] == s.sent[0].params["call_hash"]


def free_port():
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def test_registry_down_means_no_announce(monkeypatch, registry, capsys):
    _, _, path = registry
    s = fake_connect(monkeypatch)
    assert cli.main(announce_argv(f"http://127.0.0.1:{free_port()}", path)) == 2
    assert s.sent == [] and "not announcing" in capsys.readouterr().err


def _one_shot_server(behaviour):
    """Accept one connection, read the request, then die without answering (or hang)."""
    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    sock.listen(1)
    release = threading.Event()

    def run():
        conn, _ = sock.accept()
        conn.recv(65536)
        if behaviour == "hang":
            release.wait(10)
        conn.close()
        sock.close()

    threading.Thread(target=run, daemon=True).start()
    return f"http://127.0.0.1:{sock.getsockname()[1]}", release


@pytest.mark.parametrize("behaviour", ["die", "hang"])
def test_registry_killed_or_stalled_mid_request_means_no_announce(monkeypatch, registry, capsys, behaviour):
    _, store, path = registry
    monkeypatch.setattr(cli, "REGISTER_TIMEOUT_S", 1)
    s = fake_connect(monkeypatch)
    url, release = _one_shot_server(behaviour)
    try:
        assert cli.main(announce_argv(url, path)) == 2
    finally:
        release.set()
    assert s.sent == [] and s.announcements == {}
    assert "nothing sent" in capsys.readouterr().err


def test_wrong_token_is_refused_and_nothing_announced(monkeypatch, registry, tmp_path, capsys):
    api, store, _ = registry
    s = fake_connect(monkeypatch)
    bad = tmp_path / "bad.token"
    save_token(bad, new_token())
    assert cli.main(announce_argv(api, bad)) == 2
    assert s.sent == [] and store.intents(VAULT) == [] and "HTTP 401" in capsys.readouterr().err


def test_no_token_file_names_enroll(monkeypatch, registry, tmp_path, capsys):
    import airlock.intents as intents

    monkeypatch.setattr(intents, "TOKEN_DIR", tmp_path / "none")
    s = fake_connect(monkeypatch)
    assert cli.main(announce_argv(registry[0])) == 2
    err = capsys.readouterr().err
    assert s.sent == [] and "airlock enroll" in err and "--no-intent" in err


def test_refused_announce_registers_nothing(monkeypatch, registry):
    """Pre-checks run before registration: an announce refused on chain grounds leaves no stray intent."""
    api, store, path = registry
    s = fake_connect(monkeypatch)
    s.proxies[VAULT] = [d for d in s.proxies[VAULT] if d["delegate"] != HOT.ss58_address]
    assert cli.main(announce_argv(api, path)) == 2
    assert s.sent == [] and store.intents(VAULT) == []


def test_no_intent_announces_and_says_not_yours(monkeypatch, capsys):
    s = fake_connect(monkeypatch)
    assert cli.main(announce_argv("http://127.0.0.1:1", extra=["--no-intent"])) == 0
    assert sent_names(s) == ["announce"] and "NOT YOURS" in capsys.readouterr().out


def test_token_and_no_intent_are_exclusive():
    with pytest.raises(SystemExit):
        cli.parser().parse_args(announce_argv("http://x", "/t", ["--no-intent"]))


def test_enroll_saves_a_private_token_the_registry_accepts(monkeypatch, registry, tmp_path, capsys):
    api, store, _ = registry
    fake_connect(monkeypatch)
    before = store.owner(VAULT).token_sha256
    out = tmp_path / "new.token"
    import time
    time.sleep(1.1)  # a re-enrolment must be newer (issued_at in whole seconds)
    assert cli.main(["enroll", "--cold", "//Alice", "--vault", VAULT, "--api", api, "--token-file", str(out)]) == 0
    assert oct(out.stat().st_mode & 0o777) == "0o600"
    from airlock.intents import token_digest
    assert store.owner(VAULT).token_sha256 == token_digest(out.read_text().strip()) != before


def test_enroll_by_a_non_cold_key_is_refused_locally(monkeypatch, registry, tmp_path, capsys):
    fake_connect(monkeypatch)
    out = tmp_path / "x.token"
    assert cli.main(["enroll", "--cold", "//Bob", "--vault", VAULT, "--api", registry[0],
                     "--token-file", str(out)]) == 2
    assert not out.exists() and "delay-0" in capsys.readouterr().err
