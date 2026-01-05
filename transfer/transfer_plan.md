# Transfer Plan for Thinking Router Mapping

The work performed in `C:\Users\sambr\Atlas` (feature/thinking-router-prototype) should be interpreted as a *reference implementation* for the future CAAL-based project. The notes below capture what we changed, why, and how to replicate the same design inside the destination repo.

## Decision log

1. **Persist every voice turn and decision**  
   - Added `MemoryStore` usage inside `orchestrator/tools/voice.py` so every transcript (STT output, assistant reply, text chat inputs) is written to `data/memory_store.db`.  
   - Introduced helpers `_get_bucket_id`, `_record_memory_turn`, `_record_memory_thought`, `_update_register`, and `_build_register_summary` that wrap the store and safeguard against unexpected failures.  
   - For `/voice/process` we now log the user utterance immediately after STT, log the assistant reply when it returns, capture a “voice_process” thought containing commit/trace/state metadata, and refresh the 140-character register summary so clients can search the latest bucket via `MemoryStore.search_registers`.

2. **Route results and metadata accompany each log**  
   - After `RouteProcessor` makes a decision, we persist a thought (role `processor`) containing the processor route/topic, command/job ids, the current `StateTracker` state, and whether side effects were permitted.  
   - The same metadata feeds `VoiceProcessResponse`/`TextChatResponse` so downstream clients can display job/state info while the turn is stored for future auditing.

3. **Record text chat turns too**  
   - Every `TextChatRequest` message now lands in the same memory bucket (via `_get_bucket_id`) and triggers the same turn/thought/register tracking around the processor result so the thought system is consistent whether the input came from voice or text.

4. **Reference plan updates**  
   - `docs/voice_thinking_router_plan.md` captures the higher-level thinking-router objectives that depend on these logs. Use it to understand the interactions between `MemoryStore`, `RouteProcessor`, job streams, and the register search APIs.

## How to apply this to the CAAL repo

1. **Copy helper logic**  
   - Import or implement `MemoryStore` equivalent in CAAL and replace the `orchestrator/tools/voice.py` helper functions in the target repo’s voice handling code (likely under `/frontend` or `/server`).  
   - Provide bucket naming (e.g., `bucket:{conversation_id}`) and summary building identical to `_build_register_summary` so the register remains <=140 characters.

2. **Hook voice endpoints**  
   - After STT completes, call the memory helper to log the raw transcript. After the LLM reply or command result is decided, log the assistant text and thought metadata (include commit/trace id, processor route info, command/job IDs, `allow_side_effects`, `StateTracker` state).  
   - Mirror `_record_memory_thought`’s metadata shape so the destination store can later filter by `route_source`, `topic`, etc.

3. **Attach metadata to responses**  
   - Ensure the response objects returned to clients include the `command_result` and `job_info` dictionaries so the UI knows if a long-running job was queued or if a command executed with/without confirmation.

4. **Document the flow**  
   - Carry over the implementation notes from `docs/voice_thinking_router_plan.md` into the CAAL docs (`docs/voice_pipeline_current.md` etc.) so future contributors understand the memory/register/job expectations.

5. **Inspect helper script copies**  
   - Use the files inside the `transfer/` folder (next to this repo root) as complete references for the helper implementations and metadata usage. Copy the contents verbatim when implementing the CAAL version of the bright logic.

## References

- Source helper file: `orchestrator/tools/voice.py` (see transfer copy for quick reference).  
- Design plan: `docs/voice_thinking_router_plan.md`.
