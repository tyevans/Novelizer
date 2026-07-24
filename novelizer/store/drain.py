from __future__ import annotations

import asyncio

from agent_kit import _is_rate_limit_error


async def drain_pending(
    pending,
    *,
    poison,
    pool,
    drain_concurrency: int,
    run_one,
    save_cursor,
    logger,
    label: str,
) -> int:
    """Parallel background drain, shared verbatim by CanonIndexer and KGProjector.

    Under Phase 4's strict background gate every agent waits on the drain, so a
    sequential catch_up would simply become the room's new source of idleness
    (design doc section 7). This drains the pending window concurrently while
    keeping the cursor's advance provably safe:

      * Dedupe by aggregate_id. Chapter 7 revised four times inside one window
        is four events but ONE current record, and the op hydrates that current
        record regardless of which event triggered it -- so re-embedding /
        re-extracting it four times is pure waste. The op runs once per
        aggregate, driven by the aggregate's LATEST event (max sequence, since
        `pending` is ascending), whose event_type/sequence carry the provenance.

      * Partition by aggregate and drain partitions concurrently, fan-out capped
        at `drain_concurrency` workers -- a task-count bound (1000 pending
        aggregates must not spawn 1000 tasks), independent of the pool. Each op
        that does LLM/embedding work also draws a permit from `pool` when one is
        set, so agents + both drains share ONE endpoint ceiling and one AIMD
        controller. `pool=None` => no permit gating, but STILL parallel.

      * Feed exactly one AIMD signal per partition op, mirroring
        Scheduler._run: congestion on a real 429, success on a clean op, and
        NOTHING on a plain crash -- a malformed record or a bug is not
        congestion and must not shrink the fleet-wide ceiling every other
        consumer draws from.

      * Advance the cursor over the longest contiguous SUCCESS prefix by
        sequence. A sequence is DONE if its aggregate's op succeeded OR the
        aggregate was poison-skipped this pass; the walk stops at the first
        not-done sequence (the barrier). Advancing past a failed sequence would
        drop its embedding/facts for good -- a permanently-lost event, the one
        thing the poison ladder must never do silently -- so the cursor pins
        just before the barrier and every sequence beyond it re-drains next
        pass (redundant but idempotent). Later partitions are NOT blocked by an
        earlier failure; only the cursor is.

      * Record a poison failure for the BARRIER sequence only (the first
        not-done), never for a sequence beyond it. After `poison_skip_after`
        consecutive failures the barrier aggregate is abandoned and the cursor
        jumps past it -- the Phase-0 deadlock-avoidance property preserved under
        parallelism: a permanently-failing aggregate at seq K wedges the cursor
        for exactly `poison_skip_after` passes, then the cursor jumps past it and
        the rest of the backlog drains.

    Returns `processed`: the number of sequences the cursor advanced over via
    SUCCESS this pass (the contiguous success-prefix length in sequences),
    excluding poison-skips. N events for one aggregate, one op => processed == N.
    """
    if not pending:
        return 0

    # Dedup: `pending` is ascending by sequence, so overwriting per aggregate_id
    # leaves each aggregate's latest event -- the single event whose event_type
    # and sequence drive its one hydrate-current-record op.
    latest_by_agg: dict = {}
    for ev in pending:
        latest_by_agg[ev.aggregate_id] = ev
    ops = list(latest_by_agg.values())

    succeeded: set = set()
    failures: dict = {}

    async def run_partition(ev) -> None:
        try:
            if pool is None:
                await run_one(ev)
            else:
                async with pool.slot():
                    try:
                        await run_one(ev)
                    except Exception as e:
                        if _is_rate_limit_error(e):
                            pool.note_rate_limited()
                        raise
                    else:
                        pool.note_success()
        except Exception as e:
            # A partition op never re-raises out of the drain: its failure is
            # recorded here and resolved by the poison ladder in the prefix walk
            # below, preserving catch_up's never-raise contract.
            failures[ev.aggregate_id] = e
        else:
            succeeded.add(ev.aggregate_id)

    # Bounded worker pool: exactly `n_workers` coroutines pull from one shared
    # iterator, so the fan-out never materializes one task per aggregate. next()
    # is atomic under a single event loop (no await inside the `for`), so the
    # shared iterator is safe to consume from concurrent workers.
    n_workers = max(1, min(drain_concurrency, len(ops)))
    work = iter(ops)

    async def worker() -> None:
        for ev in work:
            await run_partition(ev)

    await asyncio.gather(*[worker() for _ in range(n_workers)])

    processed = 0
    cursor: int | None = None
    skipped: set = set()
    for ev in pending:
        agg = ev.aggregate_id
        if agg in succeeded:
            # A recovered blip is not poison, so a successful sequence clears
            # its tally (the ladder counts CONSECUTIVE failures).
            poison.record_success(ev.sequence)
            cursor = ev.sequence
            processed += 1
        elif agg in skipped:
            # A later sequence of an aggregate already abandoned this pass: DONE,
            # so the cursor advances over it, but a skip is not a success and
            # does not count toward processed.
            cursor = ev.sequence
        elif poison.record_failure(ev.sequence):
            # Budget spent on the barrier: abandon the whole aggregate and jump
            # the cursor past it. Losing this event's embedding/facts beats a
            # cursor pinned on it forever, which under the strict gate would
            # pause every agent forever.
            exc = failures.get(agg)
            logger.error(
                "%s abandoning seq %s (aggregate %s) after %s consecutive "
                "failures (%s: %s); it will never be indexed",
                label, ev.sequence, agg, poison.skip_after,
                type(exc).__name__, exc,
            )
            skipped.add(agg)
            cursor = ev.sequence
        else:
            # The barrier: a still-runnable failure. The cursor cannot pass it
            # without dropping the event, so stop -- sequences beyond it, even
            # ones that succeeded this pass, wait for the next pass.
            exc = failures.get(agg)
            logger.warning(
                "%s stopped at seq %s (%s: %s); will retry",
                label, ev.sequence, type(exc).__name__, exc,
            )
            break

    if cursor is not None:
        save_cursor(cursor)
    return processed
