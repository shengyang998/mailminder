"""Run state in SQLite: what was read, what the model said, what was written.

The model's answer for each message is cached, so planning and writing can be
redone (after a dry run, after an error) without paying for the model again.
"""

from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from .mailbox import Message

MAX_ATTEMPTS = 3

SCHEMA = """
CREATE TABLE IF NOT EXISTS messages (
    msg_key TEXT PRIMARY KEY,
    account TEXT NOT NULL, mailbox TEXT NOT NULL, uid INTEGER NOT NULL,
    message_id TEXT, sent_at TEXT, sender TEXT, subject TEXT,
    status TEXT NOT NULL,            -- extracted | failed | skipped
    events_json TEXT,                -- the model's answer, cached
    attempts INTEGER NOT NULL DEFAULT 0,
    error TEXT,
    applied INTEGER NOT NULL DEFAULT 0,
    updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS seen (
    account TEXT, mailbox TEXT, uidvalidity INTEGER, uid INTEGER,
    PRIMARY KEY (account, mailbox, uidvalidity, uid)
);
CREATE TABLE IF NOT EXISTS cursors (
    account TEXT, mailbox TEXT, uidvalidity INTEGER, last_uid INTEGER,
    PRIMARY KEY (account, mailbox)
);
CREATE TABLE IF NOT EXISTS events (
    uid TEXT PRIMARY KEY, href TEXT NOT NULL, title TEXT, start TEXT,
    status TEXT NOT NULL, msg_key TEXT, updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS runs (
    id INTEGER PRIMARY KEY AUTOINCREMENT, started_at TEXT, finished_at TEXT,
    ok INTEGER, dry_run INTEGER, fetched INTEGER, extracted INTEGER,
    created INTEGER, cancelled INTEGER, error TEXT
);
CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT);
"""


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


@dataclass
class Pending:
    msg_key: str
    message: Message
    events: list[dict]


