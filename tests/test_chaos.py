"""Tests for the deterministic fault injection harness across all 5 crash windows."""

import pytest
from fastapi.testclient import TestClient

from bench.chaos import CrashPoint, run_chaos_iteration, run_chaos_suite
from relay.runtime import ExecutionRuntime
from relay.sink import app as sink_app


@pytest.fixture
def runtime():
    return ExecutionRuntime(sink_url="http://testserver/sink")


@pytest.fixture
def sink_client():
    c = TestClient(sink_app)
    c.post("/sink/clear")
    yield c
    c.post("/sink/clear")


@pytest.mark.parametrize(
    "crash_point",
    [
        CrashPoint.BEFORE_EFFECT,
        CrashPoint.AFTER_EFFECT_BEFORE_COMMIT,
        CrashPoint.AFTER_COMMIT_BEFORE_ACK,
        CrashPoint.DURING_LEDGER_WRITE,
        CrashPoint.AFTER_LEASE_RELEASE,
    ],
)
def test_each_crash_window_recovers_with_zero_duplicates(runtime, sink_client, crash_point):
    """Each crash window must survive, recover, and result in 0 duplicate effects."""
    sink_client.post("/sink/clear")

    recovered = run_chaos_iteration(runtime, sink_client, crash_point, run_index=1)
    assert recovered is True

    stats = sink_client.get("/sink/stats").json()
    assert stats["duplicate_count"] == 0


def test_chaos_suite_small(runtime, sink_client):
    """Run a multi-window chaos suite and assert 0 duplicate applied effects."""
    sink_client.post("/sink/clear")

    results = run_chaos_suite(runtime=runtime, sink_client=sink_client, runs_per_window=2)
    assert results["duplicate_count"] == 0
    assert results["successful_recoveries"] == 10
