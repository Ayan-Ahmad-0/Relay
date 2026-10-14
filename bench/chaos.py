"""Deterministic Fault Injection Harness (Chaos Benchmark).

Simulates crash terminations at 5 distinct execution windows:
1. before_effect
2. after_effect_before_commit
3. after_commit_before_ack
4. during_ledger_write
5. after_lease_release

Executes 40 deterministic kills per window (200 total runs) and verifies against
the external sink service that duplicate applied effects == 0.
"""

from __future__ import annotations

import argparse
import logging
import sys
from enum import StrEnum
from typing import Any
from uuid import UUID

import httpx

from relay.db import get_db_connection, transaction
from relay.models import RunStatus
from relay.runtime import ExecutionRuntime, FencingTokenError

logger = logging.getLogger("relay.chaos")


class CrashPoint(StrEnum):
    BEFORE_EFFECT = "before_effect"
    AFTER_EFFECT_BEFORE_COMMIT = "after_effect_before_commit"
    AFTER_COMMIT_BEFORE_ACK = "after_commit_before_ack"
    DURING_LEDGER_WRITE = "during_ledger_write"
    AFTER_LEASE_RELEASE = "after_lease_release"


class ChaosInjectedCrash(Exception):
    """Simulated worker kill/crash exception."""

    pass


class ChaosWorkflow:
    def __init__(
        self,
        runtime: ExecutionRuntime,
        sink_client: Any | None = None,
        crash_at: CrashPoint | None = None,
    ) -> None:
        self.runtime = runtime
        self.sink_client = sink_client
        self.crash_at = crash_at

    def execute(self, run_id: UUID, claim_epoch: int) -> dict[str, Any]:
        """Execute support ticket workflow with targeted crash points."""
        idempotency_key = f"idemp-chaos-{run_id}-step1"
        payload = {"ticket_id": str(run_id), "reply": "Resolution confirmed."}

        # Step 1: Inbound Triage
        self.runtime.start_step(run_id, claim_epoch, 1, "triage_and_effect")

        if self.crash_at == CrashPoint.BEFORE_EFFECT:
            raise ChaosInjectedCrash("Crash injected: BEFORE_EFFECT")

        if self.crash_at == CrashPoint.AFTER_EFFECT_BEFORE_COMMIT:
            # Emulate: outbound HTTP call succeeds to sink, but worker dies before DB commit
            sink_payload = {
                "idempotency_key": idempotency_key,
                "tool_name": "act_reply",
                "payload": payload,
                "metadata": {"run_id": str(run_id), "step_sequence": 1},
            }
            # Record pending row first
            with transaction() as cur:
                cur.execute(
                    """
                    INSERT INTO effects (
                        run_id, step_sequence, tool_name,
                        idempotency_key, status, payload, created_at
                    )
                    VALUES (%s, 1, 'act_reply', %s, 'pending', %s, clock_timestamp())
                    ON CONFLICT (idempotency_key) DO NOTHING;
                    """,
                    (run_id, idempotency_key, '{"ticket_id": "test"}'),
                )

            # Fire HTTP to sink
            if self.sink_client is not None:
                self.sink_client.post(self.runtime.sink_url, json=sink_payload)
            else:
                with httpx.Client() as cl:
                    cl.post(self.runtime.sink_url, json=sink_payload)

            # Crash right before DB commit marking applied!
            raise ChaosInjectedCrash("Crash injected: AFTER_EFFECT_BEFORE_COMMIT")

        # Standard safe execution
        self.runtime.apply_effect(
            run_id=run_id,
            claim_epoch=claim_epoch,
            step_sequence=1,
            tool_name="act_reply",
            idempotency_key=idempotency_key,
            payload=payload,
            client=self.sink_client,
        )

        if self.crash_at == CrashPoint.AFTER_COMMIT_BEFORE_ACK:
            raise ChaosInjectedCrash("Crash injected: AFTER_COMMIT_BEFORE_ACK")

        self.runtime.complete_step(run_id, claim_epoch, 1)

        # Step 2: Ledger Write
        if self.crash_at == CrashPoint.DURING_LEDGER_WRITE:
            self.runtime.start_step(run_id, claim_epoch, 2, "ledger_write")
            raise ChaosInjectedCrash("Crash injected: DURING_LEDGER_WRITE")

        self.runtime.start_step(run_id, claim_epoch, 2, "ledger_write")
        self.runtime.complete_step(run_id, claim_epoch, 2)

        # Completion & lease release
        self.runtime.complete_run(run_id, claim_epoch, {"status": "success"})

        if self.crash_at == CrashPoint.AFTER_LEASE_RELEASE:
            raise ChaosInjectedCrash("Crash injected: AFTER_LEASE_RELEASE")

        return {"status": "success"}


