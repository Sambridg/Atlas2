#!/usr/bin/env python3
"""
CAAL Voice Framework - Voice Agent
==================================

A voice assistant with MCP integrations for n8n workflows.

Usage:
    python voice_agent.py dev

Configuration:
    - .env: Environment variables (MCP URL, model settings)
    - prompt/default.md: Agent system prompt

Environment Variables:
    SPEACHES_URL        - Speaches STT service URL (default: "http://speaches:8000")
    KOKORO_URL          - Kokoro TTS service URL (default: "http://kokoro:8880")
    WHISPER_MODEL       - Whisper model for STT (default: "Systran/faster-whisper-small")
    TTS_VOICE           - Kokoro voice name (default: "af_heart")
    OLLAMA_MODEL        - Ollama model name (default: "ministral-3:8b")
    OLLAMA_THINK        - Enable thinking mode (default: "false")
    TIMEZONE            - Timezone for date/time (default: "Pacific Time")
"""

from __future__ import annotations

import asyncio
import logging
import os
import sys
import time

import requests

# Add src directory to path for local development
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "src"))

from dotenv import load_dotenv

# Load environment variables from .env
_script_dir = os.path.dirname(os.path.abspath(__file__))
load_dotenv(os.path.join(_script_dir, ".env"))

from livekit import agents
from livekit.agents import AgentSession, Agent, mcp
from livekit.plugins import silero, openai

from caal.integrations import (
    load_mcp_config,
    initialize_mcp_servers,
    WebSearchTools,
    discover_n8n_workflows,
)
from caal.ids import generate_library_id, make_bucket_id, new_call_id, normalize_conversation_id
from caal.memory_store import MemoryStore
from caal.audio_store import AudioStore
from caal.secret_scanner import scrub as scrub_secrets
from caal.job_queue import TracingJobQueue
from caal.router import decide_route
from caal.trace_store import TraceStore
from caal.state_tracker import GuardState, StateTracker
from caal.prompt_templates import build_prompt
from caal import session_registry
from caal.stt import WakeWordGatedSTT
from caal.types import EventType, ValidatorStatus
from caal.validators import run_validators

logger = logging.getLogger("voice-agent")
logger.setLevel(logging.INFO)

# Suppress verbose logs from dependencies
logging.getLogger("httpx").setLevel(logging.WARNING)
logging.getLogger("httpcore").setLevel(logging.WARNING)
logging.getLogger("openai._base_client").setLevel(logging.WARNING)
logging.getLogger("mcp").setLevel(logging.WARNING)  # MCP client SSE/JSON-RPC spam
logging.getLogger("livekit").setLevel(logging.WARNING)  # LiveKit internal logs
logging.getLogger("livekit_api").setLevel(logging.WARNING)  # Rust bridge logs
logging.getLogger("livekit.agents.voice").setLevel(logging.WARNING)  # Suppress segment sync warnings
logging.getLogger("livekit.plugins.openai.tts").setLevel(logging.WARNING)  # Suppress "no request_id" spam
logging.getLogger("caal").setLevel(logging.INFO)  # Our package - INFO level

# =============================================================================
# Configuration
# =============================================================================

# Infrastructure config (from .env only - URLs, tokens, etc.)
SPEACHES_URL = os.getenv("SPEACHES_URL", "http://speaches:8000")
WHISPER_MODEL = os.getenv("WHISPER_MODEL", "Systran/faster-whisper-small")
KOKORO_URL = os.getenv("KOKORO_URL", "http://kokoro:8880")
TTS_MODEL = os.getenv("TTS_MODEL", "kokoro")  # "kokoro" for Kokoro-FastAPI, "prince-canuma/Kokoro-82M" for mlx-audio
TIMEZONE_ID = os.getenv("TIMEZONE", "America/Los_Angeles")
TIMEZONE_DISPLAY = os.getenv("TIMEZONE_DISPLAY", "Pacific Time")
# OpenAI-compatible LLM configuration
OPENAI_MODEL = os.getenv("OPENAI_MODEL", "gpt-4o-mini")
OPENAI_TEMPERATURE = float(os.getenv("OPENAI_TEMPERATURE", "0.7"))
OPENAI_NUM_CTX = int(os.getenv("OPENAI_NUM_CTX", "8192"))
OPENAI_BASE_URL = os.getenv("OPENAI_BASE_URL")

# Import settings module for runtime-configurable values
from caal import settings as settings_module

