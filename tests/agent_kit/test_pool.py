from __future__ import annotations
import asyncio

from hypothesis import given, settings, strategies as st

# RED: agent_kit.pool does not exist yet. Every test in this file fails at
# collection with `ModuleNotFoundError: No module named 'agent_kit.pool'` until
# Phase 3 lands the class. That is the intended red state for the whole file.
from agent_kit.pool import AdaptivePool


class FakeClock:
    """Injectable monotonic clock, same idiom the scheduler tests use. The pool
    reads it only for the 429 cooldown window, so pinning it here makes cooldown
    behaviour deterministic instead of a race against wall time."""

    def __init__(self, now: float = 1000.0) -> None:
        self.now = now

    def __call__(self) -> float:
        return self.now

    def advance(self, dt: float) -> None:
        self.now += dt


class _Holder:
    """Acquires one pool permit and holds it until told to let go, so a test can
    pin `_active` at a chosen value across an await boundary and prove that a
    shrink never evicts an in-flight permit."""

    def __init__(self, pool: AdaptivePool) -> None:
        self.pool = pool
        self.entered = asyncio.Event()
        self._release = asyncio.Event()
        self._task: asyncio.Task | None = None

    async def _run(self) -> None:
        async with self.pool.slot():
            self.entered.set()
            await self._release.wait()

    def start(self) -> "_Holder":
        self._task = asyncio.create_task(self._run())
        return self

    async def wait_entered(self) -> None:
        await self.entered.wait()

    async def let_go(self) -> None:
        self._release.set()
        assert self._task is not None
        await self._task


# --- construction -----------------------------------------------------------


async def test_starts_at_full_size_with_no_active_permits():
    pool = AdaptivePool(6)
    assert pool._limit == 6
    assert pool._active == 0
    assert pool.size == 6


# --- permit lifecycle -------------------------------------------------------


async def test_slot_increments_active_on_entry_and_decrements_on_exit():
    pool = AdaptivePool(2)
    async with pool.slot():
        assert pool._active == 1
    assert pool._active == 0


async def test_permit_released_even_when_the_body_raises():
    """A leaked permit permanently shrinks usable concurrency; at limit 1 it is
    a permanent deadlock. The context manager must decrement `_active` on the
    way out whether the body returned or raised."""
    pool = AdaptivePool(1)
    try:
        async with pool.slot():
            assert pool._active == 1
            raise ValueError("boom")
    except ValueError:
        pass
    assert pool._active == 0
    # The pool is still usable — a leaked permit at limit 1 would hang here.
    async with pool.slot():
        assert pool._active == 1
    assert pool._active == 0


async def test_limit_one_serializes_two_slot_bodies():
    """With `_limit == 1` two bodies must never overlap — this is the mutual
    exclusion the whole pool exists to provide."""
    pool = AdaptivePool(1)
    live = 0
    peak = 0

    async def worker() -> None:
        nonlocal live, peak
        async with pool.slot():
            live += 1
            peak = max(peak, live)
            await asyncio.sleep(0.01)
            live -= 1

    await asyncio.gather(worker(), worker())
    assert peak == 1
    assert pool._active == 0


async def test_active_never_exceeds_limit_under_many_concurrent_holders():
    pool = AdaptivePool(3)
    peak = 0

    async def worker() -> None:
        nonlocal peak
        async with pool.slot():
            peak = max(peak, pool._active)
            assert pool._active <= pool._limit
            await asyncio.sleep(0.005)

    await asyncio.gather(*[worker() for _ in range(12)])
    assert peak == 3, "the ceiling was never actually saturated"
    assert pool._active == 0


async def test_release_wakes_a_blocked_acquirer_on_the_next_loop_step():
    """A blocked acquirer must proceed the instant a permit frees, via the
    Condition — not after a fixed poll interval. A pool that polled with
    `await asyncio.sleep(0.05)` would still be sleeping after a handful of
    zero-delay loop turns; a Condition-notified waiter is already running."""
    pool = AdaptivePool(1)
    hold_started = asyncio.Event()
    release_now = asyncio.Event()
    b_entered = asyncio.Event()

    async def holder() -> None:
        async with pool.slot():
            hold_started.set()
            await release_now.wait()

    async def waiter() -> None:
        await hold_started.wait()
        async with pool.slot():
            b_entered.set()

    ht = asyncio.create_task(holder())
    wt = asyncio.create_task(waiter())
    await hold_started.wait()
    for _ in range(5):
        await asyncio.sleep(0)  # let the waiter reach slot() and block
    assert not b_entered.is_set(), "acquired while the only permit was held"
    assert pool._active == 1

    release_now.set()
    for _ in range(5):
        await asyncio.sleep(0)  # NO wall-clock delay: a Condition wake suffices
    assert b_entered.is_set(), "waiter was not woken promptly when the permit freed"
    await asyncio.gather(ht, wt)
    assert pool._active == 0


# --- AIMD: multiplicative decrease ------------------------------------------


