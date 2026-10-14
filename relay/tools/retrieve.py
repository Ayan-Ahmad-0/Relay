"""Tool 1: Clearance (Project 1) Policy Retrieval Tool.

Authenticates via restricted service principal with limited entitlements.
Includes disk-cached fallback so an offline or cold backend never causes trace failure.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

import httpx

from relay.security import SecurityPolicyViolation, validate_network_egress

DEFAULT_CLEARANCE_URL = "http://localhost:8002"
CACHE_DIR = Path(os.getenv("CLEARANCE_CACHE_DIR", "./cache/clearance"))

# Built-in seed policy documents for cached fallback
FALLBACK_POLICIES = {
    "return_policy": {
        "title": "Standard Customer Return Policy",
        "chunks": [
            "Items may be returned within 30 days of delivery for a full refund.",
            "Refunds above $250.00 require manager approval and photo evidence.",
            "Items marked as Final Sale or Clearance are non-refundable.",
        ],
        "scope": "public",
    },
    "billing_dispute_policy": {
        "title": "Billing Dispute Resolution Policy",
        "chunks": [
            "Invoice discrepancies must be verified against math line items.",
            (
                "Overbilling claims verified by Ledgerline extraction are "
                "credited to the customer account."
            ),
            "Disputed amounts over $250.00 require human operator verification.",
        ],
        "scope": "public",
    },
    "confidential_executive_severance": {
        "title": "RESTRICTED: Executive Severance & Payroll Guidelines",
        "chunks": ["CONFIDENTIAL: Only accessible by Tier-3 People Ops Executives."],
        "scope": "restricted",
    },
}


class ClearanceClient:
    def __init__(
        self,
        base_url: str | None = None,
        api_key: str | None = None,
        timeout: float = 5.0,
    ) -> None:
        self.base_url = (base_url or os.getenv("CLEARANCE_URL", DEFAULT_CLEARANCE_URL)).rstrip("/")
        self.api_key = api_key or os.getenv("CLEARANCE_API_KEY", "relay-service-principal-key")
        self.timeout = timeout
        self._ensure_cache()

    def _ensure_cache(self) -> None:
        """Seed local cache directory with standard fallback policies."""
        CACHE_DIR.mkdir(parents=True, exist_ok=True)
        for key, data in FALLBACK_POLICIES.items():
            cache_file = CACHE_DIR / f"{key}.json"
            if not cache_file.exists():
                with open(cache_file, "w", encoding="utf-8") as f:
                    json.dump(data, f, indent=2)

    def retrieve(self, query: str, document_hint: str | None = None) -> dict[str, Any]:
        """Fetch policy chunks matching the query.

        Enforces egress allow-list, restricted service principal checks, and cached fallback.
        """
        # Security Boundary: Restricted documents cannot be accessed by Relay's service principal
        forbidden_terms = ["executive severance", "salary table", "payroll", "personnel record"]
        if any(term in query.lower() for term in forbidden_terms):
            raise SecurityPolicyViolation(
                "Access Denied: Scoped service principal lacks entitlements for restricted records"
            )

        validate_network_egress(self.base_url, {self.base_url})

        # Attempt live API call to Project 1 (Clearance)
        try:
            headers = {
                "Authorization": f"Bearer {self.api_key}",
                "Content-Type": "application/json",
            }
            payload = {"query": query, "document_hint": document_hint}
            with httpx.Client(timeout=self.timeout) as client:
                res = client.post(f"{self.base_url}/retrieve", json=payload, headers=headers)
                if res.status_code == 200:
                    return res.json()
                if res.status_code == 403:
                    raise SecurityPolicyViolation(
                        "Clearance API: Service principal unauthorized to view requested policy"
                    )
        except (httpx.ConnectError, httpx.TimeoutException, httpx.NetworkError):
            # Fall back cleanly to disk cache
            pass

        # Offline / Cached Fallback
        return self._lookup_cache(query, document_hint)

    def _lookup_cache(self, query: str, document_hint: str | None) -> dict[str, Any]:
        """Search local cache directory for matching policy fallback."""
        norm_query = query.lower()
        if "return" in norm_query or "refund" in norm_query:
            key = "return_policy"
        elif "dispute" in norm_query or "invoice" in norm_query or "billing" in norm_query:
            key = "billing_dispute_policy"
        elif document_hint and document_hint in FALLBACK_POLICIES:
            key = document_hint
        else:
            key = "return_policy"

        cache_file = CACHE_DIR / f"{key}.json"
        if cache_file.exists():
            with open(cache_file, encoding="utf-8") as f:
                data = json.load(f)
            return {
                "source": "cache_fallback",
                "document": key,
                "title": data["title"],
                "chunks": data["chunks"],
            }

        return {
            "source": "cache_fallback",
            "document": "default",
            "title": "Default Support Policy",
            "chunks": ["All customer refund requests above $250.00 require human review."],
        }
