"""The owner intent registry over HTTP (stdlib only): the endpoint `airlock announce` registers with before it signs.

  POST /api/vaults/<vault>/owner   enrol an owner token: {cold, token_sha256, issued_at, signature}  (cold-key signed)
  POST /api/intents                Authorization: Bearer <owner token>  {vault, call_hex[, call_hash]}
                                   -> 200 {call_hash, intent_id, registered_head, summary}; no valid token -> 401
  GET  /api/health                 profile, genesis, best block

There is no other way to put a preimage on the server: a thief's announcement stays hash-only and NOT YOURS. The
server reads the chain (who is a cold delegate, the best block, how to decode a call) but never signs anything. It
writes the SQLite file the watcher reads, so run both on the same --db (the per-profile default is shared).

usage: python -m airlock.api [--profile interim-sdk] [--url ws://…] [--db path] [--host 127.0.0.1] [--port 8787]
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

from .chain_profile import Chain, ChainUnreachable, ProfileMismatch, UnknownChain, _is_connection_error, connect
from .intents import Registry, RegistryError
from .ops import definitions, delay_of
from .store import Store

MAX_BODY = 64 * 1024
HEAD_CACHE_S = 1.0  # unauthenticated /api/health cannot make the registry read the chain more often than this
OWNER_PATH = re.compile(r"^/api/vaults/([1-9A-HJ-NP-Za-km-z]{40,60})/owner$")


def describe_call(chain: Chain, raw: bytes) -> str:
    """`Balances.transfer_keep_alive(dest=…, value=1.0000 UNIT)` — the call as the YOURS alert shows it. Refuses
    bytes that are not exactly one call of this runtime."""
    from scalecodec.base import ScaleBytes

    if chain.substrate.metadata is None:  # a fresh connection loads the runtime's metadata lazily
        chain.substrate.init_runtime()
    obj = chain.substrate.runtime_config.create_scale_object("Call", data=ScaleBytes(raw),
                                                            metadata=chain.substrate.metadata)
    obj.decode()
    if len(obj.get_used_bytes()) != len(raw):
        raise ValueError("trailing bytes after the call")
    v = obj.value
    call_args = v.get("call_args") or []
    pairs = call_args.items() if isinstance(call_args, dict) else ((a["name"], a["value"]) for a in call_args)
    args = []
    for name, value in pairs:
        if name in ("value", "amount") and isinstance(value, int):
            value = chain.fmt(value)
        elif isinstance(value, (dict, list)):
            value = json.dumps(value, separators=(",", ":"))[:120]
        args.append(f"{name}={value}")
    return f"{v['call_module']}.{v['call_function']}({', '.join(args)})"


class ChainReads:
    """The registry's chain reads, serialized (one websocket) and reconnected once on a dropped connection."""

    def __init__(self, url: str | None, profile: str | None):
        self.url, self.profile = url, profile
        self.lock = threading.Lock()
        self.chain = connect(url, profile)
        self._head: tuple[float, int] | None = None

    def _do(self, fn):
        with self.lock:
            try:
                return fn(self.chain)
            except Exception as e:
                if not _is_connection_error(e):
                    raise
                fresh = connect(self.url, self.profile)
                if fresh.genesis_hash != self.chain.genesis_hash:
                    fresh.substrate.close()
                    raise RegistryError(503, "the chain behind the registry changed genesis") from None
                try:
                    self.chain.substrate.close()
                except Exception:
                    pass
                self.chain = fresh
                return fn(self.chain)

    def head(self, max_age_s: float = 0.0) -> int:
        """Best block; `max_age_s` > 0 may answer from a read that recent (health checks only — an intent is
        always recorded against a fresh read)."""
        now = time.monotonic()
        if max_age_s and self._head and now - self._head[0] <= max_age_s:
            return self._head[1]
        n = self._do(lambda c: c.substrate.get_block_number(None))
        self._head = (now, n)
        return n

    def delay_of_signer(self, vault: str, account: str) -> int | None:
        return self._do(lambda c: delay_of(definitions(c.substrate.query("Proxy", "Proxies", [vault]).value), account))

    def describe(self, raw: bytes) -> str:
        return self._do(lambda c: describe_call(c, raw))


