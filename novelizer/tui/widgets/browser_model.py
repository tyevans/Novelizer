"""Pure browser rendering: ReadStore records -> tree section dicts and the
detail pane's DetailView. Same seam as the other *_model.py modules — the
async functions here do read-only ReadStore calls plus pure string/Text
construction; no Textual imports.

State cues (spec Zone 4): chapter rows carry an editorial-status dot, the
Retcons label carries ⚠ when open items exist, and the Threads section
carries the stale count. Staleness is never re-derived: is_thread_stale +
the settings-fed staleness_threshold, the SAME pair the brain panel uses.
No ids/slugs in any label."""
from __future__ import annotations

from dataclasses import dataclass

from rich.text import Text

from novelizer.brain.staleness import is_thread_stale
from novelizer.canon.threads import TERMINAL_STATES
from novelizer.store.models import EditorialStatus
from novelizer.tui.widgets.brain_model import chapter_label, chapter_number

DIM = "dim"

# Editorial status -> state dot. Real enum values are draft/reviewed/final —
# the spec sketch's approved/draft/revising names map by pipeline position:
# ● final (done), ◐ reviewed (mid-pipeline), ◌ draft.
STATUS_DOTS: dict[EditorialStatus, str] = {
    EditorialStatus.draft: "◌",
    EditorialStatus.reviewed: "◐",
    EditorialStatus.final: "●",
}


def _enum_val(v):
    """Safely extract enum value, handling both enum and non-enum types."""
    return v.value if hasattr(v, "value") else v


def _status_dot(status) -> str:
    try:
        return STATUS_DOTS[EditorialStatus(_enum_val(status))]
    except (KeyError, ValueError):
        return "·"


def word_count(prose: str) -> int:
    """Render-time word count — computed from prose, never stored (spec
    non-goal: no new events/projections)."""
    return len(prose.split())


def _thread_row_label(thread, chapters, threshold: int) -> str:
    if is_thread_stale(thread, chapters, threshold):
        return f"⚠ {thread.name} · stale"
    if thread.state.value in TERMINAL_STATES:
        return f"✓ {thread.name} · {thread.state.value}"
    return f"· {thread.name} · {thread.state.value}"


async def browser_sections(read, *, staleness_threshold: int) -> list:
    """Tree sections in the spec mockup's order. `staleness_threshold` is
    keyword-only with no default: the app reads
    settings.staleness_threshold_chapters every cycle and passes it in —
    the same M5.3 settings -> pure-param flow the brain panel uses."""
    chapters = await read.list_chapters()
    characters = await read.list_characters()
    world = await read.list_world_entries()
    retcons = await read.list_retcon_requests(status="open")
    threads = await read.list_threads()
    themes = await read.list_themes()
    open_threads = [t for t in threads if t.state.value not in TERMINAL_STATES]
    stale_count = sum(
        1 for t in threads if is_thread_stale(t, chapters, staleness_threshold)
    )
    threads_label = (
        f"Threads ({len(open_threads)} · {stale_count} stale)"
        if stale_count
        else f"Threads ({len(open_threads)})"
    )
    retcons_label = f"Retcons ({len(retcons)}) ⚠" if retcons else "Retcons (0)"
    return [
        {"key": "chapters", "label": f"Chapters ({len(chapters)})",
         "items": [{"id": c.id, "label": f"{_status_dot(c.editorial_status)} {c.title}"} for c in chapters]},
        {"key": "characters", "label": f"Characters ({len(characters)})",
         "items": [{"id": c.id, "label": c.name} for c in characters]},
        {"key": "world", "label": f"World ({len(world)})",
         "items": [{"id": e.id, "label": f"[{_enum_val(e.domain)}] {e.title}"} for e in world]},
        {"key": "retcons", "label": retcons_label,
         "items": [{"id": r.id, "label": r.description[:40]} for r in retcons]},
        {"key": "threads", "label": threads_label,
         "items": [{"id": t.id, "label": _thread_row_label(t, chapters, staleness_threshold)} for t in threads]},
        {"key": "themes", "label": f"Themes ({len(themes)})",
         "items": [{"id": t.id, "label": t.title} for t in themes]},
    ]


@dataclass(frozen=True)
class DetailView:
    title: str   # plain; the app uppercases it into #detail_scroll's border title
    body: Text


_EMPTY_VIEW = DetailView("", Text(""))


def _view(title: str, meta: str, prose: str = "",
          fields: list[tuple[str, str]] | None = None) -> DetailView:
    """Detail typography: bold title line, dim metadata line, dim-labeled
    fields, then prose with its paragraphs preserved."""
    body = Text()  # spans, not a base style — bold must not bleed into prose
    body.append(title, style="bold")
    if meta:
        body.append("\n")
        body.append(meta, style=DIM)
    for label, val in fields or []:
        if not val:
            continue
        body.append("\n")
        body.append(f"{label}: ", style=DIM)
        body.append(val)
    if prose:
        body.append("\n\n")
        body.append(prose)
    return DetailView(title, body)


async def detail_view(read, section_key: str, item_id: str) -> DetailView:
    if section_key == "chapters":
        ch = await read.get_chapter(item_id)
        if not ch:
            return _EMPTY_VIEW
        meta = f"{_enum_val(ch.editorial_status)} · {word_count(ch.prose):,} words"
        return _view(ch.title, meta, ch.prose)
    if section_key == "characters":
        c = await read.get_character(item_id)
        if not c:
            return _EMPTY_VIEW
        fields = [("Traits", c.traits), ("Arc", c.arc_status), ("Motivations", c.motivations)]
        if c.voice:
            fields.append(("Voice", c.voice))
        return _view(c.name, "", c.backstory, fields)
    if section_key == "world":
        for e in await read.list_world_entries():
            if e.id == item_id:
                return _view(e.title, _enum_val(e.domain), e.body)
        return _EMPTY_VIEW
    if section_key == "retcons":
        for r in await read.list_retcon_requests():
            if r.id == item_id:
                return _view(r.description, f"status: {_enum_val(r.status)}", "",
                             [("Proposed", r.proposed_resolution)])
        return _EMPTY_VIEW
    if section_key == "threads":
        t = await read.get_thread(item_id)
        if not t:
            return _EMPTY_VIEW
        chapters = await read.list_chapters()
        known = chapter_number(t.last_chapter_id, chapters) is not None
        last = chapter_label(t.last_chapter_id, chapters) if known else "—"
        meta = f"{t.state.value} · touched {t.touch_count}x · last touch: {last}"
        return _view(t.name, meta, t.last_note)
    if section_key == "themes":
        theme = await read.get_theme(item_id)
        if not theme:
            return _EMPTY_VIEW
        return _view(theme.title, f"touched {theme.touch_count}x", theme.last_note)
    return _EMPTY_VIEW
