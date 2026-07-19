"""Pure Zone-5 statusbar rendering: scheduler status (+ Task 2: autonomy
state, command hints) -> Rich Text. Same seam as the *_model.py modules —
no Textual imports, no I/O, unit-testable without a terminal.

The old named-summary format ("● author  ⏸ editor  ⚠ x: err") is replaced by
the spec's glyph strip: the whole cast, always visible, one glyph+mark pair
per agent in the agent's identity color. Error text never renders here —
errors land in the feed as alarm lines (spec Zone 5)."""
from __future__ import annotations

from rich.text import Text

from novelizer.canon.autonomy import AutonomyLevel, AutonomyState
from novelizer.tui.identity import identity_for
from novelizer.tui.widgets.feed_model import ALARM_STYLE

DIM = "dim"

# State marks appended to each agent's glyph. Precedence: errored > paused >
# running > idle. The spinner is static per render (any spinner char is fine).
RUNNING_MARK = "⠋"
IDLE_MARK = "·"
PAUSED_MARK = "‖"
ERROR_MARK = "!"


def _mark(s: dict) -> tuple[str, str | None]:
    """(mark, style) for one status row; style None means 'agent color'."""
    if s.get("last_error"):
        return ERROR_MARK, ALARM_STYLE
    if s.get("paused"):
        return PAUSED_MARK, DIM
    if s.get("running"):
        return RUNNING_MARK, None
    return IDLE_MARK, DIM


def roster_glyphs(status: list) -> Text:
    """The cast as a glyph strip — '✎⠋ §· ⌂· ♥· ⚖· ↺· ∿·'. Glyph in the
    agent's color; mark carries state. M5.3 status fields the strip does not
    need (last_completed, run_count, next_ready_in) are accepted and ignored."""
    if not status:
        return Text("no agents", style=DIM)
    strip = Text()
    for i, s in enumerate(status):
        if i:
            strip.append(" ")
        ident = identity_for(s["name"])
        strip.append(ident.glyph, style=ident.style)
        mark, style = _mark(s)
        strip.append(mark, style=ident.style if style is None else style)
    return strip


def roster_summary(status: list) -> str:
    """Plain-string variant of the glyph strip for string-surface needs."""
    return roster_glyphs(status).plain


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
    """The whole Zone-5 statusbar: roster glyph strip + autonomy dial.
    The command cheatsheet is gone — commands live in the Input's hint
    placeholder and :help."""
    strip = roster_glyphs(status)
    strip.append("    ")
    strip.append_text(dial_meter(state))
    return strip


# One hint chosen per app start (command_hint(index)); index 0 under test —
# deterministic, no time/random in the TUI module. Only the CLI randomizes.
PLACEHOLDER_HINTS: tuple[str, ...] = (
    ":seed a lighthouse at the end of the world",
    ":focus the storm that never lands",
    ":pause author — let the room breathe",
    ":autonomy gated_canon — take the wheel yourself",
)


def command_hint(index: int) -> str:
    return PLACEHOLDER_HINTS[index % len(PLACEHOLDER_HINTS)]
