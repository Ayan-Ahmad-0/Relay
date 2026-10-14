"""Tests for event-sourced deterministic replay and the Nondeterminism Guard."""

import pytest

from relay.replay import ExecutionContext, NondeterminismError, ReplaySession
from relay.runtime import ExecutionRuntime


@pytest.fixture
def runtime():
    return ExecutionRuntime()


def test_deterministic_recording_and_replaying(runtime):
    """Events are recorded in live run and replayed without calling suppliers."""
    run_id = runtime.create_run({"test": "replay"})
    ctx_live = runtime.claim_run(worker_id="worker-record")
    assert ctx_live is not None

    exec_live = ExecutionContext(
        run_id=run_id,
        claim_epoch=ctx_live.claim_epoch,
        runtime=runtime,
    )

    # 1. Clock read
    t1 = exec_live.get_clock()
    # 2. Tool call
    tool_calls = 0

    def mock_retrieve_policy(doc_id: str):
        nonlocal tool_calls
        tool_calls += 1
        return {"policy": "30-day return window", "doc_id": doc_id}

    res1 = exec_live.call_tool("retrieve", mock_retrieve_policy, doc_id="policy-return")
    assert tool_calls == 1

    # 3. Model call
    model_calls = 0

    def mock_llm(prompt: str):
        nonlocal model_calls
        model_calls += 1
        return {"classification": "return_request"}

    model_res1 = exec_live.call_model("Classify ticket", mock_llm)
    assert model_calls == 1

    # Replay Phase
    replay_session = ReplaySession.from_database(run_id)
    exec_replay = ExecutionContext(
        run_id=run_id,
        claim_epoch=1,
        runtime=runtime,
        replay_session=replay_session,
    )

    # Replay clock -> must equal exact recorded t1
    t1_replay = exec_replay.get_clock()
    assert t1_replay == t1

    # Replay tool -> must return cached result WITHOUT incrementing tool_calls
    res1_replay = exec_replay.call_tool("retrieve", mock_retrieve_policy, doc_id="policy-return")
    assert res1_replay == res1
    assert tool_calls == 1  # Unchanged!

    # Replay model -> must return cached result WITHOUT incrementing model_calls
    model_res1_replay = exec_replay.call_model("Classify ticket", mock_llm)
    assert model_res1_replay == model_res1
    assert model_calls == 1  # Unchanged!

    replay_session.assert_fully_replayed()


def test_nondeterminism_guard_catches_divergence(runtime):
    """Guard raises NondeterminismError if replay steps diverge from recorded sequence."""
    run_id = runtime.create_run({"test": "divergence"})
    ctx_live = runtime.claim_run(worker_id="worker-record-2")
    assert ctx_live is not None

    exec_live = ExecutionContext(
        run_id=run_id,
        claim_epoch=ctx_live.claim_epoch,
        runtime=runtime,
    )

    # Live recorded sequence: clock -> model
    exec_live.get_clock()
    exec_live.call_model("Prompt 1", lambda p: {"answer": "A"})

    # Replayer expects clock first, but replay code attempts model call first!
    replay_session = ReplaySession.from_database(run_id)
    exec_replay = ExecutionContext(
        run_id=run_id,
        claim_epoch=1,
        runtime=runtime,
        replay_session=replay_session,
    )

    with pytest.raises(NondeterminismError) as exc_info:
        exec_replay.call_model("Prompt 1", lambda p: {"answer": "A"})

    assert "Nondeterminism Guard Triggered" in str(exc_info.value)
    assert "clock_read" in str(exc_info.value)


def test_deliberate_live_clock_read_fails_guard_loudly(runtime):
    """Deliberate test step: reading live system clock instead of replaying fails loudly."""
    run_id = runtime.create_run({"test": "live_clock"})
    ctx_live = runtime.claim_run(worker_id="worker-live-clock")
    assert ctx_live is not None

    exec_live = ExecutionContext(
        run_id=run_id,
        claim_epoch=ctx_live.claim_epoch,
        runtime=runtime,
    )

    # Live recorded: tool call
    exec_live.call_tool("extract", lambda: {"amount": 100})

    replay_session = ReplaySession.from_database(run_id)
    exec_replay = ExecutionContext(
        run_id=run_id,
        claim_epoch=1,
        runtime=runtime,
        replay_session=replay_session,
    )

    # Code attempts a clock read during replay when the recorded event is a tool_result!
    with pytest.raises(NondeterminismError) as exc_info:
        exec_replay.get_clock()

    assert "Nondeterminism Guard Triggered" in str(exc_info.value)
    assert "tool_result" in str(exc_info.value)