async def test_note_rate_limited_halves_the_limit():
    pool = AdaptivePool(8, cooldown_s=0.0)
    pool.note_rate_limited()
    assert pool._limit == 4


async def test_decrease_floors_at_one_never_zero():
    """Halving must never reach 0 — a zero limit is a permanent deadlock, no
    permit can ever be acquired again."""
    pool = AdaptivePool(3, cooldown_s=0.0)
    for _ in range(10):
        pool.note_rate_limited()
    assert pool._limit == 1


async def test_cooldown_suppresses_repeat_halving_within_the_window():
    """A burst of 429s from requests already in flight when the first one landed
    must not collapse the limit to 1. Inside the cooldown window subsequent
    `note_rate_limited()` calls are no-ops; the window is pinned by the injected
    clock, never wall time."""
    clock = FakeClock()
    pool = AdaptivePool(8, cooldown_s=5.0, clock=clock)
    pool.note_rate_limited()          # 8 -> 4, cooldown until now+5
    assert pool._limit == 4
    clock.advance(1.0)                # still inside the window
    pool.note_rate_limited()          # a straggler in-flight 429 — no-op
    assert pool._limit == 4, "a burst of in-flight 429s collapsed the pool"
    clock.advance(5.0)                # past the window
    pool.note_rate_limited()          # 4 -> 2
    assert pool._limit == 2


async def test_shrink_strands_no_in_flight_permit_and_gates_new_ones():
    """A decrease must not kill permits already in flight; it only blocks *new*
    acquisitions until enough release to get back under the shrunk limit."""
    pool = AdaptivePool(4, cooldown_s=0.0)
    holders = [_Holder(pool).start() for _ in range(3)]
    for h in holders:
        await h.wait_entered()
    assert pool._active == 3 and pool._limit == 4

    pool.note_rate_limited()          # 4 -> 2, but three are already in flight
    assert pool._limit == 2
    assert pool._active == 3, "an in-flight permit was evicted by the shrink"

    blocked = _Holder(pool).start()
    for _ in range(5):
        await asyncio.sleep(0)
    assert not blocked.entered.is_set(), "acquired while over the shrunk limit"

    await holders[0].let_go()         # _active 3 -> 2, still >= limit 2
    await holders[1].let_go()         # _active 2 -> 1, now < limit 2 -> wakes
    await blocked.wait_entered()      # would hang if the wake never came
    assert pool._active == 2

    await holders[2].let_go()
    await blocked.let_go()
    assert pool._active == 0


# --- AIMD: additive increase ------------------------------------------------


async def test_recovery_is_additive_and_strictly_slower_than_decrease():
    """One 429 halves in a single step; recovering one step takes `recover_after`
    consecutive successes. That asymmetry is the entire point of AIMD."""
    pool = AdaptivePool(6, recover_after=3, cooldown_s=0.0)
    pool.note_rate_limited()          # 6 -> 3 in one call
    assert pool._limit == 3
    pool.note_success()
    pool.note_success()
    assert pool._limit == 3, "recovered before sustained success"
    pool.note_success()               # third consecutive success -> +1
    assert pool._limit == 4
    for _ in range(3):
        pool.note_success()
    assert pool._limit == 5


async def test_recovery_never_exceeds_size():
    """`size` is the endpoint's real ceiling; additive recovery must stop there
    however many successes accrue."""
    pool = AdaptivePool(2, recover_after=1, cooldown_s=0.0)
    for _ in range(10):
        pool.note_success()
    assert pool._limit == 2


async def test_a_rate_limit_resets_recovery_progress():
    """Recovery counts consecutive successes *since the last congestion signal*;
    a 429 wipes partial progress so recovery restarts from scratch."""
    pool = AdaptivePool(6, recover_after=3, cooldown_s=0.0)
    pool.note_rate_limited()          # 6 -> 3
    pool.note_success()
    pool.note_success()               # 2/3 toward the next +1
    pool.note_rate_limited()          # 3 -> 1, and the 2/3 progress is wiped
    assert pool._limit == 1
    pool.note_success()
    pool.note_success()
    assert pool._limit == 1, "recovery resumed from stale pre-429 progress"
    pool.note_success()               # third since the 429 -> +1
    assert pool._limit == 2


# --- properties -------------------------------------------------------------


@settings(deadline=None, max_examples=50)
@given(
    ops=st.lists(st.sampled_from(["rl", "ok"]), max_size=60),
    size=st.integers(min_value=1, max_value=8),
    recover_after=st.integers(min_value=1, max_value=5),
)
def test_limit_stays_within_one_and_size_under_any_signal_sequence(ops, size, recover_after):
    """The bound the pool guarantees no matter how congestion and success
    interleave: `1 <= _limit <= size` after every signal. Below 1 deadlocks;
    above size overruns the endpoint."""
    pool = AdaptivePool(size, recover_after=recover_after, cooldown_s=0.0)
    for op in ops:
        if op == "rl":
            pool.note_rate_limited()
        else:
            pool.note_success()
        assert 1 <= pool._limit <= size


