"""ID generation and helpers for correlation across the system."""

from __future__ import annotations

import os
import socket
import uuid
from pathlib import Path
from typing import Optional

# We use UUIDv7 for monotonic ordering where available (Python 3.11+).
# Fallback to UUID4 if the runtime does not support uuid.uuid7.
_uuid7 = getattr(uuid, "uuid7", None)


def _uuid7_str() -> str:
    if _uuid7:
        return str(_uuid7())
    return str(uuid.uuid4())


def get_data_dir() -> Path:
    """Return the default data directory (repo_root/data)."""
    base = Path(__file__).resolve().parents[2]
    data_dir = base / "data"
    data_dir.mkdir(parents=True, exist_ok=True)
    return data_dir


def _library_id_path() -> Path:
    return get_data_dir() / "library_id"


def generate_library_id() -> str:
    """Generate a stable library_id and persist it unless overridden."""
    env_id = os.getenv("CAAL_LIBRARY_ID")
    if env_id:
        return env_id.strip()

    path = _library_id_path()
    if path.exists():
        return path.read_text().strip()

    host = socket.gethostname()
    new_id = f"library:{host}:{_uuid7_str()}"
    path.write_text(new_id)
    return new_id


def make_bucket_id(conversation_id: str | None = None) -> str:
    """Derive a bucket id from a conversation id (distinct fields)."""
    cid = (conversation_id or "default").strip() or "default"
    return f"bucket:{cid}"


def new_round_id() -> str:
    return _uuid7_str()


def new_call_id() -> str:
    return _uuid7_str()


def new_audio_id() -> str:
    return _uuid7_str()


def normalize_conversation_id(conversation_id: Optional[str]) -> str:
    cid = (conversation_id or "default").strip()
    return cid or "default"
