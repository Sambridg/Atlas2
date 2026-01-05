from __future__ import annotations

import sqlite3
import threading
import time
import uuid
from pathlib import Path
from dataclasses import dataclass

from .schemas import versions
from .types import EventType


@dataclass
class JobRecord:
    job_id: str
    topic: str
    query: str
    status: str
    created_at: float
    updated_at: float
    result: str | None = None
    library_id: str | None = None
    conversation_id: str | None = None
    bucket_id: str | None = None
    schema_version: int = versions.JOB_VERSION


class JobQueue:
    def __init__(self, path: str | Path | None = None) -> None:
        base = Path(__file__).resolve().parents[2]
        data_dir = base / "data"
        self._path = Path(path or data_dir / "jobs.db")
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(self._path, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._lock = threading.Lock()
        self._setup()

    def _setup(self) -> None:
        with self._lock:
            self._conn.execute(
                """
                CREATE TABLE IF NOT EXISTS jobs (
                    job_id TEXT PRIMARY KEY,
                    schema_version INTEGER,
                    topic TEXT,
                    query TEXT,
                    status TEXT,
                    created_at REAL,
                    updated_at REAL,
                    result TEXT,
                    library_id TEXT,
                    conversation_id TEXT,
                    bucket_id TEXT
                )
                """
            )
            self._conn.commit()

    def create_job(
        self,
        topic: str,
        query: str,
        status: str = "queued",
        *,
        library_id: str | None = None,
        conversation_id: str | None = None,
        bucket_id: str | None = None,
    ) -> JobRecord:
        job_id = str(uuid.uuid4())
        ts = time.time()
        with self._lock:
            self._conn.execute(
                """
                INSERT INTO jobs (job_id, schema_version, topic, query, status, created_at, updated_at, library_id, conversation_id, bucket_id)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    job_id,
                    versions.JOB_VERSION,
                    topic,
                    query,
                    status,
                    ts,
                    ts,
                    library_id,
                    conversation_id,
                    bucket_id,
                ),
            )
            self._conn.commit()
        return JobRecord(job_id, topic, query, status, ts, ts, None, library_id, conversation_id, bucket_id, versions.JOB_VERSION)

    def update_job(self, job_id: str, status: str, result: str | None = None) -> None:
        with self._lock:
            ts = time.time()
            self._conn.execute(
                """
                UPDATE jobs
                SET status = ?, result = ?, updated_at = ?
                WHERE job_id = ?
                """,
                (status, result, ts, job_id),
            )
            self._conn.commit()

    def get_job(self, job_id: str) -> JobRecord | None:
        row = self._conn.execute(
            "SELECT * FROM jobs WHERE job_id = ?", (job_id,)
        ).fetchone()
        if row is None:
            return None
        return JobRecord(
            job_id=row["job_id"],
            schema_version=row["schema_version"] if "schema_version" in row.keys() else versions.JOB_VERSION,
            topic=row["topic"],
            query=row["query"],
            status=row["status"],
            created_at=row["created_at"],
            updated_at=row["updated_at"],
            result=row["result"],
            library_id=row["library_id"],
            conversation_id=row["conversation_id"],
            bucket_id=row["bucket_id"],
        )

    def list_jobs(self) -> list[JobRecord]:
        rows = self._conn.execute("SELECT * FROM jobs ORDER BY created_at DESC").fetchall()
        return [
            JobRecord(
                job_id=row["job_id"],
                schema_version=row["schema_version"] if "schema_version" in row.keys() else versions.JOB_VERSION,
                topic=row["topic"],
                query=row["query"],
                status=row["status"],
                created_at=row["created_at"],
                updated_at=row["updated_at"],
                result=row["result"],
                library_id=row["library_id"],
                conversation_id=row["conversation_id"],
                bucket_id=row["bucket_id"],
            )
            for row in rows
        ]


class TracingJobQueue(JobQueue):
    """JobQueue that emits trace events when enqueuing/updating jobs."""

    def __init__(self, trace_store=None, *args, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        self._trace_store = trace_store

    def _log(self, event_type: str, payload: dict[str, object]) -> None:
        if not self._trace_store:
            return
        try:
            round_id = payload.get("round_id")
            if round_id:
                self._trace_store.append_event(round_id, event_type, payload)
        except Exception:
            # Logging failures should not break queue behavior
            pass

    def create_job(
        self,
        topic: str,
        query: str,
        status: str = "queued",
        *,
        library_id: str | None = None,
        conversation_id: str | None = None,
        bucket_id: str | None = None,
        round_id: str | None = None,
    ) -> JobRecord:
        job = super().create_job(
            topic,
            query,
            status,
            library_id=library_id,
            conversation_id=conversation_id,
            bucket_id=bucket_id,
        )
        self._log(
            EventType.JOB_ENQUEUED.value,
            {
                "round_id": round_id,
                "job_id": job.job_id,
                "topic": topic,
                "query": query,
                "class": status,
                "library_id": library_id,
                "conversation_id": conversation_id,
                "bucket_id": bucket_id,
            },
        )
        return job

    def update_job(self, job_id: str, status: str, result: str | None = None, round_id: str | None = None) -> None:
        super().update_job(job_id, status, result)
        event_type = EventType.JOB_PROGRESS.value
        if status in {"completed", "failed"}:
            event_type = EventType.JOB_RESULT.value
        self._log(
            event_type,
            {
                "round_id": round_id,
                "job_id": job_id,
                "status": status,
                "result": result,
            },
        )
