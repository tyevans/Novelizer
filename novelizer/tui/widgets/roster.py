"""Pure Zone-5 statusbar rendering: scheduler status (+ Task 2: autonomy
state, command hints) -> Rich Text. Same seam as the *_model.py modules —
no Textual imports, no I/O, unit-testable without a terminal.

The old named-summary format ("● author  ⏸ editor  ⚠ x: err") is replaced by
the spec's glyph strip: the whole cast, always visible, one glyph+mark pair
per agent in the agent's identity color. Error text never renders here —
errors land in the feed as alarm lines (spec Zone 5)."""
from __future__ import annotations

from rich.text import Text

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
