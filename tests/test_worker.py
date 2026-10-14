"""Tests for Relay Worker loop and execution lifecycle."""

import pytest

from relay.db import get_db_connection
from relay.models import RunStatus
from relay.runtime import ExecutionRuntime
from relay.worker import Worker


@pytest.fixture
def runtime():
    return ExecutionRuntime()


def test_worker_process_default_run(runtime):
    """Worker claims queued run and executes default steps to completion."""
    run_id = runtime.create_run({"ticket_id": 201, "customer": "Alice"})
    worker = Worker(worker_id="worker-test-1", runtime=runtime)

    processed = worker.process_one_run()
    assert processed is True

    # Verify run completed in DB
    with get_db_connection() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT status, output_payload, error FROM runs WHERE id = %s", (run_id,))
            row = cur.fetchone()
            assert row["status"] == RunStatus.COMPLETED
            assert row["output_payload"]["status"] == "success"
            assert row["output_payload"]["processed_by"] == "worker-test-1"
            assert row["error"] is None

    # Next attempt on empty queue returns False
    assert worker.process_one_run() is False


def test_worker_suspension_workflow(runtime):
    """Worker workflow triggering zero-cost suspension stops execution cleanly."""
    run_id = runtime.create_run({"ticket_id": 202, "amount": 5000})

    def approval_workflow(run_ctx, rt):
        # Step 1: needs approval
        rt.start_step(run_ctx.id, run_ctx.claim_epoch, 1, "check_amount")
        rt.complete_step(run_ctx.id, run_ctx.claim_epoch, 1)
        rt.suspend_for_approval(
            run_ctx.id,
            run_ctx.claim_epoch,
            2,
            action_type="approve_large_payout",
            proposed_payload={"amount": 5000},
        )
        return {"suspended": True}

    worker = Worker(worker_id="worker-suspend-test", runtime=runtime)
    processed = worker.process_one_run(workflow_handler=approval_workflow)
    assert processed is True

    # Verify run remains in waiting_approval state and is not completed
    with get_db_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT status, worker_id, lease_expires_at FROM runs WHERE id = %s", (run_id,)
            )
            row = cur.fetchone()
            assert row["status"] == RunStatus.WAITING_APPROVAL
            assert row["worker_id"] is None
            assert row["lease_expires_at"] is None


def test_worker_failure_handling(runtime):
    """Worker catches unexpected handler exceptions and records run status as failed."""
    run_id = runtime.create_run({"ticket_id": 203, "corrupt": True})

    def crashing_workflow(run_ctx, rt):
        raise ValueError("Simulated handler crash")

    worker = Worker(worker_id="worker-crash-test", runtime=runtime)
    processed = worker.process_one_run(workflow_handler=crashing_workflow)
    assert processed is True

    with get_db_connection() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT status, error FROM runs WHERE id = %s", (run_id,))
            row = cur.fetchone()
            assert row["status"] == RunStatus.FAILED
            assert "Simulated handler crash" in row["error"]
