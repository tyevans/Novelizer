"""Single source of truth for agent identity: glyph, label, color.

The spec's identity table (docs/superpowers/specs/
2026-07-18-mission-control-design-pass-design.md) rendered as data. Every
place an agent appears — feed speaker column, and (Phase 3) roster and room
view — reads from here. Spec color names map to Rich styles that read well on
both dark and light terminals:

    amber -> gold3, violet -> medium_purple, teal -> dark_cyan,
    rose -> hot_pink3, steel blue -> steel_blue, orange -> dark_orange,
    green -> green3, white/bold -> bold, dim -> dim
"""
from __future__ import annotations
from dataclasses import dataclass


@dataclass(frozen=True)
class AgentIdentity:
    key: str        # canonical agent_name, e.g. "character_keeper"
    label: str      # short feed label, e.g. "Keeper"
    glyph: str      # single-cell glyph from the spec table
    fallback: str   # single ASCII letter if the terminal lacks the glyph
    style: str      # Rich style string — defined once, here only


IDENTITIES: dict[str, AgentIdentity] = {
    "author": AgentIdentity("author", "Author", "✎", "A", "gold3"),
    "editor": AgentIdentity("editor", "Editor", "§", "E", "medium_purple"),
    "world_architect": AgentIdentity("world_architect", "Architect", "⌂", "W", "dark_cyan"),
    "character_keeper": AgentIdentity("character_keeper", "Keeper", "♥", "K", "hot_pink3"),
    "continuity_checker": AgentIdentity("continuity_checker", "Continuity", "⚖", "C", "steel_blue"),
    "retconner": AgentIdentity("retconner", "Retconner", "↺", "R", "dark_orange"),
    "structure_analyst": AgentIdentity("structure_analyst", "Analyst", "∿", "S", "green3"),
    "director": AgentIdentity("director", "Director", "★", "D", "bold"),
    "system": AgentIdentity("system", "System", "·", "-", "dim"),
}

# glyph + space + longest label ("Continuity", 10 cells) = 12; the feed's
# fixed speaker column pads to this so lines scan like a screenplay.
SPEAKER_WIDTH = 12


def identity_for(agent_name: str) -> AgentIdentity:
    """Registry lookup with a dim, title-cased fallback for unknown names
    (preserves the existing 'Mystery Agent' behavior of _agent_label)."""
    ident = IDENTITIES.get(agent_name)
    if ident is not None:
        return ident
    label = agent_name.replace("_", " ").title() or "System"
    return AgentIdentity(agent_name, label, "·", "-", "dim")
