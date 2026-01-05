"""Trace storage for rounds and events (training-ready logging)."""

from __future__ import annotations

import json
import sqlite3
import threading
import time
from pathlib import Path
from typing import Any, Dict, Iterable, Optional

from .ids import get_data_dir, new_call_id, new_round_id, normalize_conversation_id
from .schemas import versions


class TraceStore:
    """SQLite-backed trace store with round/event ordering."""

    def __init__(self, path: str | Path | None = None) -> None:
        base = get_data_dir()
        self._path = Path(path or base / "traces.db")
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(self._path, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._lock = threading.Lock()
        self._conv_locks: dict[str, threading.Lock] = {}
        self._setup()

    def _setup(self) -> None:
        with self._lock:
            cur = self._conn.cursor()
            cur.execute(
                """
                CREATE TABLE IF NOT EXISTS rounds (
                    round_id TEXT PRIMARY KEY,
                    schema_version INTEGER,
                    library_id TEXT,
                    bucket_id TEXT,
                    conversation_id TEXT,
                    round_seq INTEGER,
                    state_in TEXT,
                    state_out TEXT,
                    audio_id TEXT,
                    created_at REAL,
                    status TEXT,
                    failure_code TEXT,
                    failure_reason TEXT
                )
                """
            )
            cur.execute(
                """
                CREATE TABLE IF NOT EXISTS events (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    round_id TEXT,
                    schema_version INTEGER,
                    event_seq INTEGER,
                    event_type TEXT,
                    call_id TEXT,
                    timestamp REAL,
                    payload TEXT,
                    status TEXT,
                    failure_code TEXT,
                    failure_reason TEXT
                )
                """
            )
            self._conn.commit()

    def start_round(
        self,
        *,
        library_id: str,
        bucket_id: str,
        conversation_id: str | None,
        state_in: str,
        audio_id: str | None = None,
    ) -> dict[str, Any]:
        """Create a round header and return the persisted record."""
        conversation_id = normalize_conversation_id(conversation_id)
        conv_lock = self._get_conv_lock(conversation_id)
        with conv_lock, self._lock:
            cur = self._conn.cursor()
            cur.execute(
                "SELECT COALESCE(MAX(round_seq), 0) + 1 FROM rounds WHERE conversation_id = ?",
                (conversation_id,),
            )
            round_seq = cur.fetchone()[0]
            round_id = new_round_id()
            created_at = time.time()
            cur.execute(
                """
                INSERT INTO rounds (
                    round_id, schema_version, library_id, bucket_id, conversation_id,
                    round_seq, state_in, state_out, audio_id, created_at,
                    status, failure_code, failure_reason
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    round_id,
                    versions.TRACE_ROUND_VERSION,
                    library_id,
                    bucket_id,
                    conversation_id,
                    round_seq,
                    state_in,
                    None,
                    audio_id,
                    created_at,
                    "ok",
                    None,
                    None,
                ),
            )
            self._conn.commit()
        return {
            "round_id": round_id,
            "round_seq": round_seq,
            "library_id": library_id,
            "bucket_id": bucket_id,
            "conversation_id": conversation_id,
            "state_in": state_in,
            "audio_id": audio_id,
            "created_at": created_at,
            "status": "ok",
        }

    def append_event(
        self,
        round_id: str,
        event_type: str,
        payload: dict[str, Any],
        *,
        status: str = "ok",
        call_id: str | None = None,
        failure_code: str | None = None,
        failure_reason: str | None = None,
        event_seq: int | None = None,
        timestamp: float | None = None,
    ) -> int:
        """Append an event to a round and return the event_seq."""
        payload_json = json.dumps(payload, ensure_ascii=False)
        with self._lock:
            cur = self._conn.cursor()
            if event_seq is None:
                cur.execute(
                    "SELECT COALESCE(MAX(event_seq), 0) + 1 FROM events WHERE round_id = ?",
                    (round_id,),
                )
                event_seq = cur.fetchone()[0]
            ts = timestamp or time.time()
            cur.execute(
                """
                INSERT INTO events (
                    round_id, schema_version, event_seq, event_type, call_id,
                    timestamp, payload, status, failure_code, failure_reason
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    round_id,
                    versions.TRACE_EVENT_VERSION,
                    event_seq,
                    event_type,
                    call_id or new_call_id(),
                    ts,
                    payload_json,
                    status,
                    failure_code,
                    failure_reason,
                ),
            )
            self._conn.commit()
        return event_seq

    def mark_round(
        self,
        round_id: str,
        *,
        status: str,
        state_out: str | None = None,
        failure_code: str | None = None,
        failure_reason: str | None = None,
    ) -> None:
        with self._lock:
            self._conn.execute(
                """
                UPDATE rounds
                SET status = ?, state_out = COALESCE(?, state_out),
                    failure_code = ?, failure_reason = ?
                WHERE round_id = ?
                """,
                (status, state_out, failure_code, failure_reason, round_id),
            )
            self._conn.commit()

    def export_jsonl(self, out_path: str | Path) -> Path:
        """Export rounds and events to a JSONL file (round header then events)."""
        out = Path(out_path)
        rows: Iterable[sqlite3.Row]
        with self._lock, out.open("w", encoding="utf-8") as fh:
            rows = self._conn.execute(
                "SELECT * FROM rounds ORDER BY created_at ASC, round_seq ASC"
            ).fetchall()
            for row in rows:
                fh.write(json.dumps({"type": "round", **dict(row)}, default=str) + "\n")
                events = self._conn.execute(
                    "SELECT * FROM events WHERE round_id = ? ORDER BY event_seq ASC",
                    (row["round_id"],),
                ).fetchall()
                for ev in events:
                    fh.write(json.dumps({"type": "event", **dict(ev)}, default=str) + "\n")
        return out

    def fetch_round(self, round_id: str) -> dict[str, Any] | None:
        with self._lock:
            row = self._conn.execute("SELECT * FROM rounds WHERE round_id = ?", (round_id,)).fetchone()
        return dict(row) if row else None

    def fetch_events(self, round_id: str) -> list[dict[str, Any]]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT * FROM events WHERE round_id = ? ORDER BY event_seq ASC", (round_id,)
            ).fetchall()
        return [dict(r) for r in rows]

    def _get_conv_lock(self, conversation_id: str) -> threading.Lock:
        with self._lock:
            if conversation_id not in self._conv_locks:
                self._conv_locks[conversation_id] = threading.Lock()
            return self._conv_locks[conversation_id]
