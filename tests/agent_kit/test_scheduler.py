from __future__ import annotations
import asyncio
import contextlib

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


# --- Phase 3: the shared AIMD pool gates EXECUTION, not dispatch ------------
#
# There is no concurrency limiter anywhere today, and background KG extraction
# makes its own LLM calls entirely outside max_concurrent_agents -- two
# independent consumers on one vLLM endpoint with no shared ceiling, the source
# of 429 pile-ups. Phase 3 threads one shared pool through the scheduler: an
# optional injectable `pool` (default None => today's behavior, mirroring the
# override_provider seam), consulted inside _run around agent.run_once().
#
# A permit covers ONE WHOLE AGENT RUN, so it is acquired in _run, not in tick.
# tick() still creates every dispatchable task -- dispatch does not block on the
# pool; the runs then serialize on the permit inside _run. That is deliberate:
# the KG drain draws from the same pool, so gating dispatch would stall the tick
# loop on a ceiling another consumer holds.
#
# These tests drive the scheduler against a local SpyPool rather than the real
# AdaptivePool, so they pin the scheduler's contract (slot bracket + which AIMD
# signal fires for which outcome) independently of the pool's internals, and so
# a missing agent_kit.pool never collection-errors this file. They still start
# RED: Scheduler does not accept `pool=` yet -> TypeError: __init__() got an
# unexpected keyword argument 'pool'.


class SpyPool:
    """The pool surface the scheduler touches: an async `slot()` context manager
    plus the two explicit AIMD signals. Enforces a fixed concurrency limit and
    records how often each signal fired, so a scheduler test can prove the
    wiring without depending on the real AdaptivePool."""

    def __init__(self, limit: int = 99) -> None:
        self._sema = asyncio.Semaphore(limit)
        self._active = 0
        self.max_concurrent = 0
        self.rate_limited = 0
        self.successes = 0

    @contextlib.asynccontextmanager
    async def slot(self):
        await self._sema.acquire()
        self._active += 1
        self.max_concurrent = max(self.max_concurrent, self._active)
        try:
            yield
        finally:
            self._active -= 1
            self._sema.release()

    def note_rate_limited(self) -> None:
        self.rate_limited += 1

    def note_success(self) -> None:
        self.successes += 1


class RateLimitError(Exception):
    """Named + status-coded so agent_kit.base._is_rate_limit_error matches it on
    either signal, exactly as it matches a real provider 429."""
    status_code = 429


class RateLimitedAgent(StubAgent):
    async def run_once(self):
        raise RateLimitError("429 slow down")


class PoolSlowAgent(StubAgent):
    """Records the peak number of run_once bodies running at once, so a test can
    prove the pool serialized them below the dispatch cap."""

    def __init__(self, name, score, live, log):
        super().__init__(name, score)
        self._live = live
        self._log = log

    async def run_once(self):
        self.ran += 1
        self._live[0] += 1
        self._log.append(self._live[0])
        await asyncio.sleep(0.01)
        self._live[0] -= 1


async def test_pool_defaults_to_none_and_runs_unlimited():
    """The default seam: no pool wired => _run behaves exactly as today."""
    a = StubAgent("a", 0.9)
    sched = Scheduler([a], clock=lambda: 1000.0)
    assert sched._pool is None
    await sched.tick()
    await sched.drain_in_flight()
    assert a.ran == 1
    assert sched.status()[0]["run_count"] == 1


async def test_pool_none_still_records_error_on_a_rate_limit_crash():
    """Regression guard: adding the pool seam must not change the no-pool error
    path -- a 429 with pool=None is recorded and re-raised, same as any crash."""
    a = RateLimitedAgent("a", 0.9)
    sched = Scheduler([a], clock=lambda: 1000.0)
    await sched.tick()
    await sched.drain_in_flight()
    assert "RateLimitError" in sched.status()[0]["last_error"]


