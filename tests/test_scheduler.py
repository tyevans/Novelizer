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
