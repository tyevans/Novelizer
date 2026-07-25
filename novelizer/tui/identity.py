"""Single source of truth for agent identity: glyph, label, color.

The spec's identity table (docs/superpowers/specs/
2026-07-18-mission-control-design-pass-design.md) rendered as data. Every
place an agent appears — feed speaker column, and (Phase 3) roster and room
view — reads from here. Spec color names map to colors that read well on both
dark and light terminals:

    amber -> gold3 #d7af00, violet -> medium_purple #8787d7,
    teal -> dark_cyan #00af87, rose -> hot_pink3 #d75f87,
    steel blue -> steel_blue #5f87af, orange -> dark_orange #ff8700,
    green -> green3 #00d700, sky -> sky_blue3 #5fafd7,
    turquoise -> turquoise2 #00d7ff, orchid -> orchid #d75fd7,
    white/bold -> bold, dim -> dim

Colors are spelled as hex, not as the Rich 256-color names they came from,
because these strings are parsed by *two* renderers: Rich (feed, proposals,
roster, vitals) and Textual (engine-room tab titles, via Content.styled).
Textual's parser only knows CSS color names, so a Rich name like "gold3"
raises there and the style is dropped silently — which left every engine-room
tab uncolored except the Muse's, whose "orchid" happens to also be CSS. Hex
parses identically in both. Keep it that way; tests/tui/test_identity.py
asserts every style parses under both parsers.
"""
from __future__ import annotations
from dataclasses import dataclass


@dataclass(frozen=True)
class AgentIdentity:
    key: str        # canonical agent_name, e.g. "character_keeper"
    label: str      # short feed label, e.g. "Keeper"
    glyph: str      # single-cell glyph from the spec table
    fallback: str   # single ASCII letter if the terminal lacks the glyph
    style: str      # style string — defined once, here only; Rich- and
    # Textual-parseable, so colors are hex (see the module docstring)


IDENTITIES: dict[str, AgentIdentity] = {
    "author": AgentIdentity("author", "Author", "✎", "A", "#d7af00"),
    "editor": AgentIdentity("editor", "Editor", "§", "E", "#8787d7"),
    "world_architect": AgentIdentity("world_architect", "Architect", "⌂", "W", "#00af87"),
    "character_keeper": AgentIdentity("character_keeper", "Keeper", "♥", "K", "#d75f87"),
    "continuity_checker": AgentIdentity("continuity_checker", "Continuity", "⚖", "C", "#5f87af"),
    "retconner": AgentIdentity("retconner", "Retconner", "↺", "R", "#ff8700"),
    "structure_analyst": AgentIdentity("structure_analyst", "Analyst", "∿", "S", "#00d700"),
    "summarizer": AgentIdentity("summarizer", "Summary", "≡", "Z", "#5fafd7"),
    "plotter": AgentIdentity("plotter", "Plotter", "⌖", "P", "#00d7ff"),
    "muse": AgentIdentity("muse", "Muse", "✦", "M", "#d75fd7"),
    # Added when the parity tests caught them absent: all three commit canon,
    # so all three were already appearing in the feed -- under the dim
    # unknown-agent fallback, whose title-cased "Flaglabeler" also overflowed
    # the speaker column and broke the feed's alignment.
    "curator": AgentIdentity("curator", "Curator", "❖", "U", "#af87ff"),
    "triage": AgentIdentity("triage", "Triage", "⑂", "T", "#ffaf5f"),
    "flaglabeler": AgentIdentity("flaglabeler", "Flags", "⚑", "F", "#87d7af"),
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
# agent-construction import chain.
#
# "Keep in sync" as a comment did not hold: this drifted to 9 of 13 agents, and
# because novelizer/tui/app.py gates render_agent_live() on `agent in
# AGENT_NAMES`, the four missing ones (curator, summarizer, triage, flaglabeler)
# had no Engine Room lane and were never drawn live -- they worked invisibly.
# tests/tui/test_identity_registry_parity.py now asserts this equals the
# registry, so the next added agent fails a test instead of vanishing.
AGENT_NAMES = (
    "world_architect", "character_keeper", "muse", "plotter", "author",
    "editor", "continuity_checker", "retconner", "curator", "structure_analyst",
    "summarizer", "triage", "flaglabeler",
)

_VERBS = {
    "author": "drafting",
    "editor": "reviewing",
    "world_architect": "worldbuilding",
    "character_keeper": "tending characters",
    "continuity_checker": "checking continuity",
    "retconner": "retconning",
    "structure_analyst": "scoring structure",
    "summarizer": "summarizing",
    "plotter": "plotting",
    "muse": "drawing inspiration",
    "curator": "curating canon",
    "triage": "triaging flags",
    "flaglabeler": "labelling flags",
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
