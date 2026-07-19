import asyncio
import pytest
from novelizer.scheduler import Scheduler


class StubAgent:
    def __init__(self, name, score, interval=0):
        self.name = name; self._score = score; self.interval = interval
        self.paused = False; self._last = -999; self.ran = 0
    async def readiness(self): return self._score
    def ready_for_interval(self, now): return (now - self._last) >= self.interval
    def mark_ran(self, now): self._last = now; self.ran += 1
    async def run_once(self): pass
    def pause(self): self.paused = True
    def resume(self): self.paused = False


class SlowAgent(StubAgent):
    """Records (start, end) event-loop timestamps around a sleep, so tests can
    prove genuine temporal overlap rather than just fast sequential execution."""
    def __init__(self, name, score, delay=0.05, log=None, interval=0):
        super().__init__(name, score, interval=interval)
        self._delay = delay
        self._log = log if log is not None else []
    async def run_once(self):
        loop = asyncio.get_event_loop()
        start = loop.time()
        await asyncio.sleep(self._delay)
        end = loop.time()
        self._log.append((self.name, start, end))


class StubRead:
    def __init__(self, signals=None): self._signals = signals or []
    async def list_unconsumed_signals(self, target_agent=None): return self._signals


async def _drain(sched):
    """Await every currently in-flight task to completion."""
    tasks = list(sched._in_flight.values())
    if tasks:
        await asyncio.gather(*tasks, return_exceptions=True)


async def test_runs_highest_readiness():
    a = StubAgent("a", 0.2); b = StubAgent("b", 0.9)
    sched = Scheduler([a, b], StubRead(), clock=lambda: 1000.0, max_concurrent_agents=1)
    dispatched = await sched.tick()
    assert dispatched == ["b"]
    await _drain(sched)
    assert b.ran == 1 and a.ran == 0


async def test_skips_paused_and_zero_score():
    a = StubAgent("a", 0.0); b = StubAgent("b", 0.5)
    b.pause()
    sched = Scheduler([a, b], StubRead(), clock=lambda: 1000.0)
    assert await sched.tick() == []


async def test_override_signal_forces_agent():
    from novelizer.store.models import DirectorSignal, SignalKind
    a = StubAgent("a", 0.9); b = StubAgent("b", 0.1)
    sig = DirectorSignal(kind=SignalKind.override, body="", target_agent="b")
    sched = Scheduler([a, b], StubRead([sig]), clock=lambda: 1000.0, max_concurrent_agents=1)
    assert await sched.tick() == ["b"]
    await _drain(sched)


async def test_respects_interval():
    a = StubAgent("a", 0.9, interval=10)
    sched = Scheduler([a], StubRead(), clock=lambda: 1000.0)
    assert await sched.tick() == ["a"]
    await _drain(sched)
    # same clock -> not interval-ready now
    sched2 = Scheduler([a], StubRead(), clock=lambda: 1005.0)
    assert await sched2.tick() == []


async def test_status_reports_paused_and_currently_in_flight():
    a = StubAgent("a", 0.2); b = SlowAgent("b", 0.9, delay=0.05)
    sched = Scheduler([a, b], StubRead(), clock=lambda: 1000.0, max_concurrent_agents=1)
    before = {s["name"]: s for s in sched.status()}
    assert before["a"]["running"] is False and before["b"]["running"] is False
    await sched.tick()  # dispatches b (highest score), does not await completion
    st = {s["name"]: s for s in sched.status()}
    assert st["b"]["running"] is True, "status() must reflect genuine in-flight state"
    sched.pause_agent("a")
    st = {s["name"]: s for s in sched.status()}
    assert st["a"]["paused"] is True and st["b"]["paused"] is False
    await _drain(sched)
    st = {s["name"]: s for s in sched.status()}
    assert st["b"]["running"] is False, "running clears once the task completes"


async def test_tick_returns_promptly_without_awaiting_dispatched_agents():
    """tick() creates tasks and returns immediately -- the dispatch cadence,
    not a wait-for-completion cadence."""
    log = []
    a = SlowAgent("a", 0.9, delay=0.2, log=log)
    sched = Scheduler([a], StubRead(), clock=lambda: 1000.0, max_concurrent_agents=1)
    loop = asyncio.get_event_loop()
    t0 = loop.time()
    dispatched = await sched.tick()
    t1 = loop.time()
    assert dispatched == ["a"]
    assert (t1 - t0) < 0.1, "tick() must not block on the dispatched agent's run_once()"
    await _drain(sched)


