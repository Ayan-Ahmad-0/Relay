"""Tests for host helpdesk triage hook (<30 LoC), shadow evaluation, and rollback."""

import pytest
from fastapi.testclient import TestClient

from relay import host_hook
from relay.api import app as api_app
from relay.host_hook import host_triage_hook
from relay.shadow import (
    baseline_triage,
    measure_feature_flag_rollback,
    run_shadow_triage_comparison,
)


@pytest.fixture
def api_client():
    return TestClient(api_app)


def test_host_hook_feature_flag_fallback():
    # 1. When flag is disabled (default)
    host_hook.RELAY_ENABLED = False

    event = {
        "id": "CW-100",
        "content": "Can I get a refund for my order?",
        "subject": "Refund Request",
    }

    result = host_triage_hook(event, fallback_func=baseline_triage)
    assert result["status"] == "baseline_processed"
    assert result["category"] == "billing"


def test_host_hook_relay_dispatch_when_enabled(monkeypatch, api_client):
    host_hook.RELAY_ENABLED = True

    event = {
        "id": "CW-101",
        "sender": {"email": "customer@example.com"},
        "subject": "Overbilled invoice",
        "content": "I was charged twice.",
    }

    class MockClient:
        def __init__(self, *args, **kwargs):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *args):
            pass

        def post(self, url, json=None, **kwargs):
            return api_client.post("/tickets", json=json)

    monkeypatch.setattr("relay.host_hook.httpx.Client", MockClient)

    result = host_triage_hook(event, fallback_func=baseline_triage)
    assert result["status"] == "dispatched_to_relay"
    assert "run_id" in result

    # Reset flag
    host_hook.RELAY_ENABLED = False


def test_shadow_triage_comparison_and_rollback():
    sample_tickets = [
        {"id": "T1", "subject": "Refund needed", "body": "Charged twice on invoice"},
        {"id": "T2", "subject": "App crashed", "body": "Error 500 on checkout screen"},
        {"id": "T3", "subject": "Damaged box", "body": "Need to return broken item"},
        {"id": "T4", "subject": "General question", "body": "What are your business hours?"},
    ]

    report = run_shadow_triage_comparison(sample_tickets)
    assert report["total_tickets"] == 4
    assert report["agreement_pct"] == 100.0
    assert report["rollback_latency_ms"] < 1.0  # Sub-millisecond rollback!


def test_measure_feature_flag_rollback_latency():
    metrics = measure_feature_flag_rollback(iterations=500)
    assert metrics["total_flips"] == 500
    assert metrics["avg_rollback_latency_ms"] < 0.1  # Microsecond order
