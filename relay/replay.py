"""Event-Sourced Deterministic Replayer Engine and Nondeterminism Guard.

During replay, external calls (LLM outputs, tool responses, clocks, randomness) are
intercepted and served strictly from the immutable run_events ledger.
Any divergence in call order, count, or event type raises NondeterminismError loudly.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any
from uuid import UUID

from relay.db import get_db_connection
from relay.models import EventType, RunEventRecord


class NondeterminismError(Exception):
    """Raised when replay diverges from the recorded event sequence
    or attempts live non-determinism.
    """

    pass


class ReplaySession:
    """Consumes recorded run_events in strict sequence order during replay."""

    def __init__(self, run_id: UUID, events: list[RunEventRecord]) -> None:
        self.run_id = run_id
        self.events = events
        self.cursor = 0

    @classmethod
    def from_database(cls, run_id: UUID) -> ReplaySession:
        """Load recorded events for a run ordered strictly by sequence_num ASC."""
        with get_db_connection() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT id, run_id, sequence_num, event_type, payload, recorded_at
                    FROM run_events
                    WHERE run_id = %s
                    ORDER BY sequence_num ASC;
                    """,
                    (run_id,),
                )
                rows = cur.fetchall()

        events = [
            RunEventRecord(
                id=row["id"],
                run_id=row["run_id"],
                sequence_num=row["sequence_num"],
                event_type=EventType(row["event_type"]),
                payload=row["payload"]
                if isinstance(row["payload"], dict)
                else json.loads(row["payload"]),
                recorded_at=row["recorded_at"],
            )
            for row in rows
        ]
        return cls(run_id=run_id, events=events)

    def next_event(self, expected_type: EventType) -> dict[str, Any]:
        """Fetch next recorded event in strict sequence order.

        Enforces the Nondeterminism Guard: fails loudly if sequence is exhausted
        or event_type does not match.
        """
        if self.cursor >= len(self.events):
            raise NondeterminismError(
                f"Nondeterminism divergence on run {self.run_id}: Event log exhausted at sequence "
                f"{self.cursor + 1}, but code requested event of type '{expected_type}'"
            )

        recorded = self.events[self.cursor]
        expected_seq = self.cursor + 1

        if recorded.sequence_num != expected_seq:
            raise NondeterminismError(
                f"Event sequence corruption: expected seq {expected_seq}, "
                f"recorded seq {recorded.sequence_num}"
            )

        if recorded.event_type != expected_type:
            raise NondeterminismError(
                f"Nondeterminism Guard Triggered on run {self.run_id} at sequence {expected_seq}: "
                f"Recorded event is '{recorded.event_type}', but replay requested '{expected_type}'"
            )

        self.cursor += 1
        return recorded.payload

    def assert_fully_replayed(self) -> None:
        """Ensure all recorded events were consumed without unexecuted trailing events."""
        if self.cursor < len(self.events):
            remaining = [e.event_type.value for e in self.events[self.cursor :]]
            raise NondeterminismError(
                f"Incomplete replay on run {self.run_id}: {len(self.events) - self.cursor} "
                f"unconsumed recorded events remain: {remaining}"
            )


class ExecutionContext:
    """Unified execution environment for both live runs and deterministic replay."""

    def __init__(
        self,
        run_id: UUID,
        claim_epoch: int,
        runtime: Any,
        replay_session: ReplaySession | None = None,
    ) -> None:
        self.run_id = run_id
        self.claim_epoch = claim_epoch
        self.runtime = runtime
        self.replay_session = replay_session
        self.is_replay = replay_session is not None
        self._seq = 0

    def get_clock(self, allow_live_on_replay: bool = False) -> str:
        """Get timestamp.

        In live mode: reads system clock and appends to run_events.
        In replay mode: reads strictly from recorded run_events.
        If allow_live_on_replay is True (used in deliberate failure tests),
        it bypasses the recorded event, which causes the Nondeterminism Guard to trip!
        """
        if self.is_replay:
            if allow_live_on_replay:
                # Deliberate failure test injection
                return datetime.now(UTC).isoformat()
            assert self.replay_session is not None
            payload = self.replay_session.next_event(EventType.CLOCK_READ)
            return payload["iso"]

        # Live execution mode
        self._seq += 1
        now_iso = datetime.now(UTC).isoformat()
        self.runtime.record_event(
            run_id=self.run_id,
            claim_epoch=self.claim_epoch,
            sequence_num=self._seq,
            event_type=EventType.CLOCK_READ,
            payload={"iso": now_iso},
        )
        return now_iso

    def get_random_value(self, supplier: Callable[[], Any]) -> Any:
        """Obtain a random or non-deterministic value."""
        if self.is_replay:
            assert self.replay_session is not None
            payload = self.replay_session.next_event(EventType.RANDOM_VALUE)
            return payload["value"]

        self._seq += 1
        val = supplier()
        self.runtime.record_event(
            run_id=self.run_id,
            claim_epoch=self.claim_epoch,
            sequence_num=self._seq,
            event_type=EventType.RANDOM_VALUE,
            payload={"value": val},
        )
        return val

    def call_tool(
        self, tool_name: str, tool_func: Callable[..., dict[str, Any]], *args: Any, **kwargs: Any
    ) -> dict[str, Any]:
        """Execute or replay a tool call."""
        if self.is_replay:
            assert self.replay_session is not None
            payload = self.replay_session.next_event(EventType.TOOL_RESULT)
            if payload.get("tool_name") != tool_name:
                raise NondeterminismError(
                    f"Tool divergence: expected tool '{tool_name}', "
                    f"recorded tool '{payload.get('tool_name')}'"
                )
            return payload["result"]

        self._seq += 1
        res = tool_func(*args, **kwargs)
        self.runtime.record_event(
            run_id=self.run_id,
            claim_epoch=self.claim_epoch,
            sequence_num=self._seq,
            event_type=EventType.TOOL_RESULT,
            payload={"tool_name": tool_name, "result": res},
        )
        return res

    def call_model(
        self, prompt: str, model_func: Callable[[str], dict[str, Any]]
    ) -> dict[str, Any]:
        """Execute or replay an LLM inference step."""
        if self.is_replay:
            assert self.replay_session is not None
            payload = self.replay_session.next_event(EventType.MODEL_OUTPUT)
            return payload["output"]

        self._seq += 1
        output = model_func(prompt)
        self.runtime.record_event(
            run_id=self.run_id,
            claim_epoch=self.claim_epoch,
            sequence_num=self._seq,
            event_type=EventType.MODEL_OUTPUT,
            payload={"prompt": prompt, "output": output},
        )
        return output
