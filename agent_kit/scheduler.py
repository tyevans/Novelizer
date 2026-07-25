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
        gate_provider: Callable[[], Awaitable[bool]] | None = None,
        pool=None,
    ) -> None:
        self._agents = list(agents)
        self._tick_sleep = tick_sleep
        self._clock = clock
        self._telemetry = telemetry
        self._override_provider = override_provider
        # Optional strict background-first gate (async () -> True == OPEN). None =>
        # today's always-open behavior, mirroring the override_provider seam. When
        # present and CLOSED, tick() dispatches nothing at all -- background
        # catch-up (embedding/KG) outranks every agent run. The seam stays generic:
        # it knows nothing about lag or canon; only novelizer's factory does.
        self._gate_provider = gate_provider
        # Optional shared LLM concurrency ceiling (AdaptivePool). None => today's
        # unlimited behavior, mirroring the override_provider seam. When present,
        # a permit gates each whole run inside _run, and the same pool object is
        # shared with the KG drain -- one fleet-wide ceiling on a single endpoint.
        self._pool = pool
        self._running = False
        self._max_concurrent = max_concurrent_agents
        self._in_flight: dict[str, asyncio.Task] = {}
        # Names dispatched but still queued on an LLM permit. Dispatch is
        # deliberately NOT gated on the pool (tick creates the task; the run
        # queues inside _run), so without this set a pool frozen by 429s is
        # indistinguishable from a hung agent: both are "running" forever.
        self._awaiting_pool: set[str] = set()
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
                # "running" is work actually executing: a run still queued on an
                # LLM permit holds a dispatch slot but is doing nothing, and
                # reports waiting_on_pool instead.
                "running": a.name in self._in_flight and a.name not in self._awaiting_pool,
                "waiting_on_pool": a.name in self._awaiting_pool,
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
        # Strict background-first gate, consulted ONCE per tick (lag() hits the DB)
        # and BEFORE the override lookup and scoring: while embedding/KG catch-up
        # lags, hold every agent. Background work outranks even a Director
        # override, so a closed gate must suppress the override too -- hence this
        # precedes the override lookup rather than following it.
        #
        # Fail OPEN on a raise: novelizer's gate calls indexer.lag(), which can
        # surface a transient "database is locked" and has no never-raise wrapper
        # of its own (see kg_catch_up's failure-tolerance). A momentary DB lock
        # must not freeze the whole room, so a raising probe is treated as OPEN and
        # the exception is swallowed here rather than escaping tick().
        gate_open = True
        if self._gate_provider is not None:
            try:
                gate_open = await self._gate_provider()
            except Exception:
                logger.warning("scheduler: gate probe raised; failing open", exc_info=True)
                gate_open = True
        if not gate_open:
            # Emit eligibility so held agents show WHY ("background catch-up"),
            # then dispatch nothing. _in_flight is deliberately untouched: the gate
            # blocks NEW dispatch only and never cancels a run already in flight.
            await self._emit_eligibility(now, scores={}, gate_open=False)
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

    async def _emit_eligibility(self, now: float, scores: dict[str, float],
                                gate_open: bool = True) -> None:
        """One eligibility_changed per agent per state *change* — quiet log,
        not a per-tick heartbeat.

        gate_open threads the strict background-first gate through: when it is
        closed, an agent whose ONLY obstacle is the gate -- would-be-ready, not
        paused, not in-flight, not backing off, not scored-zero -- reports
        "background catch-up" instead of "ready". The gate reason replaces the
        "ready" reason only; paused / running / backing off / readiness 0 keep
        their own, truer reasons (the gate is not why THOSE are held)."""
        if self._telemetry is None:
            return
        for a in self._agents:
            if a.paused:
                state = (False, "paused")
            elif a.name in self._awaiting_pool:
                state = (False, "waiting on pool")
            elif a.name in self._in_flight:
                state = (False, "running")
            elif not a.ready(now):
                state = (False, "backing off")
            elif a.name in scores and scores[a.name] <= 0.0:
                state = (False, "readiness 0")
            elif not gate_open:
                state = (False, "background catch-up")
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
                #
                # The wait for the permit is marked separately from the run
                # itself: the body below only starts once the permit is in hand,
                # so clearing the mark there is exactly the moment work begins.
                self._awaiting_pool.add(agent.name)
                async with self._pool.slot():
                    self._awaiting_pool.discard(agent.name)
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
            # Belt and braces: a cancellation while queued would otherwise leave
            # the name marked as waiting forever.
            self._awaiting_pool.discard(agent.name)
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
