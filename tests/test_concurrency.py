"""Tests for isolation-level hardening, SERIALIZABLE transactions, and concurrency retries."""

import threading
import time

import pytest

from relay.db import transaction
from relay.models import RunStatus
from relay.retry import retry_on_concurrency_conflict
from relay.runtime import ExecutionRuntime


@pytest.fixture
def runtime():
    return ExecutionRuntime()


def test_concurrent_approval_resolution(runtime):
    """Two concurrent approvers acting on the same approval row.

    In SERIALIZABLE isolation with retries, one commits and the other
    detects already decided state.
    """
    runtime.create_run({"test": "concurrency_approval"})
    ctx = runtime.claim_run(worker_id="worker-concur")
    assert ctx is not None

    app_rec = runtime.suspend_for_approval(
        run_id=ctx.id,
        claim_epoch=ctx.claim_epoch,
        step_sequence=1,
        action_type="refund",
        proposed_payload={"dollars": 100},
    )

    results = []
    errors = []

    def approver_worker(approver_id: int):
        try:
            res = runtime.resolve_approval(
                token=app_rec.token,
                approved=True,
                reason=f"Approved by approver {approver_id}",
            )
            results.append(res)
        except ValueError as e:
            errors.append(str(e))

    t1 = threading.Thread(target=approver_worker, args=(1,))
    t2 = threading.Thread(target=approver_worker, args=(2,))

    t1.start()
    t2.start()
    t1.join()
    t2.join()

    # Exactly one approver must succeed, the other must see already resolved state cleanly
    assert len(results) == 1
    assert results[0] == RunStatus.QUEUED
    assert len(errors) == 1
    assert "already resolved" in errors[0]


def test_deadlock_resolution_via_retry():
    """Intentionally induce an inverted lock order deadlock between two concurrent threads.

    Verify that retry_on_concurrency_conflict intercepts SQLSTATE 40P01, retries with backoff,
    and succeeds cleanly.
    """
    # Seed two rows in leases table
    with transaction() as cur:
        cur.execute(
            """
            INSERT INTO leases (resource_id, worker_id, claim_epoch, expires_at)
            VALUES ('resource_A', 'init', 1, clock_timestamp() + interval '1 hour'),
                   ('resource_B', 'init', 1, clock_timestamp() + interval '1 hour')
            ON CONFLICT (resource_id) DO NOTHING;
            """
        )

    barrier = threading.Barrier(2)
    completed_threads = []
    attempts_a = 0
    attempts_b = 0

    @retry_on_concurrency_conflict(max_retries=5, base_delay=0.05, max_delay=0.3)
    def lock_a_then_b():
        nonlocal attempts_a
        attempts_a += 1
        with transaction() as cur:
            cur.execute("SELECT * FROM leases WHERE resource_id = 'resource_A' FOR UPDATE;")
            if attempts_a == 1:
                try:
                    barrier.wait(timeout=1.0)
                except Exception:
                    pass
                time.sleep(0.05)
            cur.execute("SELECT * FROM leases WHERE resource_id = 'resource_B' FOR UPDATE;")
            completed_threads.append("A_then_B")

    @retry_on_concurrency_conflict(max_retries=5, base_delay=0.05, max_delay=0.3)
    def lock_b_then_a():
        nonlocal attempts_b
        attempts_b += 1
        with transaction() as cur:
            cur.execute("SELECT * FROM leases WHERE resource_id = 'resource_B' FOR UPDATE;")
            if attempts_b == 1:
                try:
                    barrier.wait(timeout=1.0)
                except Exception:
                    pass
                time.sleep(0.05)
            cur.execute("SELECT * FROM leases WHERE resource_id = 'resource_A' FOR UPDATE;")
            completed_threads.append("B_then_A")

    t1 = threading.Thread(target=lock_a_then_b)
    t2 = threading.Thread(target=lock_b_then_a)

    t1.start()
    t2.start()
    t1.join()
    t2.join()

    # Both threads must complete successfully because the deadlock victim was retried and recovered!
    assert len(completed_threads) == 2
    assert "A_then_B" in completed_threads
    assert "B_then_A" in completed_threads
