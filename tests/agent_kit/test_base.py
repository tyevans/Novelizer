from __future__ import annotations
import time

import pytest
from hypothesis import given, strategies as st

from agent_kit.base import BaseAgent, PASS_BACKOFF_MULTIPLIER
from agent_kit.run_context import current_agent_name, current_run_id
from agent_kit.telemetry import TelemetryEventType


class NullRunner:
    async def ainvoke(self, inputs: dict) -> dict:
        return {}


class FakeEmitter:
    """Records every emit; in_llm_call is scriptable for phase tests."""

    def __init__(self, llm_call_run_ids: set[str] | None = None) -> None:
        self.events: list[tuple[str, str, object]] = []
        self._llm = llm_call_run_ids or set()

    async def emit(self, event_type: str, aggregate_id: str, payload) -> None:
        self.events.append((event_type, aggregate_id, payload))

    def in_llm_call(self, run_id: str) -> bool:
        return run_id in self._llm or "*" in self._llm


# --- interval / backoff machinery ---------------------------------------

@given(interval=st.integers(min_value=1, max_value=10_000),
       elapsed=st.floats(min_value=0, max_value=100_000, allow_nan=False))
def test_ready_for_interval_iff_interval_elapsed(interval, elapsed):
    agent = BaseAgent(NullRunner(), interval=interval)
    agent.mark_ran(1000.0)
    now = 1000.0 + elapsed
    assert agent.ready_for_interval(now) == (elapsed >= interval)


@given(interval=st.integers(min_value=1, max_value=10_000),
       elapsed=st.floats(min_value=0, max_value=100_000, allow_nan=False))
def test_seconds_until_ready_zero_iff_ready(interval, elapsed):
    agent = BaseAgent(NullRunner(), interval=interval)
    agent.mark_ran(1000.0)
    now = 1000.0 + elapsed
    remaining = agent.seconds_until_ready(now)
    assert remaining >= 0.0
    assert (remaining == 0.0) == agent.ready_for_interval(now)


@given(interval=st.integers(min_value=1, max_value=10_000))
def test_note_pass_backs_off_multiplier_intervals(interval):
    agent = BaseAgent(NullRunner(), interval=interval)
    agent.mark_ran(1000.0)
    agent.note_pass(now=1000.0)
    just_before = 1000.0 + interval * PASS_BACKOFF_MULTIPLIER - 0.001
    at = 1000.0 + interval * PASS_BACKOFF_MULTIPLIER
    assert not agent.ready_for_interval(just_before)
    assert agent.ready_for_interval(at)


def test_note_pass_defaults_to_monotonic_now():
    agent = BaseAgent(NullRunner(), interval=10)
    agent.note_pass()
    assert not agent.ready_for_interval(time.monotonic())


def test_pause_resume_flag():
    agent = BaseAgent(NullRunner(), interval=1)
    assert agent.paused is False
    agent.pause()
    assert agent.paused is True
    agent.resume()
    assert agent.paused is False


def test_constructor_sets_name_and_personality():
    agent = BaseAgent(NullRunner(), interval=5, name="scout", personality="terse")
    assert agent.name == "scout"
    assert agent.personality == "terse"
    assert agent.interval == 5
    assert agent.telemetry is None


def test_guarded_line():
    assert BaseAgent._guarded_line("Mood", "wry") == "\n\nMood: wry"
    assert BaseAgent._guarded_line("Mood", "") == ""


# --- watermark gating -----------------------------------------------------

class FingerprintAgent(BaseAgent):
    def __init__(self, fp):
        super().__init__(NullRunner(), interval=1, name="fp")
        self.fp = fp

    async def _fingerprint(self):
        return self.fp


async def test_watermark_none_never_gates():
    agent = FingerprintAgent(None)
    assert await agent._gate_on_watermark(0.7) == 0.7
    await agent._record_watermark()
    assert await agent._gate_on_watermark(0.7) == 0.7


async def test_watermark_gates_unchanged_fingerprint_and_rearms():
    agent = FingerprintAgent(("a",))
    assert await agent._gate_on_watermark(0.7) == 0.7
    await agent._record_watermark()
    assert await agent._gate_on_watermark(0.7) == 0.0
    agent.fp = ("b",)
    assert await agent._gate_on_watermark(0.7) == 0.7
    agent.fp = ("a",)
    agent._clear_watermark()
    assert await agent._gate_on_watermark(0.7) == 0.7


# --- run_once telemetry bracketing ---------------------------------------

class RecordingAgent(BaseAgent):
    def __init__(self, fail: Exception | None = None):
        super().__init__(NullRunner(), interval=1, name="rec")
        self._fail = fail
        self.seen_run_id: str | None = None
        self.seen_agent_name: str | None = None

    async def _run(self):
        self.seen_run_id = current_run_id.get()
        self.seen_agent_name = current_agent_name.get()
        if self._fail:
            raise self._fail


async def test_run_once_success_emits_started_then_finished():
    agent = RecordingAgent()
    emitter = FakeEmitter()
    agent.telemetry = emitter
    await agent.run_once()
    types = [e[0] for e in emitter.events]
    assert types == [TelemetryEventType.AGENT_RUN_STARTED, TelemetryEventType.AGENT_RUN_FINISHED]
    started, finished = emitter.events[0][2], emitter.events[1][2]
    assert started.agent_name == "rec"
    assert finished.run_id == started.run_id
    assert finished.duration_s >= 0.0
    # contextvars visible inside _run, reset after
    assert agent.seen_run_id == started.run_id
    assert agent.seen_agent_name == "rec"
    assert current_run_id.get() is None
    assert current_agent_name.get() == ""


async def test_run_once_failure_emits_failed_and_reraises():
    agent = RecordingAgent(fail=ValueError("boom"))
    emitter = FakeEmitter()
    agent.telemetry = emitter
    with pytest.raises(ValueError, match="boom"):
        await agent.run_once()
    types = [e[0] for e in emitter.events]
    assert types == [TelemetryEventType.AGENT_RUN_STARTED, TelemetryEventType.AGENT_RUN_FAILED]
    failed = emitter.events[1][2]
    assert failed.error_type == "ValueError"
    assert failed.error_message == "boom"
    assert failed.phase == "agent"


async def test_run_once_failure_phase_llm_call_when_recorder_says_so():
    agent = RecordingAgent(fail=RuntimeError("llm died"))
    agent.telemetry = FakeEmitter(llm_call_run_ids={"*"})
    with pytest.raises(RuntimeError):
        await agent.run_once()
    failed = agent.telemetry.events[1][2]
    assert failed.phase == "llm_call"


async def test_run_once_without_telemetry_is_silent_noop():
    agent = RecordingAgent()
    await agent.run_once()  # must not raise
    assert agent.seen_agent_name == "rec"
