"""Relay Worker process claiming and executing runs via Postgres coordination."""

from __future__ import annotations

import logging
import time
import uuid
from collections.abc import Callable
from typing import Any

from relay.db import get_db_connection
from relay.models import RunContext
from relay.runtime import ExecutionRuntime, FencingTokenError

logger = logging.getLogger("relay.worker")


class Worker:
    def __init__(
        self,
        worker_id: str | None = None,
        runtime: ExecutionRuntime | None = None,
        poll_interval: float = 0.5,
        lease_seconds: int = 30,
    ) -> None:
        self.worker_id = worker_id or f"worker-{uuid.uuid4().hex[:8]}"
        self.runtime = runtime or ExecutionRuntime()
        self.poll_interval = poll_interval
        self.lease_seconds = lease_seconds
        self._running = False

    def process_one_run(
        self,
        workflow_handler: Callable[[RunContext, ExecutionRuntime], dict[str, Any]] | None = None,
    ) -> bool:
        """Attempt to claim and process exactly one run.

        Returns True if a run was claimed and processed, False if queue was empty.
        """
        run_ctx = self.runtime.claim_run(
            worker_id=self.worker_id,
            lease_duration_seconds=self.lease_seconds,
        )
        if not run_ctx:
            return False

        logger.info(f"[{self.worker_id}] Claimed run {run_ctx.id} with epoch {run_ctx.claim_epoch}")

        try:
            handler = workflow_handler
            if handler is None:
                # If structured ticket payload is provided, execute full Clearance + Ledgerline + Act workflow
                payload = run_ctx.input_payload or {}
                if any(k in payload for k in ("subject", "body", "invoice_id", "customer_email")):
                    from relay.agent import full_support_workflow

                    def default_agent_workflow(ctx: RunContext, rt: ExecutionRuntime):
                        return full_support_workflow(ctx, rt, worker_id=self.worker_id)

                    handler = default_agent_workflow

            if handler is not None:
                output = handler(run_ctx, self.runtime)
                # Check if workflow suspended for approval
                with get_db_connection() as conn:
                    with conn.cursor() as cur:
                        cur.execute("SELECT status FROM runs WHERE id = %s", (run_ctx.id,))
                        status_row = cur.fetchone()
                        if status_row and status_row["status"] == "waiting_approval":
                            logger.info(
                                f"[{self.worker_id}] Run {run_ctx.id} entered zero-cost suspension"
                            )
                            return True
                self.runtime.complete_run(run_ctx.id, run_ctx.claim_epoch, output)
            else:
                # Default minimal workflow for basic synthetic test runs
                self.runtime.start_step(run_ctx.id, run_ctx.claim_epoch, 1, "default_triage")
                self.runtime.complete_step(run_ctx.id, run_ctx.claim_epoch, 1)
                self.runtime.complete_run(
                    run_ctx.id,
                    run_ctx.claim_epoch,
                    {"status": "success", "processed_by": self.worker_id},
                )
            logger.info(f"[{self.worker_id}] Successfully finished run {run_ctx.id}")
        except FencingTokenError as e:
            logger.warning(
                f"[{self.worker_id}] Preempted by fencing token on run {run_ctx.id}: {e}"
            )
            # Stale worker terminates processing this run without writing
        except Exception as e:
            logger.exception(f"[{self.worker_id}] Execution failed for run {run_ctx.id}: {e}")
            try:
                self.runtime.fail_run(run_ctx.id, run_ctx.claim_epoch, str(e))
            except FencingTokenError:
                pass
        return True

    def run_loop(self, max_runs: int | None = None) -> None:
        """Continuously poll and process runs."""
        self._running = True
        processed = 0
        while self._running:
            did_work = self.process_one_run()
            if did_work:
                processed += 1
                if max_runs and processed >= max_runs:
                    break
            else:
                time.sleep(self.poll_interval)

    def stop(self) -> None:
        self._running = False


def run_worker() -> None:
    logging.basicConfig(level=logging.INFO)
    worker = Worker()
    logger.info(f"Starting Relay worker: {worker.worker_id}")
    try:
        worker.run_loop()
    except KeyboardInterrupt:
        logger.info("Worker stopped by user")
        worker.stop()


if __name__ == "__main__":
    run_worker()
