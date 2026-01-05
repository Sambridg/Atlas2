from caal.trace_store import TraceStore


def test_round_and_events_ordering(tmp_path):
    store = TraceStore(tmp_path / "traces.db")

    round_header = store.start_round(
        library_id="lib:test",
        bucket_id="bucket:test",
        conversation_id="conv1",
        state_in="DEFAULT",
        audio_id=None,
    )

    e1 = store.append_event(round_header["round_id"], "input.received", {"raw_text": "hi", "channel": "voice"})
    e2 = store.append_event(round_header["round_id"], "route.selected", {"route_code": "chat"})

    assert e1 == 1
    assert e2 == 2

    store.mark_round(round_header["round_id"], status="failed", failure_code="x", failure_reason="oops")

    header = store.fetch_round(round_header["round_id"])
    assert header["status"] == "failed"
    events = store.fetch_events(round_header["round_id"])
    assert [ev["event_seq"] for ev in events] == [1, 2]
