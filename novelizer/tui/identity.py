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
    "plotter": AgentIdentity("plotter", "Plotter", "⌖", "P", "turquoise2"),
    "muse": AgentIdentity("muse", "Muse", "✦", "M", "orchid"),
    "director": AgentIdentity("director", "Director", "★", "D", "bold"),
    "system": AgentIdentity("system", "System", "·", "-", "dim"),
}

# glyph + space + longest label ("Continuity", 10 cells) = 12; the feed's
# fixed speaker column pads to this so lines scan like a screenplay.
SPEAKER_WIDTH = 12


def identity_for(agent_name: str) -> AgentIdentity:
    """Registry lookup with a dim, title-cased fallback for unknown names
    (preserves the historical 'Mystery Agent' title-case fallback for any
    agent_name not in the registry, including the empty string)."""
    ident = IDENTITIES.get(agent_name)
    if ident is not None:
        return ident
    label = agent_name.replace("_", " ").title() or "System"
    return AgentIdentity(agent_name, label, "·", "-", "dim")


# Mirrors AGENT_REGISTRY's scheduling order in novelizer/agents/registry.py --
# kept as a plain tuple (not imported) so this module stays free of the heavy
# agent-construction import chain. Keep in sync if agents are added/removed.
AGENT_NAMES = (
    "world_architect", "character_keeper", "muse", "plotter", "author",
    "editor", "continuity_checker", "retconner", "structure_analyst",
)

_VERBS = {
    "author": "drafting",
    "editor": "reviewing",
    "world_architect": "worldbuilding",
    "character_keeper": "tending characters",
    "continuity_checker": "checking continuity",
    "retconner": "retconning",
    "structure_analyst": "scoring structure",
}


class NovelizerAgentTheme:
    """novelizer's tui_kit.contracts.AgentTheme implementation, backed by
    the IDENTITIES registry and the agent-verb table above."""

    def glyph(self, agent_name: str) -> str:
        return identity_for(agent_name).glyph

    def label(self, agent_name: str) -> str:
        return identity_for(agent_name).label

    def style(self, agent_name: str) -> str:
        return identity_for(agent_name).style

    def verb(self, agent_name: str) -> str:
        return _VERBS.get(agent_name, "working")


NOVELIZER_AGENT_THEME = NovelizerAgentTheme()
