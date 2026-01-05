# Thinking Router Rules (Living Document)

Status: baseline draft. Adjust patterns, priorities, and facets as we learn.

## Input → Output
- Input: cleaned transcript (text), session flags (e.g., active job, current facet), last route.
- Output: `{type: command|facet|research|chat, command_id?, facet_id?, topic?, urgency?, require_confirm?}`.

## Priority (high → low)
1) Commands (ops/system, voice/session)
2) Research / long-task intent
3) Facets: ops, settings, memory
4) Chat fallback

## Command Catalog (initial)
- `ops.restart_agent` — “restart agent|backend|server”
- `ops.restart_frontend` — “restart frontend|web”
- `ops.status` — “status|health|check services”
- `voice.set_voice` — “set|switch voice to <name>”
- `session.stop` — “stop listening|end session”

`require_confirm: true` for restart/stop/start; not needed for status/voice.

## Facets (current)
- `facet.ops`: deploy/release/rollback/status/logs/latency
- `facet.research`: research/investigate/long answer/dig deep/do a study
- `facet.settings`: change temperature/turns/model/voice
- `facet.memory`: summarize/recap/notes/history/recall

## Patterns (examples)
- Ops commands:
  - `^(restart|stop|start)\s+(?:the\s+)?(?:agent|backend|frontend|server)`
  - `\b(status|health|check services)\b`
- Voice:
  - `\b(set|switch)\s+voice\s+to\s+([\w-]+)\b`
- Research:
  - `\b(research|investigate|dig deep|long report|do a study)\b`
- Memory:
  - `\b(summarize|recap|remind me|what did we discuss|notes?)\b`
  - Memory commands:
    - `\b(show|read)\s+(?:memory|note|bucket)\s+(?P<bucket>[\w-]+)\b`
    - `\b(list)\s+(?:memory|buckets)\b`
    - `\b(add|append)\s+note\s+to\s+(?P<bucket>[\w-]+)\b`
    - `\b(clear|forget)\s+(?:bucket|memory)\s+(?P<bucket>[\w-]+)\b`
- Research:
  - `\b(research|investigate|dig deep|long report|do a study)\b`
  - `\b(start|launch)\s+(?:research|job)\s+(?:on|about)\s+(?P<topic>[\w\s-]+)\b`
  - `\b(job status|status of job)\s+(?P<job_id>[a-f0-9-]+)\b`
  - `\b(list)\s+(?:jobs|research tasks)\b`
- Settings:
  - `\b(temperature|turns|model|voice)\b`

## Ambiguity Handling
- If multiple matches, take highest priority; if still ambiguous, ask a brief clarifier or call a tiny classifier.
- “Stop” resolves in current facet first (e.g., stop music vs stop server) else ask.

## Fallback
- If nothing matches: `{type: chat}` with an ack (“Continuing the conversation.”).

## Safety
- High-risk ops (restart/stop/start) require confirmation unless explicitly armed via a safe-mode toggle (future).

## Notes
- This table is intended to change; add/remove patterns, tweak priorities/facets as needed.
