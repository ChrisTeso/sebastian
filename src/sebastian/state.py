from __future__ import annotations

import datetime as dt
import os
import sqlite3
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path


def utc_now() -> dt.datetime:
    return dt.datetime.now(dt.UTC)


def iso(value: dt.datetime | None = None) -> str:
    return (value or utc_now()).isoformat(timespec="seconds").replace("+00:00", "Z")


def parse_iso(value: str) -> dt.datetime:
    return dt.datetime.fromisoformat(value.replace("Z", "+00:00"))


@dataclass(frozen=True)
class Trigger:
    id: int
    chat_id: int
    message_rowid: int
    message_guid_hash: str | None
    status: str
    response_hash: str | None
    send_started_at: str | None
    sent_message_rowid: int | None
    attempt_count: int


class StateStore:
    def __init__(self, path: Path) -> None:
        self.path = path
        path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.conn = sqlite3.connect(path, timeout=10)
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("PRAGMA journal_mode = WAL")
        self.conn.execute("PRAGMA synchronous = FULL")
        self.conn.execute("PRAGMA foreign_keys = ON")
        self._init_schema()
        os.chmod(path, 0o600)
        for suffix in ("-wal", "-shm"):
            sidecar = Path(str(path) + suffix)
            if sidecar.exists():
                os.chmod(sidecar, 0o600)

    def close(self) -> None:
        self.conn.close()

    def _init_schema(self) -> None:
        self.conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS meta (
                key TEXT PRIMARY KEY,
                value TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS triggers (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                chat_id INTEGER NOT NULL,
                message_rowid INTEGER NOT NULL,
                message_guid_hash TEXT,
                status TEXT NOT NULL,
                response_hash TEXT,
                discovered_at TEXT NOT NULL,
                last_attempt_at TEXT,
                next_attempt_at TEXT,
                send_started_at TEXT,
                sent_at TEXT,
                sent_message_rowid INTEGER,
                attempt_count INTEGER NOT NULL DEFAULT 0,
                error_code TEXT,
                UNIQUE(chat_id, message_rowid)
            );
            CREATE INDEX IF NOT EXISTS idx_triggers_status ON triggers(status, next_attempt_at);
            CREATE TABLE IF NOT EXISTS rate_events (
                trigger_id INTEGER PRIMARY KEY,
                chat_id INTEGER NOT NULL,
                occurred_at TEXT NOT NULL,
                FOREIGN KEY(trigger_id) REFERENCES triggers(id)
            );
            CREATE INDEX IF NOT EXISTS idx_rate_chat_time ON rate_events(chat_id, occurred_at);
            CREATE TABLE IF NOT EXISTS api_calls (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                trigger_id INTEGER NOT NULL,
                called_at TEXT NOT NULL,
                FOREIGN KEY(trigger_id) REFERENCES triggers(id)
            );
            CREATE INDEX IF NOT EXISTS idx_api_calls_time ON api_calls(called_at);
            """
        )
        columns = {
            str(row[1]) for row in self.conn.execute("PRAGMA table_info(triggers)").fetchall()
        }
        if "message_guid_hash" not in columns:
            self.conn.execute("ALTER TABLE triggers ADD COLUMN message_guid_hash TEXT")
        self.conn.execute(
            "CREATE UNIQUE INDEX IF NOT EXISTS idx_triggers_message_guid_hash "
            "ON triggers(message_guid_hash) WHERE message_guid_hash IS NOT NULL"
        )
        self.conn.commit()

    @contextmanager
    def _immediate_transaction(self):
        self.conn.execute("BEGIN IMMEDIATE")
        try:
            yield
        except Exception:
            self.conn.rollback()
            raise
        else:
            self.conn.commit()

    def get_meta(self, key: str) -> str | None:
        row = self.conn.execute("SELECT value FROM meta WHERE key = ?", (key,)).fetchone()
        return str(row[0]) if row else None

    def set_meta(self, key: str, value: str) -> None:
        self.conn.execute(
            "INSERT INTO meta(key, value) VALUES (?, ?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
            (key, value),
        )
        self.conn.commit()

    def initialize_cursor(self, latest_rowid: int) -> int:
        current = self.get_meta("last_seen_rowid")
        if current is None:
            self.set_meta("last_seen_rowid", str(latest_rowid))
            return latest_rowid
        return int(current)

    def cursor(self) -> int:
        return int(self.get_meta("last_seen_rowid") or 0)

    def advance_cursor(self, rowid: int) -> None:
        self.set_meta("last_seen_rowid", str(max(self.cursor(), rowid)))

    def discover_trigger(
        self,
        *,
        chat_id: int,
        message_rowid: int,
        message_guid_hash: str | None = None,
        per_conversation_limit: int,
        now: dt.datetime | None = None,
    ) -> str:
        moment = now or utc_now()
        timestamp = iso(moment)
        cutoff = iso(moment - dt.timedelta(minutes=1))
        with self._immediate_transaction():
            existing = self.conn.execute(
                """
                SELECT status FROM triggers
                WHERE (chat_id = ? AND message_rowid = ?)
                   OR (? IS NOT NULL AND message_guid_hash = ?)
                """,
                (chat_id, message_rowid, message_guid_hash, message_guid_hash),
            ).fetchone()
            if existing:
                return "duplicate"
            recent = int(
                self.conn.execute(
                    "SELECT COUNT(*) FROM rate_events WHERE chat_id = ? AND occurred_at >= ?",
                    (chat_id, cutoff),
                ).fetchone()[0]
            )
            status = "pending" if recent < per_conversation_limit else "rate_limited"
            cursor = self.conn.execute(
                """
                INSERT INTO triggers(
                    chat_id, message_rowid, message_guid_hash, status, discovered_at, next_attempt_at
                ) VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    chat_id,
                    message_rowid,
                    message_guid_hash,
                    status,
                    timestamp,
                    timestamp if status == "pending" else None,
                ),
            )
            if status == "pending":
                self.conn.execute(
                    "INSERT INTO rate_events(trigger_id, chat_id, occurred_at) VALUES (?, ?, ?)",
                    (cursor.lastrowid, chat_id, timestamp),
                )
        return status

    def _trigger_from_row(self, row: sqlite3.Row) -> Trigger:
        return Trigger(
            id=int(row["id"]),
            chat_id=int(row["chat_id"]),
            message_rowid=int(row["message_rowid"]),
            message_guid_hash=(
                str(row["message_guid_hash"]) if row["message_guid_hash"] else None
            ),
            status=str(row["status"]),
            response_hash=str(row["response_hash"]) if row["response_hash"] else None,
            send_started_at=str(row["send_started_at"]) if row["send_started_at"] else None,
            sent_message_rowid=(
                int(row["sent_message_rowid"]) if row["sent_message_rowid"] is not None else None
            ),
            attempt_count=int(row["attempt_count"]),
        )

    def trigger_by_id(self, trigger_id: int) -> Trigger | None:
        row = self.conn.execute("SELECT * FROM triggers WHERE id = ?", (trigger_id,)).fetchone()
        return self._trigger_from_row(row) if row else None

    def max_trigger_id(self) -> int:
        row = self.conn.execute("SELECT COALESCE(MAX(id), 0) FROM triggers").fetchone()
        return int(row[0] if row else 0)

    def triggers_after_id(self, trigger_id: int) -> list[Trigger]:
        rows = self.conn.execute(
            "SELECT * FROM triggers WHERE id > ? ORDER BY id ASC", (trigger_id,)
        ).fetchall()
        return [self._trigger_from_row(row) for row in rows]

    def due_triggers(self, now: dt.datetime | None = None) -> list[Trigger]:
        timestamp = iso(now)
        rows = self.conn.execute(
            """
            SELECT * FROM triggers
            WHERE status = 'pending' AND (next_attempt_at IS NULL OR next_attempt_at <= ?)
            ORDER BY message_rowid ASC
            """,
            (timestamp,),
        ).fetchall()
        return [self._trigger_from_row(row) for row in rows]

    def sending_triggers(self) -> list[Trigger]:
        rows = self.conn.execute(
            "SELECT * FROM triggers WHERE status = 'sending' ORDER BY message_rowid ASC"
        ).fetchall()
        return [self._trigger_from_row(row) for row in rows]

    def recover_interrupted_generation(self) -> int:
        with self.conn:
            cursor = self.conn.execute(
                "UPDATE triggers SET status='pending', next_attempt_at=? WHERE status='generating'",
                (iso(),),
            )
        return cursor.rowcount

    def recover_interrupted_agents(self) -> int:
        with self.conn:
            cursor = self.conn.execute(
                """
                UPDATE triggers SET status='agent_interrupted', next_attempt_at=NULL,
                    error_code='service_restarted'
                WHERE status='agent_running'
                """
            )
        return cursor.rowcount

    def mark_agent_running(self, trigger_id: int) -> None:
        with self.conn:
            self.conn.execute(
                """
                UPDATE triggers SET status='agent_running', last_attempt_at=?,
                    attempt_count=attempt_count+1, next_attempt_at=NULL, error_code=NULL
                WHERE id=?
                """,
                (iso(), trigger_id),
            )

    def reserve_api_call(self, trigger_id: int, daily_limit: int, now: dt.datetime | None = None) -> bool:
        moment = now or utc_now()
        start = moment.replace(hour=0, minute=0, second=0, microsecond=0)
        with self._immediate_transaction():
            count = int(
                self.conn.execute(
                    "SELECT COUNT(*) FROM api_calls WHERE called_at >= ?", (iso(start),)
                ).fetchone()[0]
            )
            if count >= daily_limit:
                self.conn.execute(
                    "UPDATE triggers SET status='daily_limited', error_code='daily_limit' WHERE id=?",
                    (trigger_id,),
                )
                return False
            self.conn.execute(
                "INSERT INTO api_calls(trigger_id, called_at) VALUES (?, ?)",
                (trigger_id, iso(moment)),
            )
            self.conn.execute(
                "UPDATE triggers SET status='generating', last_attempt_at=?, attempt_count=attempt_count+1 WHERE id=?",
                (iso(moment), trigger_id),
            )
        return True

    def mark_sending(self, trigger_id: int, response_hash: str, now: dt.datetime | None = None) -> None:
        with self.conn:
            self.conn.execute(
                """
                UPDATE triggers SET status='sending', response_hash=?, send_started_at=?,
                    error_code=NULL WHERE id=?
                """,
                (response_hash, iso(now), trigger_id),
            )

    def mark_sent(self, trigger_id: int, sent_message_rowid: int | None = None) -> None:
        with self.conn:
            self.conn.execute(
                """
                UPDATE triggers SET status='sent', sent_at=?, sent_message_rowid=?,
                    response_hash=NULL, send_started_at=NULL, next_attempt_at=NULL,
                    error_code=NULL WHERE id=?
                """,
                (iso(), sent_message_rowid, trigger_id),
            )

    def prune(self, retention_days: int, now: dt.datetime | None = None) -> None:
        moment = now or utc_now()
        minute_cutoff = iso(moment - dt.timedelta(minutes=1))
        day_start = iso(moment.replace(hour=0, minute=0, second=0, microsecond=0))
        trigger_cutoff = iso(moment - dt.timedelta(days=retention_days))
        with self.conn:
            self.conn.execute("DELETE FROM rate_events WHERE occurred_at < ?", (minute_cutoff,))
            self.conn.execute("DELETE FROM api_calls WHERE called_at < ?", (day_start,))
            self.conn.execute(
                """
                DELETE FROM triggers
                WHERE discovered_at < ?
                  AND status IN (
                    'sent', 'ignored', 'unavailable', 'rate_limited', 'daily_limited',
                    'agent_interrupted'
                  )
                  AND id NOT IN (SELECT trigger_id FROM rate_events)
                  AND id NOT IN (SELECT trigger_id FROM api_calls)
                """,
                (trigger_cutoff,),
            )

    def schedule_retry(self, trigger_id: int, delay_seconds: int, error_code: str) -> None:
        when = utc_now() + dt.timedelta(seconds=delay_seconds)
        with self.conn:
            self.conn.execute(
                """
                UPDATE triggers SET status='pending', response_hash=NULL, send_started_at=NULL,
                    next_attempt_at=?, error_code=? WHERE id=?
                """,
                (iso(when), error_code, trigger_id),
            )

    def mark_terminal(self, trigger_id: int, status: str, error_code: str | None = None) -> None:
        allowed = {
            "ignored",
            "unavailable",
            "rate_limited",
            "daily_limited",
            "agent_interrupted",
        }
        if status not in allowed:
            raise ValueError("Unsupported terminal trigger status.")
        with self.conn:
            self.conn.execute(
                "UPDATE triggers SET status=?, error_code=?, next_attempt_at=NULL WHERE id=?",
                (status, error_code, trigger_id),
            )

    def status_counts(self) -> dict[str, int]:
        rows = self.conn.execute(
            "SELECT status, COUNT(*) FROM triggers GROUP BY status ORDER BY status"
        ).fetchall()
        return {str(row[0]): int(row[1]) for row in rows}

    def api_calls_today(self) -> int:
        start = utc_now().replace(hour=0, minute=0, second=0, microsecond=0)
        return int(
            self.conn.execute(
                "SELECT COUNT(*) FROM api_calls WHERE called_at >= ?", (iso(start),)
            ).fetchone()[0]
        )
