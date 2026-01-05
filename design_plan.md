# CAAL Thinking Router Implementation Plan (refined)

This plan maps the design intent (sections 1–11) onto the CAAL codebase with explicit build steps and resolved decisions so it can be externally reviewed and verified.

## Rollout phases
- **Phase 0 – Baseline and schema:** Add ID/schema constants, create trace DB schema, and decide storage paths/retention.
- **Phase 1 – Logging spine:** Instrument voice/text entrypoints with structured round/event logging (feature-flagged), ordering, and export.
- **Phase 2 – Auth + state machine:** Extend StateTracker for levels 1–4, confirmation flows, and immutable per-round state.
- **Phase 3 – Memory model:** Split register vs bucket context package, add scoring, caching, and retrieval APIs/CLI.
- **Phase 4 – Audio linkage:** Wire audio_id capture/storage, TTL/pinning, hashes, and metadata linkage to rounds.
- **Phase 5 – Retry + notification:** Implement bounded retries, validator integration, and user-facing clarification paths.
- **Phase 6 – Secrets:** Add secret scanning and secret_ref handling across prompts, traces, and memory.
- **Phase 7 – Docs/tests:** Acceptance checks, CLI/API docs, and automated tests per component.

## Global decisions
- `library_id`: generated once on first run, persisted; override via `CAAL_LIBRARY_ID`.
- IDs: bucket and conversation stay distinct; default `bucket_id = bucket:{conversation_id}`. `round_id` uses UUIDv7 (monotonic), plus `round_seq` per conversation. `call_id` per tool/LLM/worker. `job_id` existing. `audio_id` for captures.
- Storage: traces in SQLite `data/traces.db` (rounds/events) with JSONL export. Memory/register uses existing SQLite; audio stored in configurable path (default OS app data).
- Enums: namespaced string enums (`stt.*`, `intent.*`, `auth.*`, `tool.*`, `llm.*`, `validator.*`, `feedback.*`) in a shared types module.
- Prompts: secret-scrub then size cap (~8k chars) with head/tail + hash marker if truncated.
- Ordering: `round_seq` and `event_seq` in DB tables; serialize writes per conversation; allow concurrency across conversations.
- Secrets: regex + entropy scanner; secrets replaced with `secret_ref` tokens; in-memory map seeded from env/config; never persist secrets.
- Audio: SHA256 stored; optional lightweight RMS/peak preview; TTL via `AUDIO_RETENTION_DAYS` (default 30) with pin-to-keep.
- Context: register is short UI line; bucket context package is cached per bucket with invalidation on writes. Scoring mixes pin, topic hits, citations, recency decay.
- State/auth: extend StateTracker (not replace); guard state mapped via adapter to the external state set; confirmation summaries via templates with optional LLM shortening.
- Retry: default 2 retries for LLM; tools retried only on transient errors; after retries, ask user for clarification; filter incoherent input pre-LLM.

## Section 1: Canonical IDs and correlation — DONE
- **Build steps**
  - Add `caal/ids.py` defining generators/parsers for library, bucket, conversation, round, call, job, audio IDs; include `round_seq` helper per conversation.
  - Propagate ID fields through router outputs, state tracker, job queue, memory, voice/text responses.
  - Persist IDs on every trace record; expose in APIs for correlation.
- **Verification**
  - New rounds carry `library_id`, `bucket_id`, `conversation_id`, `round_id`, `round_seq`, `audio_id` (if present) in traces and responses.

## Section 2: Decision-trace logging — DONE
- **Build steps**
  - Create `data/traces.db` with `rounds` (header) and `events` (per step) tables, including schema_version, ordering, status, failure metadata.
  - Log: raw and normalized input, routing/validator codes, prompts (capped), tool/LLM/job calls with inputs/outputs/latency, final output, user feedback.
  - Add exporter to JSONL (rounds + ordered events) for training/analysis.
  - Feature-flag instrumentation in voice/text handlers.
- **Verification**
  - Any round reconstructs ordered decision path with codes (no prose), prompts, calls, outputs, and failure markers.

## Section 3: Durability and ordering — DONE
- **Build steps**
  - Assign `round_seq` at intake per conversation; `event_seq` monotonic per round.
  - Wrap round writes in transactions; mark partial/failure explicitly; never delete evidence.
  - Serialize register/context updates per bucket; allow concurrent conversations.
- **Verification**
  - Export shows stable ordering; failures are represented with status and error info.

## Section 4: Secrets and credential handling — DONE
- **Build steps**
  - Add secret scanner (regex + entropy) for inputs, prompts, tool outputs.
  - Replace hits with `secret_ref` and store mapping in process (env/keyring-backed); do not persist secret values.
  - Leave transcripts unredacted unless a secret is detected.
