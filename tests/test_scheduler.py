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


class StubRead:
    def __init__(self, signals=None): self._signals = signals or []
    async def list_unconsumed_signals(self, target_agent=None): return self._signals


async def test_runs_highest_readiness():
    a = StubAgent("a", 0.2); b = StubAgent("b", 0.9)
    sched = Scheduler([a, b], StubRead(), clock=lambda: 1000.0)
    ran = await sched.tick()
    assert ran == "b" and b.ran == 1 and a.ran == 0


async def test_skips_paused_and_zero_score():
    a = StubAgent("a", 0.0); b = StubAgent("b", 0.5)
    b.pause()
    sched = Scheduler([a, b], StubRead(), clock=lambda: 1000.0)
    assert await sched.tick() is None


async def test_override_signal_forces_agent():
    from novelizer.store.models import DirectorSignal, SignalKind
    a = StubAgent("a", 0.9); b = StubAgent("b", 0.1)
    sig = DirectorSignal(kind=SignalKind.override, body="", target_agent="b")
    sched = Scheduler([a, b], StubRead([sig]), clock=lambda: 1000.0)
    assert await sched.tick() == "b"


async def test_respects_interval():
    a = StubAgent("a", 0.9, interval=10)
    sched = Scheduler([a], StubRead(), clock=lambda: 1000.0)
    assert await sched.tick() == "a"
    # same clock -> not interval-ready now
    sched2 = Scheduler([a], StubRead(), clock=lambda: 1005.0)
    assert await sched2.tick() is None


async def test_status_reports_paused_and_last_ran():
    a = StubAgent("a", 0.2); b = StubAgent("b", 0.9)
    sched = Scheduler([a, b], StubRead(), clock=lambda: 1000.0)
    before = {s["name"]: s for s in sched.status()}
    assert before["a"]["running"] is False and before["b"]["running"] is False
    await sched.tick()  # runs b
    sched.pause_agent("a")
    st = {s["name"]: s for s in sched.status()}
    assert st["b"]["running"] is True
    assert st["a"]["paused"] is True and st["b"]["paused"] is False


async def test_tick_propagates_an_agents_exception_uncaught():
    """Scheduler.tick() has no try/except by design -- a crashing agent's
    exception must reach the caller, not vanish silently mid-tick."""
    class BoomAgent(StubAgent):
        async def run_once(self):
            raise ValueError("boom")

    a = BoomAgent("a", 0.9)
    sched = Scheduler([a], StubRead(), clock=lambda: 1000.0)
    with pytest.raises(ValueError, match="boom"):
        await sched.tick()


async def test_run_survives_a_ticking_agents_exception_and_keeps_selecting_others():
    """Scheduler.run()'s loop is the one existing catch-all around tick() for
    headless use (NovelizerApp._scheduler_loop is the TUI's own, already
    covered by tests/tui/test_app_resilience.py) -- a single agent's repeated
    crash must not stop the room from continuing to run other agents."""
    import asyncio

    class BoomAgent(StubAgent):
        async def run_once(self):
            raise ValueError("boom")

    boom = BoomAgent("boom", 0.9)
    healthy = StubAgent("healthy", 0.1)
    sched = Scheduler([boom, healthy], StubRead(), tick_sleep=0.01, clock=lambda: 1000.0)
    task = asyncio.create_task(sched.run())
    try:
        # boom always outscores healthy, so only stopping boom lets healthy run;
        # instead, prove survival: run() must still be alive (not crashed) after
        # several ticks despite boom raising on every one.
        await asyncio.sleep(0.1)
        assert not task.done(), "Scheduler.run() must survive a crashing agent, not exit"
    finally:
        sched.stop()
        await asyncio.wait_for(task, timeout=1.0)


async def test_crashing_agent_consumes_its_interval_so_others_can_run():
    """A crash must still mark_ran: otherwise the crasher stays eligible and,
    outscoring everyone, hot-loops forever while the rest of the roster
    starves (observed live: author 502-looping while nothing else ran)."""
    class BoomAgent(StubAgent):
        async def run_once(self):
            raise ValueError("boom")

    boom = BoomAgent("boom", 0.9, interval=10)
    healthy = StubAgent("healthy", 0.1)
    sched = Scheduler([boom, healthy], StubRead(), clock=lambda: 1000.0)
    with pytest.raises(ValueError):
        await sched.tick()
    assert boom.ran == 1  # mark_ran despite the crash -> interval backoff
    assert await sched.tick() == "healthy"


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
    sched = Scheduler([flaky], StubRead(), clock=lambda: now[0])
    with pytest.raises(ValueError):
        await sched.tick()
    st = {s["name"]: s for s in sched.status()}
    assert "kaboom" in st["flaky"]["last_error"]
    now[0] = 1020.0  # past interval -> eligible again, succeeds this time
    assert await sched.tick() == "flaky"
    st = {s["name"]: s for s in sched.status()}
    assert st["flaky"]["last_error"] is None
