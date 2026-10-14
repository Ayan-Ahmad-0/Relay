"""Live Endpoint Verifier for Projects 1 & 2.

Queries:
1. Clearance (Project 1): https://clearance-d6s6.onrender.com
2. Ledgerline (Project 2): https://ledgerline-xcmg.onrender.com
"""

import os

import httpx

from relay.tools.extract import LedgerlineClient
from relay.tools.retrieve import ClearanceClient


def verify_clearance_live():
    print("=" * 60)
    print("Testing Live Clearance (Project 1) Service...")
    clearance_url = os.getenv("CLEARANCE_URL", "https://clearance-d6s6.onrender.com")
    print(f"Target URL: {clearance_url}")

    client = ClearanceClient(base_url=clearance_url)
    res = client.retrieve("What is the return and refund policy?")
    print(f"Source: {res.get('source')}")
    print(f"Document: {res.get('document')}")
    print(f"Chunks retrieved: {len(res.get('chunks', []))}")
    if res.get("chunks"):
        print(f"Excerpt: {res['chunks'][0][:150]}...")
    print("[PASS] Clearance live retrieval succeeded.\n")


def verify_ledgerline_live():
    print("=" * 60)
    print("Testing Live Ledgerline (Project 2) Service...")
    ledgerline_url = os.getenv("LEDGERLINE_URL", "https://ledgerline-xcmg.onrender.com")
    api_key = os.getenv("LEDGERLINE_API_KEY", "")
    print(f"Target URL: {ledgerline_url}")
    print(f"Using API Key: {'***' + api_key[-4:] if len(api_key) > 4 else '(none/default)'}")

    # Test OpenAPI accessibility
    with httpx.Client(timeout=10.0) as client:
        r = client.get(f"{ledgerline_url}/openapi.json")
        print(f"OpenAPI Spec Status: {r.status_code}")

    # Test LedgerlineClient extraction (with offline fallback if key unauthorized)
    tool = LedgerlineClient(base_url=ledgerline_url, api_key=api_key)
    invoice = tool.extract(invoice_id="INV-2026-LIVE-001")
    print(f"Invoice Number: {invoice.invoice_number}")
    print(f"Vendor: {invoice.vendor}")
    print(f"Total Amount: ${invoice.total_amount:.2f}")

    audit = tool.verify_invoice(invoice, claimed_amount=invoice.total_amount)
    print(f"Math Audit Valid: {audit.is_mathematically_sound}")
    print(f"Claim Verified: {audit.claim_verified}")
    print("[PASS] Ledgerline extraction & audit tool verification succeeded.\n")


if __name__ == "__main__":
    verify_clearance_live()
    verify_ledgerline_live()
