"""Causal-edge projections: the declared cause/effect links between chapters."""
from __future__ import annotations

from novelizer.canon.events import EventType
from novelizer.canon.projections.registry import ProjectionContext, projects


@projects(EventType.CAUSAL_EDGE_DECLARED)
async def causal_edge_declared(ctx: ProjectionContext) -> None:
    p = ctx.payload
    await ctx.execute(
        "INSERT INTO causal_edges (cause_chapter_id, effect_chapter_id, note) VALUES (?,?,?)",
        (p["cause_chapter_id"], p["effect_chapter_id"], p.get("note", "")),
    )
