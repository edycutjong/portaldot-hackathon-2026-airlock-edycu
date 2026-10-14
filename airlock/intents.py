"""The owner intent registry: what turns an announcement from NOT YOURS into YOURS.

An announcement carries only a call hash, and a thief holding the phished hot key can announce anything. So the
watcher marks an announcement YOURS only when the owner registered that exact call FIRST, with the owner token:

  enrol     the cold key (a delay-0 delegate of the vault, checked on chain) signs
            "airlock-enroll:v1:<genesis>:<vault key>:<sha256(token)>:<issued_at>"  -> the server keeps sha256(token)
  register  POST /api/intents  Authorization: Bearer <token>  {vault, call_hex, call_hash}
            -> the server re-hashes call_hex (blake2_256) and rejects a mismatch; no valid token -> 401
  announce  only after the 200: `airlock announce` registers, waits for it, and only then signs proxy.announce
  classify  the watcher: an intent for (vault, call hash) registered before the announcement, within
            INTENT_WINDOW_BLOCKS, not already used by another announcement -> YOURS; anything else -> NOT YOURS (I6)

The hot-key seed alone cannot register anything, so a thief who phished it cannot make his announcement look like the
owner's — even if he publishes the call's preimage (there is no other preimage endpoint). Honest scope: the token is a
file beside the CLI config; a file-level leak that exposes it together with the hot key defeats it. The server decides
the label; it can mislabel or drop an alert, but it cannot move funds or shorten the delay — the chain enforces both.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import os
import secrets
import stat
import sys
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

from substrateinterface import Keypair

from .callhash import account_key, blake2_256

DEFAULT_API = "http://127.0.0.1:8787"
TOKEN_DIR = Path.home() / ".config" / "airlock" / "owner-tokens"
ENROLL_SKEW_S = 600  # an enrolment signature is accepted for this long either side of the server's clock
MAX_CALL_BYTES = 16 * 1024


class RegistryError(RuntimeError):
    """The registry refused a request; `status` is the HTTP status it answers with."""

    def __init__(self, status: int, message: str):
        super().__init__(message)
        self.status = status


def token_digest(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def new_token() -> str:
    return secrets.token_urlsafe(32)


def enroll_message(genesis_hash: str, vault: str, token_sha256: str, issued_at: int) -> bytes:
    return f"airlock-enroll:v1:{genesis_hash.lower()}:{account_key(vault)}:{token_sha256}:{issued_at}".encode()


def call_hex_bytes(call_hex: Any) -> bytes:
    if not isinstance(call_hex, str) or not call_hex.startswith("0x"):
        raise RegistryError(400, "call_hex must be a 0x-hex string")
    try:
        raw = bytes.fromhex(call_hex[2:])
    except ValueError:
        raise RegistryError(400, "call_hex is not valid hex") from None
    if not raw or len(raw) > MAX_CALL_BYTES:
        raise RegistryError(400, f"call_hex must be 1..{MAX_CALL_BYTES} bytes")
    return raw


@dataclass
class Registry:
    """Server side, transport-free (the HTTP layer in api.py only parses and maps RegistryError to a status).

    `delay_of_signer(vault, account)` reads the delegate's delay from the chain (None = not a delegate);
    `head()` the best block; `describe(call_bytes)` decodes the call with the runtime (raises if it cannot)."""

    store: Any
    genesis_hash: str
    head: Callable[[], int]
    delay_of_signer: Callable[[str, str], int | None]
    describe: Callable[[bytes], str]
    clock: Callable[[], float] = time.time
    # one SQLite connection shared by the server's threads: store access only under this lock, chain reads never
    lock: threading.Lock = field(default_factory=threading.Lock)

    def enroll(self, vault: str, body: dict) -> dict:
        try:
            cold, digest, issued_at, signature = (str(body["cold"]), str(body["token_sha256"]),
                                                  int(body["issued_at"]), str(body["signature"]))
        except (KeyError, TypeError, ValueError, OverflowError):
            raise RegistryError(400, "enrolment needs cold, token_sha256, issued_at, signature") from None
        if len(digest) != 64 or any(c not in "0123456789abcdef" for c in digest):
            raise RegistryError(400, "token_sha256 must be 64 lowercase hex characters")
        if abs(self.clock() - issued_at) > ENROLL_SKEW_S:
            raise RegistryError(401, "enrolment signature is stale or from the future (issued_at)")
        try:
            sig = bytes.fromhex(signature.removeprefix("0x"))
            valid = Keypair(ss58_address=cold).verify(enroll_message(self.genesis_hash, vault, digest, issued_at), sig)
        except Exception:
            valid = False
        if not valid:
            raise RegistryError(401, "enrolment signature does not verify for this cold key, vault and chain")
        if self.delay_of_signer(vault, cold) != 0:
            raise RegistryError(401, "the signer is not a delay-0 (cold) delegate of this vault on chain")
        with self.lock:
            known = self.store.owner(vault)
            if known is not None and issued_at <= known.issued_at:
                raise RegistryError(409, "an enrolment at least as new is already recorded (replayed signature?)")
            self.store.enroll(vault, cold, digest, issued_at, signature)
        return {"vault": vault, "cold": cold, "issued_at": issued_at, "enrolled": True}

    def authorized(self, vault: str, token: str | None) -> bool:
        with self.lock:
            owner = self.store.owner(vault)
        # compare digests in constant time; an unknown vault and a wrong token are the same answer
        expected = owner.token_sha256 if owner else "0" * 64
        return bool(token) and hmac.compare_digest(token_digest(token), expected) and owner is not None

    def register(self, token: str | None, body: dict) -> dict:
        vault = body.get("vault") if isinstance(body, dict) else None
        if not isinstance(vault, str) or not self.authorized(vault, token):
            raise RegistryError(401, "a valid owner token for this vault is required")
        raw = call_hex_bytes(body.get("call_hex"))
        h = blake2_256(raw)
        claimed = body.get("call_hash")
        if claimed is not None and str(claimed).lower() != h:
            raise RegistryError(400, f"call_hash {claimed} is not blake2_256(call_hex) = {h}")
        try:
            summary = self.describe(raw)
        except Exception as e:
            raise RegistryError(400, f"call_hex is not a call this runtime can decode ({type(e).__name__})") from None
        head = self.head()
        with self.lock:
            intent_id = self.store.add_intent(vault, h, "0x" + raw.hex(), summary, head)
        return {"vault": vault, "call_hash": h, "intent_id": intent_id, "registered_head": head, "summary": summary}


# -- client side (the CLI) -----------------------------------------------------------------------------------------

def token_path(vault: str) -> Path:
    return TOKEN_DIR / f"{vault}.token"


def load_token(spec: str | None, vault: str) -> str:
    """The owner token: file:<path>, env:<VAR>, or by default ~/.config/airlock/owner-tokens/<vault>.token."""
    if spec and spec.startswith("env:"):
        value = os.environ.get(spec[4:], "").strip()
        if not value:
            raise RegistryError(0, f"environment variable {spec[4:]} is empty or unset")
        return value
    if spec and not spec.startswith("file:"):
        raise RegistryError(0, "--token must be file:<path> or env:<VAR> (a token on the command line ends up in "
                               "shell history)")
    path = Path(spec[5:]).expanduser() if spec else token_path(vault)
    try:
        if path.stat().st_mode & (stat.S_IRWXG | stat.S_IRWXO):
            print(f"warning: owner token {path} is readable by other users; chmod 600 it", file=sys.stderr)
        return path.read_text().strip()
    except OSError:
        raise RegistryError(0, f"no owner token at {path}: enrol the vault first (airlock enroll --vault {vault})") \
            from None


def save_token(path: Path, token: str) -> None:
    """Write the token readable by its owner only: a new 0600 file (O_EXCL, so no existing file or symlink is written
    through) renamed over the old one — never world-readable even for a moment."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name}.{secrets.token_hex(4)}.tmp")
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_EXCL, stat.S_IRUSR | stat.S_IWUSR)
    try:
        with os.fdopen(fd, "w") as f:
            f.write(token + "\n")
        os.replace(tmp, path)
    except BaseException:
        tmp.unlink(missing_ok=True)
        raise


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):  # a redirect would carry the Bearer token to another URL
        return None


