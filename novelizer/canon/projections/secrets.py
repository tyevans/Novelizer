"""Secret projections: the secrets themselves, who knows them, where they are
referenced, and the set-once moment of revelation."""
from __future__ import annotations

from novelizer.canon.events import EventType
from novelizer.canon.projections.registry import (
    ProjectionContext, load_record, projects, row_exists, upsert,
)
from novelizer.store.models import SecretRecord


async def _write_secret(ctx: ProjectionContext, record: SecretRecord) -> None:
    await ctx.execute(
        upsert("secrets", "id, data", "?,?"),
        (record.id, record.model_dump_json()),
    )


@projects(EventType.SECRET_CREATED)
async def secret_created(ctx: ProjectionContext) -> None:
    p = ctx.payload
    if not await row_exists(ctx, "secrets", p["id"]):
        record = SecretRecord(id=p["id"], title=p["title"], revealed=False)
        await _write_secret(ctx, record)
    # else: a secret id is minted exactly once. A second secret.created
    # for an id that already has a row is a projection no-op — same
    # first-plant-wins rule as thread.planted.


@projects(EventType.SECRET_REVEAL_PLANNED)
async def secret_reveal_planned(ctx: ProjectionContext) -> None:
    p = ctx.payload
    record = await load_record(ctx, "secrets", SecretRecord, p["id"])
    if record is not None:
        if not record.revealed:
            updated = record.model_copy(update={
                "reveal_window_lo": p.get("window_lo", 0),
                "reveal_window_hi": p.get("window_hi", 0),
            })
            await _write_secret(ctx, updated)
        # else: no-op once revealed.
    # else: unknown secret id -- no-op, no error raised.


@projects(EventType.SECRET_LEARNED)
async def secret_learned(ctx: ProjectionContext) -> None:
    p = ctx.payload
    await ctx.execute(
        "INSERT OR IGNORE INTO secret_knowledge (secret_id, character_id, chapter_id, note) "
        "VALUES (?,?,?,?)",
        (p["id"], p["character_id"], p.get("chapter_id", ""), p.get("note", "")),
    )


@projects(EventType.SECRET_REFERENCED)
async def secret_referenced(ctx: ProjectionContext) -> None:
    p = ctx.payload
    await ctx.execute(
        "INSERT INTO secret_references (secret_id, character_id, chapter_id, note) VALUES (?,?,?,?)",
        (p["id"], p["character_id"], p.get("chapter_id", ""), p.get("note", "")),
    )


@projects(EventType.SECRET_REVEALED)
async def secret_revealed(ctx: ProjectionContext) -> None:
    record = await load_record(ctx, "secrets", SecretRecord, ctx.payload["id"])
    if record is not None:
        if not record.revealed:
            updated = record.model_copy(update={"revealed": True})
            await _write_secret(ctx, updated)
        # else: set-once — already revealed, event is a fact in the
        # log but the projection does not change (Locked decision #2).
    # else: no row for this id yet (shouldn't happen under correct
    # agent behavior) — nothing to project, no error raised.
