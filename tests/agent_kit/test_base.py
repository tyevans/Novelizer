from __future__ import annotations
import asyncio
import math
import time

import pytest
from hypothesis import given, settings, strategies as st

from agent_kit import base as kit_base
from agent_kit.base import BaseAgent
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


# --- the interval machinery is gone --------------------------------------
#
# `interval` stops being a gate. The CONSTRUCTOR PARAMETER stays, because all
# eleven agents and AGENT_REGISTRY pass one and the settings keys that feed it
# have to keep loading; it simply stops affecting anything.

def test_interval_gating_methods_are_gone():
    """A method that still exists is a method something can still call. These
    two were the clock gate: `ready_for_interval` was the filter that ran
    before agents were ever scored, and `mark_ran` was how a dispatch charged
    an agent for the time it had not yet spent."""
    assert not hasattr(BaseAgent, "ready_for_interval")
    assert not hasattr(BaseAgent, "mark_ran")


def test_interval_gating_state_is_gone():
    agent = BaseAgent(NullRunner(), interval=10)
    assert not hasattr(agent, "_last_run")
    assert not hasattr(agent, "_backoff_until")


def test_interval_multiplier_constants_are_gone():
    """Backoff is expressed in absolute seconds now. A multiplier over an
    operator-configured interval has nothing left to multiply, and leaving the
    constants importable would invite a caller to reintroduce the unit."""
    assert not hasattr(kit_base, "PASS_BACKOFF_MULTIPLIER")
    assert not hasattr(kit_base, "RATE_LIMIT_BACKOFF_MULTIPLIER")


def test_interval_is_still_accepted_and_stored_but_inert():
    """Compatibility: every agent still passes an interval and the TUI still
    displays it. It must construct, round-trip, and change nothing."""
    agent = BaseAgent(NullRunner(), interval=900)
    assert agent.interval == 900
    assert agent.ready(0.0)
    assert agent.seconds_until_ready(0.0) == 0.0


@given(interval=st.integers(min_value=0, max_value=100_000),
       now=st.floats(min_value=0, max_value=100_000, allow_nan=False))
def test_no_interval_value_can_hold_an_agent_back(interval, now):
    """Asserted over the whole range, absurd values included: there is no
    interval large enough to make a fresh agent wait."""
    agent = BaseAgent(NullRunner(), interval=interval)
    assert agent.ready(now)
    assert agent.seconds_until_ready(now) == 0.0


def test_note_pass_defaults_to_monotonic_now():
    agent = BaseAgent(NullRunner(), interval=10)
    agent.note_pass()
    assert not agent.ready(time.monotonic())


def test_note_pass_uses_injected_clock():
    agent = BaseAgent(NullRunner(), interval=10, clock=lambda: 1000.0)
    agent.note_pass()  # no arg -> injected clock, not real monotonic
    assert not agent.ready(1000.0 + kit_base.IDLE_BACKOFF_BASE_S - 0.001)
    assert agent.ready(1000.0 + kit_base.IDLE_BACKOFF_BASE_S)


def test_default_clock_is_monotonic():
    agent = BaseAgent(NullRunner(), interval=10)
    agent.note_pass()
    assert not agent.ready(time.monotonic())


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


async def test_run_once_cancellation_emits_cancelled_and_reraises():
    """CancelledError inherits BaseException, so the broad `except Exception`
    never saw it: a cancelled run emitted neither a finished nor a failed
    event and simply vanished, leaving every starts-minus-terminals consumer
    drifting upward forever. Every run now reaches exactly one terminal
    event, and the cancellation still propagates -- swallowing it would
    break cooperative cancellation and hang shutdown."""
    agent = RecordingAgent(fail=asyncio.CancelledError())
    emitter = FakeEmitter()
    agent.telemetry = emitter
    with pytest.raises(asyncio.CancelledError):
        await agent.run_once()
    types = [e[0] for e in emitter.events]
    assert types == [TelemetryEventType.AGENT_RUN_STARTED,
                     TelemetryEventType.AGENT_RUN_CANCELLED]
    started, cancelled = emitter.events[0][2], emitter.events[1][2]
    assert cancelled.run_id == started.run_id
    assert cancelled.agent_name == "rec"
    assert cancelled.phase == "agent"
    assert cancelled.duration_s >= 0.0
    # The contextvars are still unwound: the finally arm runs on BaseException.
    assert current_run_id.get() is None
    assert current_agent_name.get() == ""


