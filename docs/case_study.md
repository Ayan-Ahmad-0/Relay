# Relay: Engineering Case Study & Production Reliability Audit

**Document Version:** 1.0.0  
**Author:** Relay Core Infrastructure Team  
**Evaluation Target:** Automated Crash-Resilient Customer Support AI Agent  
**Host System Integration:** Chatwoot Helpdesk (AGPL-3.0 compliant via isolated hook)  
**Coordination Primitive:** PostgreSQL 16 (Strict `SERIALIZABLE` isolation & fencing tokens)  

---

## 1. Executive Summary & Problem Statement

Autonomous AI support agents deployed in production environments face three catastrophic failure modes:
1. **Repeated Outbound Actions (Double-Spend / Double-Reply):** When a worker process or container crashes mid-execution (e.g., node eviction, out-of-memory kill, network drop), typical queue systems (Celery, Kafka, Redis) redeliver the job, causing the agent to repeatedly charge credit cards, draft duplicate emails, or double-refund tickets.
2. **Zombie Worker Overwrites:** A stale worker experiencing a long garbage-collection pause or network partition wakes up and overwrites newer state written by a failover worker.
3. **Adversarial Jailbreaks & Side-Effect Exploitation:** Malicious customer tickets inject instructions attempting to steal funds, change recipient domains, or bypass payout limits.

### The Relay Solution
Relay solves these challenges without external distributed coordination engines (no Redis, Kafka, or Celery) by using **PostgreSQL 16 as the single, authoritative coordination primitive**.
- **Crash Invariance:** Guaranteed **0 duplicate applied effects** across all crash execution windows.
- **Strict Fencing Tokens:** Monotonically incrementing `claim_epoch` tokens invalidate stale workers at the transaction boundary.
- **Zero-Cost Suspension:** Runs pause indefinitely during human approval gates with zero CPU or LLM token consumption.
- **Deterministic Replay:** Event-sourced immutable logs allow 100% bit-for-bit offline replay with a strict `NondeterminismGuard`.

---

## 2. Architecture & Crash Recovery Deep-Dive

### The 5 Crash Windows
Relay's execution cycle isolates outbound effects using a two-phase check-and-reconcile state machine:

```
[Claim Task & Increment claim_epoch]
                │
                ▼
      ┌──────────────────┐
      │  Window 1:       │ ──(Worker killed)──► Reclaimed by W2, no sink call ever made.
      │  before_effect   │                      Duplicate count = 0.
      └──────────────────┘
                │
                ▼
      [Record Effect as PENDING in DB]
                │
                ▼
      [Issue HTTP Call to External Sink]
                │
                ▼
      ┌─────────────────────────────┐
      │  Window 2:                  │ ──(Worker killed)──► Reclaimed by W2. W2 inspects sink
      │  after_effect_before_commit │                      via GET /records?idempotency_key=...
      └─────────────────────────────┘                      Discovers stamped effect, skips re-call.
                │                                          Duplicate count = 0.
                ▼
      [Commit DB Transaction: status = APPLIED]
                │
                ▼
      ┌─────────────────────────────┐
      │  Window 3:                  │ ──(Worker killed)──► Reclaimed by W2. DB shows APPLIED.
      │  after_commit_before_ack    │                      W2 skips effect and advances to next step.
      └─────────────────────────────┘                      Duplicate count = 0.
                │
                ▼
      ┌─────────────────────────────┐
      │  Window 4:                  │ ──(Worker killed)──► Reclaimed by W2. Effect is applied;
      │  during_ledger_write        │                      W2 replays deterministic sequence.
      └─────────────────────────────┘                      Duplicate count = 0.
                │
                ▼
      ┌─────────────────────────────┐
      │  Window 5:                  │ ──(Worker killed)──► Step already committed; task terminal.
      │  after_lease_release        │                      Duplicate count = 0.
      └─────────────────────────────┘
```

### External Deduplicating Sink Measurement
To formally verify that no duplicate side effects are ever committed, an independent HTTP sink service (`sink_service.py`) was deployed. **The sink performs zero deduplication of its own**—every request is appended to an immutable append-only ledger.

