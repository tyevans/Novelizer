from __future__ import annotations
import asyncio
import logging
import time
from typing import Sequence
from novelizer.store.models import SignalKind

logger = logging.getLogger(__name__)


class Scheduler:
    def __init__(
        self,
        agents: Sequence,
        read_store,
        tick_sleep: float = 1.0,
        clock=time.monotonic,
        max_concurrent_agents: int = 2,
    ) -> None:
        self._agents = list(agents)
        self._read = read_store
        self._tick_sleep = tick_sleep
        self._clock = clock
        self._running = False
        self._max_concurrent = max_concurrent_agents
        self._in_flight: dict[str, asyncio.Task] = {}
        self._last_error: dict[str, str] = {}
        # Incremented on every completed run (success or failure). Lets
        # callers (e.g. the TUI's error reporter) distinguish "still the same
        # stale error" from "the agent ran again and failed again" without
        # relying on error-message content, which is identical across
        # repeated identical failures.
        self._run_count: dict[str, int] = {}

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
            {
                "name": a.name,
                "paused": a.paused,
                "running": a.name in self._in_flight,
                "last_error": self._last_error.get(a.name),
                "run_count": self._run_count.get(a.name, 0),
            }
            for a in self._agents
        ]

    async def tick(self) -> list[str]:
        """Fill free dispatch-pool slots from the readiness-sorted eligible
        list. Does NOT await dispatched agents to completion -- it returns as
        soon as tasks are created, so the tick cadence becomes the dispatch
        cadence, not a wait-for-completion cadence."""
        now = self._clock()
        free_slots = self._max_concurrent - len(self._in_flight)
        if free_slots <= 0:
            return []
        signals = await self._read.list_unconsumed_signals()
        override = next((s.target_agent for s in signals
                         if s.kind == SignalKind.override and s.target_agent), None)
        eligible = [
            a for a in self._agents
            if not a.paused and a.name not in self._in_flight and a.ready_for_interval(now)
        ]
        if not eligible:
            return []

        to_dispatch: list = []
        if override:
            for a in eligible:
                if a.name == override:
                    to_dispatch.append(a)
                    eligible = [x for x in eligible if x.name != override]
                    break

        if len(to_dispatch) < free_slots:
            scored = [(await a.readiness(), a) for a in eligible]
            scored.sort(key=lambda x: x[0], reverse=True)
            for score, a in scored:
                if len(to_dispatch) >= free_slots:
                    break
                if score > 0.0:
                    to_dispatch.append(a)

        dispatched: list[str] = []
        for a in to_dispatch:
            task = asyncio.create_task(self._run(a, now))
            # A dispatched task's failure is recorded via _last_error inside
            # _run (see below) and re-raised within the task so drain_in_flight
            # / direct awaiters still observe it; nothing else awaits fire-and
            # -forget tasks under headless run(), so without this callback
            # asyncio logs "Task exception was never retrieved" for every
            # crashing agent. Retrieve (and discard) it here instead.
            task.add_done_callback(lambda t: t.exception() if not t.cancelled() else None)
            self._in_flight[a.name] = task
            dispatched.append(a.name)
        return dispatched

    async def drain_in_flight(self) -> None:
        """Await every currently in-flight dispatched task to completion.
        Task exceptions are swallowed here (already recorded via
        ``_last_error`` inside ``_run``) -- callers that need synchronous
        "wait for this tick's dispatches to finish" behavior (e.g. tests, or
        a caller stepping the scheduler by hand) should use this rather than
        awaiting ``_in_flight`` values directly."""
        tasks = list(self._in_flight.values())
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)

    async def _run(self, agent, now: float) -> None:
        logger.info("scheduler: running %s", agent.name)
        try:
            await agent.run_once()
        except Exception as e:
            self._last_error[agent.name] = f"{type(e).__name__}: {e}"
            raise
        else:
            self._last_error.pop(agent.name, None)
        finally:
            # mark_ran even on failure: a crashing agent must consume its
            # interval (backoff) instead of staying eligible and hot-looping,
            # which starves every other agent of scheduler slots. mark_ran
            # fires on task COMPLETION (this finally block), not dispatch.
            agent.mark_ran(now)
            self._in_flight.pop(agent.name, None)
            self._run_count[agent.name] = self._run_count.get(agent.name, 0) + 1

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
