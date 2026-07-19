from __future__ import annotations
import asyncio
import logging
import time
from typing import Optional, Sequence
from novelizer.store.models import SignalKind
from novelizer.telemetry.events import (
    TelemetryEventType, SchedulerPicked, SchedulerEligibilityChanged,
)

logger = logging.getLogger(__name__)


class Scheduler:
    def __init__(self, agents: Sequence, read_store, tick_sleep: float = 1.0,
                 clock=time.monotonic, telemetry=None) -> None:
        self._agents = list(agents)
        self._read = read_store
        self._tick_sleep = tick_sleep
        self._clock = clock
        self._telemetry = telemetry
        self._running = False
        self._last_ran: Optional[str] = None
        self._last_error: dict[str, str] = {}
        self._eligibility: dict[str, tuple[bool, str]] = {}

    def pause_agent(self, name: str) -> None:
        for a in self._agents:
            if a.name == name:
                a.pause()

    def resume_agent(self, name: str) -> None:
        for a in self._agents:
            if a.name == name:
                a.resume()

    def status(self) -> list:
        now = self._clock()
        return [
            {
                "name": a.name,
                "paused": a.paused,
                "running": a.name == self._last_ran,
                "last_error": self._last_error.get(a.name),
                "next_ready_in": a.seconds_until_ready(now) if hasattr(a, "seconds_until_ready") else 0.0,
            }
            for a in self._agents
        ]

    async def tick(self) -> Optional[str]:
        now = self._clock()
        signals = await self._read.list_unconsumed_signals()
        override = next((s.target_agent for s in signals
                         if s.kind == SignalKind.override and s.target_agent), None)
        eligible = [a for a in self._agents if not a.paused and a.ready_for_interval(now)]
        if not eligible:
            await self._emit_eligibility(now, scores={})
            return None
        if override:
            for a in eligible:
                if a.name == override:
                    await self._emit_eligibility(now, scores={})
                    await self._run(a, now)
                    return a.name
        scored = [(await a.readiness(), a) for a in eligible]
        scored.sort(key=lambda x: x[0], reverse=True)
        best_score, best = scored[0]
        await self._emit_eligibility(now, scores={a.name: s for s, a in scored})
        if best_score > 0.0:
            await self._run(best, now)
            return best.name
        return None

    async def _emit_eligibility(self, now: float, scores: dict[str, float]) -> None:
        """One eligibility_changed per agent per state *change* — quiet log,
        not a per-tick heartbeat. Reasons mirror exactly the predicates tick
        just evaluated."""
        if self._telemetry is None:
            return
        for a in self._agents:
            if a.paused:
                state = (False, "paused")
            elif not a.ready_for_interval(now):
                state = (False, "interval not elapsed")
            elif a.name in scores and scores[a.name] <= 0.0:
                state = (False, "readiness 0")
            else:
                state = (True, "ready")
            if self._eligibility.get(a.name) != state:
                self._eligibility[a.name] = state
                await self._telemetry.emit(
                    TelemetryEventType.SCHEDULER_ELIGIBILITY_CHANGED, a.name,
                    SchedulerEligibilityChanged(agent_name=a.name, eligible=state[0], reason=state[1]),
                )

    async def _run(self, agent, now: float) -> None:
        logger.info("scheduler: running %s", agent.name)
        if self._telemetry is not None:
            await self._telemetry.emit(
                TelemetryEventType.SCHEDULER_PICKED, agent.name,
                SchedulerPicked(agent_name=agent.name),
            )
        try:
            await agent.run_once()
        except Exception as e:
            self._last_error[agent.name] = f"{type(e).__name__}: {e}"
            raise
        else:
            self._last_error.pop(agent.name, None)
            self._last_ran = agent.name
        finally:
            # mark_ran even on failure: a crashing agent must consume its
            # interval (backoff) instead of staying eligible and hot-looping,
            # which starves every other agent of scheduler slots.
            agent.mark_ran(now)

    async def run(self) -> None:
        self._running = True
        while self._running:
            try:
                await self.tick()
            except Exception:
                logger.exception("scheduler: error in tick")
            await asyncio.sleep(self._tick_sleep)

    def stop(self) -> None:
        self._running = False
