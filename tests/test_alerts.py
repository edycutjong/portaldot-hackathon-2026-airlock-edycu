"""The alert text and its delivery: what the owner reads, and the Telegram sender against a local mock Bot API."""

import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from airlock.alerts import (NOT_YOURS, Alert, JsonlNotifier, TelegramError, TelegramNotifier, format_text,
                            read_env_file, telegram_from_config)

TOKEN = "123456:SECRET-test-token"
VAULT = "5Hb37SqmVJSzREN7ziBw1ZQJWLgDS7DLaEjKisf8a6KUYsLv"
HOT = "5FHneW46xGXgs5mUiveU4sbTyGBzmstUspZC92UhjJM694ty"
HASH = "0xf4c3c3361dcfb6b8193c568bd42e6ab4d2731097b285f573e10e7d2f7e55478a"


def alert(**over):
    base = dict(verdict=NOT_YOURS, profile_id="interim-sdk",
                profile_label="interim — not V3 (upstream polkadot-sdk substrate-node dev chain)", is_interim=True,
                vault=VAULT, delegate=HOT, call_hash=HASH, announced_block=100, block_hash="0xab", release_block=105,
                head=101, block_time_ms=3000, at_risk="902.0000 UNIT", locked=None)
    return Alert(**{**base, **over})


def test_text_leads_with_the_verdict_and_ends_with_the_veto_command():
    lines = format_text(alert()).splitlines()
    assert lines[0] == "NOT YOURS — VETO"
    assert f"airlock veto --vault {VAULT} --delegate {HOT} --hash {HASH}" in lines
    assert "Up to 902.0000 UNIT at risk" in lines


def test_text_names_the_chain_and_the_countdown():
    text = format_text(alert())
    assert "Chain: interim — not V3" in text
    assert "executable from #105. Veto must land by #104: 3 blocks (≈ 9 s) from head #101" in text
    assert f"call=proxy.rejectAnnouncement(delegate={HOT}, callHash={HASH})" in text
    assert "Airlock CLI (ships with the CLI step)" in text


def test_veto_deadline_is_one_block_before_the_call_can_execute():
    """proxy_announced passes once now - announced >= delay, so the call can land IN release_block."""
    a = alert(announced_block=100, release_block=105, head=104)
    assert (a.blocks_left, a.veto_by, a.blocks_to_veto) == (1, 104, 0)
    assert "veto immediately" in format_text(a)


def test_text_shows_locked_funds_apart():
    assert "(19,920.0000 UNIT locked, not movable)" in format_text(alert(locked="19,920.0000 UNIT"))


def test_text_when_the_delay_has_already_elapsed():
    text = format_text(alert(head=110, late_by=10))
    assert "executable from #105 (head #110) — veto immediately" in text
    assert "Seen 10 blocks late" in text


def test_text_when_the_delay_is_unknown():
    assert "its delay could not be read — veto now" in format_text(alert(release_block=None))


def test_v3_chain_is_named_with_its_profile_id():
    text = format_text(alert(profile_id="v3", profile_label="Portaldot V3 testnet", is_interim=False))
    assert "Chain: Portaldot V3 testnet (v3)" in text


def test_long_countdowns_are_in_minutes():
    assert "(≈ 50 min)" in format_text(alert(release_block=1102, block_time_ms=3000))


def test_jsonl_notifier_appends_one_line_per_alert(tmp_path):
    out = tmp_path / "a" / "alerts.jsonl"
    n = JsonlNotifier(out, clock=lambda: 42.0)
    n.send(alert(), "t1")
    n.send(alert(announced_block=101), "t2")
    rows = [json.loads(line) for line in out.read_text().splitlines()]
    assert [r["announced_block"] for r in rows] == [100, 101]
    assert rows[0]["sent_at"] == 42.0 and rows[0]["blocks_left"] == 4 and rows[0]["blocks_to_veto"] == 3
    assert rows[0]["veto_by"] == 104 and rows[0]["veto_command"].startswith("airlock")


class MockBotApi:
    """A local HTTP server that answers like api.telegram.org's sendMessage."""

    def __init__(self, status=200, reply=None):
        self.requests = []
        self.status, self.reply = status, reply if reply is not None else {"ok": True, "result": {"message_id": 1}}
        api = self

        class Handler(BaseHTTPRequestHandler):
            def do_POST(self):
                body = self.rfile.read(int(self.headers["Content-Length"]))
                api.requests.append((self.path, self.headers["Content-Type"], json.loads(body)))
                payload = json.dumps(api.reply).encode()
                self.send_response(api.status)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(payload)))
                self.end_headers()
                self.wfile.write(payload)

            def log_message(self, *args):
                pass

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.url = f"http://127.0.0.1:{self.server.server_address[1]}"
        threading.Thread(target=self.server.serve_forever, daemon=True).start()

    def close(self):
        self.server.shutdown()
        self.server.server_close()


@pytest.fixture
def bot():
    api = MockBotApi()
    yield api
    api.close()


def test_telegram_posts_send_message_with_the_alert_text(bot):
    n = TelegramNotifier(TOKEN, "777", api_base=bot.url)
    a = alert()
    n.send(a, format_text(a))
    path, ctype, body = bot.requests[0]
    assert path == f"/bot{TOKEN}/sendMessage" and ctype == "application/json"
    assert body["chat_id"] == "777" and body["text"] == format_text(a) and body["disable_web_page_preview"] is True
    assert "reply_markup" not in body  # URL button is flag-gated until a /veto page exists