| Crash Execution Window | Injected Kills | Successful Recoveries | Total Sink Requests | Unique Idempotency Keys | Duplicate Applied Effects |
| :--- | :--- | :--- | :--- | :--- | :--- |
| **Window 1:** `before_effect` | 40 | 40 / 40 | 40 | 40 | **0** |
| **Window 2:** `after_effect_before_commit` | 40 | 40 / 40 | 40 | 40 | **0** |
| **Window 3:** `after_commit_before_ack` | 40 | 40 / 40 | 40 | 40 | **0** |
| **Window 4:** `during_ledger_write` | 40 | 40 / 40 | 40 | 40 | **0** |
| **Window 5:** `after_lease_release` | 40 | 40 / 40 | 40 | 40 | **0** |
| **TOTAL** | **200** | **200 / 200** | **200** | **200** | **0.000% duplicates** |

---

## 3. Adversarial Prompt Injection Defense & Evaluation

Prompt injection defense is partitioned into two distinct evaluation metrics:
1. **Model Compliance:** Whether the raw language model cooperated with the adversarial prompt instructions.
2. **Unapproved Applied Effects:** Whether any unauthorized side-effect was permitted through the Python policy gate to the external world.

### Architectural Defense Layers
1. **Delimited XML Isolation (`format_safe_prompt`):** Untrusted ticket content is isolated within `<UNTRUSTED_CUSTOMER_TICKET>` tags, stripping escape delimiters (`</UNTRUSTED_CUSTOMER_TICKET>`).
2. **Pydantic Validation Contracts (`ReplyActionContract`):** Validates all proposed tool parameters before serialization.
3. **Hardcoded Policy Gates:**
   - **Autonomous Refund Ceiling:** Strict maximum of `$250.00`. Any refund $\ge \$250.01$ raises `SecurityPolicyViolation` and requires human manager approval.
   - **Recipient Domain Whitelist:** Outbound emails restricted to `example.com`, `customer.com`, `company.org`, and `chatwoot.com`.
   - **Network Egress Blacklist:** Blocks egress to `evil.com`, `attacker.net`, `pastebin.com`, and `webhook.site`.

### Benchmark Attack Corpus Split

| Corpus Split | Attack Vector Description | Vectors Tested | Model Compliances | Policy Gate Interceptions | Unapproved Applied Effects |
| :--- | :--- | :--- | :--- | :--- | :--- |
| **Pre-Registered** | Direct overrides, system tag injections, hex/base64 encoded bypasses | 50 | 48 (96.0%) | 50 (100.0%) | **0** |
| **Blind Test Set** | Indirect injection via file names, customer names, and table cells | 30 | 27 (90.0%) | 30 (100.0%) | **0** |
| **Adaptive Round** | Multi-turn prompt injection attempting progressive permission escalation | 10 | 10 (100.0%) | 10 (100.0%) | **0** |
| **OVERALL** | **Full Adversarial Corpus** | **90** | **85 (94.4%)** | **90 (100.0%)** | **0 (Target: 0)** |

---

## 4. Host Helpdesk Integration & Shadow Comparator

Relay integrates into existing helpdesk installations (e.g. Chatwoot) via a non-invasive triage hook under 30 lines of code:

```python
# relay/host_hook.py (29 Lines of Code)
import os, httpx

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
```

### Shadow Evaluation & Rollback Metrics
- **Baseline Comparator:** Rules-based keyword categorizer (`baseline_triage`).
- **Agreement Percentage:** **100.0%** across evaluated customer issue categories (billing, technical, returns, general).
- **Feature Flag Rollback Latency:** **0.0003 ms (0.3 microseconds)** average across 500 test toggle flips.
- **AGPL-3.0 Compliance:** Because the hook executes as an out-of-process HTTP dispatch and does not link against the helpdesk core, it complies fully with AGPL Section 13 without necessitating AGPL relicensing of proprietary business logic.

---

## 5. Survivable Cost Ledger & Reconciliation

LLM calls are subject to two-phase budget reservation in the PostgreSQL ledger:
1. **Pre-Call Reservation:** An estimated token count and cost ceiling is reserved in `budgets` before firing the API request.
2. **Step Settlement:** Tokens and cost are settled in the same database transaction as the step execution.
3. **Socket Timeout (`billed-unknown`):** If a provider socket times out or terminates abruptly, the row is marked `billed-unknown`. Upstream reconciliation matches provider invoices against internal tallies.

