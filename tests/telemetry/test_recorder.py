import asyncio
import os
import tempfile
import pytest
from novelizer.canon.event_store import EventStore
from novelizer.telemetry.bus import TelemetryBus
from novelizer.telemetry.recorder import TelemetryRecorder
from novelizer.telemetry.events import (
    TelemetryEventType, AgentRunStarted, AgentRunFinished, AgentRunFailed,
    AgentRunTruncated, LlmCallStarted, LlmCallFinished, TokenDelta,
)


@pytest.fixture
async def rig():
    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    store = EventStore(path)
    await store.init()
    bus = TelemetryBus()
    yield store, bus, TelemetryRecorder(store, bus)
    await store.close()
    os.unlink(path)


async def test_emit_persists_and_mirrors_to_bus(rig):
    store, bus, rec = rig
    q = bus.subscribe()
    await rec.emit(TelemetryEventType.AGENT_RUN_STARTED, "r1",
                   AgentRunStarted(run_id="r1", agent_name="author"))
    persisted = await store.events_since(0)
    assert persisted[0].event_type == TelemetryEventType.AGENT_RUN_STARTED
    mirrored = q.get_nowait()
    assert mirrored.sequence == persisted[0].sequence
    assert mirrored.payload["agent_name"] == "author"


async def test_store_failure_warns_drops_and_still_mirrors(rig, caplog):
    store, bus, rec = rig
    q = bus.subscribe()
    await store.close()  # subsequent appends now raise
    await rec.emit(TelemetryEventType.AGENT_RUN_STARTED, "r1",
                   AgentRunStarted(run_id="r1", agent_name="author"))  # must not raise
    assert any("telemetry" in r.message for r in caplog.records)
    mirrored = q.get_nowait()  # bus mirror still fires (spec: graceful degradation)
    assert mirrored.sequence == -1
    assert mirrored.payload["agent_name"] == "author"
    store._conn = None  # keep the fixture's second close() harmless


async def test_publish_token_reaches_bus_but_never_the_store(rig):
    store, bus, rec = rig
    q = bus.subscribe()
    rec.publish_token(TokenDelta(run_id="r1", agent_name="author", text="The "))
    assert q.get_nowait().text == "The "
    assert await store.events_since(0) == []


async def test_in_llm_call_tracks_open_calls(rig):
    store, bus, rec = rig
    assert rec.in_llm_call("r1") is False
    await rec.emit(TelemetryEventType.LLM_CALL_STARTED, "r1",
                   LlmCallStarted(run_id="r1", agent_name="author", call_index=1,
                                  model="m", prompt="p"))
    assert rec.in_llm_call("r1") is True
    await rec.emit(TelemetryEventType.LLM_CALL_FINISHED, "r1",
                   LlmCallFinished(run_id="r1", agent_name="author", call_index=1,
                                   model="m", duration_s=1.0, output_tokens=5))
    assert rec.in_llm_call("r1") is False


async def test_next_call_index_counts_per_run_and_resets_at_run_end(rig):
    store, bus, rec = rig
    assert rec.next_call_index("r1") == 1
    assert rec.next_call_index("r1") == 2
    assert rec.next_call_index("r2") == 1
    await rec.emit(TelemetryEventType.AGENT_RUN_FINISHED, "r1",
                   AgentRunFinished(run_id="r1", agent_name="author", duration_s=1.0))
    assert rec.next_call_index("r1") == 1  # bookkeeping cleared


from novelizer.telemetry.recorder import run_with_identity
from agent_kit import current_agent_name, current_run_id


async def test_run_with_identity_emits_started_then_finished(rig):
    store, bus, rec = rig
    q = bus.subscribe()
    async with run_with_identity(rec, "research") as run_id:
        assert current_agent_name.get() == "research"
        assert current_run_id.get() == run_id
    started = q.get_nowait()
    finished = q.get_nowait()
    assert started.event_type == TelemetryEventType.AGENT_RUN_STARTED
    assert started.payload["agent_name"] == "research"
    assert started.payload["run_id"] == run_id
    assert finished.event_type == TelemetryEventType.AGENT_RUN_FINISHED
    assert finished.payload["run_id"] == run_id


async def test_run_with_identity_emits_failed_and_reraises(rig):
    store, bus, rec = rig
    q = bus.subscribe()
    with pytest.raises(ValueError):
        async with run_with_identity(rec, "chat:author"):
            raise ValueError("boom")
    q.get_nowait()  # started
    failed = q.get_nowait()
    assert failed.event_type == TelemetryEventType.AGENT_RUN_FAILED
    assert failed.payload["error_type"] == "ValueError"
    assert failed.payload["error_message"] == "boom"


async def test_run_with_identity_emits_cancelled_and_reraises(rig):
    """run_with_identity gives non-BaseAgent call sites (chat, research) the
    same contract run_once gives agents, so it owes the same guarantee: every
    started run reaches exactly one terminal event. CancelledError is a
    BaseException and used to escape the `except Exception` arm entirely."""
    store, bus, rec = rig
    q = bus.subscribe()
    with pytest.raises(asyncio.CancelledError):
        async with run_with_identity(rec, "chat:author"):
            raise asyncio.CancelledError()
    q.get_nowait()  # started
    cancelled = q.get_nowait()
    assert cancelled.event_type == TelemetryEventType.AGENT_RUN_CANCELLED
    assert cancelled.payload["phase"] == "agent"


async def test_truncation_is_not_a_run_end(rig):
    """agent.run_truncated annotates a run that is still going -- the run emits
    run_finished afterwards. Treating it as a run end would clear the per-run
    LLM-call bookkeeping mid-run, so the calls after the budget fired would be
    misnumbered and `in_llm_call` would go blind for the rest of the run."""
    store, bus, rec = rig
    assert rec.next_call_index("r1") == 1
    await rec.emit(TelemetryEventType.LLM_CALL_STARTED, "r1",
                   LlmCallStarted(run_id="r1", agent_name="author", call_index=1,
                                  model="m", prompt="p"))
    assert rec.in_llm_call("r1")
    await rec.emit(TelemetryEventType.AGENT_RUN_TRUNCATED, "r1",
                   AgentRunTruncated(run_id="r1", agent_name="author",
                                     stage="forced", tool_calls=41))
    assert rec.in_llm_call("r1"), "truncation must not close the run's open LLM call"
    assert rec.next_call_index("r1") == 2, "truncation must not reset call numbering"


async def test_run_with_identity_resets_context_vars_after(rig):
    store, bus, rec = rig
    assert current_agent_name.get() == ""
    assert current_run_id.get() is None
    async with run_with_identity(rec, "research"):
        pass
    assert current_agent_name.get() == ""
    assert current_run_id.get() is None


async def test_run_with_identity_resets_context_vars_on_exception(rig):
    store, bus, rec = rig
    with pytest.raises(RuntimeError):
        async with run_with_identity(rec, "research"):
            raise RuntimeError("x")
    assert current_agent_name.get() == ""
    assert current_run_id.get() is None


async def test_run_with_identity_is_a_no_op_with_no_telemetry():
    async with run_with_identity(None, "research") as run_id:
        assert current_agent_name.get() == "research"
        assert run_id  # still a real generated id, just nothing emitted
    assert current_agent_name.get() == ""
