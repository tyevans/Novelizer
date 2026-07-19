"""Pure feed rendering: StoredEvent -> rich.text.Text.

Same seam and testability as the other *_model.py modules — no Textual
imports, no I/O, unit-testable without a terminal.
"""
from __future__ import annotations

import re
import textwrap

from rich.text import Text

from novelizer.agents.editor import VOICE_SOURCE_TAG
from novelizer.brain.leaks import LEAK_SOURCE_TAG
from novelizer.brain.mining import MINED_SOURCE_TAG
from novelizer.brain.paradoxes import PARADOX_SOURCE_TAG
from novelizer.canon.events import EventType, StoredEvent
from novelizer.tui.identity import SPEAKER_WIDTH, identity_for

# The four alarm sources map to short badges instead of printing
# "[source: voice_drift]" raw in the feed. Keys are the imported constants —
# if a tag string ever changes at its source, the mapping follows.
SOURCE_BADGES: dict[str, str] = {
    VOICE_SOURCE_TAG: "[drift]",
    LEAK_SOURCE_TAG: "[leak]",
    PARADOX_SOURCE_TAG: "[paradox]",
    MINED_SOURCE_TAG: "[mined]",
}


def _s(value: object) -> str:
    """Coerce a possibly-None/non-str payload value to a safe string."""
    return "" if value is None else str(value)


def parse_source_badge(description: str) -> tuple[str | None, str]:
    """Split a retcon description into (badge, remaining text).

    A description prefixed by a known *_SOURCE_TAG yields its short badge and
    the text with the tag stripped; anything else yields (None, description)
    untouched — unknown tags stay visible rather than being silently eaten.
    """
    for tag, badge in SOURCE_BADGES.items():
        if description.startswith(tag):
            return badge, description[len(tag):].lstrip()
    return None, description


CLAMP_WIDTH = 76
CLAMP_LINES = 2

_BOLD_RE = re.compile(r"\*\*(.+?)\*\*", re.DOTALL)


def clamp_text(s: str, width: int = CLAMP_WIDTH, max_lines: int = CLAMP_LINES) -> tuple[str, bool]:
    """Collapse whitespace and clamp to at most max_lines lines of at most
    width chars. Returns (clamped, truncated). The feed is a pulse, not a
    document — the full text is always one selection away in the detail pane.
    """
    collapsed = " ".join(s.split())
    if not collapsed:
        return "", False
    lines = textwrap.wrap(collapsed, width=width, break_long_words=True, break_on_hyphens=False)
    if len(lines) <= max_lines:
        return "\n".join(lines), False
    return "\n".join(lines[:max_lines]), True


def md_inline(s: str) -> Text:
    """Render inline markdown bold (**x** -> bold span); never show ** raw.

    Only balanced ** pairs are treated as markup; anything unpaired stays
    literal, so stray asterisks in agent prose survive.
    """
    text = Text()
    pos = 0
    for m in _BOLD_RE.finditer(s):
        text.append(s[pos:m.start()])
        text.append(m.group(1), style="bold")
        pos = m.end()
    text.append(s[pos:])
    return text


ALARM_STYLE = "bold red"
CHIP_STYLE = "dim"
CONTINUATION = " …"

# StoredEvent records no actor, so the speaker is inferred from event type —
# same convention the old _LABELS dict used, extended. retcon_request.* maps
# to the Retconner (the agent that owns the retcon queue); the *source* of an
# alarm is carried by its badge, not its speaker.
_EVENT_SPEAKERS: dict[str, str] = {
    EventType.CHAPTER_CREATED: "author",
    EventType.CHAPTER_STATUS_CHANGED: "editor",
    EventType.WORLD_ENTRY_CREATED: "world_architect",
    EventType.WORLD_ENTRY_SUPERSEDED: "world_architect",
    EventType.CHARACTER_CREATED: "character_keeper",
    EventType.CHARACTER_UPDATED: "character_keeper",
    EventType.DIRECTOR_SIGNAL_CREATED: "director",
    EventType.RETCON_REQUEST_CREATED: "retconner",
    EventType.RETCON_REQUEST_RESOLVED: "retconner",
    EventType.RETCON_REQUEST_REJECTED: "retconner",
    EventType.ANNOTATION_STRUCTURE_SCORED: "structure_analyst",
}

_ALARM_EVENTS = {EventType.RETCON_REQUEST_CREATED}

