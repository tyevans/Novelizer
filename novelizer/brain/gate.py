"""Outline-first soft gate: decides whether the Author may draft yet.

The single source of truth for "does an active blueprint exist?" Pure and
async — takes a ReadStore-shaped object, reads no wall clock, mints no events.
The gate lives in the readiness layer (the Author's readiness() short-circuits
to 0.0 while closed), never in the scheduler: keeping it soft is the whole
point.
"""
from __future__ import annotations

from novelizer.canon.events import EventType


async def has_active_blueprint(read) -> bool:
    """True once the Plotter's blueprint has been adopted (approved)."""
    return await read.get_active_blueprint() is not None


async def genesis_fallback_open(read) -> bool:
    """Unattended-run escape hatch. Opens when the Plotter has proposed a
    blueprint AND the World Architect has built world from the premise, yet
    no blueprint is active — i.e. real genesis work happened but nobody
    approved the blueprint (a run with no human at the wheel). Progress-based,
    not a wall-clock timer, to match the scheduler's event-driven design.
    """
    proposals = await read.list_proposals(status="open")
    pending_blueprint = any(
        p.target_event_type == EventType.BLUEPRINT_ADOPTED for p in proposals
    )
    if not pending_blueprint:
        return False
    return len(await read.list_world_entries()) > 0


async def author_may_draft(read, *, gate_enabled: bool) -> bool:
    """The gate the Author consults in readiness(). Disabled -> always open."""
    if not gate_enabled:
        return True
    if await has_active_blueprint(read):
        return True
    return await genesis_fallback_open(read)