async def test_run_once_cancellation_phase_llm_call_when_recorder_says_so():
    agent = RecordingAgent(fail=asyncio.CancelledError())
    emitter = FakeEmitter(llm_call_run_ids={"*"})
    agent.telemetry = emitter
    with pytest.raises(asyncio.CancelledError):
        await agent.run_once()
    assert emitter.events[1][2].phase == "llm_call"


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


# --- rate-limit backoff ---------------------------------------------------

def _rate_limit_error():
    import httpx
    import openai
    resp = httpx.Response(
        429, request=httpx.Request("POST", "http://localhost:9999/v1/chat/completions"))
    return openai.RateLimitError("Too many requests", response=resp, body=None)


class BackoffProbe(BaseAgent):
    def __init__(self, fail: Exception, interval: int = 10, clock=lambda: 1000.0):
        super().__init__(NullRunner(), interval=interval, name="probe", clock=clock)
        self._fail = fail

    async def _run(self):
        raise self._fail


async def test_rate_limit_backoff_is_no_longer_measured_in_intervals():
    """A 429 still backs the agent off, but on the seconds-based fail ladder.
    A fifteen-minute interval must not make the step-back forty-five minutes
    long -- the backoff is now bounded by the ladder cap, whatever the
    operator configured."""
    import openai
    agent = BackoffProbe(_rate_limit_error(), interval=900)
    with pytest.raises(openai.RateLimitError):
        await agent.run_once()
    assert not agent.ready(1000.0)
    assert 0.0 < agent.seconds_until_ready(1000.0) <= kit_base.FAIL_BACKOFF_CAP_S


def test_rate_limit_error_is_recognized_directly():
    """The detector is provider-agnostic by design (class name + status code),
    and it is what the AIMD pool will consult; it stays under test on its own
    now that no interval multiplier makes its effect observable."""
    assert kit_base._is_rate_limit_error(_rate_limit_error()) is True


def test_rate_limit_error_is_recognized_through_a_wrapper_chain():
    """Frameworks re-raise provider errors with the original as __cause__ or
    __context__; detection must see through one such wrapper chain."""
    wrapper = RuntimeError("graph step failed")
    wrapper.__cause__ = _rate_limit_error()
    assert kit_base._is_rate_limit_error(wrapper) is True
    assert kit_base._is_rate_limit_error(ValueError("boom")) is False


# --- progress probe + second-based backoff ladders -------------------------
#
# Everything below is the event-driven-scheduling chassis, and it is now the
# ONLY thing gating dispatch: `interval`, `_last_run`, `ready_for_interval()`
# and `mark_ran()` are gone, and the scheduler dispatches on `ready()` alone.
#
# The ladder constants are reached through `kit_base.X` rather than a top-level
# `from agent_kit.base import X` on purpose: a module-level import of a name
# that has moved or vanished would fail this whole file at collection and take
# every test above down with it, hiding which tests are actually red.


class FakeClock:
    """Settable stand-in for time.monotonic. Both ladders are absolute
    deadlines against the injected clock, so tests move time rather than
    sleeping through a 300s cap."""

    def __init__(self, now: float = 1000.0) -> None:
        self.now = now

    def __call__(self) -> float:
        return self.now

    def advance(self, dt: float) -> None:
        self.now += dt


