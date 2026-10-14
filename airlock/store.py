"""The watcher's SQLite state: which vaults it watches, every announcement it has seen, and where it stopped.

Columns follow the architecture's schema; the vault's spawner/hot/cold are filled by `airlock create` once the CLI
ships and are nullable until then (a vault can be watched by address alone). One row per announcement
(vault, delegate, call_hash, block) makes alerting idempotent: re-processing a block after a crash or reconnect never
alerts twice (I4), and an alert that was not delivered is retried.
"""

from __future__ import annotations

import sqlite3
import time
from dataclasses import dataclass
from pathlib import Path

SCHEMA = """
CREATE TABLE IF NOT EXISTS meta (
  key TEXT PRIMARY KEY,
  value TEXT NOT NULL
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
  alert_sent_at REAL,
  delivered_via TEXT,
  PRIMARY KEY (vault, delegate, call_hash, announced_block)
);
"""


class GenesisChanged(RuntimeError):
    """The database belongs to another chain (or the chain was reset): stop instead of rescanning."""


@dataclass(frozen=True)
class Stored:
    vault: str
    delegate: str
    call_hash: str
    announced_block: int
    alert_sent_at: float | None


class Store:
    def __init__(self, path: str | Path):
        self.path = str(path)
        if self.path != ":memory:":
            Path(self.path).parent.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(self.path)
        self.db.executescript(SCHEMA)
        self.db.commit()

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

    def set_last_block(self, number: int) -> None:
        self._set("last_block", str(number))
        self.db.commit()

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
            "SELECT vault, delegate, call_hash, announced_block, alert_sent_at FROM announcement "
            "WHERE vault = ? AND delegate = ? AND call_hash = ? AND announced_block = ?",
            (vault, delegate, call_hash.lower(), announced_block)).fetchone()
        return Stored(*row)

    def note_attempt(self, stored: Stored) -> None:
        self.db.execute("UPDATE announcement SET attempts = attempts + 1 "
                        "WHERE vault = ? AND delegate = ? AND call_hash = ? AND announced_block = ?",
                        (stored.vault, stored.delegate, stored.call_hash, stored.announced_block))
        self.db.commit()

    def mark_delivery(self, stored: Stored, via: list[str], complete: bool, at: float | None = None) -> None:
        """Record which channels have the alert; `alert_sent_at` is set once every owner-facing channel has it."""
        self.db.execute(
            "UPDATE announcement SET delivered_via = ?, alert_sent_at = CASE WHEN ? THEN ? ELSE alert_sent_at END "
            "WHERE vault = ? AND delegate = ? AND call_hash = ? AND announced_block = ?",
            (",".join(via), int(complete), at or time.time(), stored.vault, stored.delegate, stored.call_hash,
             stored.announced_block))
        self.db.commit()

    def delivered_via(self, stored: Stored) -> set[str]:
        row = self.db.execute("SELECT delivered_via FROM announcement "
                              "WHERE vault = ? AND delegate = ? AND call_hash = ? AND announced_block = ?",
                              (stored.vault, stored.delegate, stored.call_hash, stored.announced_block)).fetchone()
        return set(filter(None, (row[0] or "").split(","))) if row else set()

    def unsent(self, max_attempts: int) -> list[tuple[Stored, str]]:
        """Undelivered alerts with retries left: (row, alert JSON)."""
        rows = self.db.execute(
            "SELECT vault, delegate, call_hash, announced_block, alert_sent_at, alert_json FROM announcement "
            "WHERE alert_sent_at IS NULL AND attempts < ? ORDER BY announced_block", (max_attempts,)).fetchall()
        return [(Stored(*r[:5]), r[5]) for r in rows]

    def announcements(self) -> list[dict]:
        cur = self.db.execute("SELECT * FROM announcement ORDER BY announced_block")
        names = [d[0] for d in cur.description]
        return [dict(zip(names, row)) for row in cur.fetchall()]
