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


def hold_phrase(s: dict) -> str:
    """Why this agent is not producing, in a phrase, plus the condition it is
    waiting on where there is one. "" for an agent that IS producing.

    The mark says "not running"; a watcher still cannot tell a rate-limited
    fleet from a crash loop from a converged agent, and those call for three
    different responses. The precedence follows _mark's, minus its error branch:
    last_error is the PREVIOUS run's, and the fail ladder already carries the
    error into this render as "backing off". Deliberately not a countdown to a
    scheduled run: dispatch is progress-driven, so the idle seconds are only
    when the agent will next look, not when it will next work."""
    if s.get("paused"):
        return "paused"
    if s.get("waiting_on_pool"):
        # Dispatched, queued behind the shared LLM permit. Nothing this agent
        # does changes it -- the pool has to drain.
        return "waiting on LLM pool permit"
    if s.get("running"):
        return ""
    reason = s.get("hold_reason")
    seconds = int(s.get("hold_seconds") or 0)
    if reason == "backing off":
        return f"backing off after error · retry in {seconds}s"
    if reason == "awaiting progress":
        return f"awaiting story progress · rechecks in {seconds}s"
    return "ready · waiting for a dispatch slot"


def fleet_hold_summary(status: list, limit: int = 3) -> str:
    """Every hold in the fleet, grouped by reason: "2× paused · waiting on LLM
    pool permit". "" when everything is producing.

    The per-agent panes that used to caption each idle agent are gone, so one
    line has to carry this for the whole fleet. Without it a rate-limited
    fleet, a crash loop and a converged fleet all look like the same silence
    to someone glancing at an overnight run.
    """
    counts: dict[str, int] = {}
    for s in status:
        phrase = hold_phrase(s)
        if phrase:
            counts[phrase] = counts.get(phrase, 0) + 1
    ranked = sorted(counts.items(), key=lambda kv: -kv[1])[:limit]
    return " · ".join(f"{n}× {p}" if n > 1 else p for p, n in ranked)


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
