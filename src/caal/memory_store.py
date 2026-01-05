from __future__ import annotations

import json
import sqlite3
import threading
import time
from pathlib import Path
from typing import Any, Iterable

from .ids import get_data_dir
from .schemas import versions


MAX_REGISTER_LEN = 140
MEMORY_SCHEMA_VERSION = versions.MEMORY_BUCKET_VERSION


class MemoryStore:
    """Bucket-scoped memory with register summaries and context packages."""

    def __init__(self, path: str | Path | None = None) -> None:
        base = get_data_dir()
        self._path = Path(path or base / "memory_store.db")
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(self._path, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._lock = threading.Lock()
        self._setup()

    def _setup(self) -> None:
        with self._lock:
            cursor = self._conn.cursor()
            cursor.execute(
                """
                CREATE TABLE IF NOT EXISTS buckets (
                    id TEXT PRIMARY KEY,
                    summary TEXT DEFAULT '',
                    last_updated REAL DEFAULT 0,
                    schema_version INTEGER DEFAULT 1
                )
                """
            )
            cursor.execute(
                """
                CREATE TABLE IF NOT EXISTS entries (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    bucket_id TEXT,
                    speaker TEXT,
                    content TEXT,
                    created_at REAL
                )
                """
            )
            cursor.execute(
                """
                CREATE TABLE IF NOT EXISTS memory_items (
                    item_id INTEGER PRIMARY KEY AUTOINCREMENT,
                    bucket_id TEXT,
                    pinned INTEGER DEFAULT 0,
                    reference_score REAL DEFAULT 0,
                    recency REAL DEFAULT 0,
                    content TEXT,
                    metadata TEXT,
                    last_updated REAL
                )
                """
            )
            cursor.execute(
                """
                CREATE TABLE IF NOT EXISTS context_cache (
                    bucket_id TEXT PRIMARY KEY,
                    register_summary TEXT,
                    short_context TEXT,
                    long_context TEXT,
                    items_json TEXT,
                    last_updated REAL,
                    schema_version INTEGER DEFAULT 1
                )
                """
            )
            self._conn.commit()

    # ------------------------------------------------------------------ turns/registers
    def record_turn(self, bucket_id: str, speaker: str, content: str) -> None:
        """Record a turn and refresh register/context cache."""
        with self._lock:
            ts = time.time()
            self._conn.execute(
                "INSERT INTO entries (bucket_id, speaker, content, created_at) VALUES (?, ?, ?, ?)",
                (bucket_id, speaker, content, ts),
            )
            register = self._build_register(bucket_id)
            self._conn.execute(
                """
                INSERT OR REPLACE INTO buckets (id, summary, last_updated, schema_version)
                VALUES (?, ?, ?, ?)
                """,
                (bucket_id, register, ts, MEMORY_SCHEMA_VERSION),
            )
            self._update_context_cache(bucket_id, register, ts)
            self._conn.commit()

    def _build_register(self, bucket_id: str, limit: int = 4) -> str:
        cursor = self._conn.execute(
            "SELECT speaker, content FROM entries WHERE bucket_id = ? ORDER BY id DESC LIMIT ?",
            (bucket_id, limit),
        )
        rows = list(reversed(cursor.fetchall()))
        parts = [f"[{row['speaker']}] {row['content']}" for row in rows]
        summary = " ".join(parts)
        if len(summary) > MAX_REGISTER_LEN:
            summary = summary[: MAX_REGISTER_LEN - 3] + "..."
        return summary

    def list_buckets(self) -> list[str]:
        cursor = self._conn.execute("SELECT id FROM buckets")
        return [row["id"] for row in cursor.fetchall()]

    def get_summary(self, bucket_id: str) -> str | None:
        row = self._conn.execute(
            "SELECT summary FROM buckets WHERE id = ?", (bucket_id,)
        ).fetchone()
        return row["summary"] if row else None

    def append_note(self, bucket_id: str, note: str) -> None:
        self.record_turn(bucket_id, "note", note)

    def clear_bucket(self, bucket_id: str) -> None:
        with self._lock:
            self._conn.execute("DELETE FROM entries WHERE bucket_id = ?", (bucket_id,))
            self._conn.execute("DELETE FROM buckets WHERE id = ?", (bucket_id,))
            self._conn.execute("DELETE FROM memory_items WHERE bucket_id = ?", (bucket_id,))
            self._conn.execute("DELETE FROM context_cache WHERE bucket_id = ?", (bucket_id,))
            self._conn.commit()

    def get_bucket_details(self, bucket_id: str) -> dict[str, Any] | None:
        row = self._conn.execute(
            "SELECT id, summary, last_updated FROM buckets WHERE id = ?", (bucket_id,)
        ).fetchone()
        if row is None:
            return None
        entries = self._conn.execute(
            "SELECT speaker, content FROM entries WHERE bucket_id = ? ORDER BY id DESC LIMIT 5",
            (bucket_id,),
        ).fetchall()
        return {
            "id": row["id"],
            "summary": row["summary"],
            "last_updated": row["last_updated"],
            "recent_entries": [{"speaker": r["speaker"], "content": r["content"]} for r in entries],
        }

    # ------------------------------------------------------------------ memory items / scoring
    def add_memory_item(
        self,
        bucket_id: str,
        content: str,
        *,
        metadata: dict[str, Any] | None = None,
        pinned: bool = False,
        reference_score: float = 0.0,
    ) -> int:
        with self._lock:
            ts = time.time()
            cur = self._conn.cursor()
            cur.execute(
                """
                INSERT INTO memory_items (bucket_id, pinned, reference_score, recency, content, metadata, last_updated)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    bucket_id,
                    int(pinned),
                    reference_score,
                    ts,
                    content,
                    json.dumps(metadata or {}),
                    ts,
                ),
            )
            item_id = cur.lastrowid
            register = self._build_register(bucket_id)
            self._update_context_cache(bucket_id, register, ts)
            self._conn.commit()
            return int(item_id)

    def update_reference(
        self,
        bucket_id: str,
        item_id: int,
        *,
        delta: float = 1.0,
        pinned: bool | None = None,
    ) -> None:
        with self._lock:
            ts = time.time()
            cur = self._conn.cursor()
            cur.execute(
                """
                UPDATE memory_items
                SET reference_score = reference_score + ?, recency = ?,
                    pinned = COALESCE(?, pinned)
                WHERE bucket_id = ? AND item_id = ?
                """,
                (delta, ts, int(pinned) if pinned is not None else None, bucket_id, item_id),
            )
            register = self._build_register(bucket_id)
            self._update_context_cache(bucket_id, register, ts)
            self._conn.commit()

    # ------------------------------------------------------------------ context packages
    def get_context_package(self, bucket_id: str) -> dict[str, Any]:
        row = self._conn.execute(
            "SELECT register_summary, short_context, long_context, items_json, last_updated "
            "FROM context_cache WHERE bucket_id = ?",
            (bucket_id,),
        ).fetchone()
        if row:
            return {
                "bucket_id": bucket_id,
                "register_summary": row["register_summary"],
                "short_context": row["short_context"],
                "long_context": row["long_context"],
                "items": json.loads(row["items_json"] or "[]"),
                "last_updated": row["last_updated"],
            }
        # If no cache, build on the fly
        with self._lock:
            register = self._build_register(bucket_id)
            ts = time.time()
            self._update_context_cache(bucket_id, register, ts)
            self._conn.commit()
        return self.get_context_package(bucket_id)

    def _update_context_cache(self, bucket_id: str, register: str, ts: float) -> None:
        items = self._compute_ranked_items(bucket_id)
        short_context = self._format_items(items, limit=3)
        long_context = self._format_items(items, limit=10)
        self._conn.execute(
            """
            INSERT OR REPLACE INTO context_cache (
                bucket_id, register_summary, short_context, long_context,
                items_json, last_updated, schema_version
            ) VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                bucket_id,
                register,
                short_context,
                long_context,
                json.dumps(items),
                ts,
                MEMORY_SCHEMA_VERSION,
            ),
        )

    def _compute_ranked_items(self, bucket_id: str, *, decay: float = 0.00001) -> list[dict[str, Any]]:
        """Rank items by pin (infinite), reference_score, recency decay."""
        rows = self._conn.execute(
            "SELECT item_id, pinned, reference_score, recency, content, metadata FROM memory_items WHERE bucket_id = ?",
            (bucket_id,),
        ).fetchall()
        now = time.time()
        ranked: list[tuple[float, sqlite3.Row]] = []
        for row in rows:
            recency_score = max(0.0, 1.0 - decay * max(0, now - row["recency"]))
            base = row["reference_score"] + recency_score
            if row["pinned"]:
                base += 1e6
            ranked.append((base, row))
        ranked.sort(key=lambda r: r[0], reverse=True)
        return [
            {
                "item_id": int(row["item_id"]),
                "pinned": bool(row["pinned"]),
                "score": score,
                "content": row["content"],
                "metadata": json.loads(row["metadata"] or "{}"),
            }
            for score, row in ranked
        ]

    def _format_items(self, items: Iterable[dict[str, Any]], limit: int) -> str:
        parts = []
        for item in list(items)[:limit]:
            tag = "[PIN]" if item["pinned"] else ""
            parts.append(f"{tag}{item['content']}")
        return "\n".join(parts)
