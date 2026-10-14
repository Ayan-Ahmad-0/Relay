"""Relay Ingestion and Webhook / Approval Callback API.

Provides endpoints to submit support tickets, wake suspended runs upon human approval,
and perform background sweeps consuming zero CPU during waits.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any
from uuid import UUID

from fastapi import FastAPI, HTTPException
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from relay.db import get_db_connection
from relay.runtime import ExecutionRuntime

app = FastAPI(title="Relay Ingestion & Human-in-the-Loop Callback API", version="1.0.0")
runtime = ExecutionRuntime()

web_dir = Path(__file__).resolve().parent.parent / "web"
if web_dir.exists():
    app.mount("/demo", StaticFiles(directory=str(web_dir), html=True), name="demo")


class TicketIngestionRequest(BaseModel):
    ticket_id: int | str
    customer_email: str
    subject: str
    body: str
    invoice_id: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)


class ApprovalDecisionRequest(BaseModel):
    approved: bool
    reason: str = Field(default="", max_length=500)


@app.post("/tickets", status_code=201)
def ingest_ticket(req: TicketIngestionRequest) -> dict[str, Any]:
    """Ingest new support ticket into Relay queue."""
    payload = req.model_dump()
    run_id = runtime.create_run(payload)
    return {"status": "enqueued", "run_id": str(run_id)}


@app.get("/runs/{run_id}")
def get_run_status(run_id: UUID) -> dict[str, Any]:
    """Inspect status of a run."""
    with get_db_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT id, status, claim_epoch, worker_id, lease_expires_at,
                       input_payload, output_payload, error, created_at, updated_at
                FROM runs
                WHERE id = %s;
                """,
                (run_id,),
            )
            row = cur.fetchone()
            if not row:
                raise HTTPException(status_code=404, detail="Run not found")
            return {
                "id": str(row["id"]),
                "status": row["status"],
                "claim_epoch": row["claim_epoch"],
                "worker_id": row["worker_id"],
                "created_at": str(row["created_at"]),
                "updated_at": str(row["updated_at"]),
            }


@app.post("/approvals/{token}/decision")
def submit_approval_decision(token: str, req: ApprovalDecisionRequest) -> dict[str, Any]:
    """Wake suspended run upon human manager approval callback."""
    try:
        new_status = runtime.resolve_approval(token, approved=req.approved, reason=req.reason)
        return {
            "status": "resolved",
            "decision": "approved" if req.approved else "rejected",
            "run_status": new_status.value,
        }
    except ValueError as err:
        raise HTTPException(status_code=400, detail=str(err)) from err


@app.post("/sweep")
def trigger_background_sweep() -> dict[str, Any]:
    """Background sweep identifying expired leases or expired approval gates."""
    with get_db_connection() as conn:
        with conn.transaction():
            with conn.cursor() as cur:
                # Mark expired pending approvals as rejected
                cur.execute(
                    """
                    UPDATE approvals
                    SET status = 'rejected',
                        decision_reason = 'Approval expired before human action',
                        decided_at = clock_timestamp()
                    WHERE status = 'pending' AND expires_at < clock_timestamp()
                    RETURNING id, run_id;
                    """
                )
                expired_approvals = cur.fetchall()
                for row in expired_approvals:
                    cur.execute(
                        """
                        UPDATE runs
                        SET status = 'failed', updated_at = clock_timestamp()
                        WHERE id = %s;
                        """,
                        (row["run_id"],),
                    )

                return {
                    "sweep_completed": True,
                    "expired_approvals_count": len(expired_approvals),
                }