MEMORY_DB = os.getenv("MEMORY_DB_PATH", None)
memory_store = MemoryStore(MEMORY_DB)
audio_store = AudioStore(os.getenv("AUDIO_DB_PATH", None))
job_queue = TracingJobQueue(trace_store=TraceStore(), path=os.getenv("JOB_DB_PATH", None))
state_tracker = StateTracker()
trace_store = TraceStore()
LIBRARY_ID = generate_library_id()
TRACE_ENABLED = os.getenv("TRACE_ENABLED", "true").lower() == "true"
AUDIO_RETENTION_DAYS = int(os.getenv("AUDIO_RETENTION_DAYS", "30"))
LLM_RETRY_LIMIT = int(os.getenv("LLM_RETRY_LIMIT", "2"))

# Startup dependency checks (optional fast-fail)
REQUIRED_ENVS = ["OPENAI_API_KEY", "OPENAI_MODEL"]
for key in REQUIRED_ENVS:
    if not os.getenv(key):
        logger.warning(f"Missing required env {key}; LLM calls may fail.")


def get_runtime_settings() -> dict:
    """Get runtime-configurable settings.

    These can be changed via the settings UI without rebuilding.
    Falls back to .env values for backwards compatibility.
    """
    settings = settings_module.load_settings()

    return {
        "tts_voice": settings.get("tts_voice") or os.getenv("TTS_VOICE", "am_puck"),
        "model": settings.get("model") or os.getenv("OPENAI_MODEL", "gpt-4o-mini"),
        "temperature": settings.get("temperature", float(os.getenv("OPENAI_TEMPERATURE", "0.7"))),
        "num_ctx": settings.get("num_ctx", int(os.getenv("OPENAI_NUM_CTX", "8192"))),
        "max_turns": settings.get("max_turns", int(os.getenv("OLLAMA_MAX_TURNS", "20"))),
        "tool_cache_size": settings.get("tool_cache_size", int(os.getenv("TOOL_CACHE_SIZE", "3"))),
    }


def load_prompt() -> str:
    """Load and populate prompt template with date context."""
    return settings_module.load_prompt_with_context(
        timezone_id=TIMEZONE_ID,
        timezone_display=TIMEZONE_DISPLAY,
    )


def _bucket_id_from_decision(decision: dict) -> str:
    return (
        decision.get("topic")
        or decision.get("facet_id")
        or decision.get("command_id")
        or "general"
    )


def _get_memory_summary(decision: dict[str, object]) -> str:
    bucket = decision.get("bucket_id") or _bucket_id_from_decision(decision)
    summary = memory_store.get_summary(bucket)
    return summary or "No stored summary yet."

def _log_event(round_id: str | None, event_type: str, payload: dict, status: str = "ok") -> None:
    if not TRACE_ENABLED or not round_id:
        return
    try:
        trace_store.append_event(round_id, event_type, payload, status=status)
    except Exception:
        logger.exception("Failed to log trace event", extra={"event_type": event_type})


