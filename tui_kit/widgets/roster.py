"""Pure glyph-strip rendering: the cast's status as one glyph+mark pair
per agent, in the agent's theme color. No Textual imports, no I/O,
unit-testable without a terminal."""
from __future__ import annotations

from rich.text import Text

from tui_kit.contracts import AgentTheme

DIM = "dim"
ALARM_STYLE = "bold red"

# State marks appended to each agent's glyph. Precedence: errored > paused >
# waiting on the LLM pool > running > idle. The spinner is static per render
# (any spinner char is fine). The pool wait sits above running because a run
# queued behind the shared permit is doing nothing: a spinner there would make
# a 429 pile-up read as a hung agent, and the idle dot would hide it.
RUNNING_MARK = "⠋"
WAITING_MARK = "⋯"
IDLE_MARK = "·"
PAUSED_MARK = "‖"
ERROR_MARK = "!"


def _mark(s: dict) -> tuple[str, str | None]:
    """(mark, style) for one status row; style None means 'agent color'."""
    if s.get("last_error"):
        return ERROR_MARK, ALARM_STYLE
    if s.get("paused"):
        return PAUSED_MARK, DIM
    if s.get("waiting_on_pool"):
        return WAITING_MARK, DIM
    if s.get("running"):
        return RUNNING_MARK, None
    return IDLE_MARK, DIM


def roster_glyphs(status: list, theme: AgentTheme) -> Text:
    """The cast as a glyph strip — '✎⠋ §· ⌂· ♥· ⚖· ↺· ∿·'. Glyph in the
    agent's theme color; mark carries state. Status fields the strip does
    not need (last_completed, run_count, next_ready_in) are accepted and
    ignored."""
    if not status:
        return Text("no agents", style=DIM)
    strip = Text()
    for i, s in enumerate(status):
        if i:
            strip.append(" ")
        style = theme.style(s["name"])
        strip.append(theme.glyph(s["name"]), style=style)
        mark, mark_style = _mark(s)
        strip.append(mark, style=style if mark_style is None else mark_style)
    return strip


def roster_summary(status: list, theme: AgentTheme) -> str:
    """Plain-string variant of the glyph strip for string-surface needs."""
    return roster_glyphs(status, theme).plain
