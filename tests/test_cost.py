"""Tests for survivable cost metering, budget reservations, timeouts, and reconciliation."""

import pytest

from relay.cost import BudgetExceededError, CostLedger
from relay.runtime import ExecutionRuntime


@pytest.fixture
def runtime():
    return ExecutionRuntime()


@pytest.fixture
def ledger():
    return CostLedger()


def test_budget_reservation_and_step_settlement(runtime, ledger):
    run_id = runtime.create_run({"test": "cost_settle"})
    ledger.init_budget(run_id, max_cost_cents=200)

    # Reserve 25 cents
    call_id = ledger.reserve_llm_budget(
        run_id=run_id,
        step_sequence=1,
        model="gpt-4o",
        estimated_tokens=500,
        estimated_cost_cents=25,
    )
    assert call_id is not None

    # Settle call with actual 20 cents
    ledger.settle_llm_call(call_id, actual_tokens=450, actual_cost_cents=20)

    # Reconcile against provider invoice of 20 cents
    recon = ledger.reconcile_dashboard(run_id, provider_invoiced_cents=20)
    assert recon["internal_known_spent_cents"] == 20
    assert recon["reconciliation_delta_cents"] == 0
    assert recon["reconciled"] is True


def test_provider_timeout_billed_unknown_and_reconciliation(runtime, ledger):
    run_id = runtime.create_run({"test": "cost_timeout"})
    ledger.init_budget(run_id, max_cost_cents=100)

    call_id = ledger.reserve_llm_budget(
        run_id=run_id,
        step_sequence=1,
        model="gpt-4o",
        estimated_tokens=600,
        estimated_cost_cents=30,
    )

    # Simulate provider timeout
    ledger.record_timeout_billed_unknown(call_id)

    # Reconcile when provider dashboard billed 25 cents for the dropped socket call
    recon = ledger.reconcile_dashboard(run_id, provider_invoiced_cents=25)
    assert recon["billed_unknown_count"] == 1
    assert recon["billed_unknown_estimated_cents"] == 30
    assert recon["reconciliation_delta_cents"] == 25
    assert recon["reconciled"] is True


def test_budget_exceeded_error_blocking(runtime, ledger):
    run_id = runtime.create_run({"test": "cost_exceeded"})
    ledger.init_budget(run_id, max_cost_cents=50)

    # First call uses 40 cents
    c1 = ledger.reserve_llm_budget(
        run_id=run_id,
        step_sequence=1,
        model="gpt-4o",
        estimated_tokens=200,
        estimated_cost_cents=40,
    )
    ledger.settle_llm_call(c1, actual_tokens=200, actual_cost_cents=40)

    # Second call requesting 30 cents exceeds remaining 10 cents allowance
    with pytest.raises(BudgetExceededError) as exc_info:
        ledger.reserve_llm_budget(
            run_id=run_id,
            step_sequence=2,
            model="gpt-4o",
            estimated_tokens=300,
            estimated_cost_cents=30,
        )
    assert "Budget Exceeded" in str(exc_info.value)
