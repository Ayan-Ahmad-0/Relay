"""Unit and integration tests for Relay tools (Clearance, Ledgerline, Act, and Runner)."""

import time

import pytest
from fastapi.testclient import TestClient

from relay.runtime import ExecutionRuntime
from relay.security import (
    ReplyActionContract,
    SecurityPolicyViolation,
)
from relay.sink import app as sink_app
from relay.tools.act import HostHelpdeskClient
from relay.tools.extract import ExtractedInvoice, InvoiceLineItem, LedgerlineClient
from relay.tools.retrieve import ClearanceClient
from relay.tools.runner import ToolTimeoutError, run_isolated_tool


def _sample_add(a: int, b: int) -> int:
    return a + b


def _sample_hang() -> str:
    time.sleep(5.0)
    return "never"


@pytest.fixture
def sink_client():
    c = TestClient(sink_app)
    c.post("/sink/clear")
    yield c
    c.post("/sink/clear")


def test_clearance_tool_public_and_restricted():
    client = ClearanceClient()

    # Public return policy -> retrieved successfully (via cached fallback)
    res = client.retrieve(query="What is the customer return window?")
    assert res["document"] == "return_policy"
    assert any("30 days" in chunk for chunk in res["chunks"])

    # Restricted document -> blocked by service principal entitlements
    with pytest.raises(SecurityPolicyViolation) as exc_info:
        client.retrieve(query="Fetch executive severance and payroll records")
    assert "Scoped service principal lacks entitlements" in str(exc_info.value)


def test_ledgerline_tool_math_audit():
    # Test arithmetic verification
    invoice = ExtractedInvoice(
        invoice_number="INV-999",
        vendor="Acme Cloud",
        line_items=[
            InvoiceLineItem(description="Item A", quantity=2, unit_price=40.0, total=80.0),
            InvoiceLineItem(description="Item B", quantity=1, unit_price=20.0, total=20.0),
        ],
        subtotal=100.0,
        tax=10.0,
        total_amount=110.0,
    )
    result = LedgerlineClient.verify_invoice(invoice, claimed_amount=110.0)
    assert result.is_mathematically_sound is True
    assert result.calculated_total == 110.0
    assert result.claim_verified is True


def test_act_tool_policy_gate_and_sink(sink_client):
    runtime = ExecutionRuntime(sink_url="http://testserver/sink")
    host_client = HostHelpdeskClient(runtime=runtime)

    runtime.create_run({"ticket": "act-test"})
    ctx = runtime.claim_run(worker_id="worker-act")
    assert ctx is not None

    # Safe refund within policy ceiling ($50.00 <= $250.00)
    safe_contract = ReplyActionContract(
        ticket_id=123,
        recipient_email="user@example.com",
        reply_text="Your $50 refund is issued.",
        proposed_refund=50.0,
    )
    effect = host_client.act_reply(
        run_id=ctx.id,
        claim_epoch=ctx.claim_epoch,
        step_sequence=1,
        contract=safe_contract,
        client=sink_client,
    )
    assert effect.status == "applied"

    # Verify sink received request
    stats = sink_client.get("/sink/stats").json()
    assert stats["total_received"] == 1

    # Unauthorized high refund ($500.00 > $250.00 ceiling) -> blocked before sink
    with pytest.raises(SecurityPolicyViolation) as exc_info:
        ReplyActionContract(
            ticket_id=124,
            recipient_email="user@example.com",
            reply_text="Unauthorized $500 payout",
            proposed_refund=500.0,
        )
    assert "exceeds autonomous limit" in str(exc_info.value)


def test_isolated_subprocess_runner():
    # Normal tool in isolated process
    val = run_isolated_tool(_sample_add, 10, 20, timeout_seconds=2.0)
    assert val == 30

    # Hanging tool times out and gets killed
    with pytest.raises(ToolTimeoutError) as exc_info:
        run_isolated_tool(_sample_hang, timeout_seconds=0.5)
    assert "timed out" in str(exc_info.value)
