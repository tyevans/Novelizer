from __future__ import annotations
import math
import time

import pytest
from hypothesis import given, settings, strategies as st

from agent_kit import base as kit_base
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


def test_note_pass_uses_injected_clock():
    agent = BaseAgent(NullRunner(), interval=10, clock=lambda: 1000.0)
    agent.mark_ran(1000.0)
    agent.note_pass()  # no arg -> injected clock, not real monotonic
    assert not agent.ready_for_interval(1029.9)
    assert agent.ready_for_interval(1030.0)


def test_default_clock_is_monotonic():
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


async def test_run_once_rate_limit_failure_backs_off_extra_intervals():
    """A run killed by a rate limit means the endpoint is saturated; the agent
    must step back RATE_LIMIT_BACKOFF_MULTIPLIER intervals instead of rejoining
    the pile-up at full cadence next interval."""
    import openai
    from agent_kit.base import RATE_LIMIT_BACKOFF_MULTIPLIER
    agent = BackoffProbe(_rate_limit_error())
    with pytest.raises(openai.RateLimitError):
        await agent.run_once()
    agent.mark_ran(1000.0)  # the scheduler's finally-block does this
    horizon = 1000.0 + agent.interval * RATE_LIMIT_BACKOFF_MULTIPLIER
    assert not agent.ready_for_interval(horizon - 0.001)
    assert agent.ready_for_interval(horizon)


async def test_run_once_wrapped_rate_limit_failure_still_backs_off():
    """Frameworks re-raise provider errors with the original as __cause__ or
    __context__; the backoff must see through one such wrapper chain."""
    wrapper = RuntimeError("graph step failed")
    wrapper.__cause__ = _rate_limit_error()
    agent = BackoffProbe(wrapper)
    with pytest.raises(RuntimeError):
        await agent.run_once()
    agent.mark_ran(1000.0)
    assert not agent.ready_for_interval(1000.0 + agent.interval + 0.001)


async def test_run_once_generic_failure_does_not_back_off():
    agent = BackoffProbe(ValueError("boom"))
    with pytest.raises(ValueError):
        await agent.run_once()
    agent.mark_ran(1000.0)
    assert agent.ready_for_interval(1000.0 + agent.interval)


# --- progress probe + second-based backoff ladders -------------------------
#
# Everything below is the event-driven-scheduling chassis. It is deliberately
# PARALLEL to the interval machinery pinned above: `interval`, `_last_run`,
# `ready_for_interval()` and `mark_ran()` keep their exact current behavior in
# this phase, and the scheduler still dispatches on them. The ladders are what
# a later phase flips dispatch over to.
#
# The ladder constants are reached through `kit_base.X` rather than a top-level
# `from agent_kit.base import X` on purpose: before they exist, a module-level
# import would fail this whole file at collection and take the twenty tests
# above down with it, hiding which tests are actually the new red.


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
    """note_rate_limited still moves the legacy interval backoff, but the
    ladder is what a later phase gates on -- a 429 must land on it too, or
    flipping the gate over silently drops rate-limit backpressure."""
    clock = FakeClock()
    agent = LadderAgent(fail=_rate_limit_error(), interval=10, clock=clock)
    import openai
    with pytest.raises(openai.RateLimitError):
        await agent.run_once()
    assert agent._fail_streak == 1
    assert not agent.ready(clock())
    # additive: the legacy multiplier backoff is untouched
    assert agent._backoff_until == clock() + 10 * kit_base.RATE_LIMIT_BACKOFF_MULTIPLIER


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


def test_ready_ignores_interval_and_last_run():
    """`ready` is the whole change: dispatch stops waiting on a clock. An
    agent that has never run and carries a fifteen-minute interval is
    dispatchable right now, while ready_for_interval -- still live this
    phase -- keeps saying no."""
    agent = BaseAgent(NullRunner(), interval=900)
    agent.mark_ran(1000.0)
    assert agent.ready(1000.0)
    assert not agent.ready_for_interval(1000.0)