@settings(deadline=None, max_examples=25)
@given(
    n_workers=st.integers(min_value=2, max_value=10),
    size=st.integers(min_value=1, max_value=4),
    signals=st.lists(st.sampled_from(["rl", "ok", None]), max_size=10),
)
async def test_concurrent_holders_never_exceed_size(n_workers, size, signals):
    """Over an arbitrary interleaving of acquire/release/note_rate_limited/
    note_success, the number of bodies simultaneously inside `slot()` never
    exceeds `size`, and `_active <= size` at every observable boundary — the
    pool is a real fleet-wide ceiling, which is the property that does not hold
    today with no limiter at all."""
    pool = AdaptivePool(size, recover_after=2, cooldown_s=0.0)
    live = 0
    peak = 0

    async def worker(sig) -> None:
        nonlocal live, peak
        async with pool.slot():
            live += 1
            peak = max(peak, live)
            assert pool._active <= size
            assert 1 <= pool._limit <= size
            await asyncio.sleep(0)
            if sig == "rl":
                pool.note_rate_limited()
            elif sig == "ok":
                pool.note_success()
            live -= 1

    sigs = (signals + [None] * n_workers)[:n_workers]
    await asyncio.gather(*[worker(s) for s in sigs])
    assert peak <= size
    assert live == 0
    assert pool._active == 0
    assert 1 <= pool._limit <= size


async def test_lowering_size_reclamps_the_current_limit_at_once():
    """A live drop in llm_pool_size must take effect immediately, not wait for
    429s to punish the pool into compliance. If _limit sits above a freshly
    lowered size, the pool would keep admitting runs against an endpoint the
    operator just declared smaller -- the exact over-admission the ceiling
    exists to prevent."""
    pool = AdaptivePool(6, cooldown_s=0.0)
    assert pool._limit == 6
    pool.size = 2
    assert pool.size == 2
    assert pool._limit == 2


async def test_raising_size_leaves_the_current_limit_for_aimd_to_grow():
    """Widening the ceiling only moves the recovery cap; AIMD earns its way
    back up through successes rather than jumping the live limit."""
    pool = AdaptivePool(4, recover_after=2, cooldown_s=0.0)
    pool.note_rate_limited()            # _limit: 4 -> 2
    assert pool._limit == 2
    pool.size = 8
    assert pool._limit == 2             # not yanked up to the new cap
    pool.note_success(); pool.note_success()
    assert pool._limit == 3             # recovers additively, now toward 8


# --- capacity growth must wake the already-blocked -------------------------


async def test_raising_the_limit_wakes_a_waiter_without_waiting_for_a_release():
    """Additive increase creates capacity, and capacity is worthless if nobody
    notices it. A waiter blocked at the old limit must wake on the increase
    itself -- not sit there until some unrelated permit happens to release. In
    the KG drain the signal arrives from a caller holding no permit at all, so
    "the release will notify anyway" is not a guarantee the pool can lean on: a
    429-shrunk pool with one long run in flight would strand every waiter for
    the length of that run."""
    pool = AdaptivePool(2, recover_after=1, cooldown_s=0.0)
    pool.note_rate_limited()              # _limit: 2 -> 1
    holder = _Holder(pool).start()
    await holder.wait_entered()
    blocked = _Holder(pool).start()
    for _ in range(5):
        await asyncio.sleep(0)
    assert not blocked.entered.is_set(), "acquired while the only permit was held"

    pool.note_success()                   # _limit: 1 -> 2, capacity is free NOW
    assert pool._limit == 2
    for _ in range(5):
        await asyncio.sleep(0)            # a Condition wake needs no wall clock
    assert blocked.entered.is_set(), "waiter slept through a capacity increase"
    assert pool._active == 2

    await holder.let_go()
    await blocked.let_go()
    assert pool._active == 0


async def test_success_progress_does_not_survive_a_stay_at_the_ceiling():
    """Recovery progress means "consecutive successes since the pool last could
    have grown". Time spent at the ceiling is not progress toward anything: if a
    partial streak survives it, the first success after the ceiling moves back
    up jumps the limit immediately instead of earning it over `recover_after`
    runs."""
    pool = AdaptivePool(4, recover_after=3, cooldown_s=0.0)
    pool.note_rate_limited()              # _limit: 4 -> 2
    pool.note_success()
    pool.note_success()                   # 2/3 toward the next +1
    pool.size = 2                         # operator shrinks the endpoint: at ceiling
    for _ in range(5):
        pool.note_success()               # nothing to recover; not progress either
    assert pool._limit == 2

    pool.size = 4                         # room to grow again
    pool.note_success()
    assert pool._limit == 2, "a stale pre-ceiling streak bought an instant +1"
    pool.note_success()
    pool.note_success()                   # three consecutive since there was room
    assert pool._limit == 3
