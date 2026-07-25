"""Thread projections: the plot threads a book plants, touches and pays off,
plus the payoff window a planner declares for one."""
from __future__ import annotations

from novelizer.canon.events import EventType
from novelizer.canon.projections.registry import (
    ProjectionContext, load_record, projects, row_exists, upsert,
)
from novelizer.canon.threads import TERMINAL_STATES
from novelizer.store.models import ThreadRecord, ThreadState

_THREAD_COLUMNS = "id, data, state"

_STATE_BY_TYPE = {
    EventType.THREAD_TOUCHED: ThreadState.touched,
    EventType.THREAD_PAID_OFF: ThreadState.paid_off,
    EventType.THREAD_ABANDONED: ThreadState.abandoned,
}


async def _write_thread(ctx: ProjectionContext, record: ThreadRecord) -> None:
    await ctx.execute(
        upsert("threads", _THREAD_COLUMNS, "?,?,?"),
        (record.id, record.model_dump_json(), record.state.value),
    )


@projects(EventType.THREAD_PLANTED)
async def thread_planted(ctx: ProjectionContext) -> None:
    p = ctx.payload
    if not await row_exists(ctx, "threads", p["id"]):
        record = ThreadRecord(
            id=p["id"], name=p["name"], state=ThreadState.planted,
            last_note=p.get("note", ""), last_chapter_id=p.get("chapter_id", ""),
        )
        await _write_thread(ctx, record)
    # else: a thread id is minted exactly once. A second thread.planted
    # for an id that already has a row (any state, including terminal)
    # is a projection no-op — the event remains a fact in the log, but
    # first-plant-wins so replanting can never reset state/touch_count
    # or resurrect an absorbed terminal thread.


@projects(EventType.THREAD_TOUCHED, EventType.THREAD_PAID_OFF, EventType.THREAD_ABANDONED)
async def thread_advanced(ctx: ProjectionContext) -> None:
    p = ctx.payload
    t = ctx.event_type
    record = await load_record(ctx, "threads", ThreadRecord, p["id"])
    if record is not None:
        if record.state.value not in TERMINAL_STATES:
            new_state = _STATE_BY_TYPE[t]
            touch_count = record.touch_count + (1 if t == EventType.THREAD_TOUCHED else 0)
            updated = record.model_copy(update={
                "state": new_state,
                "touch_count": touch_count,
                "last_note": p.get("note", ""),
                "last_chapter_id": p.get("chapter_id", ""),
            })
            await _write_thread(ctx, updated)
        # else: absorbing terminal state — the event is a fact in the log,
        # but the threads projection does not change.
    # else: no row for this id yet (shouldn't happen under correct agent
    # behavior, since agents validate intents against known ids before
    # committing) — nothing to project, no error raised.


@projects(EventType.THREAD_RESOLUTION_PLANNED)
async def thread_resolution_planned(ctx: ProjectionContext) -> None:
    p = ctx.payload
    record = await load_record(ctx, "threads", ThreadRecord, p["id"])
    if record is not None:
        if record.state.value not in TERMINAL_STATES:
            updated = record.model_copy(update={
                "window_lo": p.get("window_lo", 0), "window_hi": p.get("window_hi", 0),
                "planned_payoff_note": p.get("planned_payoff_note", ""),
            })
            await _write_thread(ctx, updated)
        # else: no-op on a terminal thread.
    # else: unknown thread id -- no-op, no error raised.
