"""Database connection management and pooling using psycopg."""

from __future__ import annotations

import os
from collections.abc import Generator
from contextlib import contextmanager
from typing import Any

import psycopg
from psycopg.rows import dict_row
from psycopg_pool import ConnectionPool

DEFAULT_DATABASE_URL = "postgres://relay:relay@localhost:5432/relay_dev?sslmode=disable"

_pool: ConnectionPool | None = None


def get_database_url() -> str:
    return os.getenv("DATABASE_URL") or os.getenv("TEST_DATABASE_URL") or DEFAULT_DATABASE_URL


def get_connection_pool(min_size: int = 2, max_size: int = 20) -> ConnectionPool:
    """Lazily initialize and return the psycopg connection pool."""
    global _pool
    if _pool is None or _pool.closed:
        url = get_database_url()
        _pool = ConnectionPool(
            conninfo=url,
            min_size=min_size,
            max_size=max_size,
            open=True,
            kwargs={"row_factory": dict_row},
        )
    return _pool


def close_connection_pool() -> None:
    """Close active connection pool."""
    global _pool
    if _pool is not None and not _pool.closed:
        _pool.close()
        _pool = None


@contextmanager
def get_db_connection() -> Generator[psycopg.Connection[dict[str, Any]], None, None]:
    """Yield a connection from pool with dict_row factory."""
    pool = get_connection_pool()
    with pool.connection() as conn:
        yield conn


@contextmanager
def transaction(
    isolation_level: str | None = None,
) -> Generator[psycopg.Cursor[dict[str, Any]], None, None]:
    """Yield a transactional cursor, supporting SERIALIZABLE and REPEATABLE READ isolation."""
    with get_db_connection() as conn:
        with conn.transaction():
            with conn.cursor() as cur:
                if isolation_level:
                    cur.execute(f"SET TRANSACTION ISOLATION LEVEL {isolation_level};")
                yield cur
