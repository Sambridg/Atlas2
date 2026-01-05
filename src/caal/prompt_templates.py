from __future__ import annotations

from typing import Dict

TEMPLATES: Dict[str, str] = {
    "command": """Context: The user said: "{user_text}".
Command: "{command_id}" (topic: "{topic}", urgency: {urgency}).
Rules:
- Confirm you understood before acting.
- Run the requested operation and report only status.
Reply: """,
    "facet.ops": """Context: The user asked about operations (status, restarts, telemetry).
Policy:
- Use tools when available.
- Report the actions taken or data inspected.
Reply using {memory_summary}. """,
    "facet.settings": """Context: The user wants to change settings (temperature, turns, voice, model).
Policy:
- Describe the change before applying it.
- If the value is out-of-range, explain the limits.
Reply: """,
    "facet.memory": """Context: Memory bucket "{topic}" summary:
{memory_summary}
Task: Answer referencing the bucket and mentioning the last update.
Reply: """,
    "facet.research": """Context: Deep research requested on "{topic}".
Policy:
- Outline the approach before answering.
- If the answer is long, return a job ticket for follow-up.
Reply: """,
    "memory_injection": """Memory bucket: "{topic}" summary:
{memory_summary}
Prompt: Answer while incorporating those facts. """,
    "verifier": """Inputs:
 - User text: "{user_text}"
 - Draft reply: "{draft_reply}"
 - Memory summary: "{memory_summary}"
Task: Return "OK" if grounded, else rewrite accurately.
""",
}


def build_prompt(decision: dict[str, object], user_text: str, memory_summary: str, current_state: str) -> str:
    prompt_type = "command" if decision["type"] == "command" else f"facet.{decision.get('facet_id','chat')}"
    template = TEMPLATES.get(prompt_type)
    if not template:
        template = TEMPLATES["command"]
    return template.format(
        user_text=user_text,
        command_id=decision.get("command_id", "chat"),
        topic=decision.get("topic", "general"),
        urgency=decision.get("urgency", "normal"),
        memory_summary=memory_summary or "No prior memory.",
        current_state=current_state,
    )
