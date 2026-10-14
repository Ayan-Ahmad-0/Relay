"""Inverted Contention Benchmark.

Configures concurrent workers fighting over a small batch of runs and mutating
a single shared tenant quota row in PostgreSQL to maximize lock contention.

Tests concurrency scaling (2, 4, 8, 16 workers), locates the performance knee,
and asserts:
- 100% terminal state resolution
- 100% retry success
- 0 permanent serialization failures
"""

from __future__ import annotations

import argparse
import logging
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from typing import Any
from uuid import UUID

from relay.db import close_connection_pool, get_db_connection, transaction
from relay.retry import retry_on_concurrency_conflict
from relay.runtime import ExecutionRuntime, FencingTokenError

logger = logging.getLogger("relay.contention")


@retry_on_concurrency_conflict(max_retries=10, base_delay=0.02, max_delay=0.5)
def execute_contention_step(
    runtime: ExecutionRuntime,
    run_id: UUID,
    claim_epoch: int,
    tenant_id: str,
) -> None:
    """Mutate shared tenant quota row and complete step inside single transaction."""
    with transaction(isolation_level="REPEATABLE READ") as cur:
        # Fencing token check
        cur.execute("SELECT claim_epoch FROM runs WHERE id = %s FOR UPDATE;", (run_id,))
        row = cur.fetchone()
        if not row or row["claim_epoch"] != claim_epoch:
            raise FencingTokenError("Fencing epoch mismatch during contention step")

        # Mutate shared tenant quota row (high contention target)
        cur.execute(
            """
            UPDATE quota
            SET consumed_tokens = consumed_tokens + 150,
                consumed_requests = consumed_requests + 1,
                updated_at = clock_timestamp()
            WHERE tenant_id = %s;
            """,
            (tenant_id,),
        )

        cur.execute(
            """
            UPDATE step_executions
            SET status = 'completed', completed_at = clock_timestamp()
            WHERE run_id = %s AND step_sequence = 1;
            """,
            (run_id,),
        )

        cur.execute(
            """
            UPDATE runs
            SET status = 'completed',
                output_payload = '{"contention": "resolved"}'::jsonb,
                lease_expires_at = NULL,
                updated_at = clock_timestamp()
            WHERE id = %s AND claim_epoch = %s;
            """,
            (run_id, claim_epoch),
        )


def contention_worker_loop(
    worker_id: str,
    runtime: ExecutionRuntime,
    tenant_id: str,
    stop_event: threading.Event,
    stats: dict[str, int],
) -> None:
    """Worker polling queue and processing runs under extreme lock contention."""
    while not stop_event.is_set():
        run_ctx = runtime.claim_run(worker_id=worker_id, lease_duration_seconds=30)
        if not run_ctx:
            # Check if all runs are completed
            with get_db_connection() as conn:
                with conn.cursor() as cur:
                    cur.execute(
                        """
                        SELECT COUNT(*) AS pending FROM runs
                        WHERE status IN ('queued', 'running');
                        """
                    )
                    row = cur.fetchone()
                    if row and row["pending"] == 0:
                        break
            time.sleep(0.02)
            continue

        try:
            runtime.start_step(run_ctx.id, run_ctx.claim_epoch, 1, "contention_step")
            execute_contention_step(runtime, run_ctx.id, run_ctx.claim_epoch, tenant_id)
            stats["processed"] += 1
        except FencingTokenError:
            stats["fenced"] += 1
        except Exception as e:
            logger.error(f"[{worker_id}] Unhandled error: {e}")
            stats["errors"] += 1