async def test_pool_is_a_tighter_ceiling_than_the_dispatch_cap():
    """Three agents are dispatched under a cap of 3, but a limit-1 pool lets only
    one run_once body execute at a time. Dispatch is NOT gated by the pool: tick
    creates all three tasks, and they serialize inside _run on the permit."""
    live = [0]
    log: list[int] = []
    agents = [PoolSlowAgent(n, 0.9, live, log) for n in "abc"]
    pool = SpyPool(limit=1)
    sched = Scheduler(agents, clock=lambda: 1000.0, max_concurrent_agents=3, pool=pool)

    dispatched = await sched.tick()
    assert set(dispatched) == {"a", "b", "c"}, "dispatch must not block on the pool"
    assert len(sched._in_flight) == 3, "all three runs are created; they queue on the permit"
    await sched.drain_in_flight()

    assert max(log) == 1, "the pool must serialize run bodies below the dispatch cap"
    assert pool.max_concurrent == 1
    assert sum(a.ran for a in agents) == 3


async def test_a_clean_run_notes_success_exactly_once():
    a = StubAgent("a", 0.9)
    pool = SpyPool()
    sched = Scheduler([a], clock=lambda: 1000.0, pool=pool)
    await sched.tick()
    await sched.drain_in_flight()
    assert pool.successes == 1
    assert pool.rate_limited == 0


async def test_a_rate_limit_run_notes_congestion_exactly_once():
    a = RateLimitedAgent("a", 0.9)
    pool = SpyPool()
    sched = Scheduler([a], clock=lambda: 1000.0, pool=pool)
    await sched.tick()
    await sched.drain_in_flight()
    assert pool.rate_limited == 1
    assert pool.successes == 0


async def test_a_non_rate_limit_crash_notes_neither_signal():
    """Only a real 429 is congestion. A plain crash (a malformed LLM response, a
    bug) must not shrink the fleet-wide pool -- that would let one broken agent
    throttle every other consumer."""
    a = CrashingAgent("a", 0.9)  # raises RuntimeError("kaput")
    pool = SpyPool()
    sched = Scheduler([a], clock=lambda: 1000.0, pool=pool)
    await sched.tick()
    await sched.drain_in_flight()
    assert pool.rate_limited == 0
    assert pool.successes == 0


async def test_pool_permit_is_released_when_a_run_crashes():
    """The permit is released even when run_once raises -- a leaked permit
    permanently shrinks usable concurrency (at limit 1, a permanent deadlock)."""
    a = CrashingAgent("a", 0.9)
    pool = SpyPool(limit=1)
    sched = Scheduler([a], clock=lambda: 1000.0, pool=pool)
    await sched.tick()
    await sched.drain_in_flight()
    assert pool._active == 0, "a crashing run leaked its pool permit"
    # The pool is still usable: a leaked limit-1 permit would hang the next run.
    b = StubAgent("b", 0.9)
    sched2 = Scheduler([b], clock=lambda: 1000.0, pool=pool)
    await sched2.tick()
    await sched2.drain_in_flight()
    assert b.ran == 1


# --- Phase 4: the strict background catch-up gate ---------------------------
#
# Background work -- embedding indexing and KG extraction -- outranks agent runs
# (settled with the user: "background-first", "strict"). While either indexer
# lags, NO agent is dispatched at all; agents resume only when both are fully
# caught up. The Scheduler grows a `gate_provider` seam mirroring
# `override_provider` EXACTLY: an optional async predicate consulted ONCE per
# tick, BEFORE scoring/dispatch.
#
# Pinned polarity: gate_provider() -> True means OPEN (dispatch allowed), False
# means CLOSED (hold everything). This mirrors override_provider's truthy=act
# convention and makes novelizer's factory read as plain boolean truth --
# `indexer.lag() == 0 and kg_projector.lag() == 0` is True exactly when caught
# up. Default None => gate always OPEN, i.e. today's behavior; kit consumers
# that wire no gate are unaffected (regression-pinned below).
#
# agent_kit stays generic: the seam knows nothing about lag or canon. Only
# novelizer's _make_gate_provider (tested in tests/test_runtime.py) does.
#
# These start RED at construction: Scheduler does not accept `gate_provider=`
# yet -> TypeError: __init__() got an unexpected keyword argument
# 'gate_provider' (and the None-default test reds on a missing `_gate_provider`
# attribute).