class ProbeSpy:
    """Async progress probe with a scripted verdict. Records every run id it
    was handed so tests can prove the probe is asked about the run that just
    finished and not some other one."""

    def __init__(self, answer: bool = True, raises: BaseException | None = None) -> None:
        self.run_ids: list[str] = []
        self.answer = answer  # flip mid-test to change the verdict
        self._raises = raises

    async def __call__(self, run_id: str) -> bool:
        self.run_ids.append(run_id)
        if self._raises is not None:
            raise self._raises
        return self.answer

    @property
    def calls(self) -> int:
        return len(self.run_ids)


class LadderAgent(BaseAgent):
    """Scriptable body: raises `fail` when set, otherwise succeeds.
    `declare_pass` makes the body call note_pass() -- the early-exit path
    world_architect / character_keeper / continuity_checker / summarizer use."""

    def __init__(self, *, fail: BaseException | None = None, declare_pass: bool = False,
                 interval: int = 0, clock=None, progress_probe=None) -> None:
        super().__init__(NullRunner(), interval=interval, name="ladder",
                         clock=clock or FakeClock(), progress_probe=progress_probe)
        self._fail = fail
        self._declare_pass = declare_pass
        self.runs = 0

    async def _run(self):
        self.runs += 1
        if self._declare_pass:
            self.note_pass()
        if self._fail is not None:
            raise self._fail


class ScriptedAgent(BaseAgent):
    """One entry per run: "progress" (succeeds, probe says yes), "idle"
    (succeeds, probe says no), "fail" (raises). Its own probe reads the same
    script, so a single list drives both ladders' inputs."""

    def __init__(self, script, clock) -> None:
        self.script = list(script)
        self.step = 0
        super().__init__(NullRunner(), interval=0, name="scripted",
                         clock=clock, progress_probe=self._probe)

    async def _run(self):
        if self.script[self.step] == "fail":
            raise RuntimeError("scripted failure")

    async def _probe(self, run_id: str) -> bool:
        return self.script[self.step] == "progress"


async def _drive(agent: ScriptedAgent) -> None:
    for i, event in enumerate(agent.script):
        agent.step = i
        if event == "fail":
            with pytest.raises(RuntimeError):
                await agent.run_once()
        else:
            await agent.run_once()


def _expected_delay(base: float, cap: float, streak: int) -> float:
    """Deadline offset after `streak` consecutive same-kind events:
    base, 2*base, 4*base ... clamped at cap."""
    assert streak >= 1
    return min(base * 2 ** (streak - 1), cap)


def _model(script) -> tuple[int, int]:
    """Reference ladder state for a script. `_fail_streak` counts raises since
    the last run that completed; `_idle_streak` counts no-progress completions
    since the last productive one and is deaf to raises entirely -- a crash
    says nothing about whether the agent has run out of work."""
    fail_streak = idle_streak = 0
    for event in script:
        if event == "fail":
            fail_streak += 1
        else:
            fail_streak = 0
            idle_streak = idle_streak + 1 if event == "idle" else 0
    return fail_streak, idle_streak


def test_ladder_constants_are_absolute_seconds():
    """The ladders replace the interval-multiplier scheme, so their unit is
    seconds and not "some multiple of a number the operator configured."""
    assert kit_base.FAIL_BACKOFF_BASE_S == 2.0
    assert kit_base.IDLE_BACKOFF_BASE_S == 5.0
    assert kit_base.IDLE_BACKOFF_CAP_S == 300.0
    assert math.isfinite(kit_base.FAIL_BACKOFF_CAP_S)
    assert kit_base.FAIL_BACKOFF_CAP_S > kit_base.FAIL_BACKOFF_BASE_S


# --- the probe seam --------------------------------------------------------

