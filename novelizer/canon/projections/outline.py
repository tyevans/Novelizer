"""Outline projections: the story blueprint that acts as the book's spine, its
beats, and the per-chapter briefs drafted against it."""
from __future__ import annotations

from novelizer.canon.events import EventType
from novelizer.canon.projections.registry import (
    ProjectionContext, load_record, projects, row_exists, upsert,
)
from novelizer.store.models import (
    BeatRecord, BlueprintRecord, BriefStatus, ChapterBriefRecord,
)

_BLUEPRINT_COLUMNS = "id, data, active"
_BRIEF_COLUMNS = "id, data, status"


async def _write_active_blueprint(ctx: ProjectionContext, record: BlueprintRecord) -> None:
    await ctx.execute(
        upsert("blueprints", _BLUEPRINT_COLUMNS, "?,?,?"),
        (record.id, record.model_dump_json(), 1),
    )


async def _write_beat(ctx: ProjectionContext, record: BeatRecord) -> None:
    await ctx.execute(
        upsert("beats", "id, data", "?,?"),
        (record.id, record.model_dump_json()),
    )


@projects(EventType.BLUEPRINT_ADOPTED)
async def blueprint_adopted(ctx: ProjectionContext) -> None:
    # Adoption supersedes any prior active blueprint: fold active=False
    # into every existing row's data JSON (so records stay truthful on
    # read) and clear the active=1 flag column, then drop all beats --
    # superseded blueprints keep their record for audit, but their
    # beats do not survive as live targets.
    p = ctx.payload
    for row_id, row_data in await ctx.fetch_all("SELECT id, data FROM blueprints"):
        existing = BlueprintRecord.model_validate_json(row_data)
        updated = existing.model_copy(update={"active": False})
        await ctx.execute(
            "UPDATE blueprints SET data=?, active=0 WHERE id=?",
            (updated.model_dump_json(), row_id),
        )
    await ctx.execute("DELETE FROM beats")
    record = BlueprintRecord(
        id=p["blueprint_id"], framework=p["framework"],
        target_chapter_count=p["target_chapter_count"], genre=p.get("genre", ""),
        obligatory_scenes=p.get("obligatory_scenes", []), active=True, note=p.get("note", ""),
    )
    await _write_active_blueprint(ctx, record)
    for spec in p.get("beats", []):
        beat = BeatRecord(
            id=spec["beat_id"], blueprint_id=record.id, slug=spec["slug"], name=spec["name"],
            ideal_pct=spec["ideal_pct"], tolerance_pct=spec["tolerance_pct"],
            expected_polarity=spec.get("expected_polarity", ""),
        )
        await _write_beat(ctx, beat)


@projects(EventType.BLUEPRINT_RETARGETED)
async def blueprint_retargeted(ctx: ProjectionContext) -> None:
    p = ctx.payload
    row = await ctx.fetch_one(
        "SELECT data, active FROM blueprints WHERE id=?", (p["blueprint_id"],)
    )
    if row is not None and row[1]:
        record = BlueprintRecord.model_validate_json(row[0])
        updated = record.model_copy(update={"target_chapter_count": p["target_chapter_count"]})
        await _write_active_blueprint(ctx, updated)
    # else: unknown or superseded blueprint id -- no-op, no error raised.


@projects(EventType.BOOK_COMPLETED)
async def book_completed(ctx: ProjectionContext) -> None:
    # One-shot per active blueprint: fold only if it is still the
    # active row and not already completed. BLUEPRINT_ADOPTED builds
    # a fresh BlueprintRecord on adoption, so a new blueprint always
    # starts uncompleted -- no explicit reset needed here.
    p = ctx.payload
    row = await ctx.fetch_one(
        "SELECT data, active FROM blueprints WHERE id=?", (p["blueprint_id"],)
    )
    if row is not None and row[1]:
        record = BlueprintRecord.model_validate_json(row[0])
        if not record.completed:
            updated = record.model_copy(update={
                "completed": True,
                "completed_chapter_id": p.get("chapter_id", ""),
                "completed_note": p.get("note", ""),
            })
            await _write_active_blueprint(ctx, updated)
        # else: already completed -- repeat is a projection no-op.
    # else: unknown or superseded blueprint id -- no-op, no error raised.


@projects(EventType.BEAT_FULFILLED)
async def beat_fulfilled(ctx: ProjectionContext) -> None:
    p = ctx.payload
    record = await load_record(ctx, "beats", BeatRecord, p["beat_id"])
    if record is not None:
        updated = record.model_copy(update={
            "fulfilled_by_chapter_id": p.get("chapter_id", ""),
            "note": p.get("note", ""),
        })
        await _write_beat(ctx, updated)
    # else: unknown beat id -- no-op, no error raised.


@projects(EventType.CHAPTER_BRIEF_DRAFTED)
async def chapter_brief_drafted(ctx: ProjectionContext) -> None:
    p = ctx.payload
    if not await row_exists(ctx, "chapter_briefs", p["brief_id"]):
        record = ChapterBriefRecord(
            id=p["brief_id"], target_ordinal=p["target_ordinal"], goal=p["goal"],
            pov_character_id=p.get("pov_character_id", ""),
            threads_to_touch=p.get("threads_to_touch", []),
            beats_to_hit=p.get("beats_to_hit", []),
            promises_to_progress=p.get("promises_to_progress", []),
            value_shift=p.get("value_shift", ""), planned_outcome=p.get("planned_outcome", ""),
            synopsis=p.get("synopsis", ""),
        )
        await ctx.execute(
            upsert("chapter_briefs", _BRIEF_COLUMNS, "?,?,?"),
            (record.id, record.model_dump_json(), record.status.value),
        )
    # else: a brief id is minted exactly once -- first-draft-wins.


@projects(EventType.CHAPTER_BRIEF_SUPERSEDED, EventType.CHAPTER_BRIEF_FULFILLED)
async def chapter_brief_closed(ctx: ProjectionContext) -> None:
    p = ctx.payload
    record = await load_record(ctx, "chapter_briefs", ChapterBriefRecord, p["brief_id"])
    if record is not None:
        if record.status == BriefStatus.open:
            if ctx.event_type == EventType.CHAPTER_BRIEF_SUPERSEDED:
                updated = record.model_copy(update={
                    "status": BriefStatus.superseded,
                    "superseded_by_brief_id": p.get("superseded_by_brief_id", ""),
                })
            else:
                updated = record.model_copy(update={
                    "status": BriefStatus.fulfilled,
                    "fulfilled_by_chapter_id": p.get("chapter_id", ""),
                })
            await ctx.execute(
                upsert("chapter_briefs", _BRIEF_COLUMNS, "?,?,?"),
                (updated.id, updated.model_dump_json(), updated.status.value),
            )
        # else: superseded/fulfilled are absorbing -- the event is a
        # fact in the log, but the projection does not change.
    # else: unknown brief id -- no-op, no error raised.