class Ledger:
    def __init__(self, path: Path):
        self.db = sqlite3.connect(path)
        self.db.row_factory = sqlite3.Row
        self.db.executescript(SCHEMA)

    def close(self):
        self.db.close()

    # --- messages -------------------------------------------------------
    def known(self, msg_key: str) -> bool:
        row = self.db.execute("SELECT status FROM messages WHERE msg_key=?", (msg_key,)).fetchone()
        return row is not None and row["status"] in ("extracted", "skipped")

    def seen(self, account: str, mailbox: str, uidvalidity: int, uid: int) -> bool:
        return self.db.execute("SELECT 1 FROM seen WHERE account=? AND mailbox=? AND uidvalidity=? AND uid=?",
                               (account, mailbox, uidvalidity, uid)).fetchone() is not None

    def mark_seen(self, account: str, mailbox: str, uidvalidity: int, uid: int) -> None:
        with self.db:
            self.db.execute("INSERT OR IGNORE INTO seen VALUES (?,?,?,?)", (account, mailbox, uidvalidity, uid))

    def _upsert(self, account: str, m: Message, **fields) -> None:
        base = dict(msg_key=m.key, account=account, mailbox=m.mailbox, uid=m.uid, message_id=m.message_id,
                    sent_at=m.date.isoformat() if m.date else None, sender=m.sender, subject=m.subject,
                    updated_at=_now())
        base.update(fields)
        cols = ", ".join(base)
        marks = ", ".join("?" for _ in base)
        updates = ", ".join(f"{c}=excluded.{c}" for c in base if c != "msg_key")
        with self.db:
            self.db.execute(f"INSERT INTO messages ({cols}) VALUES ({marks}) "
                            f"ON CONFLICT(msg_key) DO UPDATE SET {updates}", tuple(base.values()))

    def record_extraction(self, account: str, m: Message, events: list[dict]) -> None:
        self._upsert(account, m, status="extracted", events_json=json.dumps(events, ensure_ascii=False),
                     error=None, applied=0)

    def record_failure(self, account: str, m: Message, error: str) -> bool:
        """Count a failed attempt; True once the message is given up (skipped)."""
        row = self.db.execute("SELECT attempts FROM messages WHERE msg_key=?", (m.key,)).fetchone()
        attempts = (row["attempts"] if row else 0) + 1
        give_up = attempts >= MAX_ATTEMPTS
        self._upsert(account, m, status="skipped" if give_up else "failed", attempts=attempts, error=error[:500])
        return give_up

    def pending(self) -> list[Pending]:
        rows = self.db.execute("SELECT * FROM messages WHERE status='extracted' AND applied=0 "
                               "ORDER BY sent_at, uid").fetchall()
        out = []
        for r in rows:
            sent = datetime.fromisoformat(r["sent_at"]) if r["sent_at"] else None
            msg = Message(r["mailbox"], r["uid"], r["message_id"] or "", sent, r["sender"] or "",
                          r["subject"] or "", "")
            out.append(Pending(r["msg_key"], msg, json.loads(r["events_json"] or "[]")))
        return out

    def discard_pending(self) -> int:
        """Drop the not-yet-written answers (the user declined them)."""
        with self.db:
            return self.db.execute("UPDATE messages SET applied=1, updated_at=? WHERE status='extracted' AND applied=0",
                                   (_now(),)).rowcount

    def mark_applied(self, msg_key: str) -> None:
        with self.db:
            self.db.execute("UPDATE messages SET applied=1, updated_at=? WHERE msg_key=?", (_now(), msg_key))

    def counts(self) -> dict[str, int]:
        rows = self.db.execute("SELECT status, COUNT(*) n FROM messages GROUP BY status").fetchall()
        return {r["status"]: r["n"] for r in rows}

    # --- cursors --------------------------------------------------------
    def cursor(self, account: str, mailbox: str) -> tuple[int, int] | None:
        r = self.db.execute("SELECT uidvalidity, last_uid FROM cursors WHERE account=? AND mailbox=?",
                            (account, mailbox)).fetchone()
        return (r["uidvalidity"], r["last_uid"]) if r else None

    def set_cursor(self, account: str, mailbox: str, uidvalidity: int, last_uid: int) -> None:
        with self.db:
            self.db.execute("INSERT OR REPLACE INTO cursors VALUES (?,?,?,?)", (account, mailbox, uidvalidity, last_uid))

    # --- events ---------------------------------------------------------
    def event(self, uid: str):
        return self.db.execute("SELECT * FROM events WHERE uid=?", (uid,)).fetchone()

    def save_event(self, uid: str, href: str, title: str, start: str, status: str, msg_key: str) -> None:
        with self.db:
            self.db.execute("INSERT OR REPLACE INTO events VALUES (?,?,?,?,?,?,?)",
                            (uid, href, title, start, status, msg_key, _now()))

    def events(self) -> list[sqlite3.Row]:
        return self.db.execute("SELECT * FROM events ORDER BY start").fetchall()

    def forget_event(self, uid: str) -> None:
        with self.db:
            self.db.execute("DELETE FROM events WHERE uid=?", (uid,))

    # --- runs -----------------------------------------------------------
    def start_run(self, dry_run: bool) -> int:
        with self.db:
            return self.db.execute("INSERT INTO runs (started_at, dry_run) VALUES (?,?)",
                                   (_now(), int(dry_run))).lastrowid

    def finish_run(self, run_id: int, ok: bool, stats: dict, error: str | None) -> None:
        with self.db:
            self.db.execute("UPDATE runs SET finished_at=?, ok=?, fetched=?, extracted=?, created=?, cancelled=?, "
                            "error=? WHERE id=?",
                            (_now(), int(ok), stats.get("fetched", 0), stats.get("extracted", 0),
                             stats.get("created", 0), stats.get("cancelled", 0), error, run_id))

    def recent_runs(self, n: int = 5) -> list[sqlite3.Row]:
        return self.db.execute("SELECT * FROM runs ORDER BY id DESC LIMIT ?", (n,)).fetchall()

    def get_meta(self, key: str) -> str | None:
        r = self.db.execute("SELECT value FROM meta WHERE key=?", (key,)).fetchone()
        return r["value"] if r else None

    def set_meta(self, key: str, value: str) -> None:
        with self.db:
            self.db.execute("INSERT OR REPLACE INTO meta VALUES (?,?)", (key, value))
