"""Autonomous Customer Support Agent Workflow.

Seamlessly integrates:
1. Clearance (Project 1) via retrieve tool: fetches policy chunks using scoped principal.
2. Ledgerline (Project 2) via extract tool: extracts invoice data and verifies math.
3. Host Helpdesk via act tool: stamps replies and tags through external deduplicating sink.
4. Policy & Approval Gate: suspends execution at zero cost for payouts > $250.
"""

from __future__ import annotations

import logging
from typing import Any

from relay.llm import GeminiClient
from relay.models import EventType, RunContext
from relay.runtime import ExecutionRuntime
from relay.security import ReplyActionContract
from relay.tools.act import HostHelpdeskClient
from relay.tools.extract import LedgerlineClient
from relay.tools.retrieve import ClearanceClient

logger = logging.getLogger("relay.agent")


def full_support_workflow(
    run_ctx: RunContext,
    runtime: ExecutionRuntime,
    clearance_client: ClearanceClient | None = None,
    ledgerline_client: LedgerlineClient | None = None,
    act_client: HostHelpdeskClient | None = None,
    gemini_client: GeminiClient | None = None,
    worker_id: str | None = None,
) -> dict[str, Any]:
    """Execute complete support workflow connecting Projects 1, 2, and 3."""
    payload = run_ctx.input_payload or {}
    ticket_id = payload.get("ticket_id", "T-001")
    customer_email = payload.get("customer_email", "customer@example.com")
    subject = payload.get("subject", "Customer Inquiry")
    body = payload.get("body", "")
    invoice_id = payload.get("invoice_id")
    claimed_amount = payload.get("claimed_amount") or payload.get("amount")

    clearance = clearance_client or ClearanceClient()
    ledgerline = ledgerline_client or LedgerlineClient()
    act = act_client or HostHelpdeskClient(runtime=runtime)
    worker_label = worker_id or "relay-worker"

    # Step 1: Clearance Policy Retrieval (Project 1)
    runtime.start_step(run_ctx.id, run_ctx.claim_epoch, 1, "clearance_retrieve")
    query = f"{subject} {body}".strip() or "Standard Customer Policy"
    policy_result = clearance.retrieve(query=query)
    runtime.record_event(
        run_ctx.id,
        run_ctx.claim_epoch,
        1,
        EventType.TOOL_RESULT.value,
        {"policy": policy_result},
    )
    runtime.complete_step(run_ctx.id, run_ctx.claim_epoch, 1)

    step_seq = 2
    invoice_audit = None

    # Step 2: Ledgerline Invoice Extraction & Math Audit (Project 2)
    if invoice_id or "invoice" in body.lower() or "charged" in body.lower():
        inv_id = str(invoice_id or "default")
        runtime.start_step(run_ctx.id, run_ctx.claim_epoch, step_seq, "ledgerline_extract")
        extracted_invoice = ledgerline.extract(invoice_id=inv_id)
        numeric_claim = float(claimed_amount) if claimed_amount is not None else None
        invoice_audit = ledgerline.verify_invoice(extracted_invoice, claimed_amount=numeric_claim)
        runtime.record_event(
            run_ctx.id,
            run_ctx.claim_epoch,
            step_seq,
            EventType.TOOL_RESULT.value,
            {"invoice": extracted_invoice.model_dump(), "audit": invoice_audit.model_dump()},
        )
        runtime.complete_step(run_ctx.id, run_ctx.claim_epoch, step_seq)
        step_seq += 1

    # Step 3: Zero-Cost Suspension Policy Gate
    proposed_refund = float(claimed_amount) if claimed_amount is not None else 0.0
    if proposed_refund > 250.0:
        runtime.suspend_for_approval(
            run_ctx.id,
            run_ctx.claim_epoch,
            step_seq,
            action_type="approve_large_refund",
            proposed_payload={
                "ticket_id": ticket_id,
                "customer_email": customer_email,
                "amount": proposed_refund,
            },
        )
        logger.info(
            f"Run {run_ctx.id} suspended for manager approval (Refund: ${proposed_refund:.2f})"
        )
        return {
            "status": "suspended",
            "reason": f"Refund of ${proposed_refund:.2f} requires human approval",
            "processed_by": worker_label,
        }

    # Step 4: Host Helpdesk Action & Sink Stamp (Project 3)
    runtime.start_step(run_ctx.id, run_ctx.claim_epoch, step_seq, "host_act")

    # Attempt generative reply drafting via Gemini if configured
    gemini = gemini_client or GeminiClient()
    llm_draft = None
    if gemini.is_available():
        logger.info(
            f"Generating AI support reply using Gemini ({gemini.model}) for ticket {ticket_id}..."
        )
        llm_draft = gemini.generate_support_reply(
            subject=subject,
            body=body,
            policy_context=policy_result,
            invoice_context=invoice_audit.model_dump() if invoice_audit else None,
            run_id=run_ctx.id,
            step_sequence=step_seq,
        )

    if llm_draft and llm_draft.get("text"):
        reply_content = llm_draft["text"]
        runtime.record_event(
            run_ctx.id,
            run_ctx.claim_epoch,
            step_seq,
            EventType.MODEL_OUTPUT.value,
            {
                "model": llm_draft.get("model"),
                "total_tokens": llm_draft.get("total_tokens"),
                "draft": reply_content,
            },
        )
        logger.info(f"AI reply generated successfully ({llm_draft.get('total_tokens', 0)} tokens)")
    else:
        # Deterministic template fallback
        reply_content = f"Thank you for contacting support regarding '{subject}'."
        if invoice_audit and invoice_audit.claim_verified:
            reply_content += f" We verified invoice INV-{invoice_id or 'default'}: {invoice_audit.discrepancy_explanation}."
            if proposed_refund > 0:
                reply_content += f" A refund of ${proposed_refund:.2f} has been processed."
        else:
            reply_content += f" According to policy '{policy_result.get('title')}', your request has been reviewed."

    contract = ReplyActionContract(
        ticket_id=ticket_id,
        recipient_email=customer_email,
        reply_text=reply_content,
        proposed_refund=proposed_refund,
    )

    effect_record = act.act_reply(
        run_id=run_ctx.id,
        claim_epoch=run_ctx.claim_epoch,
        step_sequence=step_seq,
        contract=contract,
    )
    runtime.complete_step(run_ctx.id, run_ctx.claim_epoch, step_seq)

    return {
        "status": "success",
        "ticket_id": ticket_id,
        "ai_generated": bool(llm_draft and llm_draft.get("text")),
        "clearance_policy": policy_result.get("document"),
        "ledgerline_audit": invoice_audit.model_dump() if invoice_audit else None,
        "effect_idempotency_key": effect_record.idempotency_key,
        "processed_by": worker_label,
    }
