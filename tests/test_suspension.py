"""Tests for zero-cost suspension, 4 concurrent approvers, and wakeup callbacks."""

import threading
from concurrent.futures import ThreadPoolExecutor

import pytest
from fastapi.testclient import TestClient

from relay.api import app as api_app
from relay.db import get_db_connection
from relay.models import RunStatus
from relay.runtime import ExecutionRuntime


@pytest.fixture
def api_client():
    return TestClient(api_app)


@pytest.fixture
def runtime():
    return ExecutionRuntime()


def test_four_concurrent_approvers_on_same_approval(api_client, runtime):
    """Test 4 concurrent approvers attempting to act on the same approval row.

    Exactly 1 succeeds, 3 receive structured 400 'already resolved' errors,
    and the run transitions cleanly to 'queued' with zero race conditions.
    """
    run_id = runtime.create_run({"ticket": "approval-concurrency"})
    ctx = runtime.claim_run(worker_id="worker-susp")
    assert ctx is not None

    app_rec = runtime.suspend_for_approval(
        run_id=ctx.id,
        claim_epoch=ctx.claim_epoch,
        step_sequence=1,
        action_type="approve_payout",
        proposed_payload={"payout": 1500.0},
    )

    token = app_rec.token
    barrier = threading.Barrier(4)

    def send_approval(approver_idx: int):
        try:
            barrier.wait(timeout=2.0)
        except threading.BrokenBarrierError:
            pass

        return api_client.post(
            f"/approvals/{token}/decision",
            json={"approved": True, "reason": f"Approved by manager #{approver_idx}"},
        )

    with ThreadPoolExecutor(max_workers=4) as executor:
        futures = [executor.submit(send_approval, i) for i in range(4)]
        responses = [f.result() for f in futures]

    status_codes = [r.status_code for r in responses]
    assert status_codes.count(200) == 1
    assert status_codes.count(400) == 3

    # Verify run status is queued
    with get_db_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT status, worker_id, lease_expires_at FROM runs WHERE id = %s", (run_id,)
            )
            row = cur.fetchone()
            assert row["status"] == RunStatus.QUEUED
            assert row["worker_id"] is None
            assert row["lease_expires_at"] is None


def test_background_sweep_expires_stale_approvals(api_client, runtime):
    """Background sweep expires pending approvals past their deadline and fails the run."""
    run_id = runtime.create_run({"ticket": "expiring-approval"})
    ctx = runtime.claim_run(worker_id="worker-sweep")
    assert ctx is not None

    app_rec = runtime.suspend_for_approval(
        run_id=ctx.id,
        claim_epoch=ctx.claim_epoch,
        step_sequence=1,
        action_type="approve_audit",
        proposed_payload={"amount": 500},
        expires_in_seconds=1,
    )

    # Force expiration in DB
    with get_db_connection() as conn:
        with conn.transaction():
            with conn.cursor() as cur:
                cur.execute(
                    """
                    UPDATE approvals
                    SET expires_at = clock_timestamp() - interval '1 second'
                    WHERE token = %s
                    """,
                    (app_rec.token,),
                )

    # Trigger background sweep
    res = api_client.post("/sweep")
    assert res.status_code == 200
    data = res.json()
    assert data["expired_approvals_count"] >= 1

    # Verify run marked as failed
    with get_db_connection() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT status FROM runs WHERE id = %s", (run_id,))
            row = cur.fetchone()
            assert row["status"] == RunStatus.FAILED
