"""The TUI's agent tables must cover the whole fleet.

`identity.py` deliberately keeps AGENT_NAMES as a plain tuple rather than
importing AGENT_REGISTRY, so the TUI stays free of the agent-construction import
chain. That is a reasonable trade, but "keep in sync" in a comment is not a
mechanism: AGENT_NAMES had drifted to 9 of 13 agents, so Curator, Summarizer,
Triage and FlagLabeler got no Engine Room lane and were never rendered live
(novelizer/tui/app.py gates render_agent_live on `agent in AGENT_NAMES`), and
three of them had no identity at all and fell back to the dim unknown-agent
style in the feed.

These tests are the mechanism. They import both sides -- which a test may do
freely -- and fail the moment an agent is added to the registry without being
given a place in the interface.
"""
from __future__ import annotations

from novelizer.agents.registry import AGENT_REGISTRY
from novelizer.tui.identity import AGENT_NAMES, IDENTITIES, SPEAKER_WIDTH, identity_for


def test_agent_names_covers_the_registry_in_scheduling_order():
    assert list(AGENT_NAMES) == [spec.name for spec in AGENT_REGISTRY], (
        "AGENT_NAMES must mirror AGENT_REGISTRY exactly, in scheduling order -- "
        "a missing name means that agent has no Engine Room lane and is never "
        "rendered live"
    )


def test_every_registry_agent_has_its_own_identity():
    missing = [spec.name for spec in AGENT_REGISTRY if spec.name not in IDENTITIES]
    assert not missing, (
        f"these agents fall back to the dim unknown-agent identity in the feed: {missing}"
    )


def test_every_agent_identity_is_visually_distinct():
    """The Engine Room reads as lanes only if each agent owns a colour."""
    colours = {name: IDENTITIES[name].style for name in AGENT_NAMES}
    assert len(set(colours.values())) == len(AGENT_NAMES), colours
    letters = {name: IDENTITIES[name].fallback for name in AGENT_NAMES}
    assert len(set(letters.values())) == len(AGENT_NAMES), letters


def test_identity_styles_are_hex_not_named_colours():
    """Textual's style parser silently drops Rich's 256-colour names.

    A named colour therefore renders as no colour at all, which is invisible in
    review and invisible in tests that only check the string round-trips.
    """
    for spec in AGENT_REGISTRY:
        style = IDENTITIES[spec.name].style
        assert style.startswith("#"), f"{spec.name} identity style {style!r} must be hex"


def test_labels_fit_the_feed_speaker_column():
    for spec in AGENT_REGISTRY:
        ident = identity_for(spec.name)
        # glyph + space + label must fit the fixed column the feed pads to.
        assert len(ident.glyph) + 1 + len(ident.label) <= SPEAKER_WIDTH, (
            f"{spec.name} label {ident.label!r} overflows the speaker column"
        )