class BlockingAgent(StubAgent):
    """run_once parks on an externally-controlled event, so a test can hold a
    run in flight across a later tick and prove the gate does not claw it back."""

    def __init__(self, name, score, release: asyncio.Event):
        super().__init__(name, score)
        self._release = release

    async def run_once(self):
        self.ran += 1
        await self._release.wait()


class RaisingGate:
    """A gate_provider that always raises -- models the real observed
    `sqlite3.OperationalError: database is locked` from an indexer's lag() on
    the scheduler's hot path (see test_kg_catch_up_never_raises_even_if_event
    _store_fails). lag() has no never-raise wrapper, so the scheduler itself
    must absorb this."""

    def __init__(self):
        self.calls = 0

    async def __call__(self) -> bool:
        self.calls += 1
        raise RuntimeError("database is locked")


async def test_gate_provider_none_means_the_gate_is_always_open():
    """Regression: the default seam is exactly today's behavior. With no gate
    wired, a ready agent dispatches, unchanged."""
    a = StubAgent("a", 0.9)
    sched = Scheduler([a], clock=lambda: 1000.0)
    assert sched._gate_provider is None
    assert await sched.tick() == ["a"]
    await sched.drain_in_flight()
    assert a.ran == 1


async def test_an_open_gate_dispatches_exactly_as_if_no_gate_were_wired():
    """The positive control for polarity: gate_provider returning True must not
    change dispatch at all -- both slots fill, same as Phase 3."""
    a = StubAgent("a", 0.9); b = StubAgent("b", 0.5)
    async def gate(): return True
    sched = Scheduler([a, b], clock=lambda: 1000.0, max_concurrent_agents=2, gate_provider=gate)
    assert set(await sched.tick()) == {"a", "b"}
    await sched.drain_in_flight()
    assert a.ran == 1 and b.ran == 1


async def test_a_closed_gate_holds_every_ready_agent_out_of_a_free_slot():
    """The core of strict background-first: with three ready agents scoring
    above zero, off both ladders, and two free slots, a CLOSED gate dispatches
    nothing at all. Nothing about the agents excuses them -- only the gate
    holds them."""
    agents = [StubAgent(n, 0.9) for n in "abc"]
    async def gate(): return False
    sched = Scheduler(agents, clock=lambda: 1000.0, max_concurrent_agents=2, gate_provider=gate)
    assert await sched.tick() == []
    assert len(sched._in_flight) == 0
    assert sum(a.ran for a in agents) == 0


async def test_a_closed_gate_suppresses_even_a_director_override():
    """Precedence, pinned: strict background-first outranks the Director. An
    override names an agent to force, but a closed background gate is consulted
    first and holds everything -- the override does NOT punch through. (The
    plain reading of "background-first beats everything"; the alternative
    "override wins" would let a Director signal race ahead of an indexer that
    must catch up first, which the design rejects.)"""
    a = StubAgent("a", 0.9); b = StubAgent("b", 0.1)
    async def override(): return "b"
    async def gate(): return False
    sched = Scheduler([a, b], clock=lambda: 1000.0, max_concurrent_agents=2,
                      override_provider=override, gate_provider=gate)
    assert await sched.tick() == []
    assert a.ran == 0 and b.ran == 0


async def test_closing_the_gate_never_cancels_an_in_flight_run():
    """THE critical invariant. The gate blocks NEW dispatch only; it never
    kills work already running. Two agents dispatched while the gate was open
    must run to completion even though the gate slams shut under them on the
    next tick -- background catch-up does not claw back in-flight runs
    (design: "In-flight runs are never killed when the gate closes"). cap=3
    leaves a free slot on the second tick, so the gate-closed branch is
    genuinely exercised while both runs stay in flight."""
    release = asyncio.Event()
    a = BlockingAgent("a", 0.9, release); b = BlockingAgent("b", 0.8, release)
    state = {"open": True}
    async def gate(): return state["open"]
    sched = Scheduler([a, b], clock=lambda: 1000.0, max_concurrent_agents=3, gate_provider=gate)

    assert set(await sched.tick()) == {"a", "b"}
    in_flight_before = dict(sched._in_flight)
    assert len(in_flight_before) == 2

    state["open"] = False
    assert await sched.tick() == [], "a closed gate must not dispatch anything new"
    assert dict(sched._in_flight) == in_flight_before, "the gate reached in and touched in-flight runs"
    assert not any(t.cancelled() for t in in_flight_before.values()), "the gate cancelled an in-flight run"
    assert not any(t.done() for t in in_flight_before.values())

    release.set()
    await sched.drain_in_flight()
    assert a.ran == 1 and b.ran == 1, "in-flight runs did not complete after the gate closed"


