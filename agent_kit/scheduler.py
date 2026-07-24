from __future__ import annotations
import asyncio
import logging
import time
from typing import Awaitable, Callable, Sequence

from agent_kit.base import _is_rate_limit_error
from agent_kit.telemetry import (
    SchedulerEligibilityChanged,
    SchedulerPicked,
    TelemetryEventType,
)

logger = logging.getLogger(__name__)


class Scheduler:
    """Readiness-sorted dispatch pool, extracted verbatim from novelizer's
    Scheduler. The one seam change: the Director-signal override lookup is
    an injectable override_provider (async () -> agent name | None) instead
    of a read_store query — domains without a Director pass nothing."""

    def __init__(
        self,
        agents: Sequence,
        tick_sleep: float = 1.0,
        clock=time.monotonic,
        max_concurrent_agents: int = 2,
        telemetry=None,
        override_provider: Callable[[], Awaitable[str | None]] | None = None,
        pool=None,
    ) -> None:
        self._agents = list(agents)
        self._tick_sleep = tick_sleep
        self._clock = clock
        self._telemetry = telemetry
        self._override_provider = override_provider
        # Optional shared LLM concurrency ceiling (AdaptivePool). None => today's
        # unlimited behavior, mirroring the override_provider seam. When present,
        # a permit gates each whole run inside _run, and the same pool object is
        # shared with the KG drain -- one fleet-wide ceiling on a single endpoint.
        self._pool = pool
        self._running = False
        self._max_concurrent = max_concurrent_agents
        self._in_flight: dict[str, asyncio.Task] = {}
        self._last_error: dict[str, str] = {}
        self._eligibility: dict[str, tuple[bool, str]] = {}
        self._last_completed: str | None = None
        # Incremented on every completed run (success or failure). Lets
        # callers distinguish "still the same stale error" from "ran again
        # and failed again" without relying on error-message content.
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
            await self._emit_eligibility(now, scores={})
            return []
        override = await self._override_provider() if self._override_provider else None
        eligible = [
            a for a in self._agents
            if not a.paused and a.name not in self._in_flight and a.ready(now)
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
            task = asyncio.create_task(self._run(a))
            # Retrieve (and discard) the exception so fire-and-forget crashes
            # don't log "Task exception was never retrieved"; failures are
            # recorded via _last_error inside _run and re-raised within the
            # task for direct awaiters.
            task.add_done_callback(lambda t: t.exception() if not t.cancelled() else None)
            self._in_flight[a.name] = task
            dispatched.append(a.name)
        return dispatched

    async def _emit_eligibility(self, now: float, scores: dict[str, float]) -> None:
        """One eligibility_changed per agent per state *change* — quiet log,
        not a per-tick heartbeat."""
        if self._telemetry is None:
            return
        for a in self._agents:
            if a.paused:
                state = (False, "paused")
            elif a.name in self._in_flight:
                state = (False, "running")
            elif not a.ready(now):
                state = (False, "backing off")
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
        ``_last_error`` inside ``_run``)."""
        tasks = list(self._in_flight.values())
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)

    async def _run(self, agent) -> None:
        logger.info("scheduler: running %s", agent.name)
        try:
            if self._pool is None:
                await agent.run_once()
            else:
                # A permit covers the whole run. On the way out, feed the pool
                # exactly one AIMD signal: congestion on a real 429, success on a
                # clean run, and nothing at all on a plain crash -- a malformed
                # response or a bug is not congestion, and must not shrink the
                # fleet-wide ceiling every other consumer draws from. The permit
                # is released by slot()'s own finally whichever way the body exits.
                async with self._pool.slot():
                    try:
                        await agent.run_once()
                    except Exception as e:
                        if _is_rate_limit_error(e):
                            self._pool.note_rate_limited()
                        raise
                    else:
                        self._pool.note_success()
        except Exception as e:
            self._last_error[agent.name] = f"{type(e).__name__}: {e}"
            raise
        else:
            self._last_error.pop(agent.name, None)
        finally:
            # A crashing agent no longer hot-loops the pool: run_once advances
            # the fail ladder on every raise, so ready() holds it out on its
            # own. Backing off is the ladder's job now, not the scheduler's.
            self._in_flight.pop(agent.name, None)
            self._run_count[agent.name] = self._run_count.get(agent.name, 0) + 1
            # Sticky display marker, distinct from the honest in-flight
            # "running" flag.
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
