"""Retry layer for Postgres SERIALIZABLE transactions, write-skew, and deadlock resolution."""

from __future__ import annotations

import logging
import random
import time
from collections.abc import Callable
from functools import wraps
from typing import Any, TypeVar

import psycopg.errors

logger = logging.getLogger("relay.retry")

T = TypeVar("T")

RETRIABLE_PG_ERRORS = (
    psycopg.errors.SerializationFailure,  # SQLSTATE 40001
    psycopg.errors.DeadlockDetected,  # SQLSTATE 40P01
)


def retry_on_concurrency_conflict(
    max_retries: int = 5,
    base_delay: float = 0.05,
    max_delay: float = 1.0,
    backoff_factor: float = 2.0,
) -> Callable[[Callable[..., T]], Callable[..., T]]:
    """Decorator retrying operations on Postgres serialization failure or deadlock.

    Applies truncated exponential backoff with full jitter to break lock synchronization.
    """

    def decorator(func: Callable[..., T]) -> Callable[..., T]:
        @wraps(func)
        def wrapper(*args: Any, **kwargs: Any) -> T:
            attempt = 0
            while True:
                try:
                    return func(*args, **kwargs)
                except RETRIABLE_PG_ERRORS as err:
                    attempt += 1
                    sqlstate = getattr(err, "sqlstate", type(err).__name__)
                    if attempt > max_retries:
                        logger.error(
                            f"Exceeded max concurrency retries ({max_retries}) for {func.__name__} "
                            f"(SQLSTATE {sqlstate}): {err}"
                        )
                        raise

                    # Truncated exponential backoff with jitter
                    delay = min(max_delay, base_delay * (backoff_factor ** (attempt - 1)))
                    jittered_delay = random.uniform(0, delay)
                    logger.warning(
                        f"Concurrency conflict ({sqlstate}) on attempt {attempt}/{max_retries} "
                        f"in {func.__name__}. Backing off for {jittered_delay:.4f}s..."
                    )
                    time.sleep(jittered_delay)

        return wrapper

    return decorator
