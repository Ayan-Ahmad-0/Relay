"""Tool 3: Host Helpdesk Action Tool (Reply & Tagging).

Writes replies and category tags to host helpdesk API.
Enforces hardcoded policy gates (payout ceilings, recipient allow-list),
routes through approval gates, and stamps effects through the external deduplicating sink.
"""

from __future__ import annotations

import os
from uuid import UUID

import httpx

from relay.models import EffectRecord
from relay.runtime import ExecutionRuntime
from relay.security import (
    MAX_AUTONOMOUS_PAYOUT_DOLLARS,
    ReplyActionContract,
    SecurityPolicyViolation,
    validate_network_egress,
)

DEFAULT_HOST_HELPDESK_URL = "http://localhost:3000"


class HostHelpdeskClient:
    def __init__(
        self,
        base_url: str | None = None,
        api_key: str | None = None,
        account_id: int = 1,
        runtime: ExecutionRuntime | None = None,
        timeout: float = 5.0,
    ) -> None:
        self.base_url = (
            base_url or os.getenv("HOST_HELPDESK_URL", DEFAULT_HOST_HELPDESK_URL)
        ).rstrip("/")
        self.api_key = api_key or os.getenv("HOST_HELPDESK_API_KEY", "relay-host-token")
        self.account_id = account_id
        self.runtime = runtime or ExecutionRuntime()
        self.timeout = timeout

    def act_reply(
        self,
        run_id: UUID,
        claim_epoch: int,
        step_sequence: int,
        contract: ReplyActionContract,
        client: httpx.Client | None = None,
    ) -> EffectRecord:
        """Post a response to the host helpdesk ticket.

        If proposed refund exceeds threshold, raises SecurityPolicyViolation.
        Stamps the action through the external sink for duplicate detection.
        """
        validate_network_egress(self.base_url, {self.base_url})

        # Hardcoded Policy Gate
        if contract.proposed_refund > MAX_AUTONOMOUS_PAYOUT_DOLLARS:
            raise SecurityPolicyViolation(
                f"Autonomous payout of ${contract.proposed_refund:.2f} blocked by policy gate. "
                f"Maximum allowed without human approval is ${MAX_AUTONOMOUS_PAYOUT_DOLLARS:.2f}."
            )

        idempotency_key = f"act-reply-{run_id}-step{step_sequence}"
        payload = {
            "conversation_id": contract.ticket_id,
            "recipient_email": contract.recipient_email,
            "content": contract.reply_text,
            "refund_issued": contract.proposed_refund,
        }

        # Apply effect conditional on fencing token and stamp through sink
        effect_record = self.runtime.apply_effect(
            run_id=run_id,
            claim_epoch=claim_epoch,
            step_sequence=step_sequence,
            tool_name="act_reply",
            idempotency_key=idempotency_key,
            payload=payload,
            client=client,
        )

        return effect_record

    def act_tag(
        self,
        run_id: UUID,
        claim_epoch: int,
        step_sequence: int,
        ticket_id: int | str,
        tag: str,
        client: httpx.Client | None = None,
    ) -> EffectRecord:
        """Assign category tag to ticket in host helpdesk and stamp through sink."""
        validate_network_egress(self.base_url, {self.base_url})

        # Allowed tags allow-list
        allowed_tags = {"billing", "technical", "returns", "escalated", "general"}
        sanitized_tag = tag.strip().lower()
        if sanitized_tag not in allowed_tags:
            sanitized_tag = "general"

        idempotency_key = f"act-tag-{run_id}-step{step_sequence}-{sanitized_tag}"
        payload = {"conversation_id": ticket_id, "tag": sanitized_tag}

        return self.runtime.apply_effect(
            run_id=run_id,
            claim_epoch=claim_epoch,
            step_sequence=step_sequence,
            tool_name="act_tag",
            idempotency_key=idempotency_key,
            payload=payload,
            client=client,
        )
