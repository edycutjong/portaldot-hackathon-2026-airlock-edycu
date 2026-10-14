"""What the owner is told when a delegate announces a spend from a watched vault, and how it reaches them.

Until the owner-token intent registry exists every announcement is NOT YOURS: an announcement carries only a call
hash, and nothing the owner registered can vouch for it yet. The alert says what is at risk, when the call becomes
executable, and carries the exact veto command to paste. Plain text, no image; the URL button is flag-gated until a
web veto page exists.

Notifiers: stdout, a JSON-lines log, and Telegram `sendMessage` over plain HTTPS. A notifier failure is reported and
retried by the watcher, never raised into the block loop. The bot token never appears in an error message.
"""

from __future__ import annotations

import json
import os
import sys
import urllib.error
import urllib.request
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Callable, Protocol

NOT_YOURS = "NOT YOURS"
TELEGRAM_API = "https://api.telegram.org"
TELEGRAM_ENV = Path.home() / ".config" / "airlock" / "telegram.env"


@dataclass(frozen=True)
class Alert:
    verdict: str
    profile_id: str
    profile_label: str
    is_interim: bool
    vault: str
    delegate: str
    call_hash: str
    announced_block: int
    block_hash: str
    release_block: int | None  # None = the delegate's delay could not be read
    head: int
    block_time_ms: int | None
    at_risk: str  # formatted transferable balance
    locked: str | None  # formatted locked balance, shown apart; None when nothing is locked
    late_by: int = 0  # blocks between the announcement and the head when the alert was built (catch-up)
    chat_id: str | None = None
    extra: dict = field(default_factory=dict)

    @property
    def blocks_left(self) -> int | None:
        return None if self.release_block is None else self.release_block - self.head

    def veto_command(self) -> str:
        return f"airlock veto --vault {self.vault} --delegate {self.delegate} --hash {self.call_hash}"

    def as_json(self) -> dict:
        return {**asdict(self), "blocks_left": self.blocks_left, "veto_command": self.veto_command()}


def _seconds(blocks: int, block_time_ms: int | None) -> str:
    if block_time_ms is None:
        return ""
    s = blocks * block_time_ms / 1000
    return f" (≈ {s:.0f} s)" if s < 120 else f" (≈ {s / 60:.0f} min)"


def format_text(a: Alert) -> str:
    """The alert as sent: verdict first, the chain named, the veto command last so it is easy to copy."""
    chain = a.profile_label if a.is_interim else f"{a.profile_label} ({a.profile_id})"
    lines = [f"{a.verdict} — VETO" if a.verdict == NOT_YOURS else a.verdict,
             f"Vault {a.vault}",
             f"Chain: {chain}",
             f"Up to {a.at_risk} at risk" + (f" ({a.locked} locked, not movable)" if a.locked else "")]
    announced = f"Announced by delegate {a.delegate} at block #{a.announced_block}"
    if a.release_block is None:
        lines.append(f"{announced}; its delay could not be read — veto now")
    elif a.blocks_left > 0:
        lines.append(f"{announced}; executable from #{a.release_block}: "
                     f"{a.blocks_left} blocks left{_seconds(a.blocks_left, a.block_time_ms)}")
    else:
        lines.append(f"{announced}; executable since #{a.release_block} — veto immediately "
                     f"(it can still be rejected until it executes)")
    if a.late_by > 1:
        lines.append(f"Seen {a.late_by} blocks late: the watcher was offline or catching up")
    lines += [f"Call hash {a.call_hash} (an announcement publishes only the hash)",
              "Veto with the cold key:",
              a.veto_command(),
              f"(= Proxy.proxy(vault, Proxy.reject_announcement({a.delegate}, {a.call_hash})) signed by the cold key)"]
    return "\n".join(lines)


class Notifier(Protocol):
    name: str

    def send(self, alert: Alert, text: str) -> None: ...


class StdoutNotifier:
    name = "stdout"

    def __init__(self, stream=None):
        self.stream = stream or sys.stdout

    def send(self, alert: Alert, text: str) -> None:
        bar = "=" * 72
        print(f"{bar}\n{text}\n{bar}", file=self.stream, flush=True)


