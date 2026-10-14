"""Minimal in-host triage hook (<30 lines of code) behind feature flag."""

import os

import httpx

RELAY_ENABLED = os.getenv("RELAY_FEATURE_FLAG", "false").lower() in ("true", "1", "yes")
RELAY_URL = os.getenv("RELAY_API_URL", "http://localhost:8000/tickets")


def host_triage_hook(event: dict, fallback_func) -> dict:
    if not globals().get("RELAY_ENABLED", False):
        return fallback_func(event)
    try:
        payload = {
            "ticket_id": event.get("id"),
            "customer_email": event.get("sender", {}).get("email", "unknown@customer.com"),
            "subject": event.get("subject", "Support Ticket"),
            "body": event.get("content", ""),
            "metadata": {"source": "chatwoot_webhook"},
        }
        with httpx.Client(timeout=2.0) as client:
            res = client.post(RELAY_URL, json=payload)
            if res.status_code == 201:
                return {"status": "dispatched_to_relay", "run_id": res.json().get("run_id")}
    except Exception:
        pass
    return fallback_func(event)
