"""Chapter projections: the prose itself, its editorial status, and per-chapter
annotations (structure scores, summaries)."""
from __future__ import annotations
import logging

from novelizer.canon.events import EventType
from novelizer.canon.projections.registry import (
    ProjectionContext, load_record, projects, upsert,
)
from novelizer.store.models import Chapter, EditorialStatus

logger = logging.getLogger(__name__)

# A revised chapter's prose more than this multiple of the original prose's
# length is a signal for a human/Retconner to notice via the feed, not
# something the Projector silently corrects (event sourcing: the log is the
# truth) -- see Locked decision 10's escape hatch.
REVISION_LENGTH_SANITY_MULTIPLE = 4

_CHAPTER_COLUMNS = "id, data, editorial_status, supersedes_id"


@projects(EventType.CHAPTER_CREATED, EventType.CHAPTER_STATUS_CHANGED)
async def chapter_written(ctx: ProjectionContext) -> None:
    p = ctx.payload
    await ctx.execute(
        upsert("chapters", _CHAPTER_COLUMNS, "?,?,?,?"),
        (p["id"], ctx.data, p.get("editorial_status", "draft"), p.get("supersedes_id")),
    )


@projects(EventType.CHAPTER_REVISED)
async def chapter_revised(ctx: ProjectionContext) -> None:
    p = ctx.payload
    existing = await load_record(ctx, "chapters", Chapter, p["chapter_id"])
    if existing is None:
        logger.warning(
            "chapter.revised for unknown chapter_id=%s -- no-op (shouldn't happen under correct signal routing)",
            p["chapter_id"],
        )
        return
    if existing.prose and len(p["prose"]) > REVISION_LENGTH_SANITY_MULTIPLE * len(existing.prose):
        logger.warning(
            "chapter.revised prose for chapter_id=%s is >%dx the original length -- "
            "committing anyway (event sourcing: the log is the truth, a length anomaly "
            "is a signal to notice via the feed, not something to silently correct)",
            p["chapter_id"], REVISION_LENGTH_SANITY_MULTIPLE,
        )
    revised = existing.model_copy(update={
        "prose": p["prose"],
        "editorial_status": EditorialStatus.draft,
        "revision_count": existing.revision_count + 1,
    })
    await ctx.execute(
        upsert("chapters", _CHAPTER_COLUMNS, "?,?,?,?"),
        (revised.id, revised.model_dump_json(), EditorialStatus.draft.value, revised.supersedes_id),
    )


@projects(EventType.ANNOTATION_STRUCTURE_SCORED)
async def structure_scored(ctx: ProjectionContext) -> None:
    await ctx.execute(
        upsert("structure_scores", "id, data", "?,?"),
        (ctx.payload["chapter_id"], ctx.data),
    )


@projects(EventType.CHAPTER_SUMMARIZED)
async def chapter_summarized(ctx: ProjectionContext) -> None:
    await ctx.execute(
        upsert("chapter_summaries", "id, data", "?,?"),
        (ctx.payload["chapter_id"], ctx.data),
    )