# event-type prefix -> readable domain chip; unlisted prefixes read fine as-is
_DOMAIN_CHIPS = {
    "world_entry": "lore",
    "causal_edge": "cause",
    "annotation": "shape",
    "director_signal": "signal",
}

WELCOME_PLAIN = (
    "★ The room is assembled: Author, Editor, Architect, Keeper, Continuity, Retconner, Analyst.",
    "★ It's quiet. Give them a world:  :seed a lighthouse keeper who taxes the tide",
)


def _speaker(agent_name: str) -> Text:
    ident = identity_for(agent_name)
    return Text(f"{ident.glyph} {ident.label}".ljust(SPEAKER_WIDTH), style=ident.style)


def _domain_chip(event_type: str) -> str:
    domain = event_type.split(".", 1)[0]
    return _DOMAIN_CHIPS.get(domain, domain)


def _detail_for(ev: StoredEvent) -> str:
    p = ev.payload
    t = ev.event_type
    if t == EventType.CHAPTER_CREATED:
        return f'drafted "{_s(p.get("title"))}"'
    if t == EventType.CHAPTER_STATUS_CHANGED:
        return f'reviewed "{_s(p.get("title"))}" — {_s(p.get("editorial_status"))}'
    if t == EventType.WORLD_ENTRY_CREATED:
        return f"lore: {_s(p.get('title'))}"
    if t == EventType.CHARACTER_CREATED:
        return f"new character: {_s(p.get('name'))}"
    if t == EventType.DIRECTOR_SIGNAL_CREATED:
        return f"signal: {_s(p.get('body'))}"
    if t == EventType.ANNOTATION_STRUCTURE_SCORED:
        try:
            tension = float(p.get("tension", 0.0))
        except (TypeError, ValueError):
            tension = 0.0
        return f"scored — tension {tension:.2f}, {_s(p.get('pacing_label'))}"
    for key in ("title", "name", "note", "description", "body"):
        val = p.get(key)
        if val:
            return _s(val)
    return t


def render_event(ev: StoredEvent) -> Text:
    """One feed line per event: canon (speaker + detail + dim domain chip),
    remark (dim italic 💬), or alarm (bold red ⚠ + source badge)."""
    if ev.event_type == EventType.AGENT_REMARKED:
        note, truncated = clamp_text(_s(ev.payload.get("note")))
        line = _speaker(_s(ev.payload.get("agent_name")) or "system")
        line.append("💬 ", style="dim italic")
        body = md_inline(f'"{note}"')
        body.stylize("dim italic")
        line.append_text(body)
        if truncated:
            line.append(CONTINUATION, style="dim")
        return line

    speaker = _EVENT_SPEAKERS.get(ev.event_type, "system")
    if ev.event_type in _ALARM_EVENTS:
        badge, rest = parse_source_badge(_s(ev.payload.get("description")))
        detail, truncated = clamp_text(f"retcon filed: {rest}")
        line = _speaker(speaker)
        body = md_inline(f"⚠ {detail}")
        body.stylize(ALARM_STYLE)
        line.append_text(body)
        if truncated:
            line.append(CONTINUATION, style="dim")
        if badge:
            line.append(f"  {badge}", style=ALARM_STYLE)
        return line

    detail, truncated = clamp_text(_detail_for(ev))
    line = _speaker(speaker)
    line.append_text(md_inline(detail))
    if truncated:
        line.append(CONTINUATION, style="dim")
    line.append(f"  ◆ {_domain_chip(ev.event_type)}", style=CHIP_STYLE)
    return line


def chapter_rule(number: int, title: str) -> Text:
    """Dim horizontal rule written before each chapter.created line, so the
    feed self-organizes into acts. `number` is the running chapter count —
    tracked by the feed loop, never stored (no new events/projections)."""
    return Text(f"── ch {number} · {title} ──", style="dim")


def welcome_lines() -> list[Text]:
    """Director-voiced two-line welcome for a story with an empty event log."""
    director = identity_for("director")
    return [Text(line, style=director.style) for line in WELCOME_PLAIN]


def worker_error_line(worker_name: str, error: Exception) -> Text:
    """Worker errors are alarms too — same plain text as before (resilience
    tests assert on it), now styled."""
    return Text(f"⚠ {worker_name} error: {error}", style=ALARM_STYLE)