async def test_progress_probe_is_asked_about_the_run_that_just_finished():
    """The probe answers "did THIS run commit anything?", so it is worthless
    unless it receives the run id the run actually executed under -- the same
    one stamped on the telemetry and on every canon commit made inside _run."""
    probe = ProbeSpy(answer=True)
    agent = LadderAgent(progress_probe=probe)
    emitter = FakeEmitter()
    agent.telemetry = emitter
    await agent.run_once()
    started, finished = emitter.events[0][2], emitter.events[1][2]
    assert probe.calls == 1
    assert probe.run_ids[0] == started.run_id == finished.run_id


async def test_progress_resets_idle_backoff():
    clock = FakeClock()
    probe = ProbeSpy(answer=False)
    agent = LadderAgent(progress_probe=probe, clock=clock)
    await agent.run_once()
    assert not agent.ready(clock())

    probe.answer = True
    await agent.run_once()
    assert agent._idle_streak == 0
    assert agent.ready(clock())


async def test_no_progress_advances_idle_backoff():
    clock = FakeClock()
    agent = LadderAgent(progress_probe=ProbeSpy(answer=False), clock=clock)
    await agent.run_once()
    assert agent._idle_streak == 1
    assert agent._idle_until == clock() + kit_base.IDLE_BACKOFF_BASE_S
    assert not agent.ready(clock())


async def test_absent_probe_fails_open_and_never_idles():
    """Kit consumers that never wire a probe must keep today's behavior: no
    probe means no evidence of idleness, and absence of evidence must not
    quiet an agent that is in fact working."""
    clock = FakeClock()
    agent = LadderAgent(progress_probe=None, clock=clock)
    for _ in range(5):
        await agent.run_once()
    assert agent._idle_streak == 0
    assert agent.ready(clock())


async def test_probe_that_raises_fails_open_and_does_not_break_the_run():
    """A probe reads the event store, which can fail. A broken probe must
    never turn a successful agent run into a failed one, and must never be
    the reason an agent goes quiet."""
    clock = FakeClock()
    probe = ProbeSpy(raises=RuntimeError("event store unreachable"))
    agent = LadderAgent(progress_probe=probe, clock=clock)
    emitter = FakeEmitter()
    agent.telemetry = emitter

    await agent.run_once()  # must not raise

    assert probe.calls == 1
    assert agent._idle_streak == 0
    assert agent.ready(clock())
    assert [e[0] for e in emitter.events] == [
        TelemetryEventType.AGENT_RUN_STARTED, TelemetryEventType.AGENT_RUN_FINISHED]


async def test_probe_not_consulted_when_the_run_failed():
    """A crashed run committed nothing by definition, so probing it would
    read as "no progress" and charge the agent on both ladders for a single
    event. The fail ladder owns that path alone."""
    clock = FakeClock()
    probe = ProbeSpy(answer=False)
    agent = LadderAgent(fail=ValueError("boom"), progress_probe=probe, clock=clock)
    with pytest.raises(ValueError):
        await agent.run_once()
    assert probe.calls == 0
    assert agent._idle_streak == 0
    assert agent._idle_until <= clock()


async def test_cancellation_leaves_both_ladders_untouched():
    """A cancelled run is evidence of nothing about this agent: it was cut off
    from outside, so it neither proves the agent is broken (fail ladder) nor
    that it has run out of work (idle ladder) -- and _note_progress's own
    principle is that an agent must never be quieted by an absence of
    evidence. Both ladders are left exactly as the cancellation found them,
    including a fail streak already standing: clearing it would be the same
    error in the other direction."""
    clock = FakeClock()
    probe = ProbeSpy(answer=False)
    agent = LadderAgent(fail=asyncio.CancelledError(), progress_probe=probe, clock=clock)
    agent._advance_fail(clock())  # a streak already standing from an earlier crash
    fail_until, fail_streak = agent._fail_until, agent._fail_streak

    with pytest.raises(asyncio.CancelledError):
        await agent.run_once()

    assert probe.calls == 0
    assert (agent._fail_streak, agent._fail_until) == (fail_streak, fail_until)
    assert agent._idle_streak == 0
    assert agent._idle_until == 0.0


