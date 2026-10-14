# Project 3: Relay - Technical Blueprint & Workflow

## 📝 Project Overview: What Relay Does
Relay is an automated customer support AI agent designed to read incoming support tickets, fetch relevant policies and invoices using external tools, and safely execute actions like drafting replies or tagging tickets[cite: 3]. Its primary engineering achievement is absolute reliability: it guarantees that it will survive unexpected server crashes without ever repeating an action (like sending duplicate emails), can pause indefinitely for human approval without consuming server resources, and resists malicious prompt injection attacks by enforcing strict, hard-coded safety policies[cite: 3]. It is designed to operate seamlessly within a company's existing open-source helpdesk software[cite: 3].

---

## 🔗 System Integration: How Projects 1, 2, and 3 Connect
Relay is built last specifically because its tools call two live, running services rather than stubs or mock endpoints[cite: 3]:

1. **Clearance (Project 1) via the `retrieve` tool:**
   * **Role:** When a ticket asks about internal rules (e.g., return or severance policies), Relay calls Clearance's API to fetch policy chunks[cite: 3].
   * **Security Boundary:** The tool authenticates using a restricted service principal whose entitlements are limited, guaranteeing the agent itself cannot access confidential records[cite: 3]. It includes a cached response fallback so a cold Clearance backend never causes a failed trace[cite: 3].
2. **Ledgerline (Project 2) via the `extract` tool:**
   * **Role:** When a billing ticket arrives with an invoice attached, Relay sends the PDF to Ledgerline's `/extract` endpoint[cite: 3].
   * **Audit & Validation:** Relay uses the extracted total and line items to mathematically verify the customer's claim before routing or drafting a reply[cite: 3].
3. **Host Application via the `act` tool:**
   * **Role:** Relay drafts responses and assigns categories inside an existing open-source helpdesk (e.g., Chatwoot/osTicket)[cite: 3].
   * **Execution Safety:** All outgoing actions route through a human approval gate and stamp through the external deduplicating sink[cite: 3].

---

## 🏗️ Implementation Workflow

### Phase 1: Project Setup & Preflight (Day 1)

1. **Repository & Infrastructure Skeleton:**
   * Initialize a repository named `relay` with Python 3.11+, `uv` or `poetry`, `ruff`, `pytest`, `gitleaks`, and `dbmate`[cite: 3].
   * Configure a local PostgreSQL 16 service container (`postgres:16`) for local testing[cite: 3].
   * Set up GitHub Actions CI running linting, formatting, secrets scanning (`gitleaks`), migrations against a fresh database, and test suites[cite: 3].

2. **Select & Audit Host Helpdesk Application:**
   * Evaluate open-source helpdesk candidates (e.g., Chatwoot, osTicket, Zammad)[cite: 3].
   * Audit the license file of the candidate[cite: 3]. If AGPL-3.0, formally decide whether to publish your modified fork or keep the host integration local-only (with video/shadow comparison tables) to comply with Section 13[cite: 3].
   * Record the host license name, URL, and audit date in `provenance.json`[cite: 3].
   * Run the host locally following its README, commit any documentation corrections as audit evidence, document a single request path, and draw its data model[cite: 3].

### Phase 2: Schema Design & Database Coordination Primitives (Days 2–3)

Postgres is the **only** coordination primitive—do not use Kafka, Redis, or Celery[cite: 3].

1. **Create `dbmate` Migrations for Core Tables:**
   * `runs`: `id` (UUID), `status` (`queued`, `running`, `waiting_approval`, `completed`, `failed`), `created_at`, `updated_at`[cite: 3].
   * `run_events`: Event-sourcing log table with `id`, `run_id`, `sequence_num` (monotonic INT), `event_type` (`model_output`, `tool_result`, `clock_read`, `random_value`), `payload` (JSONB), and `recorded_at`[cite: 3].
   * `step_executions`: Unique tracking table enforcing `UNIQUE(run_id, step_sequence)`[cite: 3].
   * `effects`: Outbound actions log with `id`, `run_id`, `step_sequence`, `tool_name`, `idempotency_key` (derived hash), `status` (`pending`, `applied`, `failed`), and timestamps[cite: 3].
   * `approvals`: State table for human gates with `id`, `run_id`, `step_sequence`, `token`, `status` (`pending`, `approved`, `rejected`), `expires_at`, and timestamps[cite: 3].
   * `leases`: Worker concurrency control table with `resource_id`, `worker_id`, `claim_epoch` (BIGINT fencing token), and `expires_at`[cite: 3].
   * `quota`: Shared per-tenant rate/resource limiting table for contention tests[cite: 3].
   * `llm_calls` & `budgets`: Pre-call reservation ledger storing token/cost estimates, `billed-unknown` flags, and final cost tallies[cite: 3].

