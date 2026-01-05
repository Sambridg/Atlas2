"""Helper for toggling local LLM usage."""

from __future__ import annotations

import os


def use_local_llm() -> bool:
    """Return True when the local Ollama LLM should be used."""
    return os.getenv("USE_LOCAL_LLM", "true").strip().lower() in {"1", "true", "yes"}
