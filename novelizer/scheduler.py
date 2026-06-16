from __future__ import annotations
import asyncio
import logging
from typing import Sequence
from novelizer.agents.base import BaseAgent
from novelizer.store.models import SignalKind
from novelizer.store.queries import Store

logger = logging.getLogger(__name__)


class Scheduler:
    def __init__(self, agents: Sequence[BaseAgent], store: Store, tick_sleep: float = 1.0) -> None:
        self._agents = list(agents)
        self._store = store
        self._tick_sleep = tick_sleep
        self._running = False

    def pause_agent(self, name: str) -> None:
        for agent in self._agents:
            if agent.name == name:
                agent.pause()

    def resume_agent(self, name: str) -> None:
        for agent in self._agents:
            if agent.name == name:
                agent.resume()

    async def tick(self) -> None:
        # Check for override signals first
        override_target: str | None = None
        signals = await self._store.list_unconsumed_signals()
        for sig in signals:
            if sig.kind == SignalKind.override and sig.target_agent:
                override_target = sig.target_agent
                break

        eligible = [
            a for a in self._agents
            if not a.paused and a.ready_for_interval()
        ]

        if not eligible:
            return

        # If an override targets one of our eligible agents, run it immediately
        if override_target:
            for agent in eligible:
                if agent.name == override_target:
                    logger.info("scheduler: override → running %s", agent.name)
                    await agent.run_once()
                    return

        # Score remaining eligible agents by readiness
        scored = []
        for agent in eligible:
            score = await agent.readiness_check()
            scored.append((score, agent))

        if not scored:
            return

        scored.sort(key=lambda x: x[0], reverse=True)
        best_score, best_agent = scored[0]
        if best_score > 0.0:
            logger.info("scheduler: running %s (score=%.2f)", best_agent.name, best_score)
            await best_agent.run_once()

    async def run(self) -> None:
        self._running = True
        logger.info("scheduler: starting")
        while self._running:
            try:
                await self.tick()
            except Exception:
                logger.exception("scheduler: error in tick")
            await asyncio.sleep(self._tick_sleep)

    def stop(self) -> None:
        self._running = False
