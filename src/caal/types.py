"""Shared enums and constants for logging and validation."""

from __future__ import annotations

from enum import Enum


class EventType(str, Enum):
    INPUT_RECEIVED = "input.received"
    INPUT_NORMALIZED = "input.normalized"
    ROUTE_SELECTED = "route.selected"
    VALIDATOR_RAN = "validator.ran"
    LLM_REQUEST = "llm.request"
    LLM_RESPONSE = "llm.response"
    TOOL_REQUEST = "tool.request"
    TOOL_RESPONSE = "tool.response"
    JOB_ENQUEUED = "job.enqueued"
    JOB_PROGRESS = "job.progress"
    JOB_RESULT = "job.result"
    CONFIRM_REQUESTED = "confirm.requested"
    CONFIRM_RECEIVED = "confirm.received"
    COMMAND_EXECUTED = "command.executed"
    COMMAND_REVERSED = "command.reversed"
    OUTPUT_EMITTED = "output.emitted"
    ROUND_FAILED = "round.failed"


class ValidatorStatus(str, Enum):
    OK = "ok"
    WARN = "warn"
    ERROR = "error"
    ESCALATE = "escalate"
    RETRY = "retry"


class AuthLevel(int, Enum):
    LEVEL1 = 1
    LEVEL2 = 2
    LEVEL3 = 3
    LEVEL4 = 4