def test_telegram_vault_chat_overrides_default(bot):
    TelegramNotifier(TOKEN, "777", api_base=bot.url).send(alert(chat_id="-100999"), "x")
    assert bot.requests[0][2]["chat_id"] == "-100999"


def test_telegram_url_button_only_when_flagged(bot):
    TelegramNotifier(TOKEN, "777", api_base=bot.url, veto_url_base="https://airlock.example/").send(alert(), "x")
    button = bot.requests[0][2]["reply_markup"]["inline_keyboard"][0][0]
    assert button == {"text": "VETO", "url": f"https://airlock.example/veto/{HASH}"}


def test_telegram_without_any_chat_id_refuses():
    with pytest.raises(TelegramError, match="no Telegram chat id"):
        TelegramNotifier(TOKEN, None, api_base="http://127.0.0.1:9").send(alert(), "x")


@pytest.mark.parametrize("status,reply,match", [
    (200, {"ok": False, "description": "Bad Request: chat not found"}, "chat not found"),
    (401, {"ok": False, "description": "Unauthorized"}, "HTTP 401"),
])
def test_telegram_errors_are_raised_without_the_token(status, reply, match):
    api = MockBotApi(status, reply)
    try:
        with pytest.raises(TelegramError, match=match) as e:
            TelegramNotifier(TOKEN, "777", api_base=api.url).send(alert(), "x")
        assert TOKEN not in str(e.value)
    finally:
        api.close()


def test_telegram_unreachable_is_raised_without_the_token():
    with pytest.raises(TelegramError, match="unreachable") as e:
        TelegramNotifier(TOKEN, "777", api_base="http://127.0.0.1:9", timeout_s=2).send(alert(), "x")
    assert TOKEN not in str(e.value)


def test_telegram_http_stack_errors_are_contained(monkeypatch):
    import http.client
    import urllib.request

    def broken(*args, **kwargs):
        raise http.client.IncompleteRead(b"partial")
    monkeypatch.setattr(urllib.request, "urlopen", broken)
    with pytest.raises(TelegramError) as e:
        TelegramNotifier(TOKEN, "777", api_base="http://127.0.0.1:9").send(alert(), "x")
    assert TOKEN not in str(e.value)


@pytest.mark.parametrize("bad", ["", "abc", "123:abc def", "123:abc #comment", ":abc"])
def test_malformed_tokens_are_refused_without_echoing_them(bad):
    with pytest.raises(ValueError) as e:
        TelegramNotifier(bad, "777")
    assert not bad or bad not in str(e.value)


def test_veto_url_base_must_be_https():
    with pytest.raises(ValueError, match="https"):
        TelegramNotifier(TOKEN, "777", veto_url_base="http://localhost:3000")


def test_telegram_repr_hides_the_token():
    assert TOKEN not in repr(TelegramNotifier(TOKEN, "777"))


def test_env_file_parsing(tmp_path):
    f = tmp_path / "telegram.env"
    f.write_text("# bot\nexport TELEGRAM_BOT_TOKEN='abc:def' # quoted\n\nTELEGRAM_CHAT_ID=\"42\"\nnoise\n"
                 "AIRLOCK_VETO_URL_BASE=https://x.example  # later\n")
    assert read_env_file(f) == {"TELEGRAM_BOT_TOKEN": "abc:def", "TELEGRAM_CHAT_ID": "42",
                                "AIRLOCK_VETO_URL_BASE": "https://x.example"}


def test_telegram_config_absent_means_stdout_only(tmp_path):
    assert telegram_from_config(tmp_path / "missing.env", environ={}) is None


def test_telegram_config_from_file_and_env_override(tmp_path):
    f = tmp_path / "telegram.env"
    f.write_text("TELEGRAM_BOT_TOKEN=111:file-token  # from BotFather\nTELEGRAM_CHAT_ID=1\n")
    n = telegram_from_config(f, environ={"AIRLOCK_TELEGRAM_CHAT_ID": "2"})
    assert n._token == "111:file-token" and n.default_chat_id == "2" and n.veto_url_base is None
    assert telegram_from_config(f, environ={"AIRLOCK_TELEGRAM_TOKEN": "222:env-token"})._token == "222:env-token"


def test_a_token_without_a_chat_id_is_a_config_error(tmp_path):
    f = tmp_path / "telegram.env"
    f.write_text("TELEGRAM_BOT_TOKEN=111:abc\n")
    with pytest.raises(ValueError, match="TELEGRAM_CHAT_ID"):
        telegram_from_config(f, environ={})


def test_a_non_object_reply_is_a_telegram_error():
    api = MockBotApi(200, ["not", "an", "object"])
    try:
        with pytest.raises(TelegramError, match="refused"):
            TelegramNotifier(TOKEN, "777", api_base=api.url).send(alert(), "x")
    finally:
        api.close()


def test_no_longer_pending_text_has_no_veto_command():
    text = format_text(alert(pending=False, head=120))
    assert text.splitlines()[0] == "NOT YOURS — NO LONGER PENDING"
    assert "airlock veto" not in text and "Check the vault: up to 902.0000 UNIT is movable now" in text
