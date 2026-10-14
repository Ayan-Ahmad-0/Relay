"""Core Execution Runtime for Relay.

Coordinates execution steps, database fencing tokens (claim_epoch),
event sourcing log, effect application via external sink, and zero-cost suspension.
"""

from __future__ import annotations

import json
import secrets
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID

import httpx

from relay.db import transaction
from relay.models import ApprovalRecord, EffectRecord, RunContext, RunStatus
from relay.retry import retry_on_concurrency_conflict


class FencingTokenError(Exception):
    """Raised when a zombie/stale worker detects a claim_epoch mismatch."""

    pass


class StepExecutionError(Exception):
    """Raised when an unrecoverable step execution error occurs."""

    pass


class ExecutionRuntime:
    def __init__(self, sink_url: str = "http://localhost:8001/sink") -> None:
        self.sink_url = sink_url

    def create_run(self, input_payload: dict[str, Any]) -> UUID:
        """Create a new queued run."""
        with transaction() as cur:
            cur.execute(
                """
                INSERT INTO runs (status, claim_epoch, input_payload, created_at, updated_at)
                VALUES ('queued', 0, %s, clock_timestamp(), clock_timestamp())
                RETURNING id;
                """,
                (json.dumps(input_payload),),
            )
            row = cur.fetchone()
            assert row is not None
            return row["id"]

    def claim_run(self, worker_id: str, lease_duration_seconds: int = 30) -> RunContext | None:
        """Atomically claim the oldest queued or expired run using SELECT FOR UPDATE SKIP LOCKED.

        Increments claim_epoch fencing token to invalidate any stale workers.
        """
        with transaction() as cur:
            cur.execute(
                """
                WITH candidate AS (
                    SELECT id FROM runs
                    WHERE status = 'queued'
                       OR (status = 'running' AND lease_expires_at < clock_timestamp())
                    ORDER BY created_at ASC
                    LIMIT 1
                    FOR UPDATE SKIP LOCKED
                )
                UPDATE runs
                SET status = 'running',
                    worker_id = %s,
                    claim_epoch = claim_epoch + 1,
                    lease_expires_at = clock_timestamp() + (%s || ' seconds')::interval,
                    updated_at = clock_timestamp()
                FROM candidate
                WHERE runs.id = candidate.id
                RETURNING runs.id, runs.status, runs.claim_epoch, runs.worker_id,
                          runs.input_payload, runs.output_payload, runs.created_at, runs.updated_at;
                """,
                (worker_id, lease_duration_seconds),
            )
            row = cur.fetchone()
            if not row:
                return None

            raw_payload = row["input_payload"]
            payload = raw_payload if isinstance(raw_payload, dict) else json.loads(raw_payload)

            return RunContext(
                id=row["id"],
                status=RunStatus(row["status"]),
                claim_epoch=row["claim_epoch"],
                worker_id=row["worker_id"],
                input_payload=payload,
                output_payload=row["output_payload"],
                created_at=row["created_at"],
                updated_at=row["updated_at"],
            )

    def _verify_fencing_token(self, cur: Any, run_id: UUID, expected_epoch: int) -> None:
        """Check held claim_epoch against current database state.

        If a zombie worker attempts to write after another worker acquired the lease,
        raises FencingTokenError immediately.
        """
        cur.execute(
            """
            SELECT claim_epoch, status FROM runs
            WHERE id = %s
            FOR UPDATE;
            """,
            (run_id,),
        )
        row = cur.fetchone()
        if not row:
            raise FencingTokenError(f"Run {run_id} no longer exists")
        if row["claim_epoch"] != expected_epoch:
            raise FencingTokenError(
                f"Zombie worker detected for run {run_id}: held epoch {expected_epoch} "
                f"!= active epoch {row['claim_epoch']}"
            )

    def start_step(
        self, run_id: UUID, claim_epoch: int, step_sequence: int, step_name: str
    ) -> None:
        """Start a step execution conditional on fencing token."""
        with transaction() as cur:
            self._verify_fencing_token(cur, run_id, claim_epoch)
            cur.execute(
                """
                INSERT INTO step_executions (run_id, step_sequence, step_name, status, started_at)
                VALUES (%s, %s, %s, 'running', clock_timestamp())
                ON CONFLICT (run_id, step_sequence) DO UPDATE
                SET status = 'running', step_name = EXCLUDED.step_name;
                """,
                (run_id, step_sequence, step_name),
            )

    def complete_step(self, run_id: UUID, claim_epoch: int, step_sequence: int) -> None:
        """Complete a step execution conditional on fencing token."""
        with transaction() as cur:
            self._verify_fencing_token(cur, run_id, claim_epoch)
            cur.execute(
                """
                UPDATE step_executions
                SET status = 'completed', completed_at = clock_timestamp()
                WHERE run_id = %s AND step_sequence = %s;
                """,
                (run_id, step_sequence),
            )

    def record_event(
        self,
        run_id: UUID,
        claim_epoch: int,
        sequence_num: int,
        event_type: str,
        payload: dict[str, Any],
    ) -> None:
        """Record an event into the deterministic event log conditional on fencing token."""
        with transaction() as cur:
            self._verify_fencing_token(cur, run_id, claim_epoch)
            cur.execute(
                """
                INSERT INTO run_events (run_id, sequence_num, event_type, payload, recorded_at)
                VALUES (%s, %s, %s, %s, clock_timestamp())
                ON CONFLICT (run_id, sequence_num) DO NOTHING;
                """,
                (run_id, sequence_num, event_type, json.dumps(payload)),
            )

    def apply_effect(
        self,
        run_id: UUID,
        claim_epoch: int,
        step_sequence: int,
        tool_name: str,
        idempotency_key: str,
        payload: dict[str, Any],
        client: httpx.Client | None = None,
    ) -> EffectRecord:
        """Apply an outbound effect through the external sink with strict idempotency and fencing.

        1. Checks database for already applied effect under idempotency_key.
        2. Records effect as 'pending' in database conditional on claim_epoch.
        3. Calls external sink.
        4. Updates effect as 'applied' conditional on claim_epoch.
        """
        # Step 1 & 2: Check or insert pending
        with transaction() as cur:
            self._verify_fencing_token(cur, run_id, claim_epoch)
            cur.execute(
                """
                SELECT id, run_id, step_sequence, tool_name, idempotency_key,
                       status, payload, response, applied_at
                FROM effects
                WHERE idempotency_key = %s;
                """,
                (idempotency_key,),
            )
            existing = cur.fetchone()
            if existing and existing["status"] == "applied":
                return EffectRecord(
                    id=existing["id"],
                    run_id=existing["run_id"],
                    step_sequence=existing["step_sequence"],
                    tool_name=existing["tool_name"],
                    idempotency_key=existing["idempotency_key"],
                    status=existing["status"],
                    payload=existing["payload"],
                    response=existing["response"],
                    applied_at=existing["applied_at"],
                )

            if not existing:
                cur.execute(
                    """
                    INSERT INTO effects (
                        run_id, step_sequence, tool_name,
                        idempotency_key, status, payload, created_at
                    )
                    VALUES (%s, %s, %s, %s, 'pending', %s, clock_timestamp())
                    RETURNING id;
                    """,
                    (run_id, step_sequence, tool_name, idempotency_key, json.dumps(payload)),
                )
                cur.fetchone()

        # Step 2.5: If effect was pending, check whether the external sink already logged it
        sink_response = None
        if existing and existing["status"] == "pending":
            records_url = self.sink_url.rstrip("/") + f"/records?idempotency_key={idempotency_key}"
            try:
                if client is not None:
                    chk = client.get(records_url, timeout=5.0)
                else:
                    with httpx.Client() as default_client:
                        chk = default_client.get(records_url, timeout=5.0)
                if chk.status_code == 200:
                    matched_records = chk.json()
                    if matched_records:
                        sink_response = matched_records[0]
            except Exception:
                pass

        # Step 3: Outbound call to external sink if not already applied
        if sink_response is None:
            sink_payload = {
                "idempotency_key": idempotency_key,
                "tool_name": tool_name,
                "payload": payload,
                "metadata": {"run_id": str(run_id), "step_sequence": step_sequence},
            }

            if client is not None:
                res = client.post(self.sink_url, json=sink_payload, timeout=10.0)
                res.raise_for_status()
                sink_response = res.json()
            else:
                with httpx.Client() as default_client:
                    res = default_client.post(self.sink_url, json=sink_payload, timeout=10.0)
                    res.raise_for_status()
                    sink_response = res.json()

        # Step 4: Mark applied conditional on fencing token
        with transaction() as cur:
            self._verify_fencing_token(cur, run_id, claim_epoch)
            cur.execute(
                """
                UPDATE effects
                SET status = 'applied',
                    response = %s,
                    applied_at = clock_timestamp()
                WHERE idempotency_key = %s
                RETURNING id, run_id, step_sequence, tool_name, idempotency_key,
                          status, payload, response, applied_at;
                """,
                (json.dumps(sink_response), idempotency_key),
            )
            applied_row = cur.fetchone()
            assert applied_row is not None
            return EffectRecord(
                id=applied_row["id"],
                run_id=applied_row["run_id"],
                step_sequence=applied_row["step_sequence"],
                tool_name=applied_row["tool_name"],
                idempotency_key=applied_row["idempotency_key"],
                status=applied_row["status"],
                payload=applied_row["payload"],
                response=applied_row["response"],
                applied_at=applied_row["applied_at"],
            )

    def suspend_for_approval(
        self,
        run_id: UUID,
        claim_epoch: int,
        step_sequence: int,
        action_type: str,
        proposed_payload: dict[str, Any],
        expires_in_seconds: int = 3600,
    ) -> ApprovalRecord:
        """Suspend the run for human approval with Zero Cost.

        Releases the worker lease completely and sets status = 'waiting_approval'.
        """
        token = secrets.token_urlsafe(32)
        expires_at = datetime.now(UTC) + timedelta(seconds=expires_in_seconds)

        with transaction() as cur:
            self._verify_fencing_token(cur, run_id, claim_epoch)
            cur.execute(
                """
                INSERT INTO approvals (
                    run_id, step_sequence, token, action_type,
                    proposed_payload, status, expires_at, created_at
                )
                VALUES (%s, %s, %s, %s, %s, 'pending', %s, clock_timestamp())
                RETURNING id, run_id, step_sequence, token, action_type,
                          proposed_payload, status, expires_at, created_at;
                """,
                (
                    run_id,
                    step_sequence,
                    token,
                    action_type,
                    json.dumps(proposed_payload),
                    expires_at,
                ),
            )
            app_row = cur.fetchone()
            assert app_row is not None

            # Zero-cost suspension: clear worker lease, pause run
            cur.execute(
                """
                UPDATE runs
                SET status = 'waiting_approval',
                    worker_id = NULL,
                    lease_expires_at = NULL,
                    updated_at = clock_timestamp()
                WHERE id = %s AND claim_epoch = %s;
                """,
                (run_id, claim_epoch),
            )

            return ApprovalRecord(
                id=app_row["id"],
                run_id=app_row["run_id"],
                step_sequence=app_row["step_sequence"],
                token=app_row["token"],
                action_type=app_row["action_type"],
                proposed_payload=app_row["proposed_payload"],
                status=app_row["status"],
                expires_at=app_row["expires_at"],
                created_at=app_row["created_at"],
            )

    @retry_on_concurrency_conflict(max_retries=5)
    def resolve_approval(self, token: str, approved: bool, reason: str = "") -> RunStatus:
        """Resolve human approval gate and re-enqueue run if approved."""
        with transaction(isolation_level="SERIALIZABLE") as cur:
            cur.execute(
                """
                SELECT id, run_id, status FROM approvals
                WHERE token = %s
                FOR UPDATE;
                """,
                (token,),
            )
            row = cur.fetchone()
            if not row:
                raise ValueError("Invalid approval token")
            if row["status"] != "pending":
                raise ValueError(f"Approval is already resolved with status '{row['status']}'")

            new_status = "approved" if approved else "rejected"
            run_status = "queued" if approved else "failed"

            cur.execute(
                """
                UPDATE approvals
                SET status = %s,
                    decision_reason = %s,
                    decided_at = clock_timestamp()
                WHERE token = %s;
                """,
                (new_status, reason, token),
            )

            cur.execute(
                """
                UPDATE runs
                SET status = %s,
                    updated_at = clock_timestamp()
                WHERE id = %s;
                """,
                (run_status, row["run_id"]),
            )

            return RunStatus(run_status)

    def complete_run(self, run_id: UUID, claim_epoch: int, output_payload: dict[str, Any]) -> None:
        """Mark run completed conditional on fencing token."""
        with transaction() as cur:
            self._verify_fencing_token(cur, run_id, claim_epoch)
            cur.execute(
                """
                UPDATE runs
                SET status = 'completed',
                    output_payload = %s,
                    lease_expires_at = NULL,
                    updated_at = clock_timestamp()
                WHERE id = %s AND claim_epoch = %s;
                """,
                (json.dumps(output_payload), run_id, claim_epoch),
            )

    def fail_run(self, run_id: UUID, claim_epoch: int, error_message: str) -> None:
        """Mark run failed conditional on fencing token."""
        with transaction() as cur:
            self._verify_fencing_token(cur, run_id, claim_epoch)
            cur.execute(
                """
                UPDATE runs
                SET status = 'failed',
                    error = %s,
                    lease_expires_at = NULL,
                    updated_at = clock_timestamp()
                WHERE id = %s AND claim_epoch = %s;
                """,
                (error_message, run_id, claim_epoch),
            )