# --- the two ladders -------------------------------------------------------

async def test_fail_ladder_advances_on_raise_and_resets_on_success():
    clock = FakeClock()
    agent = LadderAgent(fail=ValueError("boom"), clock=clock)
    for streak in (1, 2, 3):
        with pytest.raises(ValueError):
            await agent.run_once()
        assert agent._fail_streak == streak
        assert agent._fail_until == clock() + _expected_delay(
            kit_base.FAIL_BACKOFF_BASE_S, kit_base.FAIL_BACKOFF_CAP_S, streak)
        assert not agent.ready(clock())

    agent._fail = None
    await agent.run_once()
    assert agent._fail_streak == 0
    assert agent._fail_until <= clock()
    assert agent.ready(clock())


async def test_rate_limit_failure_also_advances_the_fail_ladder():
    """The fail ladder is the only gate on dispatch now, so a 429 must land on
    it -- anywhere else and rate-limit backpressure is silently lost."""
    clock = FakeClock()
    agent = LadderAgent(fail=_rate_limit_error(), interval=10, clock=clock)
    import openai
    with pytest.raises(openai.RateLimitError):
        await agent.run_once()
    assert agent._fail_streak == 1
    assert not agent.ready(clock())


async def test_failing_run_does_not_reset_idle_backoff():
    clock = FakeClock()
    agent = LadderAgent(progress_probe=ProbeSpy(answer=False), clock=clock)
    await agent.run_once()
    idle_until, idle_streak = agent._idle_until, agent._idle_streak

    agent._fail = ValueError("boom")
    with pytest.raises(ValueError):
        await agent.run_once()
    assert agent._idle_until == idle_until
    assert agent._idle_streak == idle_streak


async def test_successful_no_progress_run_resets_fail_and_advances_idle():
    """The fail ladder resets on any run that did not raise -- including one
    that made no progress. "The agent is broken" and "the agent is converged"
    are different questions, and a run that completed cleanly has answered the
    first one no matter what it produced."""
    clock = FakeClock()
    agent = LadderAgent(fail=ValueError("boom"),
                        progress_probe=ProbeSpy(answer=False), clock=clock)
    with pytest.raises(ValueError):
        await agent.run_once()
    assert agent._fail_streak == 1

    agent._fail = None
    await agent.run_once()
    assert agent._fail_streak == 0
    assert agent._fail_until <= clock()
    assert agent._idle_streak == 1
    assert not agent.ready(clock())  # held by the idle ladder alone


async def test_ladders_are_consulted_together_via_max():
    clock = FakeClock()
    agent = LadderAgent(clock=clock)
    agent._fail_until = clock() + 5.0
    agent._idle_until = clock() + 50.0
    assert not agent.ready(clock() + 49.9)
    assert agent.ready(clock() + 50.0)

    agent._fail_until = clock() + 500.0
    assert not agent.ready(clock() + 499.9)
    assert agent.ready(clock() + 500.0)


async def test_idle_backoff_is_capped_and_never_overflows():
    """A naive base * 2**streak raises OverflowError converting the int to a
    float somewhere past a thousand consecutive events -- reachable by an
    agent that is simply converged and left running. The clamp has to happen
    before the exponentiation is turned into a delay, not after."""
    clock = FakeClock()
    agent = LadderAgent(progress_probe=ProbeSpy(answer=False), clock=clock)
    for _ in range(1200):
        await agent.run_once()
    delay = agent._idle_until - clock()
    assert delay == kit_base.IDLE_BACKOFF_CAP_S
    assert math.isfinite(delay) and delay > 0.0


async def test_fail_backoff_is_capped_and_never_overflows():
    clock = FakeClock()
    agent = LadderAgent(fail=ValueError("boom"), clock=clock)
    for _ in range(1200):
        with pytest.raises(ValueError):
            await agent.run_once()
    delay = agent._fail_until - clock()
    assert delay == kit_base.FAIL_BACKOFF_CAP_S
    assert math.isfinite(delay) and delay > 0.0


