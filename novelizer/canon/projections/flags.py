"""Flag projections: the review queue of contradictions, drift and other
concerns agents raise for the Editor and the human."""
from __future__ import annotations
import json

from novelizer.canon.events import EventType
from novelizer.canon.projections.registry import ProjectionContext, projects, upsert

_FLAG_COLUMNS = "id, data, status, category, escalated"

_LEGACY_STATUS_BY_TYPE = {
    EventType.RETCON_REQUEST_CREATED: "open",
    EventType.RETCON_REQUEST_RESOLVED: "resolved",
    EventType.RETCON_REQUEST_REJECTED: "rejected",
}


async def _write_flag(ctx: ProjectionContext, status: str, escalated: int | None = None) -> None:
    """Upsert the flag row. `escalated=None` keeps whatever the payload carries."""
    p = ctx.payload
    if escalated is None:
        escalated = int(p.get("escalated", False))
    await ctx.execute(
        upsert("flags", _FLAG_COLUMNS, "?,?,?,?,?"),
        (p["id"], ctx.data, status, p.get("category", ""), escalated),
    )


@projects(EventType.FLAG_CREATED)
async def flag_created(ctx: ProjectionContext) -> None:
    await _write_flag(ctx, ctx.payload.get("status", "open"))


@projects(EventType.FLAG_RESOLVED, EventType.FLAG_REJECTED)
async def flag_decided(ctx: ProjectionContext) -> None:
    default = "resolved" if ctx.event_type == EventType.FLAG_RESOLVED else "rejected"
    await _write_flag(ctx, ctx.payload.get("status", default))


@projects(EventType.FLAG_ESCALATED)
async def flag_escalated(ctx: ProjectionContext) -> None:
    await _write_flag(ctx, ctx.payload.get("status", "open"), escalated=1)


@projects(EventType.FLAG_ESCALATION_CLEARED)
async def flag_escalation_cleared(ctx: ProjectionContext) -> None:
    await _write_flag(ctx, ctx.payload.get("status", "open"), escalated=0)


@projects(EventType.FLAG_LABELED)
async def flag_labeled(ctx: ProjectionContext) -> None:
    # Narrow update: patch only title/summary into the existing row's data blob.
    # It never writes the status/category/escalated columns, so a label built
    # from a stale read cannot resurrect a status or escalation a later flag.*
    # event has since changed -- ordering by event sequence keeps this label
    # subordinate to any create/resolve/escalate that lands after it. No-op if
    # the flag row is absent.
    p = ctx.payload
    await ctx.execute(
        "UPDATE flags SET data = json_set(data, '$.title', ?, '$.summary', ?) WHERE id=?",
        (p.get("title", ""), p.get("summary", ""), p["id"]),
    )


@projects(EventType.RETCON_REQUEST_CREATED, EventType.RETCON_REQUEST_RESOLVED,
          EventType.RETCON_REQUEST_REJECTED)
async def legacy_retcon_request(ctx: ProjectionContext) -> None:
    # Legacy alias: pre-Flag databases only ever emitted these three event types
    # for contradictions. Project them into the same `flags` table as
    # category="contradiction" so old event logs keep working without any code
    # path emitting these anymore.
    p = ctx.payload
    legacy_status = p.get("status")
    if legacy_status is None:
        legacy_status = _LEGACY_STATUS_BY_TYPE[ctx.event_type]
    aliased = dict(p)
    aliased["category"] = "contradiction"
    aliased.setdefault("related_entry_ids", aliased.pop("conflicting_entry_ids", []))
    aliased.setdefault("filed_by", "")
    aliased.setdefault("triage_passes", 0)
    await ctx.execute(
        upsert("flags", _FLAG_COLUMNS, "?,?,?,?,?"),
        (aliased["id"], json.dumps(aliased), legacy_status, "contradiction",
         int(aliased.get("escalated", False))),
    )
