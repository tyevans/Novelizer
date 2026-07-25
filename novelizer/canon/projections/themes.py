"""Theme projections: the recurring ideas a book introduces and develops."""
from __future__ import annotations

from novelizer.canon.events import EventType
from novelizer.canon.projections.registry import (
    ProjectionContext, load_record, projects, row_exists, upsert,
)
from novelizer.store.models import ThemeRecord


async def _write_theme(ctx: ProjectionContext, record: ThemeRecord) -> None:
    await ctx.execute(
        upsert("themes", "id, data", "?,?"),
        (record.id, record.model_dump_json()),
    )


@projects(EventType.THEME_INTRODUCED)
async def theme_introduced(ctx: ProjectionContext) -> None:
    p = ctx.payload
    if not await row_exists(ctx, "themes", p["id"]):
        record = ThemeRecord(
            id=p["id"], title=p["title"],
            last_note=p.get("note", ""), last_chapter_id=p.get("chapter_id", ""),
        )
        await _write_theme(ctx, record)
    # else: a theme id is minted exactly once. A second theme.introduced
    # for an id that already has a row is a projection no-op — same
    # first-mint-wins rule as thread.planted/secret.created.


@projects(EventType.THEME_DEVELOPED)
async def theme_developed(ctx: ProjectionContext) -> None:
    p = ctx.payload
    record = await load_record(ctx, "themes", ThemeRecord, p["id"])
    if record is not None:
        updated = record.model_copy(update={
            "touch_count": record.touch_count + 1,
            "last_note": p.get("note", ""),
            "last_chapter_id": p.get("chapter_id", ""),
        })
        await _write_theme(ctx, updated)
    # else: no row for this id yet (shouldn't happen under correct agent
    # behavior, since agents validate intents against known ids before
    # committing) — nothing to project, no error raised.
