"""Character-arc projections: a character's ghost/lie/truth/want/need, the
pivots planned against beats, and the arc's advance and resolution."""
from __future__ import annotations

from novelizer.canon.events import EventType
from novelizer.canon.projections.registry import (
    ProjectionContext, load_record, projects, row_exists, upsert,
)
from novelizer.store.models import ArcPivot, ArcRecord

_ARC_COLUMNS = "id, data, character_id, active"


async def _write_arc(ctx: ProjectionContext, record: ArcRecord, active: int) -> None:
    await ctx.execute(
        upsert("arcs", _ARC_COLUMNS, "?,?,?,?"),
        (record.id, record.model_dump_json(), record.character_id, active),
    )


@projects(EventType.ARC_DECLARED)
async def arc_declared(ctx: ProjectionContext) -> None:
    # Declaring supersedes the character's prior active arc: fold
    # active=False into every existing row's data JSON for this
    # character_id (so records stay truthful on read) and clear the
    # active=1 flag column, then insert the new arc as active.
    p = ctx.payload
    if await row_exists(ctx, "arcs", p["arc_id"]):
        # An arc id is minted exactly once -- true first-mint-wins.
        # A duplicate arc.declared for an existing arc_id is a
        # complete no-op: it must not deactivate the character's
        # other arcs, since nothing is actually being (re)minted.
        return
    rows = await ctx.fetch_all("SELECT id, data FROM arcs WHERE character_id=?", (p["character_id"],))
    for row_id, row_data in rows:
        existing = ArcRecord.model_validate_json(row_data)
        updated = existing.model_copy(update={"active": False})
        await ctx.execute(
            "UPDATE arcs SET data=?, active=0 WHERE id=?",
            (updated.model_dump_json(), row_id),
        )
    record = ArcRecord(
        id=p["arc_id"], character_id=p["character_id"], arc_type=p["arc_type"],
        ghost=p.get("ghost", ""), lie=p.get("lie", ""), truth=p.get("truth", ""),
        want=p.get("want", ""), need=p.get("need", ""), active=True,
    )
    await _write_arc(ctx, record, 1)


@projects(EventType.ARC_PIVOT_PLANNED)
async def arc_pivot_planned(ctx: ProjectionContext) -> None:
    p = ctx.payload
    record = await load_record(ctx, "arcs", ArcRecord, p["arc_id"])
    if record is not None:
        if not record.resolved:
            new_pivot = ArcPivot(beat_id=p["beat_id"], description=p.get("description", ""))
            pivots = list(record.pivots)
            for i, existing_pivot in enumerate(pivots):
                if existing_pivot.beat_id == p["beat_id"]:
                    pivots[i] = new_pivot
                    break
            else:
                pivots.append(new_pivot)
            updated = record.model_copy(update={"pivots": pivots})
            await _write_arc(ctx, updated, 1 if updated.active else 0)
        # else: no-op on a resolved arc.
    # else: unknown arc id -- no-op, no error raised.


@projects(EventType.ARC_ADVANCED)
async def arc_advanced(ctx: ProjectionContext) -> None:
    p = ctx.payload
    record = await load_record(ctx, "arcs", ArcRecord, p["arc_id"])
    if record is not None:
        if not record.resolved:
            updated = record.model_copy(update={
                "advance_count": record.advance_count + 1,
                "last_note": p.get("note", ""),
                "last_chapter_id": p.get("chapter_id", ""),
            })
            await _write_arc(ctx, updated, 1 if updated.active else 0)
        # else: no-op on a resolved arc.
    # else: unknown arc id -- no-op, no error raised.


@projects(EventType.ARC_RESOLVED)
async def arc_resolved(ctx: ProjectionContext) -> None:
    p = ctx.payload
    record = await load_record(ctx, "arcs", ArcRecord, p["arc_id"])
    if record is not None:
        if not record.resolved:
            updated = record.model_copy(update={
                "resolved": True,
                "outcome": p.get("outcome", ""),
                "resolved_chapter_id": p.get("chapter_id", ""),
            })
            await _write_arc(ctx, updated, 1 if updated.active else 0)
        # else: resolved is absorbing -- the event is a fact in the
        # log, but the projection does not change.
    # else: unknown arc id -- no-op, no error raised.
