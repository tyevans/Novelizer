from __future__ import annotations
import asyncio

from agent_kit.scheduler import Scheduler
from agent_kit.telemetry import TelemetryEventType


class StubAgent:
    def __init__(self, name, score, interval=0):
        self.name = name; self._score = score; self.interval = interval
        self.paused = False; self._last = -999; self.ran = 0
    async def readiness(self): return self._score
    def ready_for_interval(self, now): return (now - self._last) >= self.interval
    def mark_ran(self, now): self._last = now; self.ran += 1
    def seconds_until_ready(self, now): return max(0.0, self.interval - (now - self._last))
    async def run_once(self): pass
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


async def test_respects_interval_and_concurrency_cap():
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


async def test_crash_consumes_interval_and_records_error():
    a = CrashingAgent("a", 0.9, interval=10)
    sched = Scheduler([a], clock=lambda: 1000.0)
    assert await sched.tick() == ["a"]
    await sched.drain_in_flight()
    status = {s["name"]: s for s in sched.status()}
    assert "RuntimeError: kaput" in status["a"]["last_error"]
    assert status["a"]["run_count"] == 1
    # interval consumed: not eligible again at the same clock
    assert await sched.tick() == []


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
    elig = [e for e in emitter.events
            if e[0] == TelemetryEventType.SCHEDULER_ELIGIBILITY_CHANGED]
    assert len(elig) == 1  # second identical state emits nothing
    assert elig[0][2].reason == "readiness 0"


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