def _trim_prompt(prompt: str, max_len: int = 8000) -> tuple[str, str, str, bool]:
    """Return head, tail, hash, truncated flag."""
    import hashlib

    if len(prompt) <= max_len:
        digest = hashlib.sha256(prompt.encode("utf-8", errors="ignore")).hexdigest()
        return prompt, prompt, digest, False
    head = prompt[: max_len // 2]
    tail = prompt[-max_len // 2 :]
    digest = hashlib.sha256(prompt.encode("utf-8", errors="ignore")).hexdigest()
    return head, tail, digest, True


def _maybe_store_audio(ev) -> str | None:
    """Store audio artifact metadata if available on the event."""
    audio_path = getattr(ev, "audio_path", None)
    audio_id = getattr(ev, "audio_id", None)
    if not audio_path:
        if audio_id is None:
            logger.debug("No audio_path or audio_id on event; skipping audio store")
            return None
        meta = audio_store.add_reference(
            audio_id=audio_id,
            codec=getattr(ev, "audio_codec", None),
            sample_rate=getattr(ev, "sample_rate", None),
            retention_days=AUDIO_RETENTION_DAYS,
            pinned=False,
        )
        return meta.get("audio_id")
    meta = audio_store.add_artifact(
        path=audio_path,
        audio_id=audio_id,
        codec=getattr(ev, "audio_codec", None),
        sample_rate=getattr(ev, "sample_rate", None),
        retention_days=AUDIO_RETENTION_DAYS,
        pinned=False,
    )
    return meta.get("audio_id")


async def _respond_with_prompt(
    session: AgentSession,
    decision: dict[str, object],
    user_text: str,
    round_id: str | None,
    bucket_id: str,
) -> None:
    prompt = build_prompt(
        decision=decision,
        user_text=user_text,
        memory_summary=_get_memory_summary({"bucket_id": bucket_id}),
        current_state=state_tracker.current_state.name,
    )
    head, tail, digest, truncated = _trim_prompt(prompt)
    last_error: str | None = None
    for attempt in range(1, LLM_RETRY_LIMIT + 1):
        t0 = time.perf_counter()
        _log_event(
            round_id,
            EventType.LLM_REQUEST.value,
            {
                "model": os.getenv("OPENAI_MODEL", "gpt-4o-mini"),
                "prompt_hash": digest,
                "prompt_head": head,
                "prompt_tail": tail,
                "truncated": truncated,
                "attempt": attempt,
            },
        )
        try:
            await session.generate_reply(instructions=prompt)
            latency_ms = int((time.perf_counter() - t0) * 1000)
            _log_event(
                round_id,
                EventType.LLM_RESPONSE.value,
                {
                    "model": os.getenv("OPENAI_MODEL", "gpt-4o-mini"),
                    "latency_ms": latency_ms,
                    "text": "(streamed reply)",
                    "truncated": truncated,
                    "attempt": attempt,
                    "usage": None,
                },
            )
            break
        except Exception as e:
            last_error = str(e)
            _log_event(
                round_id,
                EventType.LLM_RESPONSE.value,
                {
                    "model": os.getenv("OPENAI_MODEL", "gpt-4o-mini"),
                    "latency_ms": int((time.perf_counter() - t0) * 1000),
                    "text": "(error)",
                    "error": last_error,
                    "attempt": attempt,
                    "usage": None,
                },
                status="failed",
            )
            if attempt >= LLM_RETRY_LIMIT:
                msg = "I'm having trouble completing that. Can you rephrase?"
                _log_event(
                    round_id,
                    EventType.OUTPUT_EMITTED.value,
                    {"text": msg, "channel": "voice", "was_escalated": False},
                )
                if round_id:
                    trace_store.mark_round(
                        round_id,
                        status="failed",
                        failure_code="llm.failed",
                        failure_reason=last_error,
                        state_out=state_tracker.state_out(),
                    )
                await session.generate_reply(instructions=msg)
                return
            continue
    memory_store.record_turn(bucket_id, "assistant", "(reply sent)")
    _log_event(
        round_id,
        EventType.OUTPUT_EMITTED.value,
        {"text": "(streamed reply sent)", "channel": "voice", "was_escalated": False},
    )
    if round_id:
        trace_store.mark_round(round_id, status="ok", state_out=state_tracker.current_state.name)


async def _process_decision(
    session: AgentSession, decision: dict[str, object], user_text: str, round_id: str | None, bucket_id: str
) -> None:
    if "conflicts" in decision:
        _log_event(
            round_id,
            EventType.CONFIRM_REQUESTED.value,
            {"chain_id": None, "command_id": None, "auth_level": decision.get("authority_level"), "summary": "route conflict"},
        )
        await _ask_for_clarification(session, decision["conflicts"], user_text, round_id)
        return
    if decision.get("type") == "command":
        if await _execute_command(session, decision, user_text, round_id, bucket_id):
            return
    await _respond_with_prompt(session, decision, user_text, round_id, bucket_id)


# =============================================================================
# Agent Definition
# =============================================================================

# Type alias for tool status callback
ToolStatusCallback = callable  # async (bool, list[str], list[dict]) -> None


class VoiceAssistant(WebSearchTools, Agent):
    """Voice assistant with MCP tools and web search."""

    def __init__(
        self,
        llm,
        mcp_servers: dict[str, mcp.MCPServerHTTP] | None = None,
        n8n_workflow_tools: list[dict] | None = None,
        n8n_workflow_name_map: dict[str, str] | None = None,
        n8n_base_url: str | None = None,
        on_tool_status: ToolStatusCallback | None = None,
        tool_cache_size: int = 3,
        max_turns: int = 20,
    ) -> None:
        super().__init__(
            instructions=load_prompt(),
            llm=llm,  # Satisfies LLM interface requirement
        )

        # All MCP servers (for multi-MCP support)
        # Named _caal_mcp_servers to avoid conflict with LiveKit's internal _mcp_servers handling
        self._caal_mcp_servers = mcp_servers or {}

        # n8n-specific for workflow execution (n8n uses webhook-based execution)
        self._n8n_workflow_tools = n8n_workflow_tools or []
        self._n8n_workflow_name_map = n8n_workflow_name_map or {}
        self._n8n_base_url = n8n_base_url

        # Callback for publishing tool status to frontend
        self._on_tool_status = on_tool_status

        # Context management: tool data cache and sliding window
        self._max_turns = max_turns


# =============================================================================
# Agent Entrypoint
# =============================================================================

async def entrypoint(ctx: agents.JobContext) -> None:
    """Main entrypoint for the voice agent."""

    # Start webhook server in the same event loop (first job only)
    global _webhook_server_task
    if _webhook_server_task is None:
        _webhook_server_task = asyncio.create_task(start_webhook_server())
        # Brief delay to check if server started successfully
        await asyncio.sleep(0.5)
        if _webhook_server_task.done():
            exc = _webhook_server_task.exception()
            if exc:
                logger.error(f"Webhook server failed to start: {exc}")

    logger.debug(f"Joining room: {ctx.room.name}")
    await ctx.connect()

    # Load MCP servers from config
    mcp_servers = {}
    try:
        mcp_configs = load_mcp_config()
        mcp_servers = await initialize_mcp_servers(mcp_configs)
    except Exception as e:
        logger.error(f"Failed to load MCP config: {e}")
        mcp_configs = []  # Ensure mcp_configs is defined for later use

    # Discover n8n workflows (n8n uses webhook-based execution, not MCP tools)
    n8n_workflow_tools = []
    n8n_workflow_name_map = {}
    n8n_base_url = None
    n8n_mcp = mcp_servers.get("n8n")
    if n8n_mcp:
        try:
            # Extract base URL from n8n MCP server config
            n8n_config = next((c for c in mcp_configs if c.name == "n8n"), None)
            if n8n_config:
                # URL format: http://HOST:PORT/mcp-server/http
                # Base URL: http://HOST:PORT
                url_parts = n8n_config.url.rsplit("/", 2)
                n8n_base_url = url_parts[0] if len(url_parts) >= 2 else n8n_config.url

            n8n_workflow_tools, n8n_workflow_name_map = await discover_n8n_workflows(
                n8n_mcp, n8n_base_url
            )
        except Exception as e:
            logger.error(f"Failed to discover n8n workflows: {e}")

    # Get runtime settings (from settings.json with .env fallback)
    runtime = get_runtime_settings()

    # Create OpenAI-compatible LLM instance
    missing_llm_envs = [k for k in ("OPENAI_API_KEY",) if not os.getenv(k)]
    if missing_llm_envs:
        raise RuntimeError(f"Missing required env vars for LLM: {', '.join(missing_llm_envs)}")
    llm = openai.LLM(
        api_key=os.environ["OPENAI_API_KEY"],
        model=runtime["model"],
        temperature=runtime["temperature"],
        max_tokens=None,
        base_url=OPENAI_BASE_URL,
    )

    # Log configuration
    logger.info("=" * 60)
    logger.info("STARTING VOICE AGENT")
    logger.info("=" * 60)
    logger.info(f"  STT: {SPEACHES_URL} ({WHISPER_MODEL})")
    logger.info(f"  TTS: {KOKORO_URL} ({runtime['tts_voice']})")
    logger.info(f"  LLM: OpenAI-compatible ({runtime['model']}, num_ctx={runtime['num_ctx']})")
    logger.info(f"  MCP: {list(mcp_servers.keys()) or 'None'}")
    logger.info("=" * 60)

    # Build STT - optionally wrapped with wake word detection
    base_stt = openai.STT(
        base_url=f"{SPEACHES_URL}/v1",
        api_key="not-needed",  # Speaches doesn't require auth
        model=WHISPER_MODEL,
    )

    # Load wake word settings
    all_settings = settings_module.load_settings()
    wake_word_enabled = all_settings.get("wake_word_enabled", False)

    # Session reference for wake word callback (set after session creation)
    _session_ref: AgentSession | None = None

    if wake_word_enabled:
        import json
        import random

        wake_word_model = all_settings.get("wake_word_model", "models/hey_jarvis.onnx")
        wake_word_threshold = all_settings.get("wake_word_threshold", 0.5)
        wake_word_timeout = all_settings.get("wake_word_timeout", 3.0)
        wake_greetings = all_settings.get("wake_greetings", ["Hey, what's up?"])

        async def on_wake_detected():
            """Play wake greeting directly via TTS, bypassing agent turn-taking."""
            nonlocal _session_ref
            if _session_ref is None:
                logger.warning("Wake detected but session not ready yet")
                return

            try:
                # Pick a random greeting
                greeting = random.choice(wake_greetings)
                logger.info(f"Wake word detected, playing greeting: {greeting}")

                # Get TTS and audio output from session
                tts = _session_ref.tts
                audio_output = _session_ref.output.audio

                # Synthesize and push audio frames directly (bypasses turn-taking)
                audio_stream = tts.synthesize(greeting)
                async for event in audio_stream:
                    if hasattr(event, "frame") and event.frame:
                        await audio_output.capture_frame(event.frame)

                # Flush to complete the audio segment
                audio_output.flush()

            except Exception as e:
                logger.warning(f"Failed to play wake greeting: {e}")

        async def on_state_changed(state):
            """Publish wake word state to connected clients."""
            payload = json.dumps({
                "type": "wakeword_state",
                "state": state.value,
            })
            try:
                await ctx.room.local_participant.publish_data(
                    payload.encode("utf-8"),
                    reliable=True,
                    topic="wakeword_state",
                )
                logger.debug(f"Published wake word state: {state.value}")
            except Exception as e:
                logger.warning(f"Failed to publish wake word state: {e}")

        stt_instance = WakeWordGatedSTT(
            inner_stt=base_stt,
            model_path=wake_word_model,
            threshold=wake_word_threshold,
            silence_timeout=wake_word_timeout,
            on_wake_detected=on_wake_detected,
            on_state_changed=on_state_changed,
        )
        logger.info(f"  Wake word: ENABLED (model={wake_word_model}, threshold={wake_word_threshold})")
    else:
        stt_instance = base_stt
        logger.info("  Wake word: disabled")

    # Create session with Speaches STT and Kokoro TTS (both OpenAI-compatible)
    logger.info(f"  STT instance type: {type(stt_instance).__name__}")
    logger.info(f"  STT capabilities: streaming={stt_instance.capabilities.streaming}")
    session = AgentSession(
        stt=stt_instance,
        llm=ollama_llm,
        stt=openai.STT(
            base_url=f"{SPEACHES_URL}/v1",
            api_key="not-needed",  # Speaches doesn't require auth
            model=WHISPER_MODEL,
        ),
        llm=llm,
        tts=openai.TTS(
            base_url=f"{KOKORO_URL}/v1",
            api_key="not-needed",  # Kokoro doesn't require auth
            model=TTS_MODEL,
            voice=runtime["tts_voice"],
        ),
        vad=silero.VAD.load(),
        allow_interruptions=False,  # Prevent background noise from interrupting agent
    )
    logger.info(f"  Session STT: {type(session.stt).__name__}")

    # Set session reference for wake word callback
    _session_ref = session

    # ==========================================================================
    # Round-trip latency tracking
    # ==========================================================================

    _transcription_time: float | None = None

    @session.on("user_input_transcribed")
    def on_user_input_transcribed(ev) -> None:
        nonlocal _transcription_time
        _transcription_time = time.perf_counter()
        logger.debug(f"User said: {ev.transcript[:80]}...")
        conversation_id = normalize_conversation_id(ctx.room.name)
        bucket_id = make_bucket_id(conversation_id)
        audio_id = _maybe_store_audio(ev)
        state_in = state_tracker.capture_state_in()
        state_tracker.assert_state_unchanged()
        round_header = trace_store.start_round(
            library_id=LIBRARY_ID,
            bucket_id=bucket_id,
            conversation_id=conversation_id,
            state_in=state_in,
            audio_id=audio_id,
        )
        scrubbed, secrets = scrub_secrets(ev.transcript or "")
        trace_store.append_event(
            round_header["round_id"],
            EventType.INPUT_RECEIVED.value,
            {"raw_text": ev.transcript, "channel": "voice", "audio_id": audio_id},
        )
        trace_store.append_event(
            round_header["round_id"],
            EventType.INPUT_NORMALIZED.value,
            {"normalized_text": scrubbed, "secret_refs": list(secrets.keys())},
        )
        validator_results = run_validators(scrubbed)
        for res in validator_results:
            trace_store.append_event(
                round_header["round_id"],
                EventType.VALIDATOR_RAN.value,
                {"validator_id": res.validator_id, "code": res.code, "status": res.status.value, "message": res.message},
            )
        if any(r.status in {ValidatorStatus.ERROR, ValidatorStatus.ESCALATE} for r in validator_results):
            msg = "I need clarification before proceeding."
            _log_event(
                round_header["round_id"],
                EventType.OUTPUT_EMITTED.value,
                {"text": msg, "channel": "voice", "was_escalated": False},
            )
            trace_store.mark_round(
                round_header["round_id"],
                status="failed",
                failure_code="validator.blocked",
                failure_reason="; ".join(r.message for r in validator_results),
                state_out=state_tracker.state_out(),
            )
            asyncio.create_task(session.generate_reply(instructions=msg))
            return
        decision = decide_route(scrubbed)
        trace_store.append_event(
            round_header["round_id"],
            EventType.ROUTE_SELECTED.value,
            {
                "route_code": decision.get("type"),
                "source_rule": decision.get("source_rule"),
                "topic": decision.get("topic"),
                "auth_level": decision.get("authority_level"),
                "macro": decision.get("macro"),
            },
        )
        memory_store.record_turn(bucket_id, "user", scrubbed)
        state_message = _apply_state_transition(decision, round_header["round_id"])
        if state_message:
            trace_store.append_event(
                round_header["round_id"],
                EventType.OUTPUT_EMITTED.value,
                {"text": state_message, "channel": "voice", "was_escalated": False},
            )
            trace_store.mark_round(round_header["round_id"], status="ok", state_out=state_tracker.state_out())
            memory_store.record_turn(bucket_id, "assistant", state_message)
            asyncio.create_task(session.generate_reply(instructions=state_message))
            return
        allowed, reason = _handle_authority(decision, round_header["round_id"])
        if not allowed:
            if reason:
                _log_event(
                    round_header["round_id"],
                    EventType.OUTPUT_EMITTED.value,
                    {"text": reason, "channel": "voice", "was_escalated": False},
                )
                asyncio.create_task(session.generate_reply(instructions=reason))
                return
            logger.warning("Command blocked")
            trace_store.append_event(
                round_header["round_id"],
                EventType.ROUND_FAILED.value,
                {"failure_code": "auth.blocked", "failure_reason": "blocked"},
                status="failed",
            )
            trace_store.mark_round(
                round_header["round_id"],
                status="failed",
                failure_code="auth.blocked",
                failure_reason="blocked",
                state_out=state_tracker.state_out(),
            )
            return
        asyncio.create_task(_process_decision(session, decision, scrubbed, round_header["round_id"], bucket_id))

    @session.on("agent_state_changed")
    def on_agent_state_changed(ev) -> None:
        nonlocal _transcription_time
        if ev.new_state == "speaking" and _transcription_time is not None:
            latency_ms = (time.perf_counter() - _transcription_time) * 1000
            logger.info(f"ROUND-TRIP LATENCY: {latency_ms:.0f}ms (LLM + TTS)")
            _transcription_time = None

        # Notify wake word STT of agent state for silence timer management
        if isinstance(stt_instance, WakeWordGatedSTT):
            stt_instance.set_agent_busy(ev.new_state in ("thinking", "speaking"))

    async def _publish_tool_status(
        tool_used: bool,
        tool_names: list[str],
        tool_params: list[dict],
    ) -> None:
        """Publish tool usage status to frontend via data packet."""
        import json
        payload = json.dumps({
            "tool_used": tool_used,
            "tool_names": tool_names,
            "tool_params": tool_params,
        })

        try:
            await ctx.room.local_participant.publish_data(
                payload.encode("utf-8"),
                reliable=True,
                topic="tool_status",
            )
            logger.debug(f"Published tool status: used={tool_used}, names={tool_names}")
        except Exception as e:
            logger.warning(f"Failed to publish tool status: {e}")
        # Trace tool usage if a round_id is present in params
        try:
            round_id = None
            if tool_params and isinstance(tool_params[0], dict):
                round_id = tool_params[0].get("round_id")
            if round_id:
                for name in tool_names or []:
                    _log_event(
                        round_id,
                        EventType.TOOL_REQUEST.value,
                        {"tool_id": name, "call_id": new_call_id(), "inputs": tool_params},
                    )
                    _log_event(
                        round_id,
                        EventType.TOOL_RESPONSE.value,
                        {
                            "tool_id": name,
                            "call_id": new_call_id(),
                            "latency_ms": None,
                            "result": "tool_used",
                            "status": "ok",
                        },
                    )
        except Exception:
            logger.debug("Skipped tool trace logging (missing round_id)", exc_info=True)

    # ==========================================================================

    # Create agent with OllamaLLM and all MCP servers
    assistant = VoiceAssistant(
        ollama_llm=ollama_llm,
        mcp_servers=mcp_servers,
        n8n_workflow_tools=n8n_workflow_tools,
        n8n_workflow_name_map=n8n_workflow_name_map,
        n8n_base_url=n8n_base_url,
        on_tool_status=_publish_tool_status,
        tool_cache_size=runtime["tool_cache_size"],
        max_turns=runtime["max_turns"],
    )

    # Create event to wait for session close (BEFORE session.start to avoid race condition)
    close_event = asyncio.Event()

    @session.on("close")
    def on_session_close(ev) -> None:
        logger.info(f"Session closed: {ev.reason}")
        state_tracker.clear_pending()
        close_event.set()

    # Register session for webhook access
    session_registry.register(ctx.room.name, session, assistant)

    # Start session AFTER handlers are registered
    await session.start(
        room=ctx.room,
        agent=assistant,
    )

    try:
        # Send initial greeting with timeout to prevent hanging on unresponsive LLM
        try:
            await asyncio.wait_for(
                session.generate_reply(
                    instructions="Greet the user briefly and let them know you're ready to help."
                ),
                timeout=30.0,
            )
        except asyncio.TimeoutError:
            logger.error("Initial greeting timed out (30s) - LLM may be unresponsive")
            # Continue anyway - user can still speak

        logger.info("Agent ready - listening for speech...")

        # Wait until session closes (room disconnects, etc.)
        await close_event.wait()

    finally:
        # Unregister session on cleanup
        session_registry.unregister(ctx.room.name)


# =============================================================================
# Model Preloading
# =============================================================================


def preload_models():
    """Preload STT and LLM models on startup.

    Ensures models are ready before first user connection, avoiding
    delays on first request (especially important on HDDs).

    Note: Kokoro (remsky/kokoro-fastapi) preloads its own models at startup.
    """
    speaches_url = os.getenv("SPEACHES_URL", "http://speaches:8000")
    whisper_model = os.getenv("WHISPER_MODEL", "Systran/faster-whisper-medium")
    ollama_host = os.getenv("OLLAMA_HOST", "http://localhost:11434")
    ollama_model = os.getenv("OLLAMA_MODEL", "ministral-3:8b")
    ollama_num_ctx = int(os.getenv("OLLAMA_NUM_CTX", "8192"))

    logger.info("Preloading models...")

    # Download Whisper STT model
    try:
        logger.info(f"  Loading STT: {whisper_model}")
        response = requests.post(
            f"{speaches_url}/v1/models?model_name={whisper_model}",
            timeout=300
        )
        if response.status_code == 200:
            logger.info("  STT ready")
        else:
            logger.warning(f"  STT model download returned {response.status_code}")
    except Exception as e:
        logger.warning(f"  Failed to preload STT model: {e}")

    # Warm up Ollama LLM with correct num_ctx (loads model into VRAM)
    try:
        logger.info(f"  Loading LLM: {ollama_model} (num_ctx={ollama_num_ctx})")
        response = requests.post(
            f"{ollama_host}/api/generate",
            json={
                "model": ollama_model,
                "prompt": "hi",
                "stream": False,
                "keep_alive": -1,
                "options": {"num_ctx": ollama_num_ctx}
            },
            timeout=180
        )
        if response.status_code == 200:
            logger.info("  LLM ready")
        else:
            logger.warning(f"  LLM warmup returned {response.status_code}")
    except Exception as e:
        logger.warning(f"  Failed to preload LLM: {e}")


# =============================================================================
# Webhook Server
# =============================================================================

WEBHOOK_PORT = int(os.getenv("WEBHOOK_PORT", "8889"))

# Global reference to webhook server task (started in entrypoint)
_webhook_server_task: asyncio.Task | None = None


async def start_webhook_server():
    """Start FastAPI webhook server in the current event loop.

    This runs the webhook server in the same event loop as the LiveKit agent,
    avoiding cross-thread async issues that cause 200x slower MCP calls.
    """
    import uvicorn
    from caal.webhooks import app

    config = uvicorn.Config(
        app,
        host="0.0.0.0",
        port=WEBHOOK_PORT,
        log_level="warning",
    )
    server = uvicorn.Server(config)
    logger.debug(f"Starting webhook server on port {WEBHOOK_PORT}")
    await server.serve()


# =============================================================================
# Main
# =============================================================================

if __name__ == "__main__":
    # Preload models before starting worker
    preload_models()

    agents.cli.run_app(
        agents.WorkerOptions(
            entrypoint_fnc=entrypoint,
            # Suppress memory warnings (models use ~1GB, this is expected)
            job_memory_warn_mb=0,
        )
    )
def _apply_state_transition(decision: dict[str, object], round_id: str | None) -> str | None:
    cmd = decision.get("command_id")
    if cmd == "state.confirm":
        pending = state_tracker.confirm_pending()
        if pending:
            _log_event(
                round_id,
                EventType.CONFIRM_RECEIVED.value,
                {
                    "confirmation_id": new_call_id(),
                    "chain_id": pending.chain_id,
                    "accepted": True,
                    "channel": "voice",
                },
            )
            if pending.command_id == "state.enter_conversation":
                state_tracker.enter_state(GuardState.CONVERSATION)
                return "Conversation mode engaged."
            if pending.command_id == "state.enter_planning":
                state_tracker.enter_state(GuardState.PLANNING)
                return "Planning mode locked."
            return None
        _log_event(
            round_id,
            EventType.CONFIRM_RECEIVED.value,
            {"confirmation_id": new_call_id(), "accepted": False, "channel": "voice"},
        )
        return "Nothing pending to confirm."
    if cmd == "state.exit":
        state_tracker.enter_state(GuardState.DEFAULT)
        return "Returned to default state."
    # Clearing pending at round end happens after processing paths in caller
    return None


def _handle_authority(decision: dict[str, object], round_id: str | None) -> tuple[bool, str | None]:
    if decision.get("type") != "command":
        return True, None
    cmd_authority = decision.get("authority_level", 1)
    chain_id = decision.get("macro") or decision.get("chain_id") or new_call_id()
    state_tracker.set_chain_level(max(cmd_authority, state_tracker.current_chain_level or 0))
    if not state_tracker.is_command_allowed(cmd_authority):
        return False, "Commands are locked in the current state."
    if cmd_authority >= 4:
        # Require two confirmations: first sets pending, second confirms same command/chain.
        if not state_tracker.pending or state_tracker.pending.command_id != decision["command_id"]:
            state_tracker.set_chain_level(cmd_authority)
            state_tracker.start_pending(decision, chain_id)
            _log_event(
                round_id,
                EventType.CONFIRM_REQUESTED.value,
                {"chain_id": chain_id, "command_id": decision.get("command_id"), "auth_level": cmd_authority, "summary": "double confirm step 1"},
            )
            return False, f"Please confirm `{decision['command_id']}` (step 1 of 2)."
        else:
            # Second confirmation required
            state_tracker.start_pending(decision, chain_id)
            _log_event(
                round_id,
                EventType.CONFIRM_REQUESTED.value,
                {"chain_id": chain_id, "command_id": decision.get("command_id"), "auth_level": cmd_authority, "summary": "double confirm step 2"},
            )
            return False, f"Please confirm `{decision['command_id']}` again (step 2 of 2)."
    elif cmd_authority == 3:
        if not state_tracker.pending or state_tracker.pending.command_id != decision["command_id"]:
            state_tracker.set_chain_level(cmd_authority)
            state_tracker.start_pending(decision, chain_id)
            _log_event(
                round_id,
                EventType.CONFIRM_REQUESTED.value,
                {"chain_id": chain_id, "command_id": decision.get("command_id"), "auth_level": cmd_authority, "summary": "confirm"},
            )
            return False, f"Confirm `{decision['command_id']}` before running."
    elif cmd_authority == 2:
        state_tracker.register_level2(decision)
    return True, None


async def _ask_for_clarification(
    session: AgentSession, conflict_ids: list[str], user_text: str, round_id: str | None
) -> None:
    options = ", ".join(conflict_ids)
    instructions = (
        f"I heard multiple possible actions ({options}) from: \"{user_text}\". "
        "Please tell me which one you meant."
    )
    _log_event(
        round_id,
        EventType.OUTPUT_EMITTED.value,
        {"text": instructions, "channel": "voice", "was_escalated": False},
    )
    await session.generate_reply(instructions=instructions)


def _memory_show(decision: dict[str, object], user_text: str) -> str:
    bucket = decision.get("topic") or "general"
    summary = memory_store.get_summary(bucket) or "No summary yet."
    return f"Memory for {bucket}: {summary}"


def _memory_list(_: dict[str, object], user_text: str) -> str:
    buckets = memory_store.list_buckets()
    return f"Tracked buckets: {', '.join(buckets) or 'none'}"


def _memory_add(decision: dict[str, object], user_text: str) -> str:
    bucket = decision.get("topic") or "general"
    memory_store.append_note(bucket, user_text)
    return f"Added note to {bucket}."


def _memory_clear(decision: dict[str, object], user_text: str) -> str:
    bucket = decision.get("topic") or "general"
    memory_store.clear_bucket(bucket)
    return f"Cleared memory bucket {bucket}."


def _state_undo(_: dict[str, object], user_text: str) -> str:
    cmd = state_tracker.undo_last()
    if cmd:
        _log_event(None, EventType.COMMAND_REVERSED.value, {"command_id": cmd, "status": "ok", "reason": "user_undo"})
        return f"Reverted {cmd}."
    return "Nothing to undo."


COMMAND_HANDLERS: dict[str, callable] = {
    "memory.show_bucket": _memory_show,
    "memory.list_buckets": _memory_list,
    "memory.add_note": _memory_add,
    "memory.clear_bucket": _memory_clear,
    "state.undo": _state_undo,
}


async def _execute_command(
    session: AgentSession, decision: dict[str, object], user_text: str, round_id: str | None, bucket_id: str
) -> bool:
    handler = COMMAND_HANDLERS.get(decision.get("command_id", ""))
    if not handler:
        return False
    reply = handler(decision, user_text)
    _log_event(
        round_id,
        EventType.COMMAND_EXECUTED.value,
        {
            "command_id": decision.get("command_id"),
            "status": "ok",
            "auth_level": decision.get("authority_level"),
            "result": reply,
            "chain_id": decision.get("macro") or decision.get("chain_id"),
        },
    )
    _log_event(
        round_id,
        EventType.OUTPUT_EMITTED.value,
        {"text": reply, "channel": "voice", "was_escalated": False},
    )
    await session.generate_reply(instructions=reply)
    memory_store.record_turn(bucket_id, "assistant", reply)
    if round_id:
        trace_store.mark_round(round_id, status="ok", state_out=state_tracker.current_state.name)
    return True
