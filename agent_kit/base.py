from __future__ import annotations
import asyncio
import logging
import math
import time
import uuid
from collections.abc import Awaitable, Callable
from typing import Protocol

from agent_kit.run_context import current_agent_name, current_run_id
from agent_kit.telemetry import (
    AgentRunCancelled,
    AgentRunFailed,
    AgentRunFinished,
    AgentRunStarted,
    TelemetryEventType,
)

logger = logging.getLogger(__name__)

# --- backoff ladders, in absolute seconds ---------------------------------
#
# Their unit is seconds rather than a multiple of an operator-configured
# interval, because dispatch no longer waits on a clock: there is no interval
# left to multiply. These are the only backpressure the chassis applies.

# A crashed run says nothing about how long the cause will last, so the first
# retry is nearly immediate — most agent failures are transient (a malformed
# LLM response, a lost connection) and a long first backoff would idle a
# working agent for a fault it would have shrugged off on the next attempt.
FAIL_BACKOFF_BASE_S = 2.0

# A genuinely broken agent doubles its way here in five failures and then stops
# growing. Kept well under the idle cap: a crash loop should still be re-probed
# often enough that the room recovers promptly once the cause clears, and its
# telemetry keeps flowing so the failure stays visible instead of going silent.
FAIL_BACKOFF_CAP_S = 60.0

# A run that completed but committed nothing has proven only that there was no
# work *at that instant*. The first step back is short so an agent that was one
# beat ahead of its input rejoins almost immediately.
IDLE_BACKOFF_BASE_S = 5.0

# Ceiling on the idle ladder: a converged agent still re-checks every five
# minutes, so a room left running overnight wakes up within five minutes of a
# human adding material rather than hours later.
IDLE_BACKOFF_CAP_S = 300.0


def _ladder_delay(base: float, cap: float, streak: int) -> float:
    """Backoff after `streak` consecutive same-kind events: base, 2×base,
    4×base … clamped at `cap`.

    The exponent is clamped BEFORE it is used, not the result afterwards. A
    converged agent left running accumulates thousands of no-progress runs, and
    a plain `base * 2 ** streak` would build a multi-thousand-bit integer and
    then raise OverflowError converting it to a float — the agent's own quiet
    would be what crashed it."""
    if streak <= 0:
        return 0.0
    doublings_to_cap = math.ceil(math.log2(cap / base)) if cap > base else 0
    return min(base * 2.0 ** min(streak - 1, doublings_to_cap), cap)


def _is_rate_limit_error(exc: BaseException | None) -> bool:
    """True if exc, or anything on its __cause__/__context__ chain, is a
    provider rate-limit error. Matched by class name and HTTP status rather
    than isinstance so the kit stays provider-agnostic and wrapped re-raises
    (frameworks chain the original) are still recognized."""
    seen: set[int] = set()
    while exc is not None and id(exc) not in seen:
        seen.add(id(exc))
        if type(exc).__name__ == "RateLimitError" or getattr(exc, "status_code", None) == 429:
            return True
        exc = exc.__cause__ or exc.__context__
    return False


class Runner(Protocol):
    async def ainvoke(self, inputs: dict) -> dict: ...


