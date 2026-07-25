"""Projections for the human/agent control surface: director signals, proposals,
autonomy level and the chat transcript. These are how instruction flows into the
run, as opposed to the narrative canon the other modules project."""
from __future__ import annotations

from novelizer.canon.events import EventType
from novelizer.canon.projections.registry import ProjectionContext, projects, upsert


@projects(EventType.DIRECTOR_SIGNAL_CREATED)
async def signal_created(ctx: ProjectionContext) -> None:
    p = ctx.payload
    await ctx.execute(
        upsert("director_signals", "id, data, consumed", "?,?,?"),
        (p["id"], ctx.data, 1 if p.get("consumed") else 0),
    )


@projects(EventType.DIRECTOR_SIGNAL_CONSUMED)
async def signal_consumed(ctx: ProjectionContext) -> None:
    await ctx.execute(
        "UPDATE director_signals SET consumed=1 WHERE id=?", (ctx.aggregate_id,)
    )


@projects(EventType.PROPOSAL_CREATED)
async def proposal_created(ctx: ProjectionContext) -> None:
    p = ctx.payload
    await ctx.execute(
        upsert("proposals", "id, data, status, proposing_agent", "?,?,?,?"),
        (p["id"], ctx.data, p.get("status", "open"), p["proposing_agent"]),
    )


@projects(EventType.PROPOSAL_APPROVED, EventType.PROPOSAL_REJECTED)
async def proposal_decided(ctx: ProjectionContext) -> None:
    new_status = "approved" if ctx.event_type == EventType.PROPOSAL_APPROVED else "rejected"
    await ctx.execute(
        "UPDATE proposals SET status=? WHERE id=?", (new_status, ctx.payload["id"])
    )


@projects(EventType.AUTONOMY_CHANGED)
async def autonomy_changed(ctx: ProjectionContext) -> None:
    await ctx.execute(upsert("autonomy_state", "id, data", "'singleton', ?"), (ctx.data,))


@projects(EventType.CHAT_USER_MESSAGED, EventType.CHAT_AGENT_REPLIED)
async def chat_message(ctx: ProjectionContext) -> None:
    p = ctx.payload
    role = "user" if ctx.event_type == EventType.CHAT_USER_MESSAGED else "agent"
    await ctx.execute(
        "INSERT OR IGNORE INTO chat_messages (message_id, agent_name, role, text) VALUES (?,?,?,?)",
        (p["message_id"], p["agent_name"], role, p.get("text", "")),
    )
