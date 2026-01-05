from caal.memory_store import MemoryStore


def test_register_and_context_are_capped(tmp_path):
    store = MemoryStore(tmp_path / "memory.db")
    bucket = "bucket:test"

    # Add enough turns to overflow 140 chars
    for i in range(10):
        store.record_turn(bucket, "user", f"message {i} " + "x" * 30)

    summary = store.get_summary(bucket)
    assert summary is not None
    assert len(summary) <= 140

    package = store.get_context_package(bucket)
    assert package["register_summary"] is not None
    assert len(package["register_summary"]) <= 140
    assert package["items"] == []  # no memory_items yet


def test_memory_items_scoring(tmp_path):
    store = MemoryStore(tmp_path / "memory.db")
    bucket = "bucket:test"

    first = store.add_memory_item(bucket, "recent", reference_score=1.0)
    second = store.add_memory_item(bucket, "pinned", pinned=True, reference_score=0.0)
    store.update_reference(bucket, first, delta=1.0)

    package = store.get_context_package(bucket)
    ids_in_order = [item["item_id"] for item in package["items"]]
    assert second in ids_in_order  # pinned appears
    assert ids_in_order[0] == second  # pinned outranks others
