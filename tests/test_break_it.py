"""Unit test for Phase 6 break-it sabotage verification.

Asserts that intentionally sabotaging any of the 3 foundational safety properties:
  1. Fencing token check
  2. Nondeterminism guard
  3. Idempotency key generation
causes immediate, loud test failures.
"""

from scripts.verify_sabotage import SABOTAGE_EXPERIMENTS, run_experiment


def test_all_break_it_sabotages_are_caught():
    for exp in SABOTAGE_EXPERIMENTS:
        caught = run_experiment(exp)
        assert caught, f"Sabotage {exp['name']} was NOT caught by test suite!"
