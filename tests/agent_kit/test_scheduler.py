from __future__ import annotations
import asyncio

from hypothesis import given, settings, strategies as st

from agent_kit.base import BaseAgent
from agent_kit.scheduler import Scheduler
from agent_kit.telemetry import TelemetryEventType


class StubAgent:
    """The dispatch surface the scheduler actually touches. `interval` is still
    accepted -- the constructor parameter survives, every agent still passes
    one -- and it is present here precisely so tests can prove it no longer
    reaches dispatch. The two ladder deadlines are what `ready()` reads."""

    def __init__(self, name, score, interval=0):
        self.name = name; self._score = score; self.interval = interval
        self.paused = False; self.ran = 0
        self._fail_until = 0.0; self._idle_until = 0.0
    async def readiness(self): return self._score
    def ready(self, now): return now >= max(self._fail_until, self._idle_until)
    def seconds_until_ready(self, now):
        return max(0.0, self._fail_until - now, self._idle_until - now)
    async def run_once(self): self.ran += 1
    def pause(self): self.paused = True
    def resume(self): self.paused = False


class CrashingAgent(StubAgent):
    async def run_once(self): raise RuntimeError("kaput")


class FakeEmitter:
    def __init__(self):
        self.events = []
    async def emit(self, event_type, aggregate_id, payload):
        self.events.append((event_type, aggregate_id, payload))
    def in_llm_call(self, run_id): return False


def _eligibility(emitter):
    return [p for t, _, p in emitter.events
            if t == TelemetryEventType.SCHEDULER_ELIGIBILITY_CHANGED]


async def test_runs_highest_readiness_first():
    a = StubAgent("a", 0.2); b = StubAgent("b", 0.9)
    sched = Scheduler([a, b], clock=lambda: 1000.0, max_concurrent_agents=1)
    assert await sched.tick() == ["b"]
    await sched.drain_in_flight()
    assert b.ran == 1 and a.ran == 0


async def test_skips_paused_and_zero_score():
    a = StubAgent("a", 0.0); b = StubAgent("b", 0.5)
    b.pause()
    sched = Scheduler([a, b], clock=lambda: 1000.0)
    assert await sched.tick() == []


async def test_respects_concurrency_cap():
    a = StubAgent("a", 0.9); b = StubAgent("b", 0.8); c = StubAgent("c", 0.7)
    sched = Scheduler([a, b, c], clock=lambda: 1000.0, max_concurrent_agents=2)
    assert await sched.tick() == ["a", "b"]
    await sched.drain_in_flight()


async def test_override_provider_dispatches_named_agent_first():
    a = StubAgent("a", 0.9); b = StubAgent("b", 0.1)
    async def provider(): return "b"
    sched = Scheduler([a, b], clock=lambda: 1000.0,
                      max_concurrent_agents=1, override_provider=provider)
    assert await sched.tick() == ["b"]
    await sched.drain_in_flight()


async def test_no_override_provider_means_pure_readiness_order():
    a = StubAgent("a", 0.3); b = StubAgent("b", 0.6)
    sched = Scheduler([a, b], clock=lambda: 1000.0, max_concurrent_agents=1)
    assert await sched.tick() == ["b"]
    await sched.drain_in_flight()


async def test_crash_records_error_and_counts_the_run():
    a = CrashingAgent("a", 0.9, interval=10)
    sched = Scheduler([a], clock=lambda: 1000.0)
    assert await sched.tick() == ["a"]
    await sched.drain_in_flight()
    status = {s["name"]: s for s in sched.status()}
    assert "RuntimeError: kaput" in status["a"]["last_error"]
    assert status["a"]["run_count"] == 1


async def test_success_clears_last_error_and_marks_last_completed():
    a = StubAgent("a", 0.9)
    sched = Scheduler([a], clock=lambda: 1000.0)
    await sched.tick(); await sched.drain_in_flight()
    status = {s["name"]: s for s in sched.status()}
    assert status["a"]["last_error"] is None
    assert status["a"]["last_completed"] is True


async def test_eligibility_emitted_on_change_only():
    a = StubAgent("a", 0.0)
    emitter = FakeEmitter()
    sched = Scheduler([a], clock=lambda: 1000.0, telemetry=emitter)
    await sched.tick()
    await sched.tick()
    elig = _eligibility(emitter)
    assert len(elig) == 1  # second identical state emits nothing
    assert elig[0].reason == "readiness 0"


async def test_picked_emitted_per_dispatch():
    a = StubAgent("a", 0.9)
    emitter = FakeEmitter()
    sched = Scheduler([a], clock=lambda: 1000.0, telemetry=emitter)
    await sched.tick(); await sched.drain_in_flight()
    picked = [e for e in emitter.events if e[0] == TelemetryEventType.SCHEDULER_PICKED]
    assert len(picked) == 1 and picked[0][2].agent_name == "a"


