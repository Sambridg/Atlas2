import json
import sqlite3
import tempfile
from pathlib import Path

from caal.trace_store import TraceStore
from caal.types import EventType


def test_trace_taxonomy_export_order():
    tmpdir = tempfile.mkdtemp()
    try:
        db_path = Path(tmpdir) / "traces.db"
        store = TraceStore(db_path)
        round_header = store.start_round(
            library_id="lib:test",
            bucket_id="bucket:test",
            conversation_id="conv1",
            state_in="DEFAULT",
            audio_id=None,
        )
        rid = round_header["round_id"]
        store.append_event(rid, EventType.INPUT_RECEIVED.value, {"raw_text": "hi", "channel": "voice"})
        store.append_event(rid, EventType.INPUT_NORMALIZED.value, {"normalized_text": "hi"})
        store.append_event(rid, EventType.ROUTE_SELECTED.value, {"route_code": "chat"})
        store.append_event(rid, EventType.OUTPUT_EMITTED.value, {"text": "ok", "channel": "voice"})
        store.mark_round(rid, status="ok", state_out="DEFAULT")

        out_path = store.export_jsonl(Path(tmpdir) / "out.jsonl")
        lines = out_path.read_text().splitlines()
        assert lines[0].startswith('{"type": "round"')
        assert any('"event_type": "input.received"' in line for line in lines)
        assert any('"event_type": "route.selected"' in line for line in lines)
    finally:
        try:
            Path(tmpdir).unlink(missing_ok=True)
        except Exception:
            pass


def test_trace_events_status_fields():
    tmpdir = tempfile.mkdtemp()
    try:
        db_path = Path(tmpdir) / "traces.db"
        store = TraceStore(db_path)
        round_header = store.start_round(
            library_id="lib:test",
            bucket_id="bucket:test",
            conversation_id="conv1",
            state_in="DEFAULT",
            audio_id=None,
        )
        rid = round_header["round_id"]
        store.append_event(rid, EventType.LLM_REQUEST.value, {"prompt_hash": "h"}, status="ok")
        store.append_event(rid, EventType.LLM_RESPONSE.value, {"text": "x"}, status="failed", failure_code="err")
        evs = store.fetch_events(rid)
        assert evs[1]["status"] == "failed"
        assert evs[1]["failure_code"] == "err"
    finally:
        try:
            Path(tmpdir).unlink(missing_ok=True)
        except Exception:
            pass
