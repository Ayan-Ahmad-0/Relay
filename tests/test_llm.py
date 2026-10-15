"""Tests for Google Gemini AI integration and fallback behavior."""

from __future__ import annotations

from unittest.mock import MagicMock, patch
from uuid import uuid4

import httpx

from relay.llm import GeminiClient


def test_gemini_availability():
    client_no_key = GeminiClient(api_key="")
    assert not client_no_key.is_available()

    client_with_key = GeminiClient(api_key="test-api-key")
    assert client_with_key.is_available()


def test_gemini_generates_reply_and_settles_tokens():
    mock_response = MagicMock()
    mock_response.status_code = 200
    mock_response.json.return_value = {
        "candidates": [
            {
                "content": {
                    "parts": [
                        {
                            "text": "Hello, we verified invoice INV-100 and applied a refund of $50.00."
                        }
                    ]
                }
            }
        ],
        "usageMetadata": {
            "promptTokenCount": 85,
            "candidatesTokenCount": 25,
            "totalTokenCount": 110,
        },
    }

    mock_ledger = MagicMock()
    mock_ledger.reserve_llm_budget.return_value = uuid4()

    client = GeminiClient(api_key="test-key", cost_ledger=mock_ledger)

    with patch("httpx.Client.post", return_value=mock_response):
        run_id = uuid4()
        res = client.generate_support_reply(
            subject="Billing issue",
            body="I was charged twice.",
            policy_context={"title": "Billing Policy", "document": "Refunds within 30 days."},
            invoice_context={
                "claim_verified": True,
                "discrepancy_explanation": "Double charge verified",
                "discrepancy_amount": 50.0,
            },
            run_id=run_id,
            step_sequence=3,
        )

        assert res is not None
        assert "Hello, we verified invoice INV-100" in res["text"]
        assert res["total_tokens"] == 110
        assert res["model"] == "gemini-1.5-flash"
        assert mock_ledger.settle_llm_call.called


def test_gemini_graceful_timeout_fallback():
    mock_ledger = MagicMock()
    call_id = uuid4()
    mock_ledger.reserve_llm_budget.return_value = call_id

    client = GeminiClient(api_key="test-key", cost_ledger=mock_ledger)

    with patch("httpx.Client.post", side_effect=httpx.TimeoutException("Read timeout")):
        res = client.generate_support_reply(
            subject="Question",
            body="Help needed",
            run_id=uuid4(),
            step_sequence=3,
        )
        assert res is None
        mock_ledger.mark_billed_unknown.assert_called_once_with(call_id)


def test_gemini_error_status_fallback():
    mock_response = MagicMock()
    mock_response.status_code = 403
    mock_response.text = "API key not valid."

    mock_ledger = MagicMock()
    call_id = uuid4()
    mock_ledger.reserve_llm_budget.return_value = call_id

    client = GeminiClient(api_key="invalid-key", cost_ledger=mock_ledger)

    with patch("httpx.Client.post", return_value=mock_response):
        res = client.generate_support_reply(
            subject="Question",
            body="Help needed",
            run_id=uuid4(),
            step_sequence=3,
        )
        assert res is None
        mock_ledger.mark_billed_unknown.assert_called_once_with(call_id)