- **Verification**
  - No stored record contains raw secrets; references are recoverable only in-process.

## Section 5: Voice audio capture and linkage — DONE
- **Build steps**
  - Define `audio_id` and metadata schema; store audio path + sha256 (+ optional RMS preview) in DB/metadata.
  - Default storage under OS app data (configurable); add retention cleanup with pin support.
  - Link `audio_id` to rounds and traces; include in responses for correlation.
- **Verification**
  - Each transcripted round referencing audio has a resolvable `audio_id`, path, hash, and retention status.

## Section 6: Register vs bucket context package — DONE
- **Build steps**
  - Keep register summary <=140 chars for UI. Cache bucket context package per bucket (pinned + frequent + recent items with scoring).
  - Recompute/invalidate on memory writes; expose retrieval for LLM injection and UI.
  - Add APIs/CLI to inspect register and bucket context.
- **Verification**
  - Register is short and current; context package is available with pinned/high-score items ordered.

## Section 7: Metadata schema versioning — DONE
- **Build steps**
  - Centralize schema_version constants (e.g., `caal/schemas/versions.py`) per object type (trace round/event, bucket context, register, job, state).
  - Include version on every persisted object; add migration/import path from existing memory/job stores.
- **Verification**
  - Exports show schema_version; older data can be imported once into the new schema.

## Section 8: Command authorization and confirmation (levels 1–4) — DONE
- **Build steps**
  - Extend StateTracker to carry auth levels, pending confirmations, level-2 undo, macro handling, and highest-level gating for chains.
  - Map router commands to auth levels; gate at round start; no mid-round state changes.
  - Confirmation summaries via templates; optional LLM shortening for voice/text delivery.
  - Mixed chains/macros: authorize at max level; pause on ambiguity/high-risk deltas.
- **Verification**
  - Commands show required level; chains authorized once; confirmations/undos logged; state remains stable through the round.

## Section 9: Retry policy and user notification — DONE
- **Build steps**
  - Implement validator interface with CAAL adapters; use signals to decide retry/escalation.
  - LLM retries: up to 2 on cheap/local models; then ask user for clarification.
  - Tool retries only on transient errors; never repeat side-effecting actions without user confirmation.
  - Filter incoherent utterances before LLM; include retry/escalation info in responses.
- **Verification**
  - Logs show retry counts and reasons; after retries, user receives a clarification prompt.

## Section 10: Conversation state machine (no mid-round changes) — DONE
- **Build steps**
  - Define external states: default, conversation, action, research (extensible). Adapter maps to existing GuardState.
  - Capture `state_in` per round; apply transitions only between rounds based on explicit request or end-of-round rules.
  - State influences routing defaults and escalation preferences (research escalates sooner; action prefers local + validation).
  - Preserve UI cues (label/icon/short description).
- **Verification**
  - Traces show stable `state_in` and any transition applied only at round boundary.

## Section 11: Bucket memory accumulation and retrieval — DONE
- **Build steps**
  - Track pinned items, reference counts, recency; store document/file refs with optional excerpt/summary.
  - Reference signals: explicit pin, router topic hits, citations in outputs, recency decay. Weighted scoring feeds retrieval.
  - Provide APIs/CLI: add to memory, list/search registers, dump bucket context, retrieve thoughts/logs.
  - Two-tier context for LLM: short always-injected + optional long when budget allows.
- **Verification**
  - Retrieval returns pinned/high-score/recent items in order; context respects size tiers; registers searchable.

## Cross-cutting
- **Exports/APIs:** Endpoints for trace search/export, register/context browse, job/state, audio lookup.
- **Feature flags:** Gate new logging/trace paths and audio retention to allow safe rollout.
- **Docs/tests:** Add acceptance checks per section; unit/integration tests for traces, state transitions, auth flows, retries, memory retrieval, audio retention, and secret scanning.
- **Startup guards:** Voice agent warns on missing critical env vars and fails fast if LLM envs are absent; external services (STT/TTS/LiveKit) must still be available or mocked.