class JsonlNotifier:
    """Append each alert as one JSON line (evidence, and the record a benchmark reads)."""

    name = "jsonl"

    def __init__(self, path: str | Path, clock: Callable[[], float] | None = None):
        import time

        self.path = Path(path)
        self.clock = clock or time.time

    def send(self, alert: Alert, text: str) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.path.open("a") as f:
            f.write(json.dumps({"sent_at": self.clock(), **alert.as_json(), "text": text}) + "\n")


class TelegramError(RuntimeError):
    pass


class TelegramNotifier:
    """Bot API `sendMessage`. `api_base` is overridable so tests can point it at a local HTTP server."""

    name = "telegram"

    def __init__(self, token: str, default_chat_id: str | None, api_base: str = TELEGRAM_API,
                 veto_url_base: str | None = None, timeout_s: float = 10):
        if not token:
            raise ValueError("Telegram bot token is empty")
        self._token = token
        self.default_chat_id = default_chat_id
        self.api_base = api_base.rstrip("/")
        self.veto_url_base = veto_url_base  # flag: only set once a /veto page exists
        self.timeout_s = timeout_s

    def __repr__(self) -> str:  # never leak the token through a log line
        return f"TelegramNotifier(chat={self.default_chat_id}, api={self.api_base}, token=<redacted>)"

    def _redact(self, message: str) -> str:
        return message.replace(self._token, "<redacted>")

    def payload(self, alert: Alert, text: str) -> dict:
        chat_id = alert.chat_id or self.default_chat_id
        if not chat_id:
            raise TelegramError("no Telegram chat id for this vault and no default chat id")
        body = {"chat_id": chat_id, "text": text, "disable_web_page_preview": True}
        if self.veto_url_base:
            body["reply_markup"] = {"inline_keyboard": [[{
                "text": "VETO", "url": f"{self.veto_url_base.rstrip('/')}/veto/{alert.call_hash}"}]]}
        return body

    def send(self, alert: Alert, text: str) -> None:
        body = json.dumps(self.payload(alert, text)).encode()
        request = urllib.request.Request(f"{self.api_base}/bot{self._token}/sendMessage", data=body,
                                         headers={"Content-Type": "application/json"}, method="POST")
        try:
            with urllib.request.urlopen(request, timeout=self.timeout_s) as response:
                reply = json.loads(response.read() or b"{}")
        except urllib.error.HTTPError as e:
            detail = e.read().decode(errors="replace")[:300]
            raise TelegramError(self._redact(f"Telegram HTTP {e.code}: {detail}")) from None
        except (urllib.error.URLError, TimeoutError, OSError, ValueError) as e:
            raise TelegramError(self._redact(f"Telegram unreachable: {type(e).__name__}: {e}")) from None
        if not reply.get("ok"):
            raise TelegramError(self._redact(f"Telegram refused the message: {reply.get('description', reply)}"))


def read_env_file(path: Path) -> dict[str, str]:
    """KEY=VALUE lines; blank lines and # comments ignored; surrounding quotes stripped."""
    values = {}
    for line in path.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        values[key.strip().removeprefix("export ").strip()] = value.strip().strip("'\"")
    return values


def telegram_from_config(path: Path = TELEGRAM_ENV, environ: dict | None = None) -> TelegramNotifier | None:
    """Telegram if a token is configured ($AIRLOCK_TELEGRAM_TOKEN or the env file), else None (stdout only)."""
    environ = os.environ if environ is None else environ
    values = read_env_file(path) if path.is_file() else {}
    token = environ.get("AIRLOCK_TELEGRAM_TOKEN") or values.get("TELEGRAM_BOT_TOKEN")
    if not token:
        return None
    chat = environ.get("AIRLOCK_TELEGRAM_CHAT_ID") or values.get("TELEGRAM_CHAT_ID")
    veto_url = environ.get("AIRLOCK_VETO_URL_BASE") or values.get("AIRLOCK_VETO_URL_BASE")
    return TelegramNotifier(token, chat, veto_url_base=veto_url or None)
