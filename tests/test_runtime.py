"""Integration tests for Postgres coordination primitives, fencing tokens,
and zero-cost suspension.
"""

from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from relay.db import get_db_connection
from relay.models import RunStatus
from relay.runtime import ExecutionRuntime, FencingTokenError
from relay.sink import app as sink_app


@pytest.fixture(scope="module")
def runtime():
    # Use live database URL from environment or default
    return ExecutionRuntime(sink_url="http://testserver/sink")


@pytest.fixture
def sink_client():
    c = TestClient(sink_app)
    c.post("/sink/clear")
    yield c
    c.post("/sink/clear")


def test_task_claiming_and_fencing_token(runtime):
    """Verify task claiming increments claim_epoch fencing token."""
    run_id = runtime.create_run({"ticket_id": 101, "subject": "Refund inquiry"})

    # Worker 1 claims run
    ctx1 = runtime.claim_run(worker_id="worker-alpha", lease_duration_seconds=30)
    assert ctx1 is not None
    assert ctx1.id == run_id
    assert ctx1.status == RunStatus.RUNNING
    assert ctx1.claim_epoch == 1
    assert ctx1.worker_id == "worker-alpha"

    # Worker 2 attempts to claim while lease is active -> queue is empty
    ctx2 = runtime.claim_run(worker_id="worker-beta", lease_duration_seconds=30)
    assert ctx2 is None


def test_zombie_worker_fencing_protection(runtime):
    """A stale worker whose lease expired and was claimed by another worker CANNOT commit."""
    run_id = runtime.create_run({"ticket_id": 102, "subject": "Zombie test"})

    # Worker 1 claims run at epoch 1
    w1_ctx = runtime.claim_run(worker_id="worker-1", lease_duration_seconds=1)
    assert w1_ctx is not None
    assert w1_ctx.claim_epoch == 1

    # Simulate lease expiration in DB
    with get_db_connection() as conn:
        with conn.transaction():
            with conn.cursor() as cur:
                cur.execute(
                    """
                    UPDATE runs
                    SET lease_expires_at = clock_timestamp() - interval '1 second'
                    WHERE id = %s
                    """,
                    (run_id,),
                )

    # Worker 2 claims the expired run -> epoch increments to 2
    w2_ctx = runtime.claim_run(worker_id="worker-2", lease_duration_seconds=30)
    assert w2_ctx is not None
    assert w2_ctx.id == run_id
    assert w2_ctx.claim_epoch == 2
    assert w2_ctx.worker_id == "worker-2"

    # Worker 1 (the zombie) wakes up and attempts to start a step with stale epoch 1
    with pytest.raises(FencingTokenError) as exc_info:
        runtime.start_step(run_id, claim_epoch=1, step_sequence=1, step_name="stale_step")

    assert "Zombie worker detected" in str(exc_info.value)

    # Worker 2 with active epoch 2 succeeds cleanly
    runtime.start_step(run_id, claim_epoch=2, step_sequence=1, step_name="legit_step")
    runtime.complete_step(run_id, claim_epoch=2, step_sequence=1)
    runtime.complete_run(run_id, claim_epoch=2, output_payload={"status": "success"})


def test_outbound_effect_idempotency_with_sink(runtime, sink_client):
    """Effects are idempotent: repeated application uses cached DB record."""
    runtime.create_run({"ticket_id": 103, "claim": 50.0})
    ctx = runtime.claim_run(worker_id="worker-effects", lease_duration_seconds=30)
    assert ctx is not None

    key = f"idemp-key-{uuid4().hex[:12]}"
    payload = {"reply": "Your refund of $50 has been queued."}

    # First call applies effect and contacts sink
    effect1 = runtime.apply_effect(
        run_id=ctx.id,
        claim_epoch=ctx.claim_epoch,
        step_sequence=1,
        tool_name="act_reply",
        idempotency_key=key,
        payload=payload,
        client=sink_client,
    )
    assert effect1.status == "applied"
    assert effect1.idempotency_key == key

    # Second call with the same idempotency key
    effect2 = runtime.apply_effect(
        run_id=ctx.id,
        claim_epoch=ctx.claim_epoch,
        step_sequence=1,
        tool_name="act_reply",
        idempotency_key=key,
        payload=payload,
        client=sink_client,
    )
    assert effect2.id == effect1.id
    assert effect2.status == "applied"

    # Verify external sink was called exactly once, no duplicates!
    stats = sink_client.get("/sink/stats").json()
    assert stats["total_received"] == 1
    assert stats["duplicate_count"] == 0


def test_zero_cost_suspension_and_approval_resumption(runtime):
    """Suspension sets status to waiting_approval, clears leases to 0 cost, and resumes."""
    run_id = runtime.create_run({"ticket_id": 104, "claim": 2000.0})
    ctx = runtime.claim_run(worker_id="worker-suspender", lease_duration_seconds=30)
    assert ctx is not None

    # Step requires human approval
    app_record = runtime.suspend_for_approval(
        run_id=ctx.id,
        claim_epoch=ctx.claim_epoch,
        step_sequence=1,
        action_type="approve_high_value_refund",
        proposed_payload={"amount": 2000.0, "reason": "Customer damaged goods"},
    )
    assert app_record.status == "pending"
    assert app_record.token is not None

    # Verify run is in waiting_approval state and lease is released
    with get_db_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT status, worker_id, lease_expires_at FROM runs WHERE id = %s",
                (run_id,),
            )
            row = cur.fetchone()
            assert row["status"] == "waiting_approval"
            assert row["worker_id"] is None
            assert row["lease_expires_at"] is None

    # Human manager approves the token
    new_run_status = runtime.resolve_approval(
        app_record.token, approved=True, reason="Receipt verified"
    )
    assert new_run_status == RunStatus.QUEUED

    # Run is back in queue and can be re-claimed by any worker
    resumed_ctx = runtime.claim_run(worker_id="worker-resumer", lease_duration_seconds=30)
    assert resumed_ctx is not None
    assert resumed_ctx.id == run_id
    assert resumed_ctx.claim_epoch == ctx.claim_epoch + 1


def test_deterministic_event_log(runtime):
    """run_events stores sequential execution events strictly."""
    run_id = runtime.create_run({"ticket_id": 105})
    ctx = runtime.claim_run(worker_id="worker-logger")
    assert ctx is not None

    runtime.record_event(
        run_id=ctx.id,
        claim_epoch=ctx.claim_epoch,
        sequence_num=1,
        event_type="clock_read",
        payload={"iso": "2026-10-14T00:00:00Z"},
    )
    runtime.record_event(
        run_id=ctx.id,
        claim_epoch=ctx.claim_epoch,
        sequence_num=2,
        event_type="model_output",
        payload={"choice": "refund"},
    )

    with get_db_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT sequence_num, event_type, payload
                FROM run_events
                WHERE run_id = %s
                ORDER BY sequence_num ASC
                """,
                (run_id,),
            )
            rows = cur.fetchall()
            assert len(rows) == 2
            assert rows[0]["sequence_num"] == 1
            assert rows[0]["event_type"] == "clock_read"
            assert rows[1]["sequence_num"] == 2
            assert rows[1]["event_type"] == "model_output"