def make_handler(registry: Registry, health: dict, log=None, health_head=None):
    log = log or (lambda m: print(m, file=sys.stderr, flush=True))
    health_head = health_head or registry.head

    class Handler(BaseHTTPRequestHandler):
        server_version = "airlock-registry"
        sys_version = ""
        timeout = 10  # per read: a stalled client ties up only its own thread (the server is threaded)

        def log_message(self, fmt, *args):  # the default logs to stderr with the request line; keep it, minus noise
            log(f"{self.address_string()} {fmt % args}")

        def _send(self, status: int, body: dict) -> None:
            data = json.dumps(body).encode()
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(data)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(data)

        def _body(self) -> Any:
            try:
                length = int(self.headers.get("Content-Length") or 0)
            except ValueError:
                raise RegistryError(400, "bad Content-Length") from None
            if length <= 0:
                raise RegistryError(400, "a JSON body is required")
            if length > MAX_BODY:
                raise RegistryError(413, f"body over {MAX_BODY} bytes")
            try:
                return json.loads(self.rfile.read(length))
            except ValueError:
                raise RegistryError(400, "body is not JSON") from None

        def _bearer(self) -> str | None:
            auth = self.headers.get("Authorization") or ""
            return auth[7:].strip() or None if auth.startswith("Bearer ") else None

        def do_GET(self):
            if self.path == "/api/health":
                try:
                    self._send(200, {**health, "best_block": health_head()})
                except Exception as e:
                    self._send(503, {**health, "error": f"chain unreadable ({type(e).__name__})"})
            else:
                self._send(404, {"error": "not found"})

        def do_POST(self):
            try:
                if self.path == "/api/intents":
                    token = self._bearer()
                    if not token:  # refuse before reading the body: no token, nothing else matters
                        raise RegistryError(401, "a valid owner token for this vault is required")
                    body = self._body()
                    if not isinstance(body, dict):
                        raise RegistryError(400, "body must be a JSON object")
                    self._send(200, registry.register(token, body))
                elif m := OWNER_PATH.match(self.path):
                    body = self._body()
                    if not isinstance(body, dict):
                        raise RegistryError(400, "body must be a JSON object")
                    self._send(200, registry.enroll(m.group(1), body))
                else:
                    self._send(404, {"error": "not found"})
            except RegistryError as e:
                self._send(e.status or 400, {"error": str(e)})
            except Exception as e:  # a chain read failed: the owner must not announce on a half answer
                log(f"registry error: {type(e).__name__}: {e}")
                self._send(503, {"error": f"registry could not complete the request ({type(e).__name__})"})

    return Handler


def serve(registry: Registry, health: dict, host: str, port: int, log=None, health_head=None) -> ThreadingHTTPServer:
    """Threaded: a client that connects and stalls holds one thread, never the owner's registration. Chain reads are
    serialized by ChainReads, store access by the Registry's lock (never held across a chain read)."""
    server = ThreadingHTTPServer((host, port), make_handler(registry, health, log, health_head))
    server.daemon_threads = True
    return server


def default_db(profile_id: str) -> Path:
    from .watcher import default_db as watcher_db

    return watcher_db(profile_id)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="python -m airlock.api", description=__doc__.split("\n\n")[0])
    ap.add_argument("--profile", default=None, help="expected chain profile (default $AIRLOCK_PROFILE, else auto)")
    ap.add_argument("--url", default=None, help="node endpoint (default $AIRLOCK_WSS, else the profile default)")
    ap.add_argument("--db", type=Path, default=None, help="the watcher's SQLite file (default: the watcher's default)")
    ap.add_argument("--host", default="127.0.0.1", help="listen address (default 127.0.0.1: local only)")
    ap.add_argument("--port", type=int, default=8787)
    args = ap.parse_args(argv)
    try:
        reads = ChainReads(args.url, args.profile)
    except (UnknownChain, ProfileMismatch, ChainUnreachable) as e:
        print(f"REGISTRY FAILED: {type(e).__name__}: {e}", file=sys.stderr)
        return 1
    chain = reads.chain
    store = Store(args.db or default_db(chain.profile.id))
    store.bind_chain(chain.genesis_hash, chain.profile.id)
    registry = Registry(store=store, genesis_hash=chain.genesis_hash, head=reads.head,
                        delay_of_signer=reads.delay_of_signer, describe=reads.describe)
    health = {"profile": chain.profile.id, "label": chain.profile.label, "genesis_hash": chain.genesis_hash}
    server = serve(registry, health, args.host, args.port, health_head=lambda: reads.head(HEAD_CACHE_S))
    print(f"intent registry on http://{args.host}:{server.server_port} · {chain.profile.label} · genesis "
          f"{chain.genesis_hash} · db {store.path}", file=sys.stderr, flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        return 130
    finally:
        server.server_close()
        store.close()
        chain.substrate.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