def test_fresh_agent_is_ready():
    agent = BaseAgent(NullRunner(), interval=0)
    assert agent.ready(0.0)


def test_ready_ignores_interval_however_large():
    """`ready` is the whole change: dispatch stops waiting on a clock. An
    agent carrying a fifteen-minute interval is dispatchable right now, and
    stays dispatchable a moment after it has run."""
    agent = BaseAgent(NullRunner(), interval=900)
    assert agent.ready(1000.0)
    assert agent.ready(1000.1)


# --- note_pass as an early exit -------------------------------------------

def test_note_pass_engages_the_idle_ladder_directly():
    """An agent that has already established it has nothing to do should not
    have to wait for a probe to say the same thing. note_pass is that
    shortcut, so it must move the same ladder the probe moves."""
    clock = FakeClock()
    agent = BaseAgent(NullRunner(), interval=10, clock=clock)
    for streak in (1, 2, 3):
        agent.note_pass()
        assert agent._idle_streak == streak
        assert agent._idle_until == clock() + _expected_delay(
            kit_base.IDLE_BACKOFF_BASE_S, kit_base.IDLE_BACKOFF_CAP_S, streak)
        assert not agent.ready(clock())


def test_note_pass_backoff_does_not_scale_with_the_interval():
    """A declared pass costs the same step back whatever the operator set the
    interval to. Two agents an order of magnitude apart in interval must come
    back at the same moment."""
    clock = FakeClock()
    brief = BaseAgent(NullRunner(), interval=10, clock=clock)
    patient = BaseAgent(NullRunner(), interval=900, clock=clock)
    brief.note_pass()
    patient.note_pass()
    assert brief._idle_until == patient._idle_until == clock() + kit_base.IDLE_BACKOFF_BASE_S


async def test_note_pass_inside_a_run_wins_over_the_probe():
    """The declared verdict is the authoritative one. If the probe also ran,
    a "progress" answer would immediately undo the pass (the AGENT_REMARKED
    chatter commit alone can produce one) and a "no progress" answer would
    double-advance the ladder for a single run."""
    clock = FakeClock()
    probe = ProbeSpy(answer=True)
    agent = LadderAgent(declare_pass=True, progress_probe=probe, clock=clock)
    await agent.run_once()
    assert probe.calls == 0
    assert agent._idle_streak == 1
    assert agent._idle_until == clock() + kit_base.IDLE_BACKOFF_BASE_S
    assert not agent.ready(clock())


async def test_note_pass_survives_a_probeless_agent():
    """Fail-open must mean "do not advance", not "reset": a probeless agent
    that declares a pass has to stay backed off, or the four note_pass
    callers become no-ops for every consumer without a probe."""
    clock = FakeClock()
    agent = LadderAgent(declare_pass=True, progress_probe=None, clock=clock)
    await agent.run_once()
    assert agent._idle_streak == 1
    assert not agent.ready(clock())


# --- seconds_until_ready ---------------------------------------------------
#
# The countdown over the two ladders, and nothing else -- the interval and the
# legacy backoff terms go with the gate they belonged to. This is what the TUI
# status bar renders through Scheduler.status(), so it must agree exactly with
# what the scheduler will actually do: any term left in that dispatch no longer
# consults would show a countdown for a wait that will not happen.

def test_seconds_until_ready_includes_the_fail_ladder():
    agent = BaseAgent(NullRunner(), interval=0)
    agent._fail_until = 1050.0
    assert agent.seconds_until_ready(1000.0) == 50.0


def test_seconds_until_ready_includes_the_idle_ladder():
    agent = BaseAgent(NullRunner(), interval=0)
    agent._idle_until = 1075.0
    assert agent.seconds_until_ready(1000.0) == 75.0