async def test_a_crashing_agent_does_not_raise_out_of_tick():
    """Task exceptions surface within the dispatched task itself (recorded via
    last_error), not synchronously out of tick() -- tick() must not crash the
    scheduler loop over a dispatched task's eventual failure."""
    class BoomAgent(StubAgent):
        async def run_once(self):
            raise ValueError("boom")

    a = BoomAgent("a", 0.9)
    sched = Scheduler([a], StubRead(), clock=lambda: 1000.0, max_concurrent_agents=1)
    dispatched = await sched.tick()
    assert dispatched == ["a"]
    await _drain(sched)  # exception raised inside the task, swallowed by gather(return_exceptions=True)
    st = {s["name"]: s for s in sched.status()}
    assert "boom" in st["a"]["last_error"]


async def test_run_survives_a_ticking_agents_exception_and_keeps_selecting_others():
    """Scheduler.run()'s loop is the one existing catch-all around tick() for
    headless use (NovelizerApp._scheduler_loop is the TUI's own, already
    covered by tests/tui/test_app_resilience.py) -- a single agent's repeated
    crash must not stop the room from continuing to run other agents."""
    class BoomAgent(StubAgent):
        async def run_once(self):
            raise ValueError("boom")

    boom = BoomAgent("boom", 0.9)
    healthy = StubAgent("healthy", 0.1)
    sched = Scheduler([boom, healthy], StubRead(), tick_sleep=0.01, clock=lambda: 1000.0)
    task = asyncio.create_task(sched.run())
    try:
        await asyncio.sleep(0.1)
        assert not task.done(), "Scheduler.run() must survive a crashing agent, not exit"
    finally:
        sched.stop()
        await asyncio.wait_for(task, timeout=1.0)
        await _drain(sched)


async def test_crashing_agent_consumes_its_interval_so_others_can_run():
    """A crash must still mark_ran (on task completion): otherwise the crasher
    stays eligible and, outscoring everyone, hot-loops forever while the rest
    of the roster starves (observed live: author 502-looping while nothing
    else ran)."""
    class BoomAgent(StubAgent):
        async def run_once(self):
            raise ValueError("boom")

    boom = BoomAgent("boom", 0.9, interval=10)
    healthy = StubAgent("healthy", 0.1)
    sched = Scheduler([boom, healthy], StubRead(), clock=lambda: 1000.0, max_concurrent_agents=1)
    await sched.tick()
    await _drain(sched)
    assert boom.ran == 1  # mark_ran despite the crash -> interval backoff
    assert await sched.tick() == ["healthy"]
    await _drain(sched)


async def test_status_reports_run_count_incrementing_on_every_completion():
    """run_count increments on every completed run (success or failure) --
    lets callers distinguish a repeated identical failure from a stale one,
    since last_error's text alone is unchanged across repeats."""
    class BoomAgent(StubAgent):
        async def run_once(self):
            raise ValueError("boom")

    a = BoomAgent("a", 0.9, interval=1)
    sched = Scheduler([a], StubRead(), clock=lambda: 1000.0, max_concurrent_agents=1)
    await sched.tick()
    await _drain(sched)
    st = {s["name"]: s for s in sched.status()}
    assert st["a"]["run_count"] == 1
    await sched.tick()
    await _drain(sched)
    st = {s["name"]: s for s in sched.status()}
    assert st["a"]["run_count"] == 1, "interval not yet elapsed on fixed clock -- no re-dispatch"


async def test_status_reports_last_error_and_clears_on_success():
    class FlakyAgent(StubAgent):
        def __init__(self, *a, **kw):
            super().__init__(*a, **kw)
            self.fail_next = True
        async def run_once(self):
            if self.fail_next:
                self.fail_next = False
                raise ValueError("kaboom")

    now = [1000.0]
    flaky = FlakyAgent("flaky", 0.9, interval=10)
    sched = Scheduler([flaky], StubRead(), clock=lambda: now[0], max_concurrent_agents=1)
    await sched.tick()
    await _drain(sched)
    st = {s["name"]: s for s in sched.status()}
    assert "kaboom" in st["flaky"]["last_error"]
    now[0] = 1020.0  # past interval -> eligible again, succeeds this time
    assert await sched.tick() == ["flaky"]
    await _drain(sched)
    st = {s["name"]: s for s in sched.status()}
    assert st["flaky"]["last_error"] is None


# --- Task 11: pool-size dispatch shape ---

async def test_tick_dispatches_up_to_max_concurrent_agents():
    a = SlowAgent("a", 0.9, delay=0.05)
    b = SlowAgent("b", 0.8, delay=0.05)
    sched = Scheduler([a, b], StubRead(), clock=lambda: 1000.0, max_concurrent_agents=2)
    dispatched = await sched.tick()
    assert set(dispatched) == {"a", "b"}
    assert set(sched._in_flight.keys()) == {"a", "b"}
    await _drain(sched)


async def test_tick_does_not_exceed_pool_size():
    a = StubAgent("a", 0.9); b = StubAgent("b", 0.5); c = StubAgent("c", 0.1)
    sched = Scheduler([a, b, c], StubRead(), clock=lambda: 1000.0, max_concurrent_agents=2)
    dispatched = await sched.tick()
    assert set(dispatched) == {"a", "b"}, "highest-scored two of three, pool size 2"
    await _drain(sched)


