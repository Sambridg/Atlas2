# Local‑First Voice Capture: Viability & Updated Implementation Plan

This document reviews the proposed “local-first voice capture + offline queue + resumable send” plan against the current Atlas codebase, highlights what already exists, and lists concrete, ready-to-implement steps with code references.

## Current State (as built today)

- **Recording is already local-first**: `mobile_client/src/audio/AudioSessionController.ts` records to disk via `AudioPipelineNative`, producing `.opusp` + `.idx` + metadata. Live streaming is disabled (Stage 4 comment); clips are queued after stop.
- **Persistent outbound queue exists**: `mobile_client/src/services/queue/QueueStore.ts` uses `react-native-sqlite-storage` to persist queue items. `QueueWorker.ts` drains the queue; `AudioClipUploader.ts` streams audio over `/ws/control` + `/ws/audio` with resume/NACK handling. Queue item types include `audio_clip_upload` and `voice_process` (see `QueueTypes.ts`).
- **Upload transport**: uplink uses the dual-socket WebSocket transport (`mobile_client/src/services/dualSocketTransport.ts`) to talk to `/ws/control` and `/ws/audio`. Server handler is `orchestrator/tools/transport_ws.py` → `save_commit` in `orchestrator/voice/audio_commit_store.py`.
- **Voice processing**: After upload, `QueueWorker.voice_process` calls `/voice/process` (`orchestrator/tools/voice.py`), which drives STT via `orchestrator/voice/transcribers` and returns text/LLM response.
- **Downlink TTS**: `/ws/control` sends `tts.start.ok` and audio frames over `/ws/audio`; handled client-side by `TtsDownlinkReceiver.ts` + `TtsPlaybackController.ts`.
- **Tasks/SSE**: `/tasks/stream` (see `orchestrator/tasks/api.py`) already provides task updates; `tests/test_sse_integration.py` covers it.
- **Known blocker**: the live orchestrator currently returns `404` for `/ws/control` (see `logs/dev/orchestrator.log` and local `ws_test.py`), so no control/audio sockets succeed. This must be fixed before relying on any WS path or replaced with an HTTP upload path.

## Gaps vs. the proposed plan

1. **Control WS instability**: Current transport is WS-only; with `/ws/control` returning 404, uploads fail. A resumable HTTP path would bypass this.
2. **Feature flag**: No VOICE_LOCAL_FIRST flag exists; local-first is effectively on already, but not configurable.
3. **Resumable HTTP upload**: Not implemented. Everything uploads via WS audio frames.
4. **Explicit diagnostics**: No single-command diag for voice uploads; logging exists but is scattered.
5. **Docs**: No consolidated “current pipeline” or “new pipeline” docs.

## Updated, repo-aware plan

### Phase 0 — Preconditions
- **Fix/control test**: Bring `/ws/control` back online or decide to bypass it. Validate with `python ws_test.py` (local) and watch for `WS control handshake started` in `orchestrator/tools/transport_ws.py` logs.
- **Single-process run for debugging**: Start without `--reload` to avoid multiple uvicorn workers during WS troubleshooting (see `dev.ps1` launcher logic).
- **Keep fallback**: Do not delete WS upload until HTTP path is stable; dual-path rollout is required.

### Phase 1 — Document current pipeline
- Add `docs/voice_pipeline_current.md` capturing:
  - Client capture: `AudioSessionController.ts` → local files (`.opusp/.idx`).
  - Queue persistence: `QueueStore.ts`, `QueueWorker.ts`, `AudioClipUploader.ts` (WS upload), `QueueConfig.ts` backoff and retry limits.
  - Server uplink: `/ws/control` + `/ws/audio` in `orchestrator/tools/transport_ws.py` → `save_commit` → `voice/process` in `orchestrator/tools/voice.py` → STT (`orchestrator/voice/transcribers`) → LLM → `voiceResponseStore.ts` consumer.
  - Downlink TTS: `/ws/control` + `/ws/audio` → `TtsDownlinkReceiver.ts` → `TtsPlaybackController.ts`.

### Phase 2 — Feature flag + client guardrails
- Add `VOICE_LOCAL_FIRST` flag (default **true** to match current behavior) in mobile config (e.g., `mobile_client/src/config.ts` or a new feature flag module).
- When the flag is **off**, allow the existing live-stream path (if revived) to run; when **on**, keep “record to disk, queue for upload” behavior and short-circuit any live streaming code paths.
- UI copy: ensure ChatInputBar shows “Recording…/Recorded (queued)” independently of network state (already implied by state machine, confirm messaging in `ChatShellScreen.tsx` / `MessageBubble.tsx`).