def test_seconds_until_ready_ignores_elapsed_ladder_deadlines():
    """Deadlines in the past must contribute nothing. A negative countdown
    would render as a negative "next ready in" in the status bar."""
    agent = BaseAgent(NullRunner(), interval=0)
    agent._fail_until = 500.0
    agent._idle_until = 200.0
    assert agent.seconds_until_ready(1000.0) == 0.0


def test_seconds_until_ready_drops_the_interval_term():
    """A fifteen-minute interval must not show up as a fifteen-minute wait for
    an agent the scheduler would dispatch this very tick."""
    agent = BaseAgent(NullRunner(), interval=900)
    assert agent.seconds_until_ready(1000.0) == 0.0


@settings(deadline=None, max_examples=50)
@given(
    interval=st.integers(min_value=0, max_value=1000),
    now=st.floats(min_value=0, max_value=5000, allow_nan=False),
    fail_offset=st.floats(min_value=0, max_value=400, allow_nan=False),
    idle_offset=st.floats(min_value=0, max_value=400, allow_nan=False),
)
def test_seconds_until_ready_is_the_max_over_the_two_ladders(
        interval, now, fail_offset, idle_offset):
    agent = BaseAgent(NullRunner(), interval=interval)
    agent._fail_until = now + fail_offset
    agent._idle_until = now + idle_offset
    assert agent.seconds_until_ready(now) == pytest.approx(max(fail_offset, idle_offset))


@settings(deadline=None, max_examples=50)
@given(
    interval=st.integers(min_value=0, max_value=1000),
    now=st.floats(min_value=0, max_value=5000, allow_nan=False),
    fail_offset=st.floats(min_value=-400, max_value=400, allow_nan=False),
    idle_offset=st.floats(min_value=-400, max_value=400, allow_nan=False),
)
def test_zero_seconds_until_ready_iff_ready(interval, now, fail_offset, idle_offset):
    """Now an equivalence, where it was only one direction: with the interval
    gate gone the countdown and the dispatch decision read the same two
    deadlines, so the status bar can never disagree with the scheduler."""
    agent = BaseAgent(NullRunner(), interval=interval)
    agent._fail_until = now + fail_offset
    agent._idle_until = now + idle_offset
    remaining = agent.seconds_until_ready(now)
    assert remaining >= 0.0
    assert math.isfinite(remaining)
    assert (remaining == 0.0) == agent.ready(now)


# --- ladder properties -----------------------------------------------------

@settings(deadline=None, max_examples=25)
@given(n=st.integers(min_value=1, max_value=12))
async def test_consecutive_no_progress_runs_never_shrink_backoff(n):
    """Monotone and bounded: each successive "still converged" verdict must
    cost at least as much as the last, and the sequence must plateau at the
    cap rather than growing without limit."""
    clock = FakeClock()
    agent = ScriptedAgent(["idle"] * n, clock)
    offsets = []
    for i in range(n):
        agent.step = i
        await agent.run_once()
        offsets.append(agent._idle_until - clock())
    assert offsets == sorted(offsets)
    assert all(0.0 < o <= kit_base.IDLE_BACKOFF_CAP_S for o in offsets)


@settings(deadline=None, max_examples=25)
@given(n=st.integers(min_value=0, max_value=12))
async def test_one_progress_run_returns_the_agent_to_ready(n):
    """No streak is deep enough to be unrecoverable. The room is supposed to
    go straight back to flat out the moment it starts producing again."""
    clock = FakeClock()
    agent = ScriptedAgent(["idle"] * n + ["progress"], clock)
    await _drive(agent)
    assert agent._idle_streak == 0
    assert agent.ready(clock())
    assert agent.seconds_until_ready(clock()) == 0.0


@settings(deadline=None, max_examples=50)
@given(script=st.lists(st.sampled_from(["progress", "idle", "fail"]),
                       min_size=1, max_size=12))