def run_chaos_iteration(
    runtime: ExecutionRuntime,
    sink_client: Any,
    crash_point: CrashPoint,
    run_index: int,
) -> bool:
    """Run a single run through a crash and recovery lifecycle."""
    run_id = runtime.create_run({"run_index": run_index, "target_crash": crash_point.value})

    # Worker 1: claims run and hits crash point
    w1_ctx = runtime.claim_run(worker_id=f"w1-{crash_point}-{run_index}", lease_duration_seconds=1)
    assert w1_ctx is not None

    w1_flow = ChaosWorkflow(runtime=runtime, sink_client=sink_client, crash_at=crash_point)
    try:
        w1_flow.execute(run_id, w1_ctx.claim_epoch)
    except ChaosInjectedCrash:
        pass  # Expected worker crash

    # Simulate lease expiration so recovery worker can claim
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

    # Worker 2 (Recovery Worker): claims run with incremented epoch and finishes safely
    w2_ctx = runtime.claim_run(worker_id=f"w2-{crash_point}-{run_index}", lease_duration_seconds=30)
    if w2_ctx is not None and w2_ctx.status != RunStatus.COMPLETED:
        recovery_flow = ChaosWorkflow(runtime=runtime, sink_client=sink_client, crash_at=None)
        try:
            recovery_flow.execute(run_id, w2_ctx.claim_epoch)
        except FencingTokenError:
            pass

    # Verify run resolved to completed
    with get_db_connection() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT status FROM runs WHERE id = %s", (run_id,))
            row = cur.fetchone()
            assert row is not None
            return row["status"] == RunStatus.COMPLETED


def run_chaos_suite(
    runtime: ExecutionRuntime,
    sink_client: Any,
    runs_per_window: int = 40,
) -> dict[str, Any]:
    """Execute full 5-window chaos benchmark suite."""
    windows = list(CrashPoint)
    total_runs = len(windows) * runs_per_window
    successful_recoveries = 0

    print(
        f"--- Starting Chaos Benchmark: {len(windows)} windows x {runs_per_window} runs "
        f"= {total_runs} total ---"
    )

    with get_db_connection() as conn:
        with conn.transaction():
            with conn.cursor() as cur:
                cur.execute(
                    "TRUNCATE TABLE runs, run_events, step_executions, effects, approvals, leases CASCADE;"
                )

    for window in windows:
        for i in range(runs_per_window):
            ok = run_chaos_iteration(runtime, sink_client, window, i)
            if ok:
                successful_recoveries += 1

    # Fetch stats from sink
    res = sink_client.get("/sink/stats")
    stats = res.json()

    print("\n--- Chaos Fault Injection Benchmark Results ---")
    print(f"Total Injected Crash Runs: {total_runs}")
    print(f"Successful Recoveries:     {successful_recoveries}/{total_runs}")
    print(f"External Sink Requests:    {stats['total_received']}")
    print(f"External Sink Unique Keys: {stats['unique_keys']}")
    print(f"Duplicate Applied Effects: {stats['duplicate_count']}")

    assert stats["duplicate_count"] == 0, (
        f"VIOLATION: Found {stats['duplicate_count']} duplicate applied effects!"
    )
    assert successful_recoveries == total_runs, (
        "VIOLATION: Not all crashed runs recovered to completed state!"
    )

    return {
        "total_runs": total_runs,
        "successful_recoveries": successful_recoveries,
        "external_requests": stats["total_received"],
        "unique_keys": stats["unique_keys"],
        "duplicate_count": stats["duplicate_count"],
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Relay Deterministic Fault Injection Benchmark")
    parser.add_argument(
        "--runs-per-window",
        type=int,
        default=40,
        help="Runs per crash window (default 40 = 200 total)",
    )
    parser.add_argument(
        "--small", action="store_true", help="Quick run with 2 runs per window (10 total)"
    )
    args = parser.parse_args()

    count = 2 if args.small else args.runs_per_window
    runtime = ExecutionRuntime()

    from fastapi.testclient import TestClient

    from relay.db import close_connection_pool
    from relay.sink import app as sink_app

    client = TestClient(sink_app)
    client.post("/sink/clear")

    try:
        results = run_chaos_suite(runtime=runtime, sink_client=client, runs_per_window=count)
    finally:
        close_connection_pool()

    if results["duplicate_count"] == 0:
        print("\n[SUCCESS] Chaos Benchmark Passed: 0 Duplicate Applied Effects!")
        sys.exit(0)
    else:
        print("\n[FAILED] Duplicate applied effects detected!")
        sys.exit(1)


if __name__ == "__main__":
    main()