async def test_a_raising_gate_fails_open_rather_than_freezing_the_room():
    """Fail-open on the hot path. lag() can raise a transient "database is
    locked", and it has no never-raise wrapper of its own. A gate_provider that
    raises must be treated as OPEN (dispatch allowed) and the exception must not
    escape tick() -- a momentary DB lock cannot be allowed to freeze every agent
    in the room."""
    a = StubAgent("a", 0.9)
    gate = RaisingGate()
    sched = Scheduler([a], clock=lambda: 1000.0, gate_provider=gate)
    dispatched = await sched.tick()  # must NOT raise
    assert dispatched == ["a"], "a raising gate must fail open, not hold the room"
    await sched.drain_in_flight()
    assert a.ran == 1
    assert gate.calls == 1, "the gate is consulted once per tick"


async def test_a_held_agent_reports_the_background_catch_up_reason():
    """Visible progress is part of the design (section 8): a held agent's
    eligibility reason must say WHY it is held, so the readout does not look
    like an unexplained hang. A would-be-ready agent under a closed gate reports
    (eligible=False, reason="background catch-up")."""
    a = StubAgent("a", 0.9)
    async def gate(): return False
    emitter = FakeEmitter()
    sched = Scheduler([a], clock=lambda: 1000.0, gate_provider=gate, telemetry=emitter)
    await sched.tick()
    elig = _eligibility(emitter)
    assert [(p.agent_name, p.eligible, p.reason) for p in elig] == [("a", False, "background catch-up")]


async def test_the_gate_reason_replaces_only_the_ready_reason_not_the_truer_ones():
    """Pinned interpretation of "background catch-up for agents that would
    OTHERWISE be ready": the gate relabels only agents whose sole obstacle is
    the gate. A paused or backing-off agent keeps its own, truer reason -- the
    gate is not why THOSE are held, and a readout that blamed the gate for a
    paused agent would mislead."""
    ready = StubAgent("ready", 0.9)
    paused = StubAgent("paused", 0.9); paused.pause()
    backing = StubAgent("backing", 0.9); backing._idle_until = 1050.0
    async def gate(): return False
    emitter = FakeEmitter()
    sched = Scheduler([ready, paused, backing], clock=lambda: 1000.0,
                      gate_provider=gate, telemetry=emitter)
    await sched.tick()
    by_name = {p.agent_name: (p.eligible, p.reason) for p in _eligibility(emitter)}
    assert by_name["ready"] == (False, "background catch-up")
    assert by_name["paused"] == (False, "paused")
    assert by_name["backing"] == (False, "backing off")


async def test_the_gate_reopening_lets_a_held_agent_dispatch_again():
    """Strict blocking is temporary, not terminal: the instant both indexers
    catch up (gate flips back to open), the held agent takes its slot. A gate
    that stayed shut would merely relocate the idleness this project exists to
    remove."""
    a = StubAgent("a", 0.9)
    state = {"open": False}
    async def gate(): return state["open"]
    sched = Scheduler([a], clock=lambda: 1000.0, gate_provider=gate)
    assert await sched.tick() == []
    assert a.ran == 0
    state["open"] = True
    assert await sched.tick() == ["a"]
    await sched.drain_in_flight()
    assert a.ran == 1


# --- properties: closed => zero dispatch; open == Phase 3 -------------------