class BaseAgent:
    """Generic poll/work/commit loop chassis, extracted from novelizer's
    BaseAgent (its fiction-specific commit helpers stay behind). Behavior is
    verbatim; the constructor drops the read_store/committer the generic
    half never used, and telemetry is an injected TelemetryEmitter."""

    name: str = "agent"

    def __init__(
        self,
        runner,
        interval: int,
        name: str | None = None,
        personality: str = "",
        clock=time.monotonic,
        progress_probe: Callable[[str], Awaitable[bool]] | None = None,
    ) -> None:
        self._runner = runner
        self.interval = interval
        if name is not None:
            self.name = name
        self.personality = personality
        self.paused = False
        self._last_fingerprint: tuple | None = None
        self.telemetry = None  # TelemetryEmitter; injected post-construction
        # "Did run <id> actually produce anything?" — public and injectable
        # post-construction like telemetry, so a consumer can wire it without
        # every subclass in its tree having to thread the kwarg through.
        self.progress_probe = progress_probe
        self._clock = clock
        # Two independent ladders, each an absolute deadline against `clock`.
        # Independent because "the agent is broken" and "the agent is
        # converged" are different questions: a crash is no evidence that the
        # agent has run out of work, and a quiet agent is not a broken one.
        self._fail_until = 0.0
        self._fail_streak = 0
        self._idle_until = 0.0
        self._idle_streak = 0
        # Run id of the run whose body called note_pass(), if any. A declared
        # pass is authoritative, so the probe is not consulted for that run.
        self._pass_declared_run: str | None = None

    @staticmethod
    def _guarded_line(label: str, value: str) -> str:
        """Return an optional "\\n\\n{label}: {value}" line, or "" if value is falsy."""
        return f"\n\n{label}: {value}" if value else ""

    def pause(self) -> None:
        self.paused = True

    def resume(self) -> None:
        self.paused = False

    def ready(self, now: float) -> bool:
        """Dispatchable on the progress ladders alone: no interval, no clock.
        An agent that has never run is ready now, however large its configured
        interval is — waiting on a clock while a dispatch slot sits free is the
        whole bug this replaces."""
        return now >= max(self._fail_until, self._idle_until)

    def seconds_until_ready(self, now: float) -> float:
        """Countdown over the two ladders, and nothing else — dispatch consults
        exactly these, so the status bar reads the same deadlines the scheduler
        does and can never disagree with it. Clamped at zero: this renders
        directly, and a past deadline must not show as a negative wait."""
        return max(0.0, self._fail_until - now, self._idle_until - now)

    def hold(self, now: float) -> tuple[str, float] | None:
        """Which ladder is holding this agent out of dispatch, and for how long
        -- None when it is dispatchable. seconds_until_ready() answers "how
        long"; this answers "why", and the two ladders mean opposite things to
        someone watching: "backing off" is an agent in trouble, "awaiting
        progress" is an agent with nothing to react to. The distinction lives
        here because the ladders do -- a countdown alone cannot be reverse-
        engineered into a reason.

        Reports whichever deadline actually governs (ready() waits for the later
        of the two), and prefers the fail ladder on a tie: an erroring agent is
        the more urgent of two simultaneous facts."""
        fail = self._fail_until - now
        idle = self._idle_until - now
        if fail <= 0.0 and idle <= 0.0:
            return None
        return ("backing off", fail) if fail >= idle else ("awaiting progress", idle)

    def note_pass(self, now: float | None = None) -> None:
        """Record an explicit "nothing to do" verdict by engaging the idle
        ladder. Same clock family as the scheduler's default (time.monotonic);
        inject `clock` at construction to keep agent backoff and a scheduler's
        injected clock in the same timeline.

        This is the early exit for an agent that has already established it has
        nothing to do: it need not wait for a probe to reach the same verdict,
        and it works for consumers that wired no probe at all."""
        if now is None:
            now = self._clock()
        self._pass_declared_run = current_run_id.get()
        self._advance_idle(now)

    def _advance_idle(self, now: float) -> None:
        self._idle_streak += 1
        self._idle_until = now + _ladder_delay(
            IDLE_BACKOFF_BASE_S, IDLE_BACKOFF_CAP_S, self._idle_streak)

    def _reset_idle(self) -> None:
        self._idle_streak = 0
        self._idle_until = 0.0

    def _advance_fail(self, now: float) -> None:
        self._fail_streak += 1
        self._fail_until = now + _ladder_delay(
            FAIL_BACKOFF_BASE_S, FAIL_BACKOFF_CAP_S, self._fail_streak)

    def _reset_fail(self) -> None:
        self._fail_streak = 0
        self._fail_until = 0.0

    async def _note_progress(self, run_id: str) -> None:
        """Move the idle ladder for a run that completed, using the probe's
        verdict on whether that run actually committed anything.

        Fails open in three ways, all of them the same judgment: an agent must
        never be quieted by an absence of evidence. No probe wired, a probe
        that raised (it reads a store, which can be down), and a probe that
        said yes all reset the ladder. Only a probe that positively answered
        "this run produced nothing" advances it."""
        if self._pass_declared_run == run_id:
            return  # the body already declared a pass; that verdict wins
        if self.progress_probe is None:
            self._reset_idle()
            return
        try:
            made_progress = await self.progress_probe(run_id)
        except Exception:
            # A broken probe must not turn a successful run into a failed one,
            # and must not be the reason an agent goes silent. Logged rather
            # than swallowed outright: failing open is indistinguishable from a
            # healthy busy room, so a permanently broken probe would otherwise
            # never surface.
            logger.warning("progress probe failed for %s run %s; assuming progress",
                           self.name, run_id, exc_info=True)
            self._reset_idle()
            return
        if made_progress:
            self._reset_idle()
        else:
            self._advance_idle(self._clock())

    async def _fingerprint(self) -> tuple | None:
        """External state this agent's work depends on. None (default)
        disables watermarking. Subclasses return a small tuple; captured
        AFTER the agent's own commits, so its own writes never re-trigger it."""
        return None

    async def _gate_on_watermark(self, score: float) -> float:
        fp = await self._fingerprint()
        if fp is not None and fp == self._last_fingerprint:
            return 0.0
        return score

    async def _record_watermark(self) -> None:
        self._last_fingerprint = await self._fingerprint()

    def _clear_watermark(self) -> None:
        self._last_fingerprint = None

    async def readiness(self) -> float:
        return 0.0

    async def _run(self) -> None:
        """Subclasses put their poll/work/commit body here; run_once brackets
        it with machinery telemetry and ambient run context."""

    async def run_once(self) -> None:
        run_id = str(uuid.uuid4())
        started = time.monotonic()
        rid_token = current_run_id.set(run_id)
        name_token = current_agent_name.set(self.name)
        await self._emit_telemetry(
            TelemetryEventType.AGENT_RUN_STARTED, run_id,
            AgentRunStarted(run_id=run_id, agent_name=self.name),
        )
        try:
            await self._run()
        except asyncio.CancelledError:
            # CancelledError is a BaseException, so it used to slip past the
            # arm below and the run reached NO terminal event at all: it simply
            # vanished, and anything counting starts minus terminals (a
            # concurrency accumulator, most visibly) drifted upward forever.
            # Its own event type rather than run_failed, because a cancelled
            # run is evidence of nothing about this agent -- it was cut off
            # from outside. Both ladders are deliberately left untouched for
            # the same reason: it neither proves the agent is broken nor, per
            # _note_progress's principle that an agent must never be quieted by
            # an absence of evidence, that it has run out of work. Re-raised
            # unconditionally -- swallowing a cancellation breaks cooperative
            # cancellation and can hang shutdown.
            await self._emit_telemetry(
                TelemetryEventType.AGENT_RUN_CANCELLED, run_id,
                AgentRunCancelled(
                    run_id=run_id, agent_name=self.name,
                    phase=self._phase(run_id), duration_s=time.monotonic() - started),
            )
            raise
        except Exception as e:
            phase = self._phase(run_id)
            await self._emit_telemetry(
                TelemetryEventType.AGENT_RUN_FAILED, run_id,
                AgentRunFailed(run_id=run_id, agent_name=self.name,
                               error_type=type(e).__name__, error_message=str(e),
                               phase=phase, duration_s=time.monotonic() - started),
            )
            # The fail ladder takes every raise, rate limit included: dispatch
            # gates on it alone, so a 429 needs no separate backoff — it backs
            # the agent off through this same path, and Phase 3's shared pool
            # reabsorbs rate-limit backpressure fleet-wide. The idle ladder is
            # untouched: a run that crashed committed nothing by definition, and
            # charging both ladders for one event would double-penalize it.
            self._advance_fail(self._clock())
            raise
        else:
            await self._emit_telemetry(
                TelemetryEventType.AGENT_RUN_FINISHED, run_id,
                AgentRunFinished(run_id=run_id, agent_name=self.name,
                                 duration_s=time.monotonic() - started),
            )
            # Any run that completed has answered "is this agent broken?" —
            # no, whatever it did or did not produce.
            self._reset_fail()
            await self._note_progress(run_id)
        finally:
            current_run_id.reset(rid_token)
            current_agent_name.reset(name_token)

    def _phase(self, run_id: str) -> str:
        """Where a run ended: inside an open LLM call, or in the agent body."""
        return "llm_call" if (self.telemetry and self.telemetry.in_llm_call(run_id)) else "agent"

    async def _emit_telemetry(self, event_type: str, aggregate_id: str, payload) -> None:
        if self.telemetry is None:
            return
        await self.telemetry.emit(event_type, aggregate_id, payload)
