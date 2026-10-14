-- migrate:up

-- Extension for UUID generation if needed
CREATE EXTENSION IF NOT EXISTS "pgcrypto";

-- 1. Runs table
CREATE TABLE IF NOT EXISTS runs (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    status VARCHAR(32) NOT NULL DEFAULT 'queued' CHECK (status IN ('queued', 'running', 'waiting_approval', 'completed', 'failed')),
    claim_epoch BIGINT NOT NULL DEFAULT 0,
    worker_id VARCHAR(128) NULL,
    lease_expires_at TIMESTAMPTZ NULL,
    input_payload JSONB NOT NULL DEFAULT '{}'::jsonb,
    output_payload JSONB NULL,
    error TEXT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp()
);

CREATE INDEX IF NOT EXISTS idx_runs_queue ON runs (status, created_at) WHERE status = 'queued';
CREATE INDEX IF NOT EXISTS idx_runs_lease ON runs (status, lease_expires_at);

-- 2. Run Events table (Event-sourced determinism log)
CREATE TABLE IF NOT EXISTS run_events (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    run_id UUID NOT NULL REFERENCES runs(id) ON DELETE CASCADE,
    sequence_num INT NOT NULL,
    event_type VARCHAR(64) NOT NULL CHECK (event_type IN ('model_output', 'tool_result', 'clock_read', 'random_value')),
    payload JSONB NOT NULL DEFAULT '{}'::jsonb,
    recorded_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
    CONSTRAINT unique_run_sequence UNIQUE (run_id, sequence_num)
);

CREATE INDEX IF NOT EXISTS idx_run_events_order ON run_events (run_id, sequence_num ASC);

-- 3. Step Executions table
CREATE TABLE IF NOT EXISTS step_executions (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    run_id UUID NOT NULL REFERENCES runs(id) ON DELETE CASCADE,
    step_sequence INT NOT NULL,
    step_name VARCHAR(128) NOT NULL,
    status VARCHAR(32) NOT NULL DEFAULT 'running' CHECK (status IN ('running', 'completed', 'failed')),
    started_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
    completed_at TIMESTAMPTZ NULL,
    CONSTRAINT unique_run_step_sequence UNIQUE (run_id, step_sequence)
);

-- 4. Effects table (Outbound actions log with idempotency keys)
CREATE TABLE IF NOT EXISTS effects (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    run_id UUID NOT NULL REFERENCES runs(id) ON DELETE CASCADE,
    step_sequence INT NOT NULL,
    tool_name VARCHAR(64) NOT NULL,
    idempotency_key VARCHAR(128) NOT NULL UNIQUE,
    status VARCHAR(32) NOT NULL DEFAULT 'pending' CHECK (status IN ('pending', 'applied', 'failed')),
    payload JSONB NOT NULL DEFAULT '{}'::jsonb,
    response JSONB NULL,
    applied_at TIMESTAMPTZ NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp()
);

CREATE INDEX IF NOT EXISTS idx_effects_run ON effects (run_id, step_sequence);

-- 5. Approvals table (Zero-cost suspension gates)
CREATE TABLE IF NOT EXISTS approvals (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    run_id UUID NOT NULL REFERENCES runs(id) ON DELETE CASCADE,
    step_sequence INT NOT NULL,
    token VARCHAR(128) NOT NULL UNIQUE,
    action_type VARCHAR(64) NOT NULL,
    proposed_payload JSONB NOT NULL DEFAULT '{}'::jsonb,
    status VARCHAR(32) NOT NULL DEFAULT 'pending' CHECK (status IN ('pending', 'approved', 'rejected')),
    decision_reason TEXT NULL,
    expires_at TIMESTAMPTZ NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
    decided_at TIMESTAMPTZ NULL,
    CONSTRAINT unique_run_step_approval UNIQUE (run_id, step_sequence)
);

CREATE INDEX IF NOT EXISTS idx_approvals_token ON approvals (token);

-- 6. Leases table (Worker concurrency control with fencing tokens)
CREATE TABLE IF NOT EXISTS leases (
    resource_id VARCHAR(128) PRIMARY KEY,
    worker_id VARCHAR(128) NOT NULL,
    claim_epoch BIGINT NOT NULL DEFAULT 1,
    acquired_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
    expires_at TIMESTAMPTZ NOT NULL
);

-- 7. Quota table (Shared per-tenant contention control)
CREATE TABLE IF NOT EXISTS quota (
    tenant_id VARCHAR(128) PRIMARY KEY,
    allocated_tokens BIGINT NOT NULL DEFAULT 100000,
    consumed_tokens BIGINT NOT NULL DEFAULT 0,
    allocated_requests INT NOT NULL DEFAULT 1000,
    consumed_requests INT NOT NULL DEFAULT 0,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp()
);

-- 8. LLM Calls & Budgets
CREATE TABLE IF NOT EXISTS budgets (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    run_id UUID NOT NULL REFERENCES runs(id) ON DELETE CASCADE,
    max_cost_cents INT NOT NULL DEFAULT 500,
    reserved_cost_cents INT NOT NULL DEFAULT 0,
    spent_cost_cents INT NOT NULL DEFAULT 0,
    created_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
    CONSTRAINT unique_run_budget UNIQUE (run_id)
);

CREATE TABLE IF NOT EXISTS llm_calls (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    run_id UUID NOT NULL REFERENCES runs(id) ON DELETE CASCADE,
    step_sequence INT NOT NULL,
    model VARCHAR(128) NOT NULL,
    estimated_tokens INT NOT NULL,
    actual_tokens INT NULL,
    estimated_cost_cents INT NOT NULL,
    actual_cost_cents INT NULL,
    status VARCHAR(32) NOT NULL DEFAULT 'reserved' CHECK (status IN ('reserved', 'completed', 'billed-unknown', 'failed')),
    billed_unknown BOOLEAN NOT NULL DEFAULT false,
    created_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
    completed_at TIMESTAMPTZ NULL
);

CREATE INDEX IF NOT EXISTS idx_llm_calls_run ON llm_calls (run_id, step_sequence);

-- migrate:down

DROP TABLE IF EXISTS llm_calls;
DROP TABLE IF EXISTS budgets;
DROP TABLE IF EXISTS quota;
DROP TABLE IF EXISTS leases;
DROP TABLE IF EXISTS approvals;
DROP TABLE IF EXISTS effects;
DROP TABLE IF EXISTS step_executions;
DROP TABLE IF EXISTS run_events;
DROP TABLE IF EXISTS runs;
