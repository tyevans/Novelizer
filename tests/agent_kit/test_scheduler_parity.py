"""Behavioral parity between agent_kit.Scheduler and novelizer's Scheduler.

The temporary-copy honesty check: until the novelizer cutover campaign,
identical scripted scenarios must produce identical dispatch traces. Tests
are exempt from import contracts, so importing novelizer here is fine.
"""
from __future__ import annotations

from agent_kit.scheduler import Scheduler as KitScheduler
from novelizer.scheduler import Scheduler as NovelizerScheduler


class StubAgent:
    def __init__(self, name, scores, interval=5):
        self.name = name; self._scores = list(scores); self.interval = interval
        self.paused = False; self._last = -999; self.ran = 0
    async def readiness(self):
        return self._scores.pop(0) if self._scores else 0.0
    def ready_for_interval(self, now): return (now - self._last) >= self.interval
    def mark_ran(self, now): self._last = now; self.ran += 1
    def seconds_until_ready(self, now): return max(0.0, self.interval - (now - self._last))
    async def run_once(self): pass
    def pause(self): self.paused = True
    def resume(self): self.paused = False


class StubRead:
    async def list_unconsumed_signals(self, target_agent=None): return []


SCENARIO = [
    ("a", [0.9, 0.1, 0.5, 0.0]),
    ("b", [0.2, 0.8, 0.5, 0.0]),
    ("c", [0.0, 0.0, 0.9, 0.9]),
]
CLOCKS = [1000.0, 1000.0, 1006.0, 1012.0]


async def _trace(make_sched):
    agents = [StubAgent(n, list(s)) for n, s in SCENARIO]
    trace = []
    for t in CLOCKS:
        sched = make_sched(agents, t)
        trace.append(await sched.tick())
        await sched.drain_in_flight()
    return trace


async def test_identical_dispatch_traces():
    kit = await _trace(lambda ags, t: KitScheduler(
        ags, clock=lambda: t, max_concurrent_agents=2))
    nov = await _trace(lambda ags, t: NovelizerScheduler(
        ags, StubRead(), clock=lambda: t, max_concurrent_agents=2))
    assert kit == nov
