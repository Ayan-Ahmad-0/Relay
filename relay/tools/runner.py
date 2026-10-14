"""Isolated Subprocess Tool Runner.

Executes tool calls in isolated execution sandboxes with explicit timeout limits,
error isolation, and process termination on hung or runaway subprocesses.
"""

from __future__ import annotations

import multiprocessing as mp
import queue
from collections.abc import Callable
from typing import Any


class ToolTimeoutError(Exception):
    """Raised when an isolated tool subprocess exceeds its execution timeout."""

    pass


class ToolExecutionError(Exception):
    """Raised when an isolated tool raises an unhandled exception."""

    pass


def _worker_wrapper(
    target_func: Callable[..., Any],
    args: tuple[Any, ...],
    kwargs: dict[str, Any],
    out_q: mp.Queue[Any],
) -> None:
    try:
        result = target_func(*args, **kwargs)
        out_q.put(("ok", result))
    except Exception as err:
        out_q.put(("err", str(err), type(err).__name__))


def run_isolated_tool(
    func: Callable[..., Any],
    *args: Any,
    timeout_seconds: float = 5.0,
    **kwargs: Any,
) -> Any:
    """Execute a tool function inside an isolated spawn subprocess with hard timeout.

    If tool exceeds timeout, the worker process is killed immediately to prevent leaks.
    """
    ctx = mp.get_context("spawn")
    out_q = ctx.Queue()

    proc = ctx.Process(target=_worker_wrapper, args=(func, args, kwargs, out_q))
    proc.start()
    proc.join(timeout=timeout_seconds)

    if proc.is_alive():
        proc.terminate()
        proc.join(timeout=1.0)
        if proc.is_alive():
            proc.kill()
        raise ToolTimeoutError(f"Tool execution timed out after {timeout_seconds:.1f}s")

    try:
        msg = out_q.get_nowait()
    except queue.Empty as err:
        raise ToolExecutionError("Tool subprocess terminated without producing output") from err

    if msg[0] == "ok":
        return msg[1]
    else:
        err_msg, err_type = msg[1], msg[2]
        raise ToolExecutionError(f"Tool subprocess failed with {err_type}: {err_msg}")