# no proxy from the environment either: the token goes straight to the registry or not at all
_OPENER = urllib.request.build_opener(urllib.request.ProxyHandler({}), _NoRedirect)
LOOPBACK = ("127.0.0.1", "localhost", "::1")


def check_api_url(api: str) -> None:
    """The owner token travels in a header: https anywhere, plain http only to this machine."""
    try:
        parsed = urllib.parse.urlsplit(api)
        host = parsed.hostname
    except ValueError:
        raise RegistryError(0, f"not a registry URL: {api!r}") from None
    if parsed.scheme == "https" or (parsed.scheme == "http" and host in LOOPBACK):
        return
    raise RegistryError(0, f"refusing to send the owner token to {api}: use https://, or http:// on 127.0.0.1")


def post(api: str, path: str, body: dict, token: str | None = None, timeout_s: float = 10) -> dict:
    """POST JSON; any answer but 200 — or no answer — raises RegistryError (status 0 = unreachable). Redirects are not
    followed."""
    check_api_url(api)
    headers = {"Content-Type": "application/json"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    request = urllib.request.Request(api.rstrip("/") + path, data=json.dumps(body).encode(), headers=headers,
                                     method="POST")
    try:
        with _OPENER.open(request, timeout=timeout_s) as response:
            return json.loads(response.read() or b"{}")
    except urllib.error.HTTPError as e:
        try:
            detail = json.loads(e.read() or b"{}").get("error", "")
        except ValueError:
            detail = ""
        raise RegistryError(e.code, f"registry answered HTTP {e.code}" + (f": {detail}" if detail else "")) from None
    except Exception as e:  # refused, reset mid-request, timed out, garbage reply: all "not registered"
        raise RegistryError(0, f"registry at {api} unreachable or no valid answer ({type(e).__name__}: {e})") from None


def register_intent(api: str, token: str, vault: str, call: bytes, timeout_s: float = 10) -> dict:
    """Register before announcing; returns the server's record only if it hashed the call to the same hash."""
    h = blake2_256(call)
    reply = post(api, "/api/intents", {"vault": vault, "call_hex": "0x" + call.hex(), "call_hash": h}, token,
                 timeout_s)
    if not isinstance(reply, dict) or reply.get("call_hash") != h:
        raise RegistryError(0, f"registry answered for a different call hash ({reply!r}), expected {h}")
    return reply


def enroll(api: str, cold: Keypair, vault: str, genesis_hash: str, token: str, issued_at: int | None = None) -> dict:
    issued_at = int(time.time()) if issued_at is None else issued_at
    digest = token_digest(token)
    signature = "0x" + cold.sign(enroll_message(genesis_hash, vault, digest, issued_at)).hex()
    return post(api, f"/api/vaults/{vault}/owner", {"cold": cold.ss58_address, "token_sha256": digest,
                                                     "issued_at": issued_at, "signature": signature})