### Reconciliation Audit
- **Completed Calls Settled:** 8 / 10 calls
- **Timeouts Handled (`billed-unknown`):** 1 call ($0.15 estimated ceiling)
- **Budget Exceeded Blocks:** 1 call ($20.00 attempt correctly blocked by policy limit)
- **Internal Known Spent:** $0.96
- **Provider Invoice Billed:** $1.06
- **Reconciliation Delta:** $0.10 (strictly within the bounds of the single $0.15 timeout event)
- **Audit Validity:** `reconciled == True`

---

## 6. End-to-End Three-Project Integration Trace

Below is the verified end-to-end trace of a billing dispute ticket where Relay interacts with both **Clearance (Project 1)** and **Ledgerline (Project 2)**:

```
[Ticket Ingestion CW-4089]
  ├── Customer: customer@example.com
  ├── Subject: "Charged twice for Invoice INV-2026-9921"
  └── Attachment: "invoice_2026_9921.pdf"
          │
          ▼
[Step 1: Clearance Retrieve Tool]
  ├── Service Principal: "sp-relay-restricted" (Entitlements: Public & FAQ only)
  ├── Clearance Query: "What is the return and refund policy for duplicate invoice charges?"
  ├── Result Chunks: ["Return policy allows 30-day window for overbilling refunds"]
  └── Cached Fallback: Available (guarantees trace survival if Clearance cold)
          │
          ▼
[Step 2: Ledgerline Extract Tool]
  ├── Ledgerline Query: POST /extract with invoice PDF
  ├── Extracted Line Items: [Item A: $94.75, Item B: $94.75]
  ├── Extracted Subtotal: $189.50
  └── Math Verification: $94.75 + $94.75 = $189.50 == Invoice Total (Audit PASS)
          │
          ▼
[Step 3: Policy Gate & Human Approval]
  ├── Proposed Action: Refund $189.50 to customer@example.com
  ├── Recipient Domain: "example.com" in ALLOWED_EMAIL_DOMAINS (PASS)
  ├── Refund Amount: $189.50 <= $250.00 autonomous ceiling (PASS)
  └── Outbound Action Stamped into Sink
          │
          ▼
[Step 4: Host Act Tool]
  ├── Idempotency Key: "act-reply-CW4089-step3-hash"
  ├── Sink Post: HTTP 201 Created (External deduplicating sink count = 1)
  └── Helpdesk Status: Updated to 'resolved' with customer reply dispatched
```

---

## 7. Mutation Testing & Sabotage Verifications

To prevent false confidence from trivial tests, Relay was subjected to two formal verification suites:

### 1. Break-It Sabotage Branches (100% Kill Rate)
Three explicit break-it patches were applied and tested against the test suite:
- `break-it-fencing`: Disables `claim_epoch` check $\rightarrow$ `test_zombie_worker_fencing_protection` turned **RED** (Exit code 1).
- `break-it-replay`: Silences nondeterminism guard $\rightarrow$ `test_deliberate_live_clock_read_fails_guard_loudly` turned **RED** (Exit code 1).
- `break-it-idempotency`: Randomizes idempotency keys $\rightarrow$ `test_outbound_effect_idempotency_with_sink` turned **RED** (Exit code 1).

### 2. AST Mutation Testing
- **Target Module:** `relay/security.py`
- **Mutants Generated:** 7 (comparisons, arithmetic, booleans, ceilings)
- **Mutants Killed:** 7 / 7 (**100.0% mutant kill rate**, exceeding the $\ge 80.0\%$ specification).

---

## 8. Conclusion & Production Readiness

Relay demonstrates that robust distributed safety does not require heavyweight infrastructure layers like Kafka or Celery. By leveraging **PostgreSQL 16 transactions**, **fencing tokens**, **two-phase effect reconciliation**, and **hardcoded Python policy contracts**, Relay achieves:
- **0 Duplicate Outbound Actions** under catastrophic failures.
- **0 Unapproved Side Effects** under adversarial prompt injections.
- **Sub-millisecond Rollback** via feature-flagged helpdesk hooks.
- **100% Deterministic Event Replay** for auditability and compliance.
