from __future__ import annotations
import asyncio
import logging
import time
from typing import Optional, Sequence
from novelizer.store.models import SignalKind

logger = logging.getLogger(__name__)


class Scheduler:
    def __init__(self, agents: Sequence, read_store, tick_sleep: float = 1.0, clock=time.monotonic) -> None:
        self._agents = list(agents)
        self._read = read_store
        self._tick_sleep = tick_sleep
        self._clock = clock
        self._running = False
        self._last_ran: Optional[str] = None

    def pause_agent(self, name: str) -> None:
        for a in self._agents:
            if a.name == name:
                a.pause()

    def resume_agent(self, name: str) -> None:
        for a in self._agents:
            if a.name == name:
                a.resume()

    def status(self) -> list:
        return [
            {"name": a.name, "paused": a.paused, "running": a.name == self._last_ran}
            for a in self._agents
        ]

    async def tick(self) -> Optional[str]:
        now = self._clock()
        signals = await self._read.list_unconsumed_signals()
        override = next((s.target_agent for s in signals
                         if s.kind == SignalKind.override and s.target_agent), None)
        eligible = [a for a in self._agents if not a.paused and a.ready_for_interval(now)]
        if not eligible:
            return None
        if override:
            for a in eligible:
                if a.name == override:
                    await self._run(a, now)
                    return a.name
        scored = [(await a.readiness(), a) for a in eligible]
        scored.sort(key=lambda x: x[0], reverse=True)
        best_score, best = scored[0]
        if best_score > 0.0:
            await self._run(best, now)
            return best.name
        return None

    async def _run(self, agent, now: float) -> None:
        logger.info("scheduler: running %s", agent.name)
        await agent.run_once()
        agent.mark_ran(now)
        self._last_ran = agent.name

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
