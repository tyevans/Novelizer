from __future__ import annotations

import contextlib
import time
from typing import Callable

# --- one shared LLM concurrency ceiling, managed by AIMD --------------------
#
# Two consumers share one vLLM endpoint today -- agents via the scheduler and
# background KG extraction -- with no common concurrency limit. That is the
# source of the 429 pile-ups: nothing bounds how many requests hit the endpoint
# at once. This pool is that single fleet-wide ceiling. Both consumers draw
# permits from one instance, so the endpoint sees at most `_limit` runs at a
# time no matter which consumer they came from.
#
# asyncio.Semaphore is the obvious tool and the wrong one: its limit is fixed at
# construction. AIMD needs to *shrink* the ceiling on a 429 and grow it back on
# sustained success, so the pool is built over a mutable `_limit`, a live
# `_active` count, and an asyncio.Condition for the blocking wait.


class AdaptivePool:
    """A resizable concurrency limiter with AIMD backpressure.

    `slot()` is an async context manager gating one whole agent run (not one LLM
    call -- per-call permits would have to be released inside framework
    callbacks, where an exception leaks the permit; per-run acquisition has
    exactly one owner and one release site).

    `size` is the endpoint's real ceiling: the AIMD recovery cap, and the
    live-resize seam the runtime writes when `llm_pool_size` changes. `_limit`
    is the *current* ceiling AIMD moves between 1 and `size`.
    """

    def __init__(
        self,
        size: int,
        recover_after: int = 5,
        cooldown_s: float = 5.0,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        # _size is the recovery ceiling and the seam the runtime pokes on a
        # live llm_pool_size change; the `size` property clamps _limit when it
        # drops. AIMD manages _limit under it.
        self._size = size
        self._limit = size
        self._active = 0
        self._recover_after = recover_after
        self._cooldown_s = cooldown_s
        self._clock = clock
        # Consecutive successes since the last congestion signal. A 429 wipes
        # this, so recovery always restarts from scratch after a rate limit.
        self._success_streak = 0
        # End of the current 429 cooldown window, on the injected clock. A burst
        # of 429s from requests already in flight when the first landed must not
        # collapse the limit to 1 in one tick -- only the first halves.
        self._cooldown_until = 0.0
        # asyncio.Condition, not a Semaphore: a Semaphore's limit cannot shrink.
        # Constructed lazily so the pool can be built outside a running loop.
        self._cond: "object | None" = None
        # Strong ref to the in-flight wake task from _wake_waiters; without one
        # the loop holds only a weak reference and the notify can be collected
        # before it runs.
        self._wake_task: "object | None" = None

    @property
    def size(self) -> int:
        return self._size

    @size.setter
    def size(self, value: int) -> None:
        # Lowering the ceiling must bite immediately: without this clamp a live
        # llm_pool_size drop leaves _limit above the new cap until 429s punish it
        # back down, admitting runs against an endpoint the operator just told us
        # is smaller. Raising it only moves the recovery cap -- AIMD grows _limit
        # up through successes rather than jumping the live limit.
        self._size = value
        self._limit = min(self._limit, value)

    def _condition(self):
        # Bind the Condition to whatever loop first acquires a slot. The pool is
        # constructed in Runtime.start() before the scheduler loop is running, so
        # it cannot be created in __init__ without pinning the wrong loop.
        import asyncio

        if self._cond is None:
            self._cond = asyncio.Condition()
        return self._cond

    def _wake_waiters(self) -> None:
        """Re-check every blocked acquirer after the *limit* moved.

        Capacity nobody notices is capacity that does not exist: a waiter parked
        in `wait_for(_active < _limit)` is only re-evaluated on a notify, so a
        limit increase has to notify too, not just a permit release. The notify
        needs the Condition's lock and `note_success` is sync, so the wake is
        deferred onto the loop as a task -- it takes the lock briefly and then
        drops it, so it can neither deadlock a holder nor be blocked by one.

        No loop (a pool poked before the runtime started) or no Condition (no
        acquisition has ever happened) means there is nobody to wake yet.
        """
        import asyncio

        if self._cond is None:
            return
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            return

        async def _notify() -> None:
            async with self._cond:
                self._cond.notify_all()

        self._wake_task = loop.create_task(_notify())

    @contextlib.asynccontextmanager
    async def slot(self):
        """Block until a permit is free, hold it for the body, release on exit.

        The release is in a `finally`, so a raising body still frees its permit:
        a leaked permit is permanent concurrency loss, and at limit 1 a permanent
        deadlock. Release wakes any blocked acquirer through the Condition on the
        next loop step, never a poll.
        """
        cond = self._condition()
        async with cond:
            # A shrink may leave _active temporarily above _limit; new acquirers
            # wait here until enough in-flight permits drain back under it.
            await cond.wait_for(lambda: self._active < self._limit)
            self._active += 1
        try:
            yield
        finally:
            async with cond:
                self._active -= 1
                # Wake every waiter so each re-checks _active < _limit for
                # itself; the Condition does the waiting, so a freed permit is
                # taken on the next loop turn rather than after a sleep.
                cond.notify_all()

    def note_rate_limited(self) -> None:
        """Multiplicative decrease: a 429 halves the current limit (floor 1).

        Within the cooldown window the call is a no-op, so a burst of 429s from
        requests already in flight when the first one landed collapses the limit
        by one step, not all the way to 1. `cooldown_s=0.0` disables the window.
        """
        now = self._clock()
        if self._cooldown_s > 0.0 and now < self._cooldown_until:
            return
        # Integer floor-div, floored at 1: a zero limit is a permanent deadlock,
        # no permit could ever be acquired again.
        self._limit = max(1, self._limit // 2)
        self._cooldown_until = now + self._cooldown_s
        # A congestion signal wipes recovery progress: successes only count if
        # they are consecutive *since the last* 429.
        self._success_streak = 0

    def note_success(self) -> None:
        """Additive increase: every `recover_after`th consecutive success lifts
        the limit by one, capped at `size`.

        Recovery is deliberately one step per `recover_after` successes while a
        single 429 halves in one call -- that asymmetry (slow up, fast down) is
        the whole point of AIMD, and keeps the pool from oscillating back into
        the congestion it just backed away from.
        """
        if self._limit >= self.size:
            # Already at the ceiling: nothing to recover, and nothing to count
            # either. Leaving a partial streak standing would let it survive the
            # whole stay and buy an instant +1 the moment the ceiling moves back
            # up (a lowered-then-raised llm_pool_size), skipping the sustained
            # success recovery is supposed to require.
            self._success_streak = 0
            return
        self._success_streak += 1
        if self._success_streak >= self._recover_after:
            self._limit = min(self.size, self._limit + 1)
            self._success_streak = 0
            # The increase itself frees capacity; waiters blocked at the old
            # limit must be re-checked or they sit until an unrelated release.
            self._wake_waiters()