2. **Build the Local External Sink Service:**
   * Create a lightweight independent HTTP service (`sink_service.py`) that logs every inbound request directly to a raw log file/table along with its `idempotency_key` and body[cite: 3].
   * **Rule:** The sink must perform **no deduplication** of its own[cite: 3]. It serves as the external source of truth for measuring applied effects[cite: 3].

3. **Build the Execution Runtime & Worker:**
   * Implement task-claiming via `SELECT FOR UPDATE SKIP LOCKED` on the `runs` table, acquiring a lease with an incremented `claim_epoch` fencing token[cite: 3].
   * Condition every step commit on the held `claim_epoch` so stale (zombie) workers cannot commit writes[cite: 3].
   * Execute steps in single-transaction increments[cite: 3].

### Phase 3: Deterministic Replay & Fault Injection (Days 4–5)

1. **Implement Event-Sourced Deterministic Replay:**
   * Build the replayer engine: during replay, instead of re-calling APIs or querying system clocks, intercept calls and feed matching recorded values from `run_events` in strict sequence order[cite: 3].
   * Build a **Nondeterminism Guard**: raise an immediate runtime exception if a step requests an event type or order that deviates from the recorded sequence (e.g., catching hidden `datetime.now()` calls)[cite: 3].
   * Create a deliberate test step that reads a live clock during replay to prove the guard fails loudly[cite: 3].

2. **Deterministic Fault Injection Harness (`bench/chaos.py`):**
   * Define a `CrashPoint` enum representing 5 explicit execution windows:
     * `before_effect`[cite: 3]
     * `after_effect_before_commit`[cite: 3]
     * `after_commit_before_ack`[cite: 3]
     * `during_ledger_write`[cite: 3]
     * `after_lease_release`[cite: 3]
   * Inject worker terminations at each of the 5 windows (40 deterministic kills per window = 200 total runs)[cite: 3].
   * Verify via the external sink log that **duplicate applied effects = 0** across all 200 crashes[cite: 3].

3. **Isolation-Level & Concurrency Hardening:**
   * Set transaction isolation to `REPEATABLE READ` or `SERIALIZABLE` on the approval table[cite: 3].
   * Implement exponential backoff and bounded retries on serialization failures[cite: 3].
   * Write automated tests that intentionally demonstrate write skew (two simultaneous approvers) and deadlocks (inverted lock ordering), verifying that the retry layer resolves them cleanly[cite: 3].

### Phase 4: Tools, Authority & Zero-Cost Suspension (Week 2, Days 1–3)

1. **Implement the Three Core Tools (Isolated Subprocesses):**
   * Run tools in restricted subprocesses with explicit timeouts and network egress allow-lists[cite: 3].
   * **Tool 1 (`retrieve`):** Calls Project 1 (Clearance) API using a strictly scoped service principal that cannot view restricted documents[cite: 3]. Include a cached response fallback for hosted runs[cite: 3].
   * **Tool 2 (`extract`):** Calls Project 2 (Ledgerline) `/extract` API when support tickets contain invoice attachments to verify billed amounts[cite: 3].
   * **Tool 3 (`act`):** Writes ticket replies and category tags to the host helpdesk API, routed through the approval gate and stamped through the external sink[cite: 3].

2. **Zero-Cost Suspension Engine:**
   * When an action requires human approval, update `runs.status = 'waiting_approval'`, persist state, release the database worker lease, and terminate the worker loop[cite: 3].
   * Wake suspended runs via an incoming HTTP callback or a background sweep when approval is submitted, consuming zero CPU cycles or LLM tokens during the wait[cite: 3].
   * Test with 4 concurrent approvers attempting to act on the same approval row[cite: 3].