async def test_pause_all_returns_only_newly_paused():
    a = StubAgent("a", 0.1); b = StubAgent("b", 0.1)
    b.pause()
    sched = Scheduler([a, b], clock=lambda: 1000.0)
    assert sched.pause_all() == ["a"]
    sched.resume_agents(["a"])
    assert a.paused is False and b.paused is True


async def test_genuine_concurrency_up_to_cap():
    log = []
    class SlowAgent(StubAgent):
        async def run_once(self):
            loop = asyncio.get_event_loop()
            start = loop.time(); await asyncio.sleep(0.05); log.append((self.name, start, loop.time()))
    a = SlowAgent("a", 0.9); b = SlowAgent("b", 0.8)
    sched = Scheduler([a, b], clock=lambda: 1000.0, max_concurrent_agents=2)
    await sched.tick(); await sched.drain_in_flight()
    (n1, s1, e1), (n2, s2, e2) = sorted(log, key=lambda x: x[1])
    assert s2 < e1  # overlapping, not sequential


# --- the clock gate is gone ------------------------------------------------
#
# Everything below is the point of the change. tick() used to AND two gates and
# run the clock gate FIRST, so an agent was filtered out before it was ever
# scored -- producing the steady state the design names: two free dispatch
# slots and zero eligible agents, while work sat waiting. Dispatch is now gated
# by the backoff ladders alone.


async def test_an_agent_that_just_ran_is_dispatched_again_on_the_very_next_tick():
    """THE headline. An agent carrying a fifteen-minute interval that finished
    a run one tick ago is dispatched again immediately, because it still has
    work. Before this change the clock gate held it out for the full interval
    while its dispatch slot sat empty. That is the bug this project exists to
    fix; if this test ever goes red, the clock gate is back."""
    a = StubAgent("a", 0.9, interval=900)
    sched = Scheduler([a], clock=lambda: 1000.0, max_concurrent_agents=1)

    assert await sched.tick() == ["a"]
    await sched.drain_in_flight()
    assert await sched.tick() == ["a"], "an interval must never hold back an agent with work"
    await sched.drain_in_flight()
    assert a.ran == 2


async def test_the_whole_roster_of_long_interval_agents_fills_every_slot():
    """The novelizer configuration exactly: seven agents at 300/900/300/240/
    180/120/60s against two slots. Today's steady state for this roster is zero
    eligible agents; every tick must now fill both slots."""
    intervals = (300, 900, 300, 240, 180, 120, 60)
    agents = [StubAgent(f"a{i}", 0.5, interval=iv) for i, iv in enumerate(intervals)]
    sched = Scheduler(agents, clock=lambda: 1000.0, max_concurrent_agents=2)
    for _ in range(4):
        assert len(await sched.tick()) == 2
        await sched.drain_in_flight()
    assert sum(a.ran for a in agents) == 8


async def test_a_fresh_agent_with_an_absurd_interval_dispatches_at_once():
    a = StubAgent("a", 0.9, interval=100_000)
    sched = Scheduler([a], clock=lambda: 1000.0)
    assert await sched.tick() == ["a"]
    await sched.drain_in_flight()


async def test_free_slots_and_zero_eligible_agents_is_no_longer_reachable():
    """The state the room lived in. With three unpaused agents that are scoring
    above zero and are off both ladders, a two-slot pool must never idle."""
    agents = [StubAgent(n, 0.5, interval=900) for n in "abc"]
    sched = Scheduler(agents, clock=lambda: 1000.0, max_concurrent_agents=2)
    for _ in range(5):
        await sched.tick()
        assert len(sched._in_flight) == 2, "slots sat free while work was available"
        await sched.drain_in_flight()


# --- dispatch is gated by the ladders, and only by the ladders --------------

async def test_agent_inside_its_fail_backoff_is_not_dispatched():
    a = StubAgent("a", 0.9)
    a._fail_until = 1050.0
    sched = Scheduler([a], clock=lambda: 1000.0)
    assert await sched.tick() == []


async def test_agent_inside_its_idle_backoff_is_not_dispatched():
    a = StubAgent("a", 0.9)
    a._idle_until = 1050.0
    sched = Scheduler([a], clock=lambda: 1000.0)
    assert await sched.tick() == []


async def test_agent_is_dispatched_the_instant_its_ladder_deadline_passes():
    """And regardless of the interval or of how recently it ran: the deadline
    is the only thing between a backed-off agent and a dispatch slot."""
    now = [1000.0]
    a = StubAgent("a", 0.9, interval=100_000)
    a._idle_until = 1050.0
    sched = Scheduler([a], clock=lambda: now[0])
    assert await sched.tick() == []
    now[0] = 1049.999
    assert await sched.tick() == []
    now[0] = 1050.0
    assert await sched.tick() == ["a"]
    await sched.drain_in_flight()


