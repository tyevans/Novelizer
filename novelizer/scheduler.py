from __future__ import annotations
import asyncio
import logging
import time
from typing import Sequence
from novelizer.store.models import SignalKind
from novelizer.telemetry.events import (
    TelemetryEventType, SchedulerPicked, SchedulerEligibilityChanged,
)

logger = logging.getLogger(__name__)


class Scheduler:
    def __init__(
        self,
        agents: Sequence,
        read_store,
        tick_sleep: float = 1.0,
        clock=time.monotonic,
        max_concurrent_agents: int = 2,
        telemetry=None,
    ) -> None:
        self._agents = list(agents)
        self._read = read_store
        self._tick_sleep = tick_sleep
        self._clock = clock
        self._telemetry = telemetry
        self._running = False
        self._max_concurrent = max_concurrent_agents
        self._in_flight: dict[str, asyncio.Task] = {}
        self._last_error: dict[str, str] = {}
        self._eligibility: dict[str, tuple[bool, str]] = {}
        self._last_completed: str | None = None
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

    def pause_all(self) -> list[str]:
        """Pause every not-yet-paused agent. Returns the names actually
        paused by this call, so a caller can resume only those later
        without clobbering agents that were already individually paused."""
        paused = []
        for a in self._agents:
            if not a.paused:
                a.pause()
                paused.append(a.name)
        return paused

    def resume_agents(self, names) -> None:
        for a in self._agents:
            if a.name in names:
                a.resume()

    def status(self) -> list:
        now = self._clock()
        return [
            {
                "name": a.name,
                "paused": a.paused,
                "running": a.name in self._in_flight,
                "last_error": self._last_error.get(a.name),
                "last_completed": a.name == self._last_completed,
                "run_count": self._run_count.get(a.name, 0),
                "next_ready_in": a.seconds_until_ready(now) if hasattr(a, "seconds_until_ready") else 0.0,
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
            # Nothing can be dispatched, but paused/running/interval states
            # are still cheap to evaluate — keep the eligibility trace honest.
            await self._emit_eligibility(now, scores={})
            return []
        signals = await self._read.list_unconsumed_signals()
        override = next((s.target_agent for s in signals
                         if s.kind == SignalKind.override and s.target_agent), None)
        eligible = [
            a for a in self._agents
            if not a.paused and a.name not in self._in_flight and a.ready_for_interval(now)
        ]
        if not eligible:
            await self._emit_eligibility(now, scores={})
            return []

        to_dispatch: list = []
        if override:
            for a in eligible:
                if a.name == override:
                    to_dispatch.append(a)
                    eligible = [x for x in eligible if x.name != override]
                    break

        scores: dict[str, float] = {}
        if len(to_dispatch) < free_slots:
            scored = [(await a.readiness(), a) for a in eligible]
            scored.sort(key=lambda x: x[0], reverse=True)
            scores = {a.name: s for s, a in scored}
            for score, a in scored:
                if len(to_dispatch) >= free_slots:
                    break
                if score > 0.0:
                    to_dispatch.append(a)
        await self._emit_eligibility(now, scores)

        dispatched: list[str] = []
        for a in to_dispatch:
            if self._telemetry is not None:
                await self._telemetry.emit(
                    TelemetryEventType.SCHEDULER_PICKED, a.name,
                    SchedulerPicked(agent_name=a.name),
                )
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

    async def _emit_eligibility(self, now: float, scores: dict[str, float]) -> None:
        """One eligibility_changed per agent per state *change* — quiet log,
        not a per-tick heartbeat. Reasons mirror the predicates tick just
        evaluated; an agent whose readiness was not scored this tick (pool
        full, or the override consumed the free slots) reports the state its
        cheap predicates imply."""
        if self._telemetry is None:
            return
        for a in self._agents:
            if a.paused:
                state = (False, "paused")
            elif a.name in self._in_flight:
                state = (False, "running")
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
            # Sticky display marker, distinct from the honest in-flight
            # "running" flag: fast agents complete between status polls, so
            # the TUI needs "who acted most recently" to have anything to
            # show when the pool is momentarily empty.
            self._last_completed = agent.name

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