## Data contracts (schemas)
- **RoundHeader:** `schema_version`, `library_id`, `bucket_id`, `conversation_id`, `round_id` (UUIDv7), `round_seq`, `state_in`, `state_out` (optional), `audio_id` (optional), `created_at`, `status` (`ok|partial|failed`), `failure_code` (optional), `failure_reason` (optional).
- **RoundEvent:** `schema_version`, `round_id`, `event_seq`, `event_type` (see taxonomy), `call_id` (optional), `timestamp`, `payload` (typed per event type), `status` (`ok|failed|skipped`), `failure_code`/`failure_reason` (optional).
- **Command:** `command_id`, `auth_level`, `summary`, `params` (opaque), `source_rule` (optional).
- **CommandChain:** `chain_id`, `commands` (list of Command with order), `chain_level = max(auth_level)`, `macro` (optional).
- **ConfirmationRecord:** `confirmation_id`, `chain_id`/`command_id`, `auth_level`, `summary`, `prompt`, `response`, `confirmed` (bool), `timestamp`, `channel` (`voice|text|ui`).
- **AudioArtifact:** `audio_id`, `schema_version`, `path`, `sha256`, `duration_ms` (optional), `codec`, `sample_rate`, `created_at`, `retention_ttl`, `pinned` (bool), `rms_preview` (optional).
- **BucketMemoryItem:** `item_id`, `bucket_id`, `pinned` (bool), `reference_score`, `recency`, `content`, `metadata` (refs/excerpts), `last_updated`.
- **BucketContextPackage:** `bucket_id`, `schema_version`, `register_summary`, `short_context` (always), `long_context` (optional), `items` (ordered with scores), `last_updated`.
- **ValidatorResult:** `validator_id`, `code` (`validator.*`), `status` (`ok|warn|error|escalate|retry`), `message` (short), `details` (optional).

## Event type taxonomy
Fixed `event_type` list with required payload fields:
- `input.received` (`raw_text`, `channel`, `audio_id?`)
- `input.normalized` (`normalized_text`)
- `route.selected` (`route_code`, `source_rule`, `topic?`, `auth_level`, `macro?`)
- `validator.ran` (`validator_id`, `code`, `status`, `message`)
- `llm.request` (`model`, `prompt_hash`, `prompt_head`, `prompt_tail`)
- `llm.response` (`model`, `latency_ms`, `text`, `usage?`, `truncated?`)
- `tool.request` (`tool_id`, `call_id`, `inputs`)
- `tool.response` (`tool_id`, `call_id`, `latency_ms`, `result`, `status`)
- `job.enqueued` (`job_id`, `topic`, `query`, `class`)
- `job.progress` (`job_id`, `status`, `progress?`)
- `job.result` (`job_id`, `status`, `result`)
- `confirm.requested` (`chain_id`/`command_id`, `auth_level`, `summary`)
- `confirm.received` (`confirmation_id`, `accepted`, `channel`)
- `command.executed` (`command_id`, `status`, `result`, `auth_level`)
- `command.reversed` (`command_id`, `status`, `reason`)
- `output.emitted` (`text`, `channel`, `was_escalated?`)
- `round.failed` (`failure_code`, `failure_reason`)

## Auth/state truth table (summary)
- **Level 1:** 0 confirms; execute immediately; reversal not required.
- **Level 2:** 0 confirms; execute; record for potential immediate undo; if user objects, treat undo as confirm gate.
- **Level 3:** 1 confirm before execution; no mid-round execution without confirm.
- **Level 4:** 2 confirms before execution (double confirm); no mid-round execution without both.
- **Chains/macros:** `chain_level = max(levels)`; authorize once at start; if ambiguity/high-risk deltas appear, pause and request confirm. Confirmations attach via `chain_id`.
- **State:** `state_in` fixed per round; transitions only at round boundary. Action prefers local+validation; research escalates sooner.

## Ordering and concurrency rules
- Allocate `round_seq` at intake under a per-conversation lock.
- Allocate `event_seq` monotonically within the round.
- All writes for a round happen inside one transaction; on exception, mark round failed/partial and keep events.
- Register/context updates serialized per bucket; conversations may run concurrently.

## Acceptance checks (per phase/section)
- **IDs/correlation:** Given a new round with audio, traces and responses include `library_id`, `bucket_id`, `conversation_id`, `round_id`, `round_seq`, `audio_id`.
- **Traces:** Given a routed turn, exported JSONL reconstructs ordered events matching the taxonomy with codes/enums (no freeform reasoning).
- **Durability:** Inject a failure mid-round; trace shows `round.failed` with failure_code/reason and preserved prior events.
- **Secrets:** Feed a string containing a known key pattern; stored trace/memory contains `secret_ref` and never the raw key.
- **Audio:** Upload a clip; metadata contains `audio_id`, path, sha256, retention; round links to the artifact.
- **Context:** After multiple updates, register is <=140 chars; context package returns pinned/high-score/recent items ordered.
- **Auth/state:** A chain mixing levels 2 and 4 is gated at level 4 before execution; no mid-round state change occurs; level-2 undo recorded if requested.
- **Retry/notification:** After two failed LLM attempts, response asks for clarification and logs retries; tool with side effects is not retried automatically.