async def test_both_ladders_gate_dispatch_via_the_later_deadline():
    now = [1000.0]
    a = StubAgent("a", 0.9)
    a._fail_until = 1010.0
    a._idle_until = 1080.0
    sched = Scheduler([a], clock=lambda: now[0])
    now[0] = 1010.0
    assert await sched.tick() == [], "released by the fail ladder but still idle-backed-off"
    now[0] = 1080.0
    assert await sched.tick() == ["a"]
    await sched.drain_in_flight()


# --- the scheduler no longer marks runs against a clock ---------------------

class MarkRanSpy(StubAgent):
    """_run()'s finally-block used to call mark_ran so that a crashing agent
    would consume its interval instead of hot-looping. That concern belongs to
    the fail ladder now, and mark_ran is gone from the chassis -- a scheduler
    that still called it would blow up on every real agent."""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.mark_ran_calls = 0

    def mark_ran(self, now):
        self.mark_ran_calls += 1


class CrashingMarkRanSpy(MarkRanSpy):
    async def run_once(self): raise RuntimeError("kaput")


async def test_scheduler_does_not_mark_ran_on_success():
    a = MarkRanSpy("a", 0.9, interval=10)
    sched = Scheduler([a], clock=lambda: 1000.0)
    await sched.tick(); await sched.drain_in_flight()
    assert a.mark_ran_calls == 0


async def test_scheduler_does_not_mark_ran_on_failure():
    a = CrashingMarkRanSpy("a", 0.9, interval=10)
    sched = Scheduler([a], clock=lambda: 1000.0)
    await sched.tick(); await sched.drain_in_flight()
    assert a.mark_ran_calls == 0


# --- the starvation regression ---------------------------------------------
#
# The fail ladder only moves inside BaseAgent.run_once, so this one cannot be
# written against a stub: it needs the real chassis under the real scheduler.


class NullRunner:
    async def ainvoke(self, inputs: dict) -> dict:
        return {}


class FakeClock:
    def __init__(self, now: float = 1000.0) -> None:
        self.now = now

    def __call__(self) -> float:
        return self.now

    def advance(self, dt: float) -> None:
        self.now += dt


class _ScoredAgent(BaseAgent):
    def __init__(self, name, score, clock, fail=None):
        super().__init__(NullRunner(), interval=0, name=name, clock=clock)
        self._score = score
        self._fail = fail
        self.runs = 0

    async def readiness(self):
        return self._score

    async def _run(self):
        self.runs += 1
        if self._fail is not None:
            raise self._fail


async def test_a_crashing_agent_backs_off_instead_of_starving_the_room():
    """Observed live before the interval backoff existed: the author 502-looped
    while nothing else in the room ran. With the clock gate deleted, the fail
    ladder is the only thing standing between a crashing agent that outscores
    everyone and every dispatch slot in the room."""
    clock = FakeClock()
    boom = _ScoredAgent("boom", 0.9, clock, fail=ValueError("boom"))
    healthy = _ScoredAgent("healthy", 0.1, clock)
    sched = Scheduler([boom, healthy], clock=clock, max_concurrent_agents=1)

    assert await sched.tick() == ["boom"]
    await sched.drain_in_flight()
    assert boom._fail_streak == 1

    for _ in range(20):
        await sched.tick()
        await sched.drain_in_flight()

    assert boom.runs == 1, "the crasher re-took the slot while inside its fail backoff"
    assert healthy.runs == 20, "the healthy agent was starved by the crasher"


async def test_a_crashing_agent_returns_once_its_backoff_expires():
    """Backing off is not giving up: the room has to re-probe a failing agent
    often enough to notice when the cause clears."""
    clock = FakeClock()
    boom = _ScoredAgent("boom", 0.9, clock, fail=ValueError("boom"))
    sched = Scheduler([boom], clock=clock, max_concurrent_agents=1)

    assert await sched.tick() == ["boom"]
    await sched.drain_in_flight()
    assert await sched.tick() == []

    clock.advance(boom._fail_until - clock() + 0.001)
    assert await sched.tick() == ["boom"]
    await sched.drain_in_flight()
    assert boom.runs == 2


# --- eligibility telemetry --------------------------------------------------

async def test_backed_off_agent_reports_the_backing_off_reason():
    a = StubAgent("a", 0.9, interval=900)
    a._idle_until = 1050.0
    emitter = FakeEmitter()
    sched = Scheduler([a], clock=lambda: 1000.0, telemetry=emitter)
    await sched.tick()
    elig = _eligibility(emitter)
    assert [(p.agent_name, p.eligible, p.reason) for p in elig] == [("a", False, "backing off")]


