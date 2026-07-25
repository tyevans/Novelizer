"""Promise projections: the setup/payoff obligations a book makes to its reader,
and their progression toward paid or released."""
from __future__ import annotations

from novelizer.canon.events import EventType
from novelizer.canon.projections.registry import (
    ProjectionContext, load_record, projects, row_exists, upsert,
)
from novelizer.canon.promises import TERMINAL_PROMISE_STATES
from novelizer.store.models import PromiseRecord, PromiseState

_PROMISE_COLUMNS = "id, data, state"

_STATE_BY_TYPE = {
    EventType.PROMISE_PROGRESSED: PromiseState.open,
    EventType.PROMISE_PAID: PromiseState.paid,
    EventType.PROMISE_RELEASED: PromiseState.released,
}


async def _write_promise(ctx: ProjectionContext, record: PromiseRecord) -> None:
    await ctx.execute(
        upsert("promises", _PROMISE_COLUMNS, "?,?,?"),
        (record.id, record.model_dump_json(), record.state.value),
    )


@projects(EventType.PROMISE_MADE)
async def promise_made(ctx: ProjectionContext) -> None:
    p = ctx.payload
    if not await row_exists(ctx, "promises", p["id"]):
        record = PromiseRecord(
            id=p["id"], name=p["name"], description=p.get("description", ""),
            kind=p.get("kind", "foreshadow"), thread_id=p.get("thread_id", ""),
            setup_chapter_id=p.get("chapter_id", ""),
            window_lo=p.get("window_lo", 0), window_hi=p.get("window_hi", 0),
            last_note=p.get("note", ""), last_chapter_id=p.get("chapter_id", ""),
        )
        await _write_promise(ctx, record)
    # else: a promise id is minted exactly once -- first-make-wins.


@projects(EventType.PROMISE_PROGRESSED, EventType.PROMISE_PAID, EventType.PROMISE_RELEASED)
async def promise_advanced(ctx: ProjectionContext) -> None:
    p = ctx.payload
    t = ctx.event_type
    record = await load_record(ctx, "promises", PromiseRecord, p["id"])
    if record is not None:
        if record.state.value not in TERMINAL_PROMISE_STATES:
            new_state = _STATE_BY_TYPE[t]
            progress = record.progress_count + (1 if t == EventType.PROMISE_PROGRESSED else 0)
            updated = record.model_copy(update={
                "state": new_state, "progress_count": progress,
                "last_note": p.get("note", p.get("reason", "")),
                "last_chapter_id": p.get("chapter_id", ""),
            })
            await _write_promise(ctx, updated)
        # else: paid/released are absorbing -- the event is a fact in
        # the log, but the promises projection does not change.
    # else: no row for this id yet -- nothing to project, no error raised.
