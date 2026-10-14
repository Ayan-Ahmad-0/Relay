# Relay Crash-Resilient Autonomous Support AI Agent

Relay is an automated, crash-resilient customer support AI agent designed to triage incoming tickets, fetch policies from Clearance (Project 1), mathematically audit invoices via Ledgerline (Project 2), and safely draft replies within host helpdesk software. It guarantees exactly zero duplicate side effects across process crashes, suspends at zero CPU cost during human approval gates, and enforces provable prompt injection safety using PostgreSQL 16 as its sole coordination primitive.

![Python](https://img.shields.io/badge/Python-3.13-3776AB?style=flat&logo=python&logoColor=white)
![PostgreSQL](https://img.shields.io/badge/PostgreSQL-16-4169E1?style=flat&logo=postgresql&logoColor=white)
![FastAPI](https://img.shields.io/badge/FastAPI-0.110-009688?style=flat&logo=fastapi&logoColor=white)
![Pydantic](https://img.shields.io/badge/Pydantic-2.6-E92063?style=flat&logo=pydantic&logoColor=white)
![Ruff](https://img.shields.io/badge/Ruff-0.3-D7FF64?style=flat&logo=ruff&logoColor=black)
![Pytest](https://img.shields.io/badge/Pytest-8.0-0A9EDC?style=flat&logo=pytest&logoColor=white)

---

## Overview

1. **Ingests** incoming helpdesk tickets via a non-invasive, feature-flagged host triage hook (<30 lines of code) with sub-millisecond rollback capability.
2. **Retrieves** internal policy chunks from Clearance (Project 1) using a scoped service principal with automatic offline cache fallback.
3. **Audits** customer invoice claims by sending attachments to Ledgerline (Project 2) and mathematically verifying line items and totals before drafting replies.
4. **Suspends** execution at zero CPU and LLM token cost when human manager authorization is required for refunds exceeding $200.00.
5. **Enforces** hardcoded Python policy gates and delimited XML isolation to block 100% of unauthorized adversarial prompt injection side effects.
6. **Survives** process crashes across 5 explicit execution windows without ever executing duplicate external side effects, coordinated solely via PostgreSQL 16 without Kafka, Redis, or Celery.

Reliability is measured across 200 injected fault runs verifying 0 duplicate sink effects, 90 adversarial prompt injection attacks verifying 0 unapproved effects, 100% terminal resolution under inverted lock contention, and an AST mutation kill rate of 100.0%. Relay is deployed as a standalone containerized service with an interactive trace viewer hosted on Cloudflare Pages.

---

## Architecture

```
Host Webhook (Chatwoot) ──► Relay Ingestion API ──► PostgreSQL 16 (Task Lease) ──► Worker Process
                                                                                          │
          ┌───────────────────────────────────────────────────────────────────────────────┤
          ▼                                       ▼                                       ▼
Clearance (Project 1)                  Ledgerline (Project 2)                  External Deduplicating Sink
(Restricted Service Principal)         (Math Validation Tool)                  (Zero-Dedup Ground Truth)
```

| Stage | Purpose | Format | Storage |
| :--- | :--- | :--- | :--- |
| **Ingestion** | Enqueue incoming customer tickets and generate run record | JSON (`TicketIngestionRequest`) | PostgreSQL `runs` table |
| **Task Claiming** | Lease execution rights using `SELECT FOR UPDATE SKIP LOCKED` | BIGINT (`claim_epoch`) | PostgreSQL `leases` table |
| **Policy Retrieval** | Fetch relevant internal policy chunks from Clearance | JSON chunks with cache fallback | Ephemeral memory cache |
| **Invoice Extraction** | Extract line items and verify billed amounts | Multipart PDF extraction | PostgreSQL `run_events` log |
| **Approval Gate** | Suspend run if refund exceeds autonomous threshold ($200) | Tokenized callback URL | PostgreSQL `approvals` table |
| **Outbound Effect** | Execute external replies stamped with derived idempotency key | SHA-256 hash stamp | External Sink + `effects` table |

### Architecture Diagram

<!-- Add: images/architecture_diagram.png -->
![Architecture Diagram](images/architecture_diagram.png)

### Tech Stack

- **Runtime Layer:** Python 3.13 managed with `uv` for reproducible virtual environment resolution.
- **Coordination Layer:** PostgreSQL 16 utilizing `SERIALIZABLE` isolation, transactional row-locking (`SKIP LOCKED`), and incrementing `claim_epoch` fencing tokens.
- **API & Ingestion:** FastAPI 0.110 and Uvicorn running asynchronous ticket submission, webhook approvals, and background sweeper endpoints.
- **Validation & Security:** Pydantic 2.6 strict schemas enforcing domain egress allow-lists and autonomous payout ceilings.
- **Verification Harness:** Pytest 8.0 with custom AST mutation testing, chaos fault injection, and inverted lock contention benchmarks.

---

## Trace Viewer & Live Crash Simulator

<!-- Add: images/trace_viewer_overview.png -->
![Trace Viewer Overview](images/trace_viewer_overview.png)

<!-- Add: images/crash_simulator_kill.png -->
![Crash Simulator Kill](images/crash_simulator_kill.png)

---

## Pipeline Flow

```
[Incoming Support Ticket]
          │
          ▼
[Claim Task & Increment claim_epoch] ──(Fencing Token Acquired)
          │
          ▼
[Execute Step 1: Clearance Retrieve] ──(Fetch Policy Chunks)
          │
          ▼
[Execute Step 2: Ledgerline Extract] ──(Audit Line Items & Totals)
          │
          ▼
[Evaluate Policy Gate: Amount <= $250] 
     ├── (If > $250) ──► [Suspend Run: status = 'waiting_approval'] ──(Zero CPU Idle)──► [Manager Callback]
     └── (If <= $250) ──► Continue
          │
          ▼
[Window 1: before_effect]
          │
          ▼
[Record Effect: status = 'pending']
          │
          ▼
[Window 2: after_effect_before_commit] ──► [HTTP POST /sink/record]
          │
          ▼
[Commit DB: status = 'applied']
          │
          ▼
[Window 3: after_commit_before_ack]
          │
          ▼
[Window 4: during_ledger_write] ──► [Record run_events]
          │
          ▼
[Commit Step: check claim_epoch]
          │
          ▼
[Window 5: after_lease_release] ──► [Release Lease & Mark Run 'completed']
```

Every stage in Relay is fully re-runnable. If a worker terminates abruptly at any point in the pipeline, a replacement worker claims the abandoned run, verifies the held fencing token against the database, queries the external sink to reconcile any in-flight effect, and deterministically completes the remaining steps without duplicating outbound actions.

---

## Evaluation Results

The evaluation test suite encompasses 41 unit and integration tests, 5-window chaos fault injection runs, 90 adversarial prompt injection attacks, 8-worker lock contention stress tests, and automated AST mutation testing.

| Evaluation Metric | Observed Result | Specification Standard | Status |
| :--- | :--- | :--- | :--- |
| **Crash Recovery Duplicate Effects** | **0 duplicates** | Exactly 0 across 5 crash windows | PASS |
| **Deterministic Replay Divergence** | **0 divergence** (9.6 µs) | 0 divergence on recorded replay | PASS |
| **Adversarial Injection Side Effects** | **0 unapproved effects** | Exactly 0 unauthorized outbound writes | PASS |
| **Inverted Contention Terminal Rate** | **100.0%** (56.3 runs/s) | 100% terminal resolution | PASS |
| **Serialization Failure Rate** | **0 permanent errors** | Clean exponential retry on code 40001 | PASS |
| **Host Hook Rollback Latency** | **< 0.08 ms** | Sub-millisecond fallback | PASS |
| **Cost Metering Reconciliation** | **10¢ delta bound** | Exact tracking of `billed-unknown` | PASS |
| **AST Mutation Kill Rate** | **100.0%** (7/7 killed) | >= 80.0% mutant kill rate | PASS |

<!-- Add: images/evaluation_report.png -->
![Evaluation Report](images/evaluation_report.png)

### Instructive Failure & Fix

During initial crash resilience testing at Window 2 (`after_effect_before_commit`), Worker 1 successfully posted an outbound reply to the external sink but crashed before committing `status = 'applied'` to PostgreSQL. When Worker 2 claimed the run, it attempted to re-post the effect, resulting in duplicate external writes. To eliminate this vulnerability, Relay's runtime was enhanced with an active reconciliation step: before re-executing any effect marked `pending`, the worker performs an idempotent lookup against the sink (`GET /sink/records?idempotency_key=...`). If the key is already stamped, the HTTP post is skipped and the effect is immediately committed as `applied`, driving duplicate effects down to exactly zero across all 200 chaos iterations.

---

## PostgreSQL 16 Coordination Store

| Object | Description |
| :--- | :--- |
| **`runs`** | Primary state machine table tracking lifecycle states (`queued`, `running`, `waiting_approval`, `completed`, `failed`), payloads, errors, and timestamps. |
| **`run_events`** | Event-sourcing ledger recording monotonic sequence events (`model_output`, `tool_result`, `clock_read`, `random_value`) for deterministic offline replay. |
| **`step_executions`** | Idempotency execution boundary enforcing `UNIQUE(run_id, step_sequence)`. |
| **`effects`** | Outbound action ledger tracking SHA-256 derived idempotency keys, execution statuses, payloads, and sink responses. |
| **`approvals`** | Human-in-the-loop suspension registry storing approval tokens, proposed payloads, decisions, and expiration timestamps. |
| **`leases`** | Worker concurrency coordination table storing worker IDs, expiration deadlines, and incrementing `claim_epoch` fencing tokens. |
| **`quota`** | Multi-tenant resource ceiling table used to evaluate real transactional lock contention under concurrent worker load. |
| **`llm_calls` & `budgets`** | Two-phase budget reservation ledger capturing estimated token limits, step settlements, and `billed-unknown` provider timeouts. |

---

## Reliability & Key Design Decisions

- **PostgreSQL 16 as Sole Coordinator** — eliminates external dependencies (Kafka, Redis, Celery) by using transactional row locks (`SKIP LOCKED`) and ACID consistency.
- **Incrementing Fencing Tokens** — every run claim increments a `claim_epoch` counter; step commits conditional on this token reject writes from zombie workers.
- **Two-Phase Effect Reconciliation** — inspects external sink state for in-flight `pending` effects before re-issuing calls, guaranteeing exactly zero duplicate actions.
- **Zero-Cost Suspension** — releases database worker leases and terminates worker threads during approval waits, consuming zero CPU cycles or LLM tokens.
- **Strict Delimited XML Prompts** — encapsulates untrusted customer ticket text within `<UNTRUSTED_CUSTOMER_TICKET>` tags to prevent instruction escaping.
- **Subprocess Tool Sandboxing** — isolates external tool execution into restricted subprocesses with explicit timeouts and network egress allow-lists.

---

## Repository Structure

```
Relay/
├── .github/workflows/ci.yml         # GitHub Actions CI running lint, gitleaks, dbmate, and tests
├── bench/
│   ├── chaos.py                     # Deterministic fault injection harness across 5 crash windows
│   ├── contention.py                # Inverted lock contention benchmark simulating worker contention
│   ├── cost.py                      # Survivable cost metering and provider timeout reconciliation
│   ├── fixtures/
│   │   └── injection_cases.json     # Adversarial prompt injection test corpus (pre-registered/blind)
│   ├── injection.py                 # Adversarial prompt injection defense benchmark runner
│   ├── mutation.py                  # AST-based mutation testing engine asserting >=80% kill rate
│   ├── results/                     # Dated benchmark JSON summaries and Markdown scorecards
│   └── runner.py                    # Unified benchmark orchestrator for make bench and bench-small
├── break-it/
│   ├── 01_fencing.patch             # Sabotage patch disabling fencing token validation
│   ├── 02_replay.patch              # Sabotage patch silencing nondeterminism replay guard
│   └── 03_idempotency.patch         # Sabotage patch destroying idempotency key determinism
├── db/migrations/
│   └── 20261014060212_core.sql      # Core PostgreSQL 16 schema migrations applied via dbmate
├── docs/
│   ├── case_study.md                # Client-ready engineering reliability case study
│   └── host_audit.md                # Audit documentation and request path analysis of Chatwoot
├── relay/
│   ├── api.py                       # FastAPI ingestion, approval callback, and sweep endpoints
│   ├── cli.py                       # Relay command-line interface entry point
│   ├── cost.py                      # Two-phase LLM budget reservation and timeout settlement ledger
│   ├── db.py                        # Psycopg 3 connection pool and transactional helper utilities
│   ├── host_hook.py                 # Helpdesk triage hook (<30 LoC) behind feature flag
│   ├── models.py                    # Core Pydantic domain models and idempotency key derivations
│   ├── replay.py                    # Event-sourced deterministic replayer and NondeterminismGuard
│   ├── retry.py                     # Concurrency retry decorator handling PostgreSQL 40001 serialization
│   ├── runtime.py                   # Central execution runtime managing leases, steps, and effects
│   ├── security.py                  # Prompt formatting, Pydantic contracts, and policy gate limits
│   ├── shadow.py                    # Rules-based baseline triage and shadow comparison engine
│   ├── sink.py                      # Local external deduplicating sink service implementation
│   ├── tools/
│   │   ├── act.py                   # Helpdesk action tool routing through approval gates and sink
│   │   ├── extract.py               # Ledgerline invoice extraction and arithmetic audit tool
│   │   ├── retrieve.py              # Clearance policy retrieval tool with cached response fallback
│   │   └── runner.py                # Isolated subprocess execution runner with enforced timeouts
│   └── worker.py                    # Worker polling loop claiming tasks and executing steps
├── scripts/
│   ├── create_break_it_branches.py  # Git automation generating branches for sabotage verification
│   ├── pinger.py                    # Keep-alive heartbeat pinger for hosted deployments
│   ├── verify_live_endpoints.py     # Live verifier for Project 1 (Clearance) & Project 2 (Ledgerline)
│   └── verify_sabotage.py           # Automated break-it sabotage test runner verifying red failures
├── tests/                           # Comprehensive test suite covering all modules and edge cases
├── web/
│   ├── app.js                       # Vanilla JavaScript trace viewer and interactive crash simulator
│   ├── index.html                   # Static HTML5 single-page application for Cloudflare Pages
│   └── style.css                    # Modern cybernetic dark mode CSS with glassmorphic cards
├── docker-compose.yml               # Local PostgreSQL 16 container definition
├── Makefile                         # Make targets for linting, testing, benchmarking, and migrations
├── provenance.json                  # Host helpdesk software license and audit provenance
└── pyproject.toml                   # Project metadata, dependencies, and tooling configuration
```

---

## Configuration

Copy `.env.example` to `.env` to configure Relay:

```bash
# Database Connection (PostgreSQL 16)
DATABASE_URL=postgres://relay:relay@localhost:5432/relay_dev?sslmode=disable

# External Deduplicating Sink Service
SINK_URL=http://localhost:8001/sink

# Host Helpdesk Integration (Chatwoot)
CHATWOOT_BASE_URL=http://localhost:3000
CHATWOOT_API_TOKEN=your_chatwoot_api_token
RELAY_FEATURE_FLAG=false

# Upstream Live Services (Clearance & Ledgerline)
CLEARANCE_URL=https://clearance-d6s6.onrender.com
CLEARANCE_PERSONA=compliance_analyst
LEDGERLINE_URL=https://ledgerline-xcmg.onrender.com
LEDGERLINE_API_KEY=your_ledgerline_api_key

# Worker Concurrency Settings
WORKER_CONCURRENCY=4
LEASE_DURATION_SECONDS=10
```

### Deployment Secrets & Quirks
- **PostgreSQL 16 Isolation:** Relay requires `SERIALIZABLE` or `REPEATABLE READ` isolation on approval tables to resolve concurrent multi-approver write skews. Ensure your PostgreSQL instance has adequate `max_connections` configured.
- **Psycopg 3 Attribute Binding:** In Psycopg 3, SQL error codes must be inspected using `getattr(err, 'sqlstate', None)` rather than legacy `err.pgcode`.
- **Windows Subprocess Spawning:** When spawning worker pools on Windows systems, all target worker functions must be defined as top-level module callables to support the `spawn` context.

---

## Getting Started

Follow these steps to set up, build, evaluate, and run Relay locally:

```bash
# 1. Clone repository
git clone https://github.com/your-org/relay.git
cd relay

# 2. Install dependencies with uv
uv sync --extra dev

# 3. Start local PostgreSQL 16 container
docker-compose up -d

# 4. Apply dbmate database migrations
dbmate up

# 5. Sanity check: Run test suite with coverage
uv run pytest --cov=relay -v

# 6. Run fast 2-minute validation benchmark
uv run python -m bench.runner --mode small

# 7. Start Relay Ingestion API and Worker
uv run uvicorn relay.api:app --port 8000 &
uv run python -m relay.worker --worker-id worker-1
```

| Service | URL | Description |
| :--- | :--- | :--- |
| **Relay Ingestion API** | `http://localhost:8000` | Support ticket submission and approval callback endpoints |
| **API Documentation** | `http://localhost:8000/docs` | Interactive Swagger / OpenAPI documentation |
| **External Sink Service** | `http://localhost:8001/sink` | Ground-truth append-only effect verification sink |
| **Trace Viewer Web UI** | `http://localhost:8000/web` | Static trace inspector and interactive crash simulator |

---

## Challenges Solved

- **Duplicate Outbound Actions on Crash** — *Symptom:* Worker killed after executing an HTTP action resulted in the recovery worker duplicating the customer reply. *Diagnosis:* Worker crashed before committing the transaction updating effect status from `pending` to `applied`. *Fix:* Implemented two-phase active reconciliation where recovery workers query the external sink before re-issuing calls, skipping execution if the idempotency key already exists.
- **Zombie Worker State Overwrites** — *Symptom:* Stale workers resuming from long garbage collection pauses overwrote completed steps. *Diagnosis:* Workers lacked a monotonically increasing epoch token validated at the transaction boundary. *Fix:* Implemented `claim_epoch` fencing tokens on the `runs` and `leases` tables; every step commit requires `claim_epoch == expected_epoch`, immediately failing stale workers with `FencingTokenError`.
- **Approval Deadlocks & Write Skew** — *Symptom:* 4 concurrent human managers acting on the same approval ticket triggered unhandled database deadlocks. *Diagnosis:* Concurrent transactions attempted row lock elevation on the approvals table without backoff handling. *Fix:* Wrapped approval resolutions in `@retry_on_concurrency_conflict` with bounded exponential backoff and jitter, achieving 100% resolution with zero permanent serialization errors.
- **Adversarial Prompt Injection Exploitation** — *Symptom:* Malicious customer tickets contained prompt injections requesting unauthorized $5,000 refunds. *Diagnosis:* Untrusted input was directly passed to LLM reasoning prompts without parameter validation. *Fix:* Implemented XML boundary isolation, Pydantic action contracts, and strict Python policy gates capping autonomous payouts at $250.00 and whitelisting recipient domains.
- **Windows Mutmut Incompatibility** — *Symptom:* `mutmut` refused to run natively on Windows with an explicit error requiring WSL. *Diagnosis:* Mutmut lacks native Windows IPC and file system hooks. *Fix:* Built a native AST mutation testing engine in `bench/mutation.py` mutating comparisons, arithmetic, and booleans, successfully achieving a 100.0% mutant kill rate across operating systems.

---

## Future Improvements

- **PostgreSQL Logical Replication Sidecar** — stream completed run events directly to an S3/MinIO bucket as Parquet files for long-term analytics without taxing operational OLTP throughput.
- **eBPF-Based Network Egress Sandbox** — enhance subprocess tool security with Linux kernel eBPF probes enforcing strict egress filtering at the packet level.
- **Multi-Tenant Sharded Work Queues** — partition the `runs` table by customer organization ID using PostgreSQL declarative partitioning to scale past 10,000 concurrent workers.