def test_ready_ignores_the_legacy_interval_backoff():
    """`_backoff_until` belongs to the interval scheme and is measured in
    intervals; the ladders are the seconds-based replacement. Mixing them
    would double-charge note_pass, which sets both."""
    agent = BaseAgent(NullRunner(), interval=10)
    agent._backoff_until = 9_999.0
    assert agent.ready(1000.0)


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


def test_note_pass_still_moves_the_legacy_interval_backoff():
    """Additive, not a replacement: the interval scheme is still what the
    scheduler dispatches on this phase, so note_pass must keep doing both."""
    clock = FakeClock()
    agent = BaseAgent(NullRunner(), interval=10, clock=clock)
    agent.note_pass()
    assert agent._backoff_until == clock() + 10 * PASS_BACKOFF_MULTIPLIER
    assert agent._idle_until == clock() + kit_base.IDLE_BACKOFF_BASE_S


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
# This phase is additive, so dispatch still requires BOTH the interval gate and
# the ladders. seconds_until_ready is what the TUI status bar renders through
# Scheduler.status(), so it stays the max over every live gate -- interval,
# legacy backoff, and both ladders -- and only loses the first two when a later
# phase deletes the interval gate. Reporting a ladder-only countdown now would
# show "ready in 0s" for an agent the scheduler will not dispatch for minutes.

def test_seconds_until_ready_includes_the_fail_ladder():
    agent = BaseAgent(NullRunner(), interval=0)
    agent.mark_ran(1000.0)
    agent._fail_until = 1050.0
    assert agent.seconds_until_ready(1000.0) == 50.0


def test_seconds_until_ready_includes_the_idle_ladder():
    agent = BaseAgent(NullRunner(), interval=0)
    agent.mark_ran(1000.0)
    agent._idle_until = 1075.0
    assert agent.seconds_until_ready(1000.0) == 75.0


def test_seconds_until_ready_ignores_elapsed_ladder_deadlines():
    """Deadlines in the past must contribute nothing. A negative countdown
    would render as a negative "next ready in" in the status bar."""
    agent = BaseAgent(NullRunner(), interval=0)
    agent.mark_ran(1000.0)
    agent._fail_until = 500.0
    agent._idle_until = 200.0
    assert agent.seconds_until_ready(1000.0) == 0.0


@settings(deadline=None, max_examples=50)
@given(
    interval=st.integers(min_value=0, max_value=1000),
    elapsed=st.floats(min_value=0, max_value=5000, allow_nan=False),
    fail_offset=st.floats(min_value=0, max_value=400, allow_nan=False),
    idle_offset=st.floats(min_value=0, max_value=400, allow_nan=False),
)
def test_seconds_until_ready_is_the_max_over_every_live_gate(
        interval, elapsed, fail_offset, idle_offset):
    agent = BaseAgent(NullRunner(), interval=interval)
    agent.mark_ran(1000.0)
    now = 1000.0 + elapsed
    agent._fail_until = now + fail_offset
    agent._idle_until = now + idle_offset
    expected = max(0.0, interval - elapsed, agent._backoff_until - now,
                   fail_offset, idle_offset)
    assert agent.seconds_until_ready(now) == pytest.approx(expected)


@settings(deadline=None, max_examples=50)
@given(
    interval=st.integers(min_value=0, max_value=1000),
    elapsed=st.floats(min_value=0, max_value=5000, allow_nan=False),
    fail_offset=st.floats(min_value=-400, max_value=400, allow_nan=False),
    idle_offset=st.floats(min_value=-400, max_value=400, allow_nan=False),
)
def test_zero_seconds_until_ready_implies_ready(interval, elapsed, fail_offset, idle_offset):
    """The converse does not hold this phase -- the interval gate can hold an
    agent that both ladders have released -- but a zero countdown must never
    describe an agent the ladders are still backing off."""
    agent = BaseAgent(NullRunner(), interval=interval)
    agent.mark_ran(1000.0)
    now = 1000.0 + elapsed
    agent._fail_until = now + fail_offset
    agent._idle_until = now + idle_offset
    if agent.seconds_until_ready(now) == 0.0:
        assert agent.ready(now)


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
