"""Pure Zone-5 statusbar rendering: scheduler status + autonomy state ->
Rich Text. The roster glyph strip itself lives in tui_kit.widgets.roster
(domain-agnostic); this module adds novelizer's autonomy dial and composes
the two into the full statusbar, using NOVELIZER_AGENT_THEME for glyphs."""
from __future__ import annotations

from rich.text import Text

from novelizer.canon.autonomy import AutonomyLevel, AutonomyState
from novelizer.tui.identity import NOVELIZER_AGENT_THEME
from tui_kit.widgets.roster import roster_glyphs

DIM = "dim"

DIAL_SEGMENTS = 4
DIAL_FILLED = "▮"
DIAL_EMPTY = "▯"

# Trust ladder from the real AutonomyLevel enum: filled segments = trust.
# full_auto is the wide-open dial; gated_all is the floor (1 filled).
DIAL_LEVELS: dict[AutonomyLevel, int] = {
    AutonomyLevel.full_auto: 4,
    AutonomyLevel.gated_retcons: 3,
    AutonomyLevel.gated_canon: 2,
    AutonomyLevel.gated_all: 1,
}
# Color steps with trust (spec Zone 5: green → amber; red at the floor).
DIAL_STYLES: dict[AutonomyLevel, str] = {
    AutonomyLevel.full_auto: "green3",
    AutonomyLevel.gated_retcons: "gold3",
    AutonomyLevel.gated_canon: "dark_orange",
    AutonomyLevel.gated_all: "red",
}


def dial_meter(state: AutonomyState) -> Text:
    """The dial: 'AUTONOMY ▮▮▯▯ gated_canon' — filled segments = trust
    position on the ladder, per-agent overrides folded to a dim suffix."""
    filled = DIAL_LEVELS[state.global_level]
    style = DIAL_STYLES[state.global_level]
    meter = Text()  # no base style: each part styles itself via spans
    meter.append("AUTONOMY ", style=DIM)
    meter.append(DIAL_FILLED * filled, style=style)
    if filled < DIAL_SEGMENTS:
        meter.append(DIAL_EMPTY * (DIAL_SEGMENTS - filled), style=DIM)
    meter.append(f" {state.global_level.value}", style=style)
    if state.overrides:
        summary = ", ".join(f"{k}={v.value}" for k, v in state.overrides.items())
        meter.append(f" ({summary})", style=DIM)
    return meter


def status_strip(status: list, state: AutonomyState) -> Text:
    """The whole Zone-5 statusbar: roster glyph strip + autonomy dial."""
    strip = roster_glyphs(status, NOVELIZER_AGENT_THEME)
    strip.append("    ")
    strip.append_text(dial_meter(state))
    return strip