async def test_no_agent_is_ever_reported_as_waiting_on_an_interval():
    """"interval not elapsed" described a gate that no longer exists; leaving
    it in the telemetry would explain the room's quiet with a reason that is no
    longer true."""
    a = StubAgent("a", 0.9, interval=900)
    a._fail_until = 1050.0
    emitter = FakeEmitter()
    sched = Scheduler([a], clock=lambda: 1000.0, telemetry=emitter)
    await sched.tick()
    assert all(p.reason != "interval not elapsed" for p in _eligibility(emitter))


async def test_backing_off_is_emitted_once_per_state_change_not_per_tick():
    now = [1000.0]
    a = StubAgent("a", 0.9)
    a._idle_until = 1050.0
    emitter = FakeEmitter()
    sched = Scheduler([a], clock=lambda: now[0], telemetry=emitter)
    await sched.tick()
    now[0] = 1001.0
    await sched.tick()   # same state -> no new event
    now[0] = 1050.0
    await sched.tick()   # released -> one event for the change back to ready
    await sched.drain_in_flight()
    elig = _eligibility(emitter)
    assert [(p.eligible, p.reason) for p in elig] == [(False, "backing off"), (True, "ready")]


# --- properties -------------------------------------------------------------

@settings(deadline=None, max_examples=25)
@given(interval=st.integers(min_value=0, max_value=100_000),
       elapsed=st.floats(min_value=0, max_value=100_000, allow_nan=False))
async def test_no_interval_value_at_any_moment_can_block_dispatch(interval, elapsed):
    a = StubAgent("a", 0.9, interval=interval)
    sched = Scheduler([a], clock=lambda: 1000.0 + elapsed, max_concurrent_agents=1)
    try:
        assert await sched.tick() == ["a"]
    finally:
        await sched.drain_in_flight()


class DecayingAgent(StubAgent):
    """Readiness falls as the agent works through its backlog, the shape every
    real agent's readiness has (the Author's is `1.0 - drafts/3`). A fixed
    score would make the starvation question a property of the test's own
    arithmetic rather than of the scheduler."""

    async def run_once(self):
        self.ran += 1
        self._score = max(0.0, self._score - 0.34)


@settings(deadline=None, max_examples=25)
@given(n_agents=st.integers(min_value=2, max_value=6),
       cap=st.integers(min_value=1, max_value=3),
       scores=st.lists(st.floats(min_value=0.4, max_value=1.0), min_size=6, max_size=6))
async def test_every_agent_with_work_eventually_gets_a_slot(n_agents, cap, scores):
    """No starvation: readiness ordering must not permanently crowd anyone out.
    The room has no round-robin -- fairness comes from busy agents working
    their backlogs down and freeing the slot -- so the property is that every
    agent with work runs eventually, not that they run in turn."""
    agents = [DecayingAgent(f"a{i}", scores[i]) for i in range(n_agents)]
    sched = Scheduler(agents, clock=lambda: 1000.0, max_concurrent_agents=cap)
    try:
        for _ in range(60):
            await sched.tick()
            await sched.drain_in_flight()
    finally:
        await sched.drain_in_flight()
    never_ran = [a.name for a in agents if a.ran == 0]
    assert never_ran == [], f"crowded out entirely: {never_ran}"


@settings(deadline=None, max_examples=50)
@given(specs=st.lists(
           st.tuples(st.floats(min_value=0.0, max_value=1.0),
                     st.booleans(),
                     st.floats(min_value=0.0, max_value=60.0),
                     st.integers(min_value=0, max_value=100_000)),
           min_size=1, max_size=6),
       cap=st.integers(min_value=1, max_value=4))
async def test_a_free_slot_implies_no_dispatchable_agent(specs, cap):
    """The invariant the whole design rests on. After a tick that left a slot
    free, every agent it passed over must have been paused, already in flight,
    backing off, or scoring zero -- never merely waiting on a clock."""
    now = 1000.0
    agents = []
    for i, (score, paused, backoff, interval) in enumerate(specs):
        a = StubAgent(f"a{i}", score, interval=interval)
        if paused:
            a.pause()
        a._idle_until = now + backoff
        agents.append(a)
    sched = Scheduler(agents, clock=lambda: now, max_concurrent_agents=cap)
    try:
        dispatched = set(await sched.tick())
        if len(sched._in_flight) < cap:
            for a in agents:
                if a.name in dispatched:
                    continue
                excused = a.paused or not a.ready(now) or await a.readiness() <= 0.0
                assert excused, f"{a.name} was dispatchable while a slot sat free"
    finally:
        await sched.drain_in_flight()
