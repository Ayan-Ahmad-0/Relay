"""Pytest fixtures and database test harness."""

import pytest

from relay.db import get_db_connection


@pytest.fixture(autouse=True)
def clean_database():
    """Ensure clean isolation between tests by truncating tables before each test."""
    with get_db_connection() as conn:
        with conn.transaction():
            with conn.cursor() as cur:
                cur.execute(
                    """
                    TRUNCATE TABLE
                        run_events,
                        step_executions,
                        effects,
                        approvals,
                        llm_calls,
                        budgets,
                        runs,
                        leases,
                        quota
                    CASCADE;
                    """
                )
    yield