def run_contention_benchmark(
    num_runs: int = 20,
    worker_counts: list[int] | None = None,
) -> dict[str, Any]:
    """Execute inverted contention benchmark across varying worker concurrency."""
    if worker_counts is None:
        worker_counts = [2, 4, 8]

    tenant_id = "tenant-alpha"
    runtime = ExecutionRuntime()
    benchmark_results = []

    print("\n=======================================================")
    print("      RELAY INVERTED CONTENTION BENCHMARK REPORT      ")
    print("=======================================================")
    print(f"Total Runs per Test:         {num_runs}")
    print(f"Tested Worker Configurations: {worker_counts}")
    print(f"Shared Lock Contention Row:   quota (tenant_id='{tenant_id}')")
    print("-------------------------------------------------------")

    for num_workers in worker_counts:
        # 1. Reset database state for this run
        with get_db_connection() as conn:
            with conn.transaction():
                with conn.cursor() as cur:
                    cur.execute(
                        "TRUNCATE TABLE runs, step_executions, effects, approvals, quota CASCADE;"
                    )
                    cur.execute(
                        """
                        INSERT INTO quota (
                            tenant_id, allocated_tokens, consumed_tokens,
                            allocated_requests, consumed_requests
                        )
                        VALUES (%s, 1000000, 0, 10000, 0);
                        """,
                        (tenant_id,),
                    )
                    for i in range(num_runs):
                        cur.execute(
                            """
                            INSERT INTO runs (status, claim_epoch, input_payload)
                            VALUES ('queued', 0, %s);
                            """,
                            (f'{{"run_idx": {i}}}',),
                        )

        stats = {"processed": 0, "fenced": 0, "errors": 0}
        stop_event = threading.Event()
        start_time = time.perf_counter()

        with ThreadPoolExecutor(max_workers=num_workers) as executor:
            futures = [
                executor.submit(
                    contention_worker_loop,
                    f"worker-{num_workers}-{w}",
                    runtime,
                    tenant_id,
                    stop_event,
                    stats,
                )
                for w in range(num_workers)
            ]
            for f in futures:
                f.result()

        duration = time.perf_counter() - start_time
        throughput = num_runs / duration if duration > 0 else 0

        # Verify terminal states
        with get_db_connection() as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT COUNT(*) AS completed FROM runs WHERE status = 'completed';")
                row = cur.fetchone()
                completed = row["completed"] if row else 0

                cur.execute(
                    "SELECT consumed_requests, consumed_tokens FROM quota WHERE tenant_id = %s;",
                    (tenant_id,),
                )
                q_row = cur.fetchone()
                consumed_reqs = q_row["consumed_requests"] if q_row else 0

        print(
            f"Workers: {num_workers:2d} | Duration: {duration:6.2f}s | "
            f"Throughput: {throughput:5.1f} runs/s | Completed: {completed}/{num_runs} | "
            f"Quota Mutated: {consumed_reqs}"
        )

        assert completed == num_runs, f"VIOLATION: Expected {num_runs} completed, got {completed}"
        assert stats["errors"] == 0, (
            f"VIOLATION: Encountered {stats['errors']} permanent serialization failures!"
        )

        benchmark_results.append(
            {
                "num_workers": num_workers,
                "duration_sec": duration,
                "throughput_runs_per_sec": throughput,
                "completed_runs": completed,
                "quota_mutated": consumed_reqs,
            }
        )

    # Find performance knee (peak throughput configuration)
    peak_config = max(benchmark_results, key=lambda x: x["throughput_runs_per_sec"])
    print("-------------------------------------------------------")
    print(
        f"Peak: {peak_config['throughput_runs_per_sec']:.1f} runs/s "
        f"at {peak_config['num_workers']} workers"
    )
    print("Guarantees: 100% Terminal Resolution, 100% Retry Success, 0 Stranded Runs")
    print("=======================================================\n")

    return {
        "results": benchmark_results,
        "peak_throughput": peak_config,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Relay Inverted Contention Benchmark")
    parser.add_argument("--runs", type=int, default=20, help="Number of runs to contend for")
    parser.add_argument(
        "--workers", nargs="+", type=int, default=[2, 4, 8], help="Worker counts to test"
    )
    args = parser.parse_args()

    try:
        run_contention_benchmark(num_runs=args.runs, worker_counts=args.workers)
        print("\n[SUCCESS] Inverted Contention Benchmark Passed: 100% Terminal State Resolution!")
    finally:
        close_connection_pool()


if __name__ == "__main__":
    main()
