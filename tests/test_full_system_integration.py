"""Comprehensive End-to-End System Integration Tests.

Validates the three-project integration:
1. Clearance (Project 1) via retrieve tool: fetches policy chunks using scoped principal.
2. Ledgerline (Project 2) via extract tool: extracts invoice data and audits math.
3. Host Helpdesk via act tool: stamps replies and tags through external deduplicating sink.
"""

import pytest
from fastapi.testclient import TestClient

from relay.db import get_db_connection
from relay.models import RunStatus
from relay.runtime import ExecutionRuntime
from relay.security import SecurityPolicyViolation
from relay.sink import app as sink_app
from relay.tools.retrieve import ClearanceClient
from relay.worker import Worker


def test_three_project_end_to_end_integration():
    """Verify Relay calls Clearance, Ledgerline, and Host Act stamped through Sink."""
    sink_client = TestClient(sink_app)
    sink_client.post("/sink/clear")

    runtime = ExecutionRuntime(sink_url="http://testserver/sink", sink_client=sink_client)

    # Ingest a billing dispute ticket with invoice attachment
    ticket_payload = {
        "ticket_id": "CW-9001",
        "customer_email": "customer@example.com",
        "subject": "Overcharged on invoice INV-2026-001",
        "body": "I was overcharged on my recent invoice according to your return policy.",
        "invoice_id": "2026-001",
        "claimed_amount": 165.0,  # <= $250 ceiling: passes policy gate autonomously
    }

    run_id = runtime.create_run(ticket_payload)

    worker = Worker(worker_id="integration-worker-1", runtime=runtime)
    processed = worker.process_one_run()
    assert processed is True

    # 1. Verify Run Completed Successfully
    with get_db_connection() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT status, output_payload, error FROM runs WHERE id = %s", (run_id,))
            row = cur.fetchone()
            assert row["status"] == RunStatus.COMPLETED
            assert row["error"] is None
            output = row["output_payload"]
            assert output["status"] == "success"
            assert output["ticket_id"] == "CW-9001"
            assert output["clearance_policy"] == "return_policy"
            assert output["ledgerline_audit"] is not None
            assert output["ledgerline_audit"]["claim_verified"] is True

    # 2. Verify External Sink was stamped with exactly 1 unique effect
    stats = sink_client.get("/sink/stats").json()
    assert stats["total_received"] >= 1
    assert stats["duplicate_count"] == 0

    records = sink_client.get("/sink/records").json()
    stamped_keys = [r["idempotency_key"] for r in records]
    assert any(f"act-reply-{run_id}" in k for k in stamped_keys)


def test_clearance_restricted_boundary_blocked():
    """Verify scoped service principal cannot retrieve confidential records."""
    client = ClearanceClient()
    with pytest.raises(SecurityPolicyViolation) as exc_info:
        client.retrieve(query="What is the executive severance package?")
    assert "Scoped service principal lacks entitlements" in str(exc_info.value)


def test_large_refund_triggers_suspension_gate():
    """Verify refund > $250 suspends execution cleanly for human manager sign-off."""
    runtime = ExecutionRuntime()

    large_ticket = {
        "ticket_id": "CW-9002",
        "customer_email": "customer@example.com",
        "subject": "Damaged server rack invoice",
        "body": "Invoice dispute for damaged shipment.",
        "invoice_id": "INV-SERVER-01",
        "claimed_amount": 1200.0,  # Exceeds $250: requires approval
    }

    run_id = runtime.create_run(large_ticket)

    worker = Worker(worker_id="suspension-worker-1", runtime=runtime)
    processed = worker.process_one_run()
    assert processed is True

    # Verify run entered waiting_approval state and lease was released
    with get_db_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT status, worker_id, lease_expires_at FROM runs WHERE id = %s", (run_id,)
            )
            row = cur.fetchone()
            assert row["status"] == RunStatus.WAITING_APPROVAL
            assert row["worker_id"] is None
            assert row["lease_expires_at"] is None

            cur.execute("SELECT token, status FROM approvals WHERE run_id = %s", (run_id,))
            approval = cur.fetchone()
            assert approval is not None
            assert approval["status"] == "pending"