### Phase 3 — Resumable HTTP upload (new path, keep WS as fallback)
- Server: add `orchestrator/tools/voice_upload.py` and include router in `orchestrator/main.py`.
  - `POST /voice/utterances/init` → alloc temp dir under `runtime/audio_uploads/<utterance_id>` and respond `{accepted:true, chunk_size, upload_url_base}`.
  - `PUT /voice/utterances/{utterance_id}/chunks/{seq}` → append chunk to temp file; track highest seq; respond `{ack: seq}`.
  - `POST /voice/utterances/{utterance_id}/commit` → validate sha256/size, stitch to single file, write metadata, and enqueue processing (see below).
  - Storage helpers in `orchestrator/voice/voice_store.py`; expire abandoned uploads after N minutes (configurable).
  - Tests: `orchestrator/tests/test_voice_upload_resumable.py` covering happy path, missing seq retry, hash mismatch, expiry.
- Server processing: on commit, enqueue a task (reuse `orchestrator/tasks/store.py`) with `task_class=TaskClass.NOTE` or a new `voice_transcribe` descriptor pointing to the existing STT pipeline in `orchestrator/tools/voice.py` (can call the same code path used by `/voice/process` once the file is in `audio_commits`).
- Client: add an HTTP uploader parallel to `AudioClipUploader.ts`:
  - Reuse `QueueStore` for persistence; add a new queue item type `voice_http_upload` with payload `{ utteranceId, sha256, bytesTotal, filePath, codec, sampleRate }`.
  - Implement `VoiceHttpUploader.ts` that reads the `.opusp` file, slices into chunks (e.g., 32–64 KB), and drives the init → PUT chunks → commit flow with retry/backoff from `QueueConfig.ts`.
  - Wire `QueueWorker.ts` to prefer HTTP uploader when `VOICE_LOCAL_FIRST` is on and server advertises support (feature flag from `/app/version` or a new config endpoint); otherwise fall back to `AudioClipUploader` (WS).
  - Keep `FileVerifier.ts` for SHA/index validation; reuse `QueueWorker` logging (“voice_debug_session/run”).

### Phase 4 — Task and UX integration
- On server commit, create/update a task so existing `/tasks/list` and `/tasks/stream` reflect states: `queued → transcribing → completed/failed`. Map this to client UI statuses (`recording/uploading/transcribing/error`) in `chatTypes.ts` and `ChatShellScreen.tsx`.
- When STT finishes, surface transcript/LLM response via existing `/voice/process` response shape or a new `/voice/result/{utterance_id}` read endpoint; publish to `voiceResponseStore.ts` to keep current UI wiring unchanged.

### Phase 5 — Observability
- Include `utterance_id` in:
  - Client logs (queue + uploader + response store).
  - Server logs for upload endpoints and STT (`orchestrator/voice/transcribers/*`), and task transitions (`orchestrator/tasks/api.py`).
- Add a simple server diag script `scripts/diag_voice.ps1` that greps `logs/dev/orchestrator.log` and lists `runtime/audio_uploads/*` + `runtime/audio_commits/*` for a given `utterance_id`.
- Optionally add a client-side diag export hook in the queue/uploader to dump last N items and uploader state.

### Phase 6 — Tests
- **Server**: new resumable upload tests (init → chunk gaps → retry → commit; hash mismatch; expiry) plus existing WS tests remain (`tests/test_transport_ws.py`, `tests/test_transport_audio_stream.py`).
- **Client**:
  - Queue persistence on restart (`QueueStore` already has tests; extend for new type).
  - HTTP uploader resumes from `next_seq` after connectivity toggles.
  - End-to-end scripted test: record short clip, toggle network off during upload, back on, verify commit + `/voice/process` response stored in `voiceResponseStore`.

### Phase 7 — Rollout
- Default `VOICE_LOCAL_FIRST=true` with HTTP uploader feature gated by a server capability flag. Keep WS uploader as a fallback until HTTP path is proven in dev.
- Once `/ws/control` stability is restored, WS can remain for TTS downlink and/or be retired for uplink if HTTP meets performance needs.

## External references
- Existing client persistence uses `react-native-sqlite-storage` (see `QueueStore.ts`), which is suitable for the new HTTP uploader as well.
- Audio format: current pipeline uses Opus frames in a custom `.opusp` container + `.idx`; reuse this for HTTP chunks to avoid re-encoding.

## Summary of deliverables (no placeholders)
- `docs/voice_pipeline_current.md` (map of today’s flow with the file references above).
- `VOICE_LOCAL_FIRST` flag in client config + conditional path selection.
- New HTTP upload stack: `orchestrator/tools/voice_upload.py`, `orchestrator/voice/voice_store.py`, `orchestrator/tests/test_voice_upload_resumable.py`, client `VoiceHttpUploader.ts` + queue wiring.
- Task/state propagation via existing `/tasks/stream` with `utterance_id` correlation.
- Observability: `scripts/diag_voice.ps1` + utterance_id logging in upload/STT/task code paths.
- Test extensions for queue persistence and resumable HTTP upload, plus an end-to-end offline/online toggle scenario.
