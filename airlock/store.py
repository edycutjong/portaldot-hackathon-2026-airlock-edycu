"""The watcher's SQLite state: which vaults it watches, every announcement it has seen, and where it stopped.

Columns follow the architecture's schema; the vault's spawner/hot/cold are filled by `airlock create` once the CLI
ships and are nullable until then (a vault can be watched by address alone). One row per announcement
(vault, delegate, call_hash, block) means re-processing a block after a reconnect or reorg check does not alert again
(I4); each channel's delivery is recorded as soon as it succeeds, so a crash can repeat at most the channel that was
mid-send (delivery is at-least-once, never silently zero). The hashes of recently processed blocks let the watcher
notice a reorg or a reset chain instead of trusting a stale cursor.

The owner intent registry lives in the same file (`owner`, `intent`): the registry API (`airlock/api.py`) writes it,
the watcher reads it, so both must use the same database (the default path is per profile, so they do).
"""

from __future__ import annotations

import sqlite3
import time
from dataclasses import dataclass
from pathlib import Path

from .callhash import account_key

BLOCK_HASHES_KEPT = 512  # how far back a reorg can be detected and re-scanned
INTENT_WINDOW_BLOCKS = 50  # an intent vouches for an announcement made within this many blocks of its registration

SCHEMA = """
CREATE TABLE IF NOT EXISTS meta (
  key TEXT PRIMARY KEY,
  value TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS processed_block (
  number INTEGER PRIMARY KEY,
  hash TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS vault (
  address TEXT PRIMARY KEY,
  profile TEXT NOT NULL,
  spawner TEXT, hot TEXT, hot_delay INTEGER, cold TEXT,
  telegram_chat_id TEXT,
  added_at REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS announcement (
  vault TEXT NOT NULL,
  delegate TEXT NOT NULL,
  call_hash TEXT NOT NULL,
  announced_block INTEGER NOT NULL,
  block_hash TEXT NOT NULL,
  extrinsic_index INTEGER,
  release_block INTEGER,
  verdict TEXT NOT NULL,
  at_risk TEXT,
  state TEXT NOT NULL DEFAULT 'held' CHECK (state IN ('held','void','released','expired')),
  alert_text TEXT,
  alert_json TEXT,
  attempts INTEGER NOT NULL DEFAULT 0,
  first_attempt_at REAL,
  last_attempt_at REAL,
  gave_up INTEGER NOT NULL DEFAULT 0,
  alert_sent_at REAL,
  delivered_via TEXT,
  PRIMARY KEY (vault, delegate, call_hash, announced_block)
);
CREATE TABLE IF NOT EXISTS owner (
  vault_key TEXT PRIMARY KEY,        -- the vault's public key (hex): any SS58 spelling of the vault is the same row
  vault TEXT NOT NULL,
  cold TEXT NOT NULL,                -- the delay-0 delegate whose signature enrolled the token
  token_sha256 TEXT NOT NULL,        -- the token itself is never stored
  issued_at INTEGER NOT NULL,        -- signed with the token digest; a re-enrolment must be newer
  signature TEXT NOT NULL,
  enrolled_at REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS intent (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  vault_key TEXT NOT NULL,
  vault TEXT NOT NULL,
  call_hash TEXT NOT NULL,
  call_hex TEXT NOT NULL,
  summary TEXT,                      -- the decoded call, as the alert shows it
  registered_head INTEGER NOT NULL,  -- best block when it was registered: it vouches only for a LATER announcement
  registered_at REAL NOT NULL,
  used_by TEXT                       -- "<delegate key>@<block>": the one announcement it vouched for
);
CREATE INDEX IF NOT EXISTS intent_by_hash ON intent (vault_key, call_hash);
"""


class GenesisChanged(RuntimeError):
    """The database belongs to another chain (or the chain was reset): stop instead of rescanning."""


class ChainReset(GenesisChanged):
    """Same genesis, but the blocks the watcher processed are gone (a dev chain restarted from genesis, or a reorg
    deeper than the hashes kept): stop instead of trusting the cursor."""


