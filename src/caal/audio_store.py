"""Audio artifact metadata store with retention and pinning."""

from __future__ import annotations

import hashlib
import sqlite3
import threading
import time
from pathlib import Path
from typing import Any, Optional

from .ids import get_data_dir, new_audio_id
from .schemas import versions


class AudioStore:
    """SQLite store for audio artifact metadata."""

    def __init__(self, path: str | Path | None = None) -> None:
        base = get_data_dir()
        self._path = Path(path or base / "audio.db")
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(self._path, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._lock = threading.Lock()
        self._setup()

    def _setup(self) -> None:
        with self._lock:
            self._conn.execute(
                """
                CREATE TABLE IF NOT EXISTS audio_artifacts (
                    audio_id TEXT PRIMARY KEY,
                    schema_version INTEGER,
                    path TEXT,
                    sha256 TEXT,
                    duration_ms INTEGER,
                    codec TEXT,
                    sample_rate INTEGER,
                    created_at REAL,
                    retention_ttl REAL,
                    pinned INTEGER DEFAULT 0,
                    rms_preview TEXT
                )
                """
            )
            self._conn.commit()

    def add_artifact(
        self,
        *,
        path: str | Path,
        audio_id: str | None = None,
        sha256: str | None = None,
        duration_ms: int | None = None,
        codec: str | None = None,
        sample_rate: int | None = None,
        retention_days: int = 30,
        pinned: bool = False,
        rms_preview: str | None = None,
    ) -> dict[str, Any]:
        """Register an audio artifact and return its metadata."""
        aid = audio_id or new_audio_id()
        audio_path = Path(path)
        computed_hash = sha256 or self._hash_file(audio_path)
        ts = time.time()
        ttl_seconds = retention_days * 86400
        with self._lock:
            self._conn.execute(
                """
                INSERT OR REPLACE INTO audio_artifacts (
                    audio_id, schema_version, path, sha256, duration_ms, codec,
                    sample_rate, created_at, retention_ttl, pinned, rms_preview
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    aid,
                    versions.AUDIO_ARTIFACT_VERSION,
                    str(audio_path),
                    computed_hash,
                    duration_ms,
                    codec,
                    sample_rate,
                    ts,
                    ttl_seconds,
                    int(pinned),
                    rms_preview,
                ),
            )
            self._conn.commit()
        return self.get_artifact(aid) or {}

    def add_reference(
        self,
        *,
        audio_id: str | None = None,
        codec: str | None = None,
        sample_rate: int | None = None,
        retention_days: int = 30,
        pinned: bool = False,
    ) -> dict[str, Any]:
        """Register a metadata-only audio reference when the file path is unavailable."""
        aid = audio_id or new_audio_id()
        ts = time.time()
        ttl_seconds = retention_days * 86400
        with self._lock:
            self._conn.execute(
                """
                INSERT OR REPLACE INTO audio_artifacts (
                    audio_id, schema_version, path, sha256, duration_ms, codec,
                    sample_rate, created_at, retention_ttl, pinned, rms_preview
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    aid,
                    versions.AUDIO_ARTIFACT_VERSION,
                    "",
                    "",
                    None,
                    codec,
                    sample_rate,
                    ts,
                    ttl_seconds,
                    int(pinned),
                    None,
                ),
            )
            self._conn.commit()
        return self.get_artifact(aid) or {}

    def get_artifact(self, audio_id: str) -> Optional[dict[str, Any]]:
        row = self._conn.execute(
            "SELECT * FROM audio_artifacts WHERE audio_id = ?", (audio_id,)
        ).fetchone()
        return dict(row) if row else None

    def list_artifacts(self, limit: int = 50) -> list[dict[str, Any]]:
        rows = self._conn.execute(
            "SELECT * FROM audio_artifacts ORDER BY created_at DESC LIMIT ?", (limit,)
        ).fetchall()
        return [dict(r) for r in rows]

    def cleanup_expired(self) -> int:
        """Delete expired, unpinned artifacts based on retention_ttl."""
        now = time.time()
        with self._lock:
            cur = self._conn.cursor()
            cur.execute(
                """
                DELETE FROM audio_artifacts
                WHERE pinned = 0 AND created_at + retention_ttl < ?
                """,
                (now,),
            )
            deleted = cur.rowcount
            self._conn.commit()
            return deleted or 0

    def _hash_file(self, path: Path) -> str:
        h = hashlib.sha256()
        try:
            with path.open("rb") as fh:
                for chunk in iter(lambda: fh.read(8192), b""):
                    h.update(chunk)
            return h.hexdigest()
        except FileNotFoundError:
            return ""
