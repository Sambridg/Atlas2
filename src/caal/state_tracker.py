from __future__ import annotations

from dataclasses import dataclass
from enum import Enum, auto
from typing import Dict, Optional


class GuardState(Enum):
    DEFAULT = auto()
    CONVERSATION = auto()
    ACTION = auto()
    RESEARCH = auto()
    PLANNING = auto()
    COMMAND_LOCK = auto()


@dataclass
class PendingCommand:
    command_id: str
    authority_level: int
    decision: Dict[str, object]
    chain_id: str | None = None


class StateTracker:
    """Tracks conversational guard state and command confirmations."""

    def __init__(self) -> None:
        self.current_state = GuardState.DEFAULT
        self.pending: PendingCommand | None = None
        self.confirmed_macros: set[str] = set()
        self.last_level2: PendingCommand | None = None
        self.current_chain_level: int | None = None
        self.state_in_round: GuardState | None = None

    def enter_state(self, target: GuardState) -> None:
        """Transition to a new state (round boundary recommended)."""
        self.current_state = target
        self.pending = None
        self.current_chain_level = None

    def capture_state_in(self) -> str:
        """Capture the state at round start (immutable during the round)."""
        self.state_in_round = self.current_state
        return self.state_in_round.name

    def state_out(self) -> str:
        return self.current_state.name

    def assert_state_unchanged(self) -> None:
        """Ensure state hasn't changed mid-round."""
        if self.state_in_round and self.current_state != self.state_in_round:
            # Reset to state_in_round to enforce invariant
            self.current_state = self.state_in_round

    def start_pending(self, decision: Dict[str, object], chain_id: str | None = None) -> str:
        """Register a pending confirmation request."""
        self.pending = PendingCommand(decision["command_id"], decision["authority_level"], decision, chain_id)
        return f"Command `{decision['command_id']}` requires confirmation."

    def confirm_pending(self) -> Optional[PendingCommand]:
        pending = self.pending
        self.pending = None
        return pending

    def clear_pending(self) -> None:
        self.pending = None

    def register_level2(self, decision: Dict[str, object], chain_id: str | None = None) -> None:
        """Track last level-2 action for potential immediate undo."""
        self.last_level2 = PendingCommand(decision["command_id"], decision["authority_level"], decision, chain_id)

    def undo_last(self) -> Optional[str]:
        entry = self.last_level2
        self.last_level2 = None
        return entry.command_id if entry else None

    def set_chain_level(self, level: int | None) -> None:
        self.current_chain_level = level

    def is_command_allowed(self, authority_level: int) -> bool:
        """Gate execution based on current state and chain-level auth."""
        if self.current_chain_level and authority_level > self.current_chain_level:
            return False
        if self.current_state in {GuardState.DEFAULT, GuardState.CONVERSATION}:
            return True
        if authority_level <= 2:
            return True
        return False
