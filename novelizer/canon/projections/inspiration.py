"""Inspiration projections: the Muse's hands of raw material, their lifecycle,
and which items prose actually took up."""
from __future__ import annotations

from novelizer.canon.events import EventType
from novelizer.canon.projections.registry import (
    ProjectionContext, load_record, projects, row_exists, upsert,
)
from novelizer.store.models import HandStatus, InspirationHandRecord

_HAND_COLUMNS = "id, data, status"


async def _write_hand(ctx: ProjectionContext, record: InspirationHandRecord) -> None:
    await ctx.execute(
        upsert("inspiration_hands", _HAND_COLUMNS, "?,?,?"),
        (record.id, record.model_dump_json(), record.status.value),
    )


@projects(EventType.INSPIRATION_DRAWN)
async def inspiration_drawn(ctx: ProjectionContext) -> None:
    p = ctx.payload
    if not await row_exists(ctx, "inspiration_hands", p["hand_id"]):
        record = InspirationHandRecord(
            id=p["hand_id"], seed=p["seed"], corpus_version=p["corpus_version"],
            era=p["era"], names=p.get("names", []), professions=p.get("professions", []),
            settings=p.get("settings", []), beats=p.get("beats", []),
        )
        await _write_hand(ctx, record)
    # else: a hand id is minted exactly once — first-mint-wins, same
    # rule as thread.planted/secret.created/theme.introduced.


@projects(EventType.INSPIRATION_HAND_CONSUMED, EventType.INSPIRATION_HAND_SUPERSEDED)
async def inspiration_hand_closed(ctx: ProjectionContext) -> None:
    p = ctx.payload
    record = await load_record(ctx, "inspiration_hands", InspirationHandRecord, p["hand_id"])
    if record is not None:
        if record.status == HandStatus.active:
            if ctx.event_type == EventType.INSPIRATION_HAND_CONSUMED:
                updated = record.model_copy(update={
                    "status": HandStatus.consumed,
                    "consumed_chapter_id": p.get("chapter_id", ""),
                })
            else:
                updated = record.model_copy(update={"status": HandStatus.superseded})
            await _write_hand(ctx, updated)
        # else: consumed/superseded are absorbing — the event is a fact
        # in the log, but the projection does not change.
    # else: no row for this id (shouldn't happen under correct Muse
    # behavior) — nothing to project, no error raised.


@projects(EventType.INSPIRATION_UPTAKE_RECORDED)
async def inspiration_uptake_recorded(ctx: ProjectionContext) -> None:
    p = ctx.payload
    await ctx.execute(
        "INSERT OR IGNORE INTO inspiration_uptake (hand_id, kind, item, chapter_id) VALUES (?,?,?,?)",
        (p["hand_id"], p["kind"], p["item"], p.get("chapter_id", "")),
    )
