"""
CAAL - Voice Assistant
======================

A modular voice assistant with n8n workflow integrations and local LLM support.

Core Components:
    OllamaLLM: Native Ollama LLM with think parameter support for Qwen3

STT/TTS:
    - Speaches container for Faster-Whisper STT
    - Kokoro container for TTS

Integrations:
    n8n: Workflow discovery and execution via n8n MCP

Example:
    >>> from caal import OllamaLLM
    >>> from caal.integrations import load_mcp_config
    >>>
    >>> llm = OllamaLLM(model="qwen3:8b", think=False)
    >>> mcp_configs = load_mcp_config()

Repository: https://github.com/CoreWorxLab/caal
License: MIT
"""

__version__ = "0.1.0"
__author__ = "CoreWorxLab"

try:
    from .llm import OllamaLLM
except Exception:
    # Optional dependency: allow core modules (memory/trace) without LLM deps
    OllamaLLM = None

from .local_llm import use_local_llm

__all__ = [
    "OllamaLLM",
    "use_local_llm",
    "__version__",
]