async def test_agent_already_in_flight_is_excluded_from_next_tick():
    log = []
    a = SlowAgent("a", 0.9, delay=0.1, log=log)
    sched = Scheduler([a], StubRead(), clock=lambda: 1000.0, max_concurrent_agents=2)
    first = await sched.tick()
    assert first == ["a"]
    second = await sched.tick()
    assert second == [], "in-flight agent must not be re-dispatched"
    await _drain(sched)


async def test_status_reflects_agents_currently_in_flight():
    a = SlowAgent("a", 0.9, delay=0.05)
    b = StubAgent("b", 0.1)
    sched = Scheduler([a, b], StubRead(), clock=lambda: 1000.0, max_concurrent_agents=1)
    await sched.tick()  # dispatches only a (higher score, pool size 1)
    st = {s["name"]: s for s in sched.status()}
    assert st["a"]["running"] is True
    assert st["b"]["running"] is False
    await _drain(sched)
    st = {s["name"]: s for s in sched.status()}
    assert st["a"]["running"] is False


# --- Task 12: overlap proof obligation ---

async def test_two_slow_agents_run_overlapped_not_sequentially():
    """Two SlowAgents whose run_once() each sleep, dispatched via tick() at
    pool size 2 and drained to completion: agent B's start must be BEFORE
    agent A's end -- proof of genuine overlap, not just fast sequential
    execution."""
    log = []
    a = SlowAgent("a", 0.9, delay=0.08, log=log)
    b = SlowAgent("b", 0.8, delay=0.08, log=log)
    sched = Scheduler([a, b], StubRead(), clock=lambda: 1000.0, max_concurrent_agents=2)
    await sched.tick()
    await _drain(sched)
    by_name = {name: (start, end) for name, start, end in log}
    assert by_name["b"][0] < by_name["a"][1], "agent b must start before agent a ends (overlap)"


async def test_two_slow_agents_do_not_overlap_at_pool_size_1():
    """Sanity check that the overlap test above actually discriminates: at
    pool size 1, only one agent dispatches per tick, so no overlap is
    possible until the first completes and a second tick dispatches the
    next."""
    log = []
    a = SlowAgent("a", 0.9, delay=0.05, log=log, interval=1)
    b = SlowAgent("b", 0.8, delay=0.05, log=log, interval=1)
    sched = Scheduler([a, b], StubRead(), clock=lambda: 1000.0, max_concurrent_agents=1)
    await sched.tick()  # dispatches only a
    await _drain(sched)
    await sched.tick()  # a is now interval-backed-off (fixed clock); b, never dispatched, still eligible
    await _drain(sched)
    by_name = {name: (start, end) for name, start, end in log}
    assert "a" in by_name and "b" in by_name
    assert by_name["b"][0] >= by_name["a"][1], "pool size 1 must not overlap"


# --- Task 13: no-double-dispatch across repeated ticks + pool-1 serial equivalence ---

async def test_same_agent_never_double_dispatched_across_ticks():
    log = []
    a = SlowAgent("a", 0.9, delay=0.1, log=log, interval=0)
    sched = Scheduler([a], StubRead(), clock=lambda: 1000.0, max_concurrent_agents=2)
    created_tasks = set()
    for _ in range(5):
        await sched.tick()
        if "a" in sched._in_flight:
            created_tasks.add(id(sched._in_flight["a"]))
        await asyncio.sleep(0)  # let the loop schedule, but delay(0.1) keeps it in flight
    assert len(created_tasks) == 1, "only one asyncio.Task ever created for the agent"
    await _drain(sched)


async def test_pool_size_1_reproduces_todays_serial_ordering_exactly():
    """Given three eligible agents with distinct readiness scores and pool
    size 1: tick() dispatches only the highest-scored one, and it must
    complete (leave _in_flight) before the next tick() dispatches another --
    no overlap, proving pool size 1 is a true serial fallback."""
    log = []
    a = SlowAgent("a", 0.9, delay=0.02, log=log, interval=1)
    b = SlowAgent("b", 0.5, delay=0.02, log=log, interval=1)
    c = SlowAgent("c", 0.1, delay=0.02, log=log, interval=1)
    sched = Scheduler([a, b, c], StubRead(), clock=lambda: 1000.0, max_concurrent_agents=1)

    first = await sched.tick()
    assert first == ["a"]
    await _drain(sched)

    second = await sched.tick()
    assert second == ["b"]
    await _drain(sched)

    third = await sched.tick()
    assert third == ["c"]
    await _drain(sched)

    order = [name for name, _, _ in log]
    assert order == ["a", "b", "c"]
    # no pair overlaps
    spans = {name: (start, end) for name, start, end in log}
    assert spans["a"][1] <= spans["b"][0]
    assert spans["b"][1] <= spans["c"][0]