@settings(deadline=None, max_examples=50)
@given(
    specs=st.lists(
        st.tuples(st.floats(min_value=0.0, max_value=1.0), st.booleans(),
                  st.floats(min_value=0.0, max_value=60.0)),
        min_size=1, max_size=6),
    cap=st.integers(min_value=1, max_value=4),
    gate_open=st.booleans(),
)
async def test_closed_gate_dispatches_nothing_open_gate_matches_no_gate(specs, cap, gate_open):
    """For ANY gate state and ANY roster: a closed gate dispatches nothing at
    all, and an open gate dispatches EXACTLY what an unwired (default-open)
    scheduler would. Two parallel rosters with identical specs make "open ==
    Phase 3" an equality, not an approximation."""
    now = 1000.0

    def build():
        agents = []
        for i, (score, paused, backoff) in enumerate(specs):
            a = StubAgent(f"a{i}", score)
            if paused:
                a.pause()
            a._idle_until = now + backoff
            agents.append(a)
        return agents

    async def gate(): return gate_open
    gated = Scheduler(build(), clock=lambda: now, max_concurrent_agents=cap, gate_provider=gate)
    reference = Scheduler(build(), clock=lambda: now, max_concurrent_agents=cap)  # None => always open

    try:
        gated_dispatched = set(await gated.tick())
        reference_dispatched = set(await reference.tick())
        if gate_open:
            assert gated_dispatched == reference_dispatched, "an open gate must behave exactly as no gate"
        else:
            assert gated_dispatched == set(), "a closed gate must dispatch nothing, ever"
            assert len(gated._in_flight) == 0
    finally:
        await gated.drain_in_flight()
        await reference.drain_in_flight()


@settings(deadline=None, max_examples=25)
@given(n_in_flight=st.integers(min_value=1, max_value=4))
async def test_in_flight_count_is_invariant_across_the_gate_closing(n_in_flight):
    """The other half of "the gate blocks dispatch, never execution": however
    many runs are in flight when the gate closes, that same set is still in
    flight after the closing tick -- none cancelled, none added."""
    now = 1000.0
    release = asyncio.Event()
    agents = [BlockingAgent(f"a{i}", 0.9, release) for i in range(n_in_flight)]
    state = {"open": True}
    async def gate(): return state["open"]
    # cap above the roster so a free slot always remains -> the gate-closed
    # branch runs rather than an early free_slots<=0 return.
    sched = Scheduler(agents, clock=lambda: now, max_concurrent_agents=n_in_flight + 2, gate_provider=gate)
    try:
        assert len(await sched.tick()) == n_in_flight
        in_flight_before = dict(sched._in_flight)
        state["open"] = False
        assert await sched.tick() == []
        assert dict(sched._in_flight) == in_flight_before
        assert not any(t.cancelled() for t in in_flight_before.values())
    finally:
        release.set()
        await sched.drain_in_flight()


class BlockingPool:
    """A pool whose permit is withheld until the test lets go, so a dispatched
    run can be pinned in the "queued on the permit" state across an await
    boundary. Mirrors SpyPool's surface; only the gating differs."""

    def __init__(self) -> None:
        self.granted = asyncio.Event()
        self._release = asyncio.Event()

    @contextlib.asynccontextmanager
    async def slot(self):
        await self._release.wait()
        self.granted.set()
        yield

    def grant(self) -> None:
        self._release.set()

    def note_rate_limited(self) -> None: ...
    def note_success(self) -> None: ...


async def test_a_run_queued_on_a_permit_is_not_reported_as_running():
    """A 429-frozen pool must not look like a hung agent. The run holds a
    dispatch slot -- it really is in flight -- but nothing is executing, so
    status() has to separate "queued on the permit" from "doing work", and the
    eligibility reason has to say so too."""
    a = StubAgent("a", 0.9)
    pool = BlockingPool()
    emitter = FakeEmitter()
    sched = Scheduler([a], clock=lambda: 1000.0, pool=pool, telemetry=emitter)
    await sched.tick()
    for _ in range(5):
        await asyncio.sleep(0)           # let _run reach slot() and block

    st_ = sched.status()[0]
    assert st_["waiting_on_pool"] is True, "no surface distinguishes a pool wait"
    assert st_["running"] is False, "a run with no permit was reported as working"
    await sched._emit_eligibility(1000.0, scores={})
    assert _eligibility(emitter)[-1].reason == "waiting on pool"

    pool.grant()
    await sched.drain_in_flight()
    assert a.ran == 1
    assert sched.status()[0]["waiting_on_pool"] is False
