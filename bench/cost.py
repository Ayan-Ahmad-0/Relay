"""Cost Metering and Survivable Accounting Benchmark.

Simulates pre-call budget reservations, settles tokens inside step transactions,
handles simulated provider timeouts (billed-unknown), and computes reconciliation
deltas against mock provider invoice dashboards.
"""

from __future__ import annotations

import argparse
import logging
from typing import Any

from relay.cost import BudgetExceededError, CostLedger
from relay.db import close_connection_pool
from relay.runtime import ExecutionRuntime

logger = logging.getLogger("relay.cost_bench")


def run_cost_benchmark(total_runs: int = 30) -> dict[str, Any]:
    """Execute cost metering benchmark across varying provider conditions."""
    runtime = ExecutionRuntime()
    ledger = CostLedger()

    completed_calls = 0
    timeout_calls = 0
    budget_blocks = 0
    total_spent_cents = 0
    total_unknown_est_cents = 0

    print(f"\n--- Starting Cost Metering Benchmark ({total_runs} runs) ---")

    test_run_id = runtime.create_run({"benchmark": "cost"})
    ledger.init_budget(test_run_id, max_cost_cents=1000)  # $10 budget

    for i in range(total_runs):
        seq = i + 1
        est_tokens = 500
        est_cents = 15  # 15 cents estimated

        # Every 7th call simulates a provider socket timeout
        is_timeout = i % 7 == 0 and i > 0

        # Every 5th or 25th call tests budget exhaustion
        force_large = i == 5 or i == 25
        if force_large:
            est_cents = 2000  # Will trigger BudgetExceededError

        try:
            call_id = ledger.reserve_llm_budget(
                run_id=test_run_id,
                step_sequence=seq,
                model="gpt-4o-mini",
                estimated_tokens=est_tokens,
                estimated_cost_cents=est_cents,
            )

            if is_timeout:
                ledger.record_timeout_billed_unknown(call_id)
                timeout_calls += 1
                total_unknown_est_cents += est_cents
            else:
                actual_tokens = 480
                actual_cents = 12
                ledger.settle_llm_call(call_id, actual_tokens, actual_cents)
                completed_calls += 1
                total_spent_cents += actual_cents

        except BudgetExceededError:
            budget_blocks += 1

    # Simulate provider dashboard invoice reporting actual charges
    # Provider charged for completed calls + some timeout calls that succeeded upstream
    provider_dashboard_invoice = total_spent_cents + (timeout_calls * 10)
    recon = ledger.reconcile_dashboard(test_run_id, provider_dashboard_invoice)

    print("\n--- Cost Metering Benchmark Results ---")
    print(f"Total Iterations:             {total_runs}")
    print(f"Completed & Settled Calls:    {completed_calls}")
    print(f"Billed-Unknown Timeouts:      {timeout_calls}")
    print(f"Budget Exceeded Blocks:       {budget_blocks}")
    print(f"Internal Known Spent:         {recon['internal_known_spent_cents']}¢")
    print(f"Provider Dashboard Invoiced:  {recon['provider_invoiced_cents']}¢")
    print(f"Reconciliation Delta:         {recon['reconciliation_delta_cents']}¢")
    print(f"Reconciliation Valid:         {recon['reconciled']}")

    assert recon["reconciled"] is True, "Reconciliation delta exceeds billed-unknown bounds!"
    assert budget_blocks >= 1, "Budget ceiling failed to block over-budget reservation!"

    return {
        "completed_calls": completed_calls,
        "timeout_calls": timeout_calls,
        "budget_blocks": budget_blocks,
        "reconciliation": recon,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Relay Cost Metering Benchmark")
    parser.add_argument("--runs", type=int, default=30, help="Number of cost benchmark runs")
    args = parser.parse_args()

    try:
        run_cost_benchmark(total_runs=args.runs)
        print("\n[SUCCESS] Cost Metering Benchmark Passed!")
    finally:
        close_connection_pool()


if __name__ == "__main__":
    main()
