"""Google Gemini AI Integration for Relay Autonomous Support Agent.

Enables generative reasoning, intent understanding, and personalized support reply
drafting using the Google Gemini REST API. Integrates with Relay's prompt boundary
isolation (relay.security) and two-phase token budget metering (relay.cost).
"""

from __future__ import annotations

import logging
import os
from typing import Any
from uuid import UUID

import httpx

from relay.cost import CostLedger
from relay.security import format_safe_prompt

logger = logging.getLogger("relay.llm")

DEFAULT_GEMINI_MODEL = "gemini-1.5-flash"
GEMINI_API_URL = "https://generativelanguage.googleapis.com/v1beta/models"


class GeminiClient:
    """Client for Google Gemini Generative AI models via REST API."""

    def __init__(
        self,
        api_key: str | None = None,
        model: str | None = None,
        cost_ledger: CostLedger | None = None,
        timeout: float = 15.0,
    ) -> None:
        self.api_key = api_key or os.getenv("GEMINI_API_KEY") or os.getenv("GOOGLE_API_KEY")
        self.model = model or os.getenv("GEMINI_MODEL", DEFAULT_GEMINI_MODEL)
        self.cost_ledger = cost_ledger or CostLedger()
        self.timeout = timeout

    def is_available(self) -> bool:
        """Return True if a valid Gemini API key is configured."""
        return bool(self.api_key and self.api_key.strip())

    def generate_support_reply(
        self,
        subject: str,
        body: str,
        policy_context: dict[str, Any] | None = None,
        invoice_context: dict[str, Any] | None = None,
        run_id: UUID | None = None,
        step_sequence: int = 3,
    ) -> dict[str, Any] | None:
        """Generate a personalized customer support reply using Gemini.

        If Gemini is unavailable, times out, or fails, returns None so the caller
        can fall back to deterministic template generation without crashing.
        """
        if not self.is_available():
            logger.debug("Gemini API key not configured; skipping AI generation.")
            return None

        # 1. Build system instruction with tool context
        policy_title = (policy_context or {}).get("title", "Standard Customer Support Policy")
        policy_doc = (policy_context or {}).get("document", "Standard terms apply.")
        invoice_info = invoice_context or {}

        system_instruction = (
            "You are Relay, an autonomous tier-1 customer support agent. "
            "Your task is to draft a helpful, polite, and concise reply to the customer.\n\n"
            f"COMPANY POLICY ({policy_title}):\n{policy_doc}\n\n"
        )
        if invoice_info:
            system_instruction += (
                f"INVOICE AUDIT DETAILS:\n"
                f"- Verified: {invoice_info.get('claim_verified', False)}\n"
                f"- Explanation: {invoice_info.get('discrepancy_explanation', 'No discrepancies')}\n"
                f"- Discrepancy Amount: ${invoice_info.get('discrepancy_amount', 0.0):.2f}\n\n"
            )

        system_instruction += (
            "INSTRUCTIONS:\n"
            "1. Acknowledge customer's request and reference their subject.\n"
            "2. State the verification findings and policy decision clearly.\n"
            "3. If a refund or balance adjustment is verified, confirm it has been applied.\n"
            "4. Keep response under 150 words. Do NOT make up unverified claims."
        )

        # 2. Wrap untrusted customer text in security delimiters
        raw_ticket = f"Subject: {subject}\nBody: {body}"
        safe_prompt = format_safe_prompt(
            system_instructions=system_instruction,
            untrusted_ticket_body=raw_ticket,
        )

        # 3. Reserve budget in PostgreSQL ledger if run_id is supplied
        call_id = None
        if run_id:
            try:
                call_id = self.cost_ledger.reserve_llm_budget(
                    run_id=run_id,
                    step_sequence=step_sequence,
                    model=self.model,
                    estimated_tokens=500,
                    estimated_cost_cents=1,
                )
            except Exception as e:
                logger.warning(f"Could not reserve LLM budget: {e}")

        # 4. Call Google Gemini REST endpoint
        url = f"{GEMINI_API_URL}/{self.model}:generateContent?key={self.api_key}"
        payload = {
            "contents": [
                {
                    "parts": [
                        {"text": safe_prompt},
                    ]
                }
            ],
            "generationConfig": {
                "temperature": 0.2,
                "maxOutputTokens": 600,
            },
        }

        try:
            with httpx.Client(timeout=self.timeout) as client:
                resp = client.post(url, json=payload)

            if resp.status_code != 200:
                logger.warning(f"Gemini API returned HTTP {resp.status_code}: {resp.text}")
                if call_id:
                    self.cost_ledger.mark_billed_unknown(call_id)
                return None

            data = resp.json()
            candidates = data.get("candidates", [])
            if not candidates:
                logger.warning("Gemini returned no candidates in response.")
                return None

            parts = candidates[0].get("content", {}).get("parts", [])
            generated_text = "".join(p.get("text", "") for p in parts).strip()
            if not generated_text:
                return None

            # Extract token usage
            usage = data.get("usageMetadata", {})
            prompt_tokens = usage.get("promptTokenCount", 0)
            candidate_tokens = usage.get("candidatesTokenCount", 0)
            total_tokens = usage.get("totalTokenCount", prompt_tokens + candidate_tokens)

            # 5. Settle cost in PostgreSQL ledger
            if call_id:
                try:
                    # Estimate cost at $0.075 / 1M input tokens, $0.30 / 1M output tokens for Flash
                    # 1 cent = $0.01
                    self.cost_ledger.settle_llm_call(
                        llm_call_id=call_id,
                        actual_tokens=total_tokens,
                        actual_cost_cents=1,
                    )
                except Exception as e:
                    logger.warning(f"Failed to settle LLM call in ledger: {e}")

            return {
                "text": generated_text,
                "model": self.model,
                "prompt_tokens": prompt_tokens,
                "candidate_tokens": candidate_tokens,
                "total_tokens": total_tokens,
            }

        except httpx.TimeoutException:
            logger.warning(f"Gemini API call timed out after {self.timeout}s")
            if call_id:
                self.cost_ledger.mark_billed_unknown(call_id)
            return None
        except Exception as e:
            logger.warning(f"Error during Gemini API generation: {e}")
            if call_id:
                self.cost_ledger.mark_billed_unknown(call_id)
            return None
