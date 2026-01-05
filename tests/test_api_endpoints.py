import json
from fastapi.testclient import TestClient

from caal.webhooks import app


def test_health_endpoint():
    client = TestClient(app)
    resp = client.get("/health")
    assert resp.status_code == 200
    data = resp.json()
    assert "status" in data
    assert "active_sessions" in data


def test_trace_export_endpoint(monkeypatch, tmp_path):
    # Patch TraceStore.export_jsonl to avoid touching real fs
    from caal import webhooks

    def fake_export(_self, out_path):
        path = tmp_path / "fake.jsonl"
        path.write_text("test\n")
        return path

    monkeypatch.setattr(webhooks.TraceStore, "export_jsonl", fake_export)

    client = TestClient(app)
    resp = client.get("/trace/export")
    assert resp.status_code == 200
    data = resp.json()
    assert data["path"].endswith("fake.jsonl")

