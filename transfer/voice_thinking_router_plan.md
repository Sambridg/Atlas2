# Thinking Router Implementation Plan

This plan builds on the work already implemented in `src/caal/{processor,state_tracker,job_queue}` plus the voice endpoints in `orchestrator/tools/voice.py`. It aligns the desire for a reliable, auditable “command/commandless research” route with the existing data stores (memory register, job queue, state tracker) so we can immediately start building on the proven foundation described in `docs/voice_local_first_plan.md`.

## What is in place today

- **Intent routing**: `RouteProcessor.process()` (see `src/caal/processor.py`) already routes transcripts through `decide_route()`/`RouteDecision`, enforces `StateTracker` limits from `data/state_tracker.json`, executes `handle_command` and/or enqueues research jobs via `JobQueue`.  
- **Stateful controls**: `/voice/state`, `/voice/state/confirm`, `/voice/state/macro` expose the tracker API; `scripts/state_cli.py` can inspect or mutate the underlying file.  
- **Job telemetry**: `JobQueue` persists to `data/jobs.db`, `/voice/jobs/{job_id}/update` and `/voice/jobs/stream` (in `orchestrator/tools/voice.py`) already expose SSE-friendly updates; `scripts/job_cli.py`/`job_notify.py` consume those records for ad-hoc inspection.  
- **Memory schema**: `src/caal/memory_store.py` defines buckets/registers/thoughts, enforces <=140 character summaries when registering a conversation, and provides getters for recent turns plus `thoughts` entries.
- **Voice stack**: `orchestrator/tools/voice.py` still drives the Stage 5 pipeline (decode → Faster Whipser STT → `get_conversation_manager()` → register replies) and wires `RouteProcessor` for commands/research inside `/chat`.

## Gaps vs. the rewritten thinking pipeline

1. **Thought persistence is not wired in**: `RouteProcessor` produces decisions but never calls into `MemoryStore.record_turn`/`record_thought`/`update_register` so there is no searchable history or debug record.  
2. **Register search tooling is missing**: there is no API or CLI for querying `MemoryStore.search_registers()` or for retrieving saved `thoughts`.  
3. **UI/notifications don’t yet surface the job/confirmation events** that we already stream from `/voice/jobs/stream`.  
4. **Memory summaries and confirmations are not linked to the speech loop** (turn metadata lacks `utterance_id`, job IDs, etc.), so the desired “conversation bucket + 140 char register summary + thought block” is never materialized alongside the transcript that arrives from the mobile client.

## Ready-to-implement steps

1. **Persist every turn + thought**  
   - Instantiate `MemoryStore()` (reuse the same `data` folder pattern) inside `orchestrator/tools/voice.py`.  
   - When `/voice/process` (and `/chat` once the new thinking router is enabled) handles a transcript, call `memory_store.record_turn(bucket_id, "user", transcript)` and, after the assistant reply, `memory_store.record_turn(bucket_id, "assistant", assistant_text)`.  
   - For each call to `_determine_task_class`/`RouteProcessor`/`handle_command`, capture the prompt+response/decision metadata and pass it to `memory_store.record_thought(...)`, tagging the conversation/turn IDs and storing the command actions so the entire decision trail is searchable later.  
   - Use `memory_store.update_register(conversation_id, bucket_id, summary)` after each session; the 140-character truncation already lives in the method, so no extra trimming is required.

2. **Expose register/thought search**  
   - Add FastAPI routes under `/memory/registers` (GET) and `/memory/thoughts/{conversation_id}` (GET) that wrap the `MemoryStore.search_registers`/`get_thoughts` helpers; reuse the CLI pattern in `scripts/state_cli.py` to add `--search-register keyword` and `--dump-thoughts conversation_id`.  
   - Ensure both API and CLI return the same sanitized JSON blobs so operators can debug or feed data into training pipelines.  
   - Document the payloads in a new `docs/voice_memory_api.md` (cross-link back to this plan) so clients know how to query buckets/register entries/thought blocks.

3. **Link job/state/confirmation signals to the UI**  
   - `/voice/jobs/stream` already emits SSE updates; confirm the front end listens and surfaces statuses (review `mobile_client/src/services/jobQueue.ts` or the equivalent orchestrator UI).  
   - When the state tracker changes (`/voice/state`), emit an event via `emit_event` (observability already wired in `voice.py`) so any dashboard or SSE listener can show the current authority level and confirmed command list.  
   - Surface job IDs/`allow_side_effects` status inside the `VoiceProcessResponse` so the mobile app can show “command queued for confirmation” vs. “research job #XYZ enqueued”.

4. **Correlate utterance metadata**  
   - Ensure every voice response carries `utterance_id` (commit ID) and, once the HTTP resumable upload path is added, log it in `JobQueue.metadata`/`MemoryStore.thoughts.metadata`.  
   - Update `RouteProcessor.process` metadata argument to include `conversation_id`, `commit_id`, and `intent` to make debugging easier.  
   - Persist the `memory_store.register` entry with that metadata so later searches can map a bucket back to the originating speech commit.

5. **Observability / diag scripts**  
   - Extend `scripts/job_notify.py` to optionally stream A/V commit IDs + job metadata (an `--include-metadata` flag) so operators can trace which voice file triggered a job.  
   - Add `scripts/diag_voice.ps1` (or reuse `diagnose-orchestrate.ps1`) that accepts `--utterance id` and prints matching entries from `data/jobs.db`, `data/state_tracker.json`, and registries/thoughts from `MemoryStore`.
   - Keep `tests/test_state_tracker.py`, `tests/test_processor.py`, `tests/test_job_queue.py`, `tests/test_command_handlers.py` passing; add new tests that simulate email/resizable `MemoryStore.record_thought` calls to guarantee summaries stay ≤140 characters and that register searches return the expected bucket IDs.

6. **Reference CAAL & existing docs**  
   - The CoreWorxLab [CAAL repo](https://github.com/CoreWorxLab/CAAL) remains a helpful blueprint for the voice/LiveKit transport; we already borrowed its job/value models (see `caal/job_queue.py`). Keep noting CAAL’s architecture when designing future transport/offline pieces so we do not re-implement unstable websocket routes.  
   - Link this plan from `docs/voice_local_first_plan.md` so reviewers can see the “thinking router” steps alongside the capture/queue plan.

## Testing notes

- Running `py -3 -m pytest tests/test_state_tracker.py tests/test_processor.py tests/test_job_queue.py tests/test_command_handlers.py` should pass after each change. These tests already cover persistence, routing, and command execution semantics mentioned above.  
- Once `/memory/register` is live, add a test that seeds the register, triggers the API, and validates the 140-character `summary` guarantee.  
- Use `scripts/job_cli.py`/`job_notify.py` to verify jobs are created when `RouteProcessor` handles `research` routes and that SSE events carry the expected metadata.

This document should serve as the single source of truth for the “thinking router + memory register + job state” effort. Each step above links to currently committed code, so the next engineer can implement the pieces without needing further research. Once these steps are completed, the system will have the live transcript/thought logging, searchable register, and surfaced statuses that were requested.