async def test_each_ladder_tracks_only_its_own_inputs(script):
    clock = FakeClock()
    agent = ScriptedAgent(script, clock)
    await _drive(agent)

    fail_streak, idle_streak = _model(script)
    assert agent._fail_streak == fail_streak
    assert agent._idle_streak == idle_streak

    if fail_streak:
        assert agent._fail_until == clock() + _expected_delay(
            kit_base.FAIL_BACKOFF_BASE_S, kit_base.FAIL_BACKOFF_CAP_S, fail_streak)
    else:
        assert agent._fail_until <= clock()
    if idle_streak:
        assert agent._idle_until == clock() + _expected_delay(
            kit_base.IDLE_BACKOFF_BASE_S, kit_base.IDLE_BACKOFF_CAP_S, idle_streak)
    else:
        assert agent._idle_until <= clock()

    assert agent.ready(clock()) == (not fail_streak and not idle_streak)


# --- hold() ----------------------------------------------------------------
#
# seconds_until_ready() answers "how long", which is the countdown. hold()
# answers "which ladder", which is the reason -- and the two ladders mean
# opposite things to a watcher: the fail ladder says the agent is erroring, the
# idle ladder says the story gave it nothing to react to. The owner of the
# ladders derives this; nothing downstream may guess it from the deadline.

def test_hold_is_none_for_an_agent_the_scheduler_would_dispatch():
    agent = BaseAgent(NullRunner(), interval=900)
    assert agent.hold(1000.0) is None


def test_hold_names_the_fail_ladder_and_its_remaining_wait():
    agent = BaseAgent(NullRunner(), interval=0)
    agent._fail_until = 1050.0
    assert agent.hold(1000.0) == ("backing off", 50.0)


def test_hold_names_the_idle_ladder_and_its_remaining_wait():
    agent = BaseAgent(NullRunner(), interval=0)
    agent._idle_until = 1075.0
    assert agent.hold(1000.0) == ("awaiting progress", 75.0)


def test_hold_reports_the_ladder_that_actually_governs_dispatch():
    """Both ladders engaged: ready() waits for the later of the two, so the
    reason shown must be that same one -- reporting the shorter wait would put a
    countdown on screen that expires while the agent stays held."""
    agent = BaseAgent(NullRunner(), interval=0)
    agent._fail_until = 1010.0
    agent._idle_until = 1090.0
    assert agent.hold(1000.0) == ("awaiting progress", 90.0)


def test_hold_prefers_the_fail_ladder_on_a_tie():
    """Equal deadlines: "this agent is erroring" is the more urgent of the two
    facts, and the one a watcher has to act on."""
    agent = BaseAgent(NullRunner(), interval=0)
    agent._fail_until = agent._idle_until = 1040.0
    assert agent.hold(1000.0) == ("backing off", 40.0)


def test_hold_ignores_elapsed_deadlines():
    agent = BaseAgent(NullRunner(), interval=0)
    agent._fail_until = 500.0
    agent._idle_until = 900.0
    assert agent.hold(1000.0) is None


@settings(deadline=None, max_examples=50)
@given(
    now=st.floats(min_value=0, max_value=5000, allow_nan=False),
    fail_offset=st.floats(min_value=-100, max_value=400, allow_nan=False),
    idle_offset=st.floats(min_value=-100, max_value=400, allow_nan=False),
)
def test_hold_agrees_with_ready_and_seconds_until_ready(now, fail_offset, idle_offset):
    """The reason and the countdown are two views of one decision: a held agent
    always has a reason, a dispatchable one never does, and the seconds hold()
    reports are the seconds the scheduler will actually wait."""
    agent = BaseAgent(NullRunner(), interval=0)
    agent._fail_until = now + fail_offset
    agent._idle_until = now + idle_offset
    held = agent.hold(now)
    assert (held is None) == agent.ready(now)
    if held is not None:
        assert held[1] == pytest.approx(agent.seconds_until_ready(now))