@dataclass(frozen=True)
class Owner:
    vault: str
    cold: str
    token_sha256: str
    issued_at: int


@dataclass(frozen=True)
class Intent:
    id: int
    vault: str
    call_hash: str
    call_hex: str
    summary: str | None
    registered_head: int


@dataclass(frozen=True)
class Stored:
    vault: str
    delegate: str
    call_hash: str
    announced_block: int
    alert_sent_at: float | None


_KEY = "vault = ? AND delegate = ? AND call_hash = ? AND announced_block = ?"


def _key(s: Stored) -> tuple:
    return (s.vault, s.delegate, s.call_hash, s.announced_block)


class Store:
    def __init__(self, path: str | Path):
        self.path = str(path)
        if self.path != ":memory:":
            Path(self.path).parent.mkdir(parents=True, exist_ok=True)
        # the registry API and the watcher are separate processes on one file: wait for the other's write lock.
        # Each process uses its Store from one thread at a time (the API server is single-threaded), but not always
        # the thread that opened it (a server run from a test thread), so the same-thread check is off.
        self.db = sqlite3.connect(self.path, timeout=10, check_same_thread=False)
        self.db.executescript(SCHEMA)
        self._migrate()
        self.db.commit()

    # columns added after the first release of the schema: (table, column, definition)
    ADDED_COLUMNS = (
        ("announcement", "alert_json", "TEXT"),
        ("announcement", "attempts", "INTEGER NOT NULL DEFAULT 0"),
        ("announcement", "first_attempt_at", "REAL"),
        ("announcement", "last_attempt_at", "REAL"),
        ("announcement", "gave_up", "INTEGER NOT NULL DEFAULT 0"),
        ("announcement", "delivered_via", "TEXT"),
    )

    def _migrate(self) -> None:
        """A database written by an earlier version gets the columns it lacks (CREATE IF NOT EXISTS adds none)."""
        for table, column, definition in self.ADDED_COLUMNS:
            have = {row[1] for row in self.db.execute(f"PRAGMA table_info({table})")}
            if column not in have:
                self.db.execute(f"ALTER TABLE {table} ADD COLUMN {column} {definition}")

    def close(self) -> None:
        self.db.close()

    # -- chain identity and progress -------------------------------------------------------------------------------
    def _get(self, key: str) -> str | None:
        row = self.db.execute("SELECT value FROM meta WHERE key = ?", (key,)).fetchone()
        return row[0] if row else None

    def _set(self, key: str, value: str) -> None:
        self.db.execute("INSERT INTO meta (key, value) VALUES (?, ?) ON CONFLICT(key) DO UPDATE SET value = excluded.value",
                        (key, value))

    def bind_chain(self, genesis_hash: str, profile_id: str) -> None:
        """First use records the chain; later uses must be the same chain."""
        known = self._get("genesis_hash")
        if known is None:
            self._set("genesis_hash", genesis_hash.lower())
            self._set("profile", profile_id)
            self.db.commit()
        elif known != genesis_hash.lower():
            raise GenesisChanged(f"{self.path} was recorded on genesis {known} ({self._get('profile')}); "
                                 f"this chain is {genesis_hash} ({profile_id}). Refusing to rescan a different chain.")

    @property
    def last_block(self) -> int | None:
        value = self._get("last_block")
        return int(value) if value is not None else None

    def set_last_block(self, number: int, block_hash: str) -> None:
        self._set("last_block", str(number))
        self.db.execute("INSERT OR REPLACE INTO processed_block (number, hash) VALUES (?, ?)", (number, block_hash))
        self.db.execute("DELETE FROM processed_block WHERE number <= ?", (number - BLOCK_HASHES_KEPT,))
        self.db.commit()

    def rewind(self, number: int) -> None:
        """Forget processed blocks above `number` (they were reorged out); the cursor moves back to `number`."""
        self.db.execute("DELETE FROM processed_block WHERE number > ?", (number,))
        self._set("last_block", str(number))
        self.db.commit()

    def processed_hashes(self) -> list[tuple[int, str]]:
        """(number, hash) of the processed blocks still kept, newest first."""
        return self.db.execute("SELECT number, hash FROM processed_block ORDER BY number DESC").fetchall()

    # -- vaults ----------------------------------------------------------------------------------------------------
    def watch(self, address: str, profile_id: str, telegram_chat_id: str | None = None) -> None:
        self.db.execute("INSERT INTO vault (address, profile, telegram_chat_id, added_at) VALUES (?, ?, ?, ?) "
                        "ON CONFLICT(address) DO UPDATE SET telegram_chat_id = COALESCE(excluded.telegram_chat_id, "
                        "vault.telegram_chat_id)", (address, profile_id, telegram_chat_id, time.time()))
        self.db.commit()

    def vaults(self, profile_id: str) -> dict[str, str | None]:
        """Watched vault address -> its Telegram chat id (None = the default chat)."""
        return dict(self.db.execute("SELECT address, telegram_chat_id FROM vault WHERE profile = ?", (profile_id,)))

    # -- announcements ---------------------------------------------------------------------------------------------
    def record(self, *, vault: str, delegate: str, call_hash: str, announced_block: int, block_hash: str,
               extrinsic_index: int | None, release_block: int | None, verdict: str, at_risk: str,
               alert_text: str, alert_json: str) -> Stored:
        """Insert once; a re-seen announcement keeps its first row (and its delivery state)."""
        self.db.execute(
            "INSERT OR IGNORE INTO announcement (vault, delegate, call_hash, announced_block, block_hash, extrinsic_index,"
            " release_block, verdict, at_risk, alert_text, alert_json) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (vault, delegate, call_hash.lower(), announced_block, block_hash, extrinsic_index, release_block, verdict,
             at_risk, alert_text, alert_json))
        self.db.commit()
        return self.get(vault, delegate, call_hash, announced_block)

    def get(self, vault: str, delegate: str, call_hash: str, announced_block: int) -> Stored:
        row = self.db.execute(
            f"SELECT vault, delegate, call_hash, announced_block, alert_sent_at FROM announcement WHERE {_KEY}",
            (vault, delegate, call_hash.lower(), announced_block)).fetchone()
        return Stored(*row)

    def has(self, vault: str, delegate: str, call_hash: str, announced_block: int) -> bool:
        return self.db.execute(f"SELECT 1 FROM announcement WHERE {_KEY}",
                               (vault, delegate, call_hash.lower(), announced_block)).fetchone() is not None

    def note_attempt(self, stored: Stored, at: float) -> None:
        self.db.execute(f"UPDATE announcement SET attempts = attempts + 1, last_attempt_at = ?, "
                        f"first_attempt_at = COALESCE(first_attempt_at, ?) WHERE {_KEY}", (at, at, *_key(stored)))
        self.db.commit()

    def delivered_via(self, stored: Stored) -> set[str]:
        row = self.db.execute(f"SELECT delivered_via FROM announcement WHERE {_KEY}", _key(stored)).fetchone()
        return set(filter(None, (row[0] or "").split(","))) if row else set()

    def add_delivery(self, stored: Stored, channel: str) -> None:
        """Record one channel's success immediately, so a crash right after a send does not repeat it."""
        via = self.delivered_via(stored) | {channel}
        self.db.execute(f"UPDATE announcement SET delivered_via = ? WHERE {_KEY}", (",".join(sorted(via)), *_key(stored)))
        self.db.commit()

    def mark_sent(self, stored: Stored, at: float) -> None:
        """Every owner-facing channel has it."""
        self.db.execute(f"UPDATE announcement SET alert_sent_at = ? WHERE {_KEY}", (at, *_key(stored)))
        self.db.commit()

    def unsent(self) -> list[tuple[Stored, str, float | None]]:
        """Undelivered alerts not given up on, least recently attempted first: (row, alert JSON, first attempt time)."""
        rows = self.db.execute(
            "SELECT vault, delegate, call_hash, announced_block, alert_sent_at, alert_json, first_attempt_at "
            "FROM announcement WHERE alert_sent_at IS NULL AND gave_up = 0 "
            "ORDER BY COALESCE(last_attempt_at, 0), announced_block").fetchall()
        return [(Stored(*r[:5]), r[5], r[6]) for r in rows]

    def give_up(self, stored: Stored) -> None:
        self.db.execute(f"UPDATE announcement SET gave_up = 1 WHERE {_KEY}", _key(stored))
        self.db.commit()

    def announcements(self) -> list[dict]:
        cur = self.db.execute("SELECT * FROM announcement ORDER BY announced_block")
        names = [d[0] for d in cur.description]
        return [dict(zip(names, row)) for row in cur.fetchall()]

    # -- owner intent registry -------------------------------------------------------------------------------------
    def enroll(self, vault: str, cold: str, token_sha256: str, issued_at: int, signature: str) -> None:
        """Bind an owner token (by digest) to the vault. Signature and freshness are checked by the caller."""
        self.db.execute(
            "INSERT INTO owner (vault_key, vault, cold, token_sha256, issued_at, signature, enrolled_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?) ON CONFLICT(vault_key) DO UPDATE SET vault = excluded.vault, "
            "cold = excluded.cold, token_sha256 = excluded.token_sha256, issued_at = excluded.issued_at, "
            "signature = excluded.signature, enrolled_at = excluded.enrolled_at",
            (account_key(vault), vault, cold, token_sha256, issued_at, signature, time.time()))
        self.db.commit()

    def owner(self, vault: str) -> Owner | None:
        row = self.db.execute("SELECT vault, cold, token_sha256, issued_at FROM owner WHERE vault_key = ?",
                              (account_key(vault),)).fetchone()
        return Owner(*row) if row else None

    def add_intent(self, vault: str, call_hash: str, call_hex: str, summary: str | None, registered_head: int) -> int:
        cur = self.db.execute(
            "INSERT INTO intent (vault_key, vault, call_hash, call_hex, summary, registered_head, registered_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            (account_key(vault), vault, call_hash.lower(), call_hex.lower(), summary, registered_head, time.time()))
        self.db.commit()
        return cur.lastrowid

    def match_intent(self, vault: str, call_hash: str, delegate: str, announced_block: int,
                     window: int = INTENT_WINDOW_BLOCKS) -> Intent | None:
        """The owner intent that vouches for this announcement, or None (-> NOT YOURS, I6).

        An intent vouches for exactly one announcement: one of the same vault and call hash, made AFTER it was
        registered (register-before-announce) and within `window` blocks of it. The first such announcement claims it;
        processing that same announcement again (a rescan) finds it again, but a second announcement of the same call
        (a replay of a spend already made) is not vouched for."""
        tag = f"{account_key(delegate)}@{announced_block}"
        rows = self.db.execute(
            "SELECT id, vault, call_hash, call_hex, summary, registered_head, used_by FROM intent "
            "WHERE vault_key = ? AND call_hash = ? AND registered_head < ? AND registered_head + ? >= ? ORDER BY id",
            (account_key(vault), call_hash.lower(), announced_block, window, announced_block)).fetchall()
        for row in rows:
            if row[6] == tag:
                return Intent(*row[:6])
        for row in rows:
            if row[6] is None:
                cur = self.db.execute("UPDATE intent SET used_by = ? WHERE id = ? AND used_by IS NULL", (tag, row[0]))
                self.db.commit()
                if cur.rowcount:
                    return Intent(*row[:6])
        return None

    def intents(self, vault: str) -> list[dict]:
        cur = self.db.execute("SELECT * FROM intent WHERE vault_key = ? ORDER BY id", (account_key(vault),))
        names = [d[0] for d in cur.description]
        return [dict(zip(names, row)) for row in cur.fetchall()]