3. **Prompt Injection & Hardened Security Layer (`bench/injection.py`):**
   * **Architectural Defenses:**
     * Isolate untrusted ticket text within a strictly delimited data block in prompts (never concatenate directly into system instructions)[cite: 3].
     * Parse and validate all tool arguments through Pydantic contracts[cite: 3].
     * Enforce hardcoded policy gates in Python: recipient allow-lists, payout/refund amount ceilings, and domain egress rules[cite: 3].
   * **Corpus Split & Benchmark:**
     * Generate 50 pre-registered adversarial cases across multiple shapes (body text, table cells, white text, filenames, indirect injection via tool results, encoded text) and hash the file in Git before coding the policy layer[cite: 3].
     * Reserve 30 blind test cases from an independent author[cite: 3].
     * Prepare a 10-case adaptive attacker round[cite: 3].
     * Output two distinct metrics: **Model Compliances** vs. **Unapproved Applied Effects** (the latter must equal 0)[cite: 3].

### Phase 5: Host Integration, Cost Ledger & Contention Benchmark (Week 2 Day 4 – Week 3 Day 2)

1. **Host Application Integration:**
   * Write characterization tests pinning the existing behavior of the chosen helpdesk app[cite: 3].
   * Inject Relay's triage hook into the host using **fewer than 30 lines of code**, placed behind a feature flag[cite: 3].
   * Build a rules-based baseline triage function to serve as the benchmark comparator[cite: 3].
   * Execute a shadow run comparing Relay against the baseline, measuring rollback time when flipping the feature flag[cite: 3].

2. **Survivable Cost Metering (`bench/cost.py`):**
   * Reserve an estimated budget in `budgets` before firing each LLM call[cite: 3].
   * Decrement the budget and record token counts in the same transaction as the step execution[cite: 3].
   * If a provider call times out, mark the row as `billed-unknown` and output reconciliation deltas against the provider dashboard[cite: 3].

3. **Inverted Contention Benchmark (`bench/contention.py`):**
   * Configure 8 concurrent workers fighting over only 20 runs and mutating a shared tenant `quota` row to maximize real lock contention[cite: 3].
   * Increment worker counts until hitting the performance knee[cite: 3].
   * Assert 100% terminal state resolution, 100% retry success, and 0 permanent serialization failures[cite: 3].

### Phase 6: Verification, Deployment & Deliverables (Week 3, Days 3–5)

1. **Break-It Branches & Mutation Testing:**
   * Create 3 explicit break-it branches that deliberately sabotage:
     1. The fencing token validation[cite: 3].
     2. The nondeterminism replay guard[cite: 3].
     3. The idempotency key generation logic[cite: 3].
   * Configure CI to execute these branches and assert that test suites fail (turn red)[cite: 3].
   * Run `mutmut` over core modules, asserting an **$\ge 80\%$ mutant kill rate**[cite: 3].

2. **Benchmark Automation & Static Demo Site:**
   * Script `make bench` to run the chaos, replay, contention, injection, and cost benchmarks, outputting results into `bench/results/<date>/`[cite: 3].
   * Implement `make bench-small` (a fast 2-minute validation run on recorded fixtures)[cite: 3].
   * Deploy Relay on its own Render service (with external 5-minute pinger keepalive)[cite: 3].
   * Deploy the static trace viewer onto Cloudflare Pages to display precomputed run traces instantly without waiting on a sleeping database[cite: 3].
   * Add a live demo panel with a visible "Kill Worker" button to showcase on-the-fly crash recovery[cite: 3].

3. **Final Case Study & Evidence:**
   * Author a client-ready case study covering:
     * Problem statement and crash recovery explanation[cite: 3].
     * External sink duplicate effect measurements (0 across 5 windows)[cite: 3].
     * Injection defense table (separating model compliances from blocked effects)[cite: 3].
     * Host shadow comparison, flag rollback timing, and cost reconciliation[cite: 3].
     * The complete integration trace where Relay calls both Clearance and Ledgerline end-to-end[cite: 3].
   * Record a 2-minute demo video and capture a 15–20 second chat-shareable clip and still image[cite: 3].