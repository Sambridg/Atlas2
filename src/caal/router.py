from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Pattern

from typing_extensions import Literal, TypedDict


RouteType = Literal["command", "facet", "research", "chat"]
Urgency = Literal["low", "normal", "high"]


def normalize_text(text: str) -> str:
    cleaned = re.sub(r"[^\w\s-]", " ", text.lower())
    return re.sub(r"\s+", " ", cleaned).strip()


class RouteDecision(TypedDict, total=False):
    type: RouteType
    command_id: str
    facet_id: str
    topic: str
    urgency: Urgency
    require_confirm: bool
    source_rule: str


@dataclass
class RouterRule:
    name: str
    pattern: str
    rule_type: RouteType
    priority: int
    command_id: str | None = None
    facet_id: str | None = None
    require_confirm: bool = False
    urgency: Urgency = "normal"
    authority_level: int = 1
    topic_group: int | None = None
    macro: str | None = None
    is_macro_root: bool = False
    chain_id: str | None = None
    _compiled: Pattern[str] = field(init=False, repr=False)

    def __post_init__(self) -> None:
        self._compiled = re.compile(self.pattern, re.IGNORECASE)

    def match(self, text: str):
        return self._compiled.search(text)

    def extract_topic(self, match: re.Match[str]) -> str | None:
        if match is None or self.topic_group is None:
            return None
        try:
            return match.group(self.topic_group).strip()
        except IndexError:
            return None


DEFAULT_ROUTER_RULES: list[RouterRule] = [
    RouterRule(
        name="ops.restart",
        pattern=r"\b(?:restart|stop|start)\s+(?:the\s+)?(?:agent|backend|frontend|server)\b",
        rule_type="command",
        priority=10,
        command_id="ops.restart_agent",
        require_confirm=True,
        urgency="high",
        authority_level=3,
    ),
    RouterRule(
        name="ops.status",
        pattern=r"\b(status|health|check services)\b",
        rule_type="command",
        priority=15,
        command_id="ops.status",
        require_confirm=False,
    ),
    RouterRule(
        name="voice.set_voice",
        pattern=r"\b(?:set|switch)\s+voice\s+to\s+([\w-]+)\b",
        rule_type="command",
        priority=20,
        command_id="voice.set_voice",
        topic_group=1,
    ),
    RouterRule(
        name="state.enter_conversation",
        pattern=r"\b(?:enter|switch to|start)\s+(?:conversation|chat)\s+mode\b",
        rule_type="command",
        priority=5,
        command_id="state.enter_conversation",
        authority_level=3,
        require_confirm=True,
    ),
    RouterRule(
        name="state.enter_planning",
        pattern=r"\b(?:enter|switch to|start)\s+(?:planning|strategy)\s+mode\b",
        rule_type="command",
        priority=4,
        command_id="state.enter_planning",
        authority_level=4,
        require_confirm=True,
    ),
    RouterRule(
        name="state.exit",
        pattern=r"\b(?:exit|leave|back to default|unlock)\b",
        rule_type="command",
        priority=6,
        command_id="state.exit",
        authority_level=1,
    ),
    RouterRule(
        name="state.confirm",
        pattern=r"\b(?:confirm|yes|apply)\b",
        rule_type="command",
        priority=7,
        command_id="state.confirm",
        authority_level=1,
    ),
    RouterRule(
        name="state.undo",
        pattern=r"\b(?:undo|reverse|cancel)\b",
        rule_type="command",
        priority=8,
        command_id="state.undo",
        authority_level=1,
    ),
    RouterRule(
        name="research.intent",
        pattern=r"\b(research|investigate|dig deep|long report|do a study)\b",
        rule_type="research",
        priority=30,
        facet_id="facet.research",
    ),
    RouterRule(
        name="research.launch",
        pattern=r"\b(start|launch)\s+(?:research|job)\s+(?:on|about)\s+([\w\s-]+)",
        rule_type="command",
        priority=25,
        command_id="job.create",
        authority_level=2,
        topic_group=2,
    ),
    RouterRule(
        name="job.status",
        pattern=r"\b(?:job status|status of job)\s+([a-f0-9-]+)",
        rule_type="command",
        priority=26,
        command_id="job.status",
        authority_level=1,
        topic_group=1,
    ),
    RouterRule(
        name="job.list",
        pattern=r"\blist\s+(?:jobs|research tasks)\b",
        rule_type="command",
        priority=34,
        command_id="job.list",
        authority_level=1,
    ),
    RouterRule(
        name="settings.intent",
        pattern=r"\b(temperature|turns|model|voice)\b",
        rule_type="facet",
        priority=40,
        facet_id="facet.settings",
    ),
    RouterRule(
        name="memory.intent",
        pattern=r"\b(summarize|recap|remind me|what did we discuss|notes?)\b",
        rule_type="facet",
        priority=50,
        facet_id="facet.memory",
    ),
    RouterRule(
        name="memory.show",
        pattern=r"\b(show|read)\s+(?:memory|note|bucket)\s+(?:for\s+)?([A-Za-z0-9_-]+)",
        rule_type="command",
        priority=25,
        command_id="memory.show_bucket",
        topic_group=2,
        authority_level=1,
    ),
    RouterRule(
        name="memory.list",
        pattern=r"\blist\s+(?:memory|buckets)\b",
        rule_type="command",
        priority=35,
        command_id="memory.list_buckets",
        authority_level=1,
    ),
    RouterRule(
        name="memory.append",
        pattern=r"\b(add|append)\s+note\s+to\s+([A-Za-z0-9_-]+)",
        rule_type="command",
        priority=28,
        command_id="memory.add_note",
        topic_group=2,
        authority_level=2,
    ),
    RouterRule(
        name="memory.clear",
        pattern=r"\b(clear|forget)\s+(?:bucket|memory)\s+([A-Za-z0-9_-]+)",
        rule_type="command",
        priority=22,
        command_id="memory.clear_bucket",
        require_confirm=True,
        authority_level=3,
        topic_group=2,
    ),
]


def decide_route(transcript: str, context: dict[str, Any] | None = None) -> RouteDecision:
    normalized = normalize_text(transcript)
    best_priority: int | None = None
    best_matches: list[tuple[RouterRule, re.Match[str]]] = []

    for rule in DEFAULT_ROUTER_RULES:
        match = rule.match(normalized)
        if not match:
            continue
        if best_priority is None or rule.priority < best_priority:
            best_priority = rule.priority
            best_matches = [(rule, match)]
        elif rule.priority == best_priority:
            best_matches.append((rule, match))

    if not best_matches:
        return {"type": "chat", "topic": normalized, "require_confirm": False}

    rule, match = best_matches[0]
    topic = rule.extract_topic(match)
    decision: RouteDecision = {
        "type": rule.rule_type,
        "require_confirm": rule.require_confirm,
        "urgency": rule.urgency,
        "authority_level": rule.authority_level,
        "macro": rule.macro,
        "is_macro_root": rule.is_macro_root,
        "source_rule": rule.name,
        "chain_id": rule.chain_id,
    }

    if rule.command_id:
        decision["command_id"] = rule.command_id
    if rule.facet_id:
        decision["facet_id"] = rule.facet_id
    if topic:
        decision["topic"] = topic

    if len(best_matches) > 1:
        decision["conflicts"] = [r.command_id or r.name for r, _ in best_matches
                                 if r.command_id or r.facet_id]

    return decision
