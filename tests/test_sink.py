"""Unit tests for the external sink service."""

import pytest
from fastapi.testclient import TestClient

from relay.sink import app


@pytest.fixture
def client(tmp_path, monkeypatch):
    test_log = tmp_path / "test_sink.log"
    monkeypatch.setattr("relay.sink.SINK_LOG_PATH", test_log)
    c = TestClient(app)
    c.post("/sink/clear")
    yield c
    c.post("/sink/clear")


def test_sink_health(client):
    response = client.get("/health")
    assert response.status_code == 200
    assert response.json()["status"] == "healthy"


def test_sink_no_deduplication_invariant(client):
    """The sink must NEVER deduplicate: identical keys are logged verbatim."""
    record_data = {
        "idempotency_key": "test-key-hash-12345",
        "tool_name": "act_reply",
        "payload": {"ticket_id": 42, "text": "Refund processed."},
    }

    # First request
    res1 = client.post("/sink", json=record_data)
    assert res1.status_code == 200
    assert res1.json()["status"] == "recorded"
    assert res1.json()["record_index"] == 0

    # Second identical request (e.g. from an uncoordinated duplicate send or crash)
    res2 = client.post("/sink", json=record_data)
    assert res2.status_code == 200
    assert res2.json()["status"] == "recorded"
    assert res2.json()["record_index"] == 1

    # Check stats - duplicate must be detected externally
    stats = client.get("/sink/stats").json()
    assert stats["total_received"] == 2
    assert stats["unique_keys"] == 1
    assert stats["duplicate_count"] == 1
    assert stats["duplicate_keys"] == ["test-key-hash-12345"]


def test_sink_filtering(client):
    client.post(
        "/sink",
        json={"idempotency_key": "k1", "tool_name": "act_reply", "payload": {"msg": "hi"}},
    )
    client.post(
        "/sink",
        json={"idempotency_key": "k2", "tool_name": "act_tag", "payload": {"tag": "billing"}},
    )

    all_recs = client.get("/sink/records").json()
    assert len(all_recs) == 2

    k1_recs = client.get("/sink/records?idempotency_key=k1").json()
    assert len(k1_recs) == 1
    assert k1_recs[0]["tool_name"] == "act_reply"

    tag_recs = client.get("/sink/records?tool_name=act_tag").json()
    assert len(tag_recs) == 1
    assert tag_recs[0]["idempotency_key"] == "k2"
