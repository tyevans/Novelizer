"""World-entry and character projections: the canon of places, things and people."""
from __future__ import annotations

from novelizer.canon.events import EventType
from novelizer.canon.projections.registry import ProjectionContext, projects, upsert

_ENTRY_COLUMNS = "id, data, canon_status, supersedes_id"


def _entry_row(ctx: ProjectionContext) -> tuple:
    p = ctx.payload
    return (p["id"], ctx.data, p.get("canon_status", "active"), p.get("supersedes_id"))


@projects(EventType.WORLD_ENTRY_CREATED)
async def world_entry_created(ctx: ProjectionContext) -> None:
    await ctx.execute(upsert("world_entries", _ENTRY_COLUMNS, "?,?,?,?"), _entry_row(ctx))


@projects(EventType.WORLD_ENTRY_SUPERSEDED)
async def world_entry_superseded(ctx: ProjectionContext) -> None:
    if ctx.payload.get("supersedes_id"):
        await ctx.execute(
            "UPDATE world_entries SET canon_status='superseded' WHERE id=?",
            (ctx.payload["supersedes_id"],),
        )
    await ctx.execute(upsert("world_entries", _ENTRY_COLUMNS, "?,?,?,?"), _entry_row(ctx))


@projects(EventType.WORLD_ENTRY_RETIRED)
async def world_entry_retired(ctx: ProjectionContext) -> None:
    # Tombstone: the entry leaves active canon with no successor. The UPDATE is
    # a no-op on an unknown/already-gone id (0 rows), which is exactly the
    # resilience the Curator's stale-target case wants.
    await ctx.execute(
        "UPDATE world_entries SET canon_status='retired' WHERE id=?",
        (ctx.payload["entry_id"],),
    )


@projects(EventType.CHARACTER_CREATED, EventType.CHARACTER_UPDATED)
async def character_written(ctx: ProjectionContext) -> None:
    await ctx.execute(upsert("characters", _ENTRY_COLUMNS, "?,?,?,?"), _entry_row(ctx))
