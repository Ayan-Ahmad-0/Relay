"""Survivable Cost Metering and Budget Reservation Ledger.

Tracks pre-call token/cost reservations in PostgreSQL, updates tallies inside
step transactions, flags provider timeouts as billed-unknown, and computes
reconciliation deltas against provider billing invoices.
"""

from __future__ import annotations

from typing import Any
from uuid import UUID

from relay.db import transaction


class BudgetExceededError(Exception):
    """Raised when an LLM call exceeds the allocated budget ceiling for a run."""

    pass


class CostLedger:
    def init_budget(self, run_id: UUID, max_cost_cents: int = 500) -> None:
        """Initialize budget ledger entry for a run."""
        with transaction() as cur:
            cur.execute(
                """
                INSERT INTO budgets (run_id, max_cost_cents, reserved_cost_cents, spent_cost_cents)
                VALUES (%s, %s, 0, 0)
                ON CONFLICT (run_id) DO UPDATE
                SET max_cost_cents = EXCLUDED.max_cost_cents;
                """,
                (run_id, max_cost_cents),
            )

    def reserve_llm_budget(
        self,
        run_id: UUID,
        step_sequence: int,
        model: str,
        estimated_tokens: int,
        estimated_cost_cents: int,
    ) -> UUID:
        """Reserve budget before initiating an external LLM provider call."""
        with transaction() as cur:
            cur.execute(
                """
                SELECT max_cost_cents, reserved_cost_cents, spent_cost_cents
                FROM budgets
                WHERE run_id = %s
                FOR UPDATE;
                """,
                (run_id,),
            )
            budget = cur.fetchone()
            if not budget:
                # Default init if not explicitly created
                cur.execute(
                    """
                    INSERT INTO budgets (
                        run_id, max_cost_cents, reserved_cost_cents, spent_cost_cents
                    )
                    VALUES (%s, 500, 0, 0)
                    RETURNING max_cost_cents, reserved_cost_cents, spent_cost_cents;
                    """,
                    (run_id,),
                )
                budget = cur.fetchone()
                assert budget is not None

            projected_total = (
                budget["spent_cost_cents"] + budget["reserved_cost_cents"] + estimated_cost_cents
            )
            if projected_total > budget["max_cost_cents"]:
                raise BudgetExceededError(
                    f"Budget Exceeded on run {run_id}: projected cost {projected_total}¢ "
                    f"exceeds ceiling of {budget['max_cost_cents']}¢"
                )

            # Hold reservation
            cur.execute(
                """
                UPDATE budgets
                SET reserved_cost_cents = reserved_cost_cents + %s,
                    updated_at = clock_timestamp()
                WHERE run_id = %s;
                """,
                (estimated_cost_cents, run_id),
            )

            # Log reservation call
            cur.execute(
                """
                INSERT INTO llm_calls (
                    run_id, step_sequence, model, estimated_tokens,
                    estimated_cost_cents, status, created_at
                )
                VALUES (%s, %s, %s, %s, %s, 'reserved', clock_timestamp())
                RETURNING id;
                """,
                (run_id, step_sequence, model, estimated_tokens, estimated_cost_cents),
            )
            row = cur.fetchone()
            assert row is not None
            return row["id"]

    def settle_llm_call(
        self,
        llm_call_id: UUID,
        actual_tokens: int,
        actual_cost_cents: int,
    ) -> None:
        """Settle LLM call inside step transaction.

        Decrements reservation and increments spent amount.
        """
        with transaction() as cur:
            cur.execute(
                """
                UPDATE llm_calls
                SET status = 'completed',
                    actual_tokens = %s,
                    actual_cost_cents = %s,
                    completed_at = clock_timestamp()
                WHERE id = %s
                RETURNING run_id, estimated_cost_cents;
                """,
                (actual_tokens, actual_cost_cents, llm_call_id),
            )
            call_row = cur.fetchone()
            if not call_row:
                return

            run_id = call_row["run_id"]
            est_cost = call_row["estimated_cost_cents"]

            cur.execute(
                """
                UPDATE budgets
                SET reserved_cost_cents = GREATEST(0, reserved_cost_cents - %s),
                    spent_cost_cents = spent_cost_cents + %s,
                    updated_at = clock_timestamp()
                WHERE run_id = %s;
                """,
                (est_cost, actual_cost_cents, run_id),
            )

    def record_timeout_billed_unknown(self, llm_call_id: UUID) -> None:
        """Mark timed-out provider call as billed-unknown and release reservation."""
        with transaction() as cur:
            cur.execute(
                """
                UPDATE llm_calls
                SET status = 'billed-unknown',
                    billed_unknown = true,
                    completed_at = clock_timestamp()
                WHERE id = %s
                RETURNING run_id, estimated_cost_cents;
                """,
                (llm_call_id,),
            )
            call_row = cur.fetchone()
            if not call_row:
                return

            run_id = call_row["run_id"]
            est_cost = call_row["estimated_cost_cents"]

            cur.execute(
                """
                UPDATE budgets
                SET reserved_cost_cents = GREATEST(0, reserved_cost_cents - %s),
                    updated_at = clock_timestamp()
                WHERE run_id = %s;
                """,
                (est_cost, run_id),
            )

    def reconcile_dashboard(
        self,
        run_id: UUID,
        provider_invoiced_cents: int,
    ) -> dict[str, Any]:
        """Output reconciliation delta between internal ledger and provider invoice dashboard."""
        with transaction() as cur:
            cur.execute(
                """
                SELECT
                    COALESCE(SUM(actual_cost_cents), 0) AS known_spent_cents,
                    COALESCE(
                        COUNT(*) FILTER (WHERE billed_unknown = true), 0
                    ) AS unknown_count,
                    COALESCE(
                        SUM(estimated_cost_cents) FILTER (WHERE billed_unknown = true), 0
                    ) AS unknown_estimated_cents
                FROM llm_calls
                WHERE run_id = %s;
                """,
                (run_id,),
            )
            summary = cur.fetchone()
            assert summary is not None

            known_spent = summary["known_spent_cents"]
            unknown_count = summary["unknown_count"]
            unknown_est = summary["unknown_estimated_cents"]

            # Delta calculation
            delta = provider_invoiced_cents - known_spent
            reconciled = delta >= 0 and delta <= unknown_est

            return {
                "run_id": str(run_id),
                "internal_known_spent_cents": known_spent,
                "provider_invoiced_cents": provider_invoiced_cents,
                "billed_unknown_count": unknown_count,
                "billed_unknown_estimated_cents": unknown_est,
                "reconciliation_delta_cents": delta,
                "reconciled": reconciled or (delta == 0),
            }
