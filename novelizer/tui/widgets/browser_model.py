"""Pure browser rendering: ReadStore records -> tree section dicts and the
detail pane's DetailView. Same seam as the other *_model.py modules — the
async functions here do read-only ReadStore calls plus pure string/Text
construction; no Textual imports.

State cues (spec Zone 4): chapter rows carry an editorial-status dot,
the Flags label carries ⚠ when any STALE flag exists (Triage's catch-all
gave up on an unowned-category flag and it needs a human), not merely
when open items exist -- an open flag is expected to be actively worked
by its owner agent or by Triage, and the count next to the label is a
normal, non-alarming queue depth. The Threads section carries the stale
count. Staleness is never re-derived: is_thread_stale +
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
    open_flags = await read.list_flags(status="open")
    stale_flags = await read.list_flags(status="stale")
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
    flags_label = f"Flags ({len(open_flags)}) ⚠" if stale_flags else f"Flags ({len(open_flags)})"
    blueprint = await read.get_active_blueprint()
    outline_section = None
    if blueprint is not None:
        beats = await read.list_beats()
        open_briefs = await read.list_briefs("open")
        items = [{"id": "blueprint",
                  "label": f"Blueprint · {blueprint.framework} · {len(chapters)}/{blueprint.target_chapter_count}"}]
        for b in beats:
            mark = "✓" if b.fulfilled_by_chapter_id else "·"
            items.append({"id": f"beat:{b.id}", "label": f"{mark} {b.name}"})
        for br in open_briefs:
            items.append({"id": f"brief:{br.id}", "label": f"ch{br.target_ordinal}: {br.goal[:32]}"})
        outline_section = {"key": "outline", "label": f"Outline ({len(beats)} beats)", "items": items}
    sections = [
        {"key": "chapters", "label": f"Chapters ({len(chapters)})",
         "items": [{"id": c.id, "label": f"{_status_dot(c.editorial_status)} {c.title}"} for c in chapters]},
        {"key": "characters", "label": f"Characters ({len(characters)})",
         "items": [{"id": c.id, "label": c.name} for c in characters]},
        {"key": "world", "label": f"World ({len(world)})",
         "items": [{"id": e.id, "label": f"[{_enum_val(e.domain)}] {e.title}"} for e in world]},
        {"key": "flags", "label": flags_label,
         "items": [{"id": f.id, "label": f"[{f.category}] {f.description[:32]}"} for f in open_flags]},
        {"key": "threads", "label": threads_label,
         "items": [{"id": t.id, "label": _thread_row_label(t, chapters, staleness_threshold)} for t in threads]},
        {"key": "themes", "label": f"Themes ({len(themes)})",
         "items": [{"id": t.id, "label": t.title} for t in themes]},
    ]
    return ([outline_section] + sections) if outline_section else sections


@dataclass(frozen=True)
class DetailView:
    title: str   # plain; the app uppercases it into #detail_scroll's border title
    body: Text


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


async def detail_view(read, section_key: str, item_id: str) -> DetailView | None:
    """Render the detail pane for one record; None when the record (or the
    section itself) is not found — distinct from a found record whose title
    happens to be empty."""
    if section_key == "chapters":
        ch = await read.get_chapter(item_id)
        if not ch:
            return None
        meta = f"{_enum_val(ch.editorial_status)} · {word_count(ch.prose):,} words"
        return _view(ch.title, meta, ch.prose)
    if section_key == "characters":
        c = await read.get_character(item_id)
        if not c:
            return None
        fields = [("Traits", c.traits), ("Arc", c.arc_status), ("Motivations", c.motivations)]
        if c.voice:
            fields.append(("Voice", c.voice))
        return _view(c.name, "", c.backstory, fields)
    if section_key == "world":
        for e in await read.list_world_entries():
            if e.id == item_id:
                return _view(e.title, _enum_val(e.domain), e.body)
        return None
    if section_key == "flags":
        for f in await read.list_flags():
            if f.id == item_id:
                return _view(f.description, f"status: {_enum_val(f.status)}  category: {f.category}", "",
                             [("Proposed", f.proposed_resolution)])
        return None
    if section_key == "threads":
        t = await read.get_thread(item_id)
        if not t:
            return None
        chapters = await read.list_chapters()
        known = chapter_number(t.last_chapter_id, chapters) is not None
        last = chapter_label(t.last_chapter_id, chapters) if known else "—"
        meta = f"{t.state.value} · touched {t.touch_count}x · last touch: {last}"
        return _view(t.name, meta, t.last_note)
    if section_key == "themes":
        theme = await read.get_theme(item_id)
        if not theme:
            return None
        return _view(theme.title, f"touched {theme.touch_count}x", theme.last_note)
    if section_key == "outline":
        blueprint = await read.get_active_blueprint()
        if blueprint is None:
            return None
        if item_id == "blueprint":
            meta = f"{blueprint.framework} · target {blueprint.target_chapter_count} ch · {blueprint.genre}"
            return _view("Blueprint", meta)
        if item_id.startswith("beat:"):
            beat_id = item_id.split(":", 1)[1]
            beat = next((b for b in await read.list_beats() if b.id == beat_id), None)
            if beat is None:
                return None
            fulfilled = beat.fulfilled_by_chapter_id or "—"
            meta = f"ideal {beat.ideal_pct:.0%} ±{beat.tolerance_pct:.0%} · {beat.expected_polarity}"
            return _view(beat.name, meta, "", [("Fulfilled by", fulfilled)])
        if item_id.startswith("brief:"):
            brief_id = item_id.split(":", 1)[1]
            brief = next((b for b in await read.list_briefs() if b.id == brief_id), None)
            if brief is None:
                return None
            meta = f"ch {brief.target_ordinal} · {brief.status}"
            fields = [("Goal", brief.goal), ("Value shift", brief.value_shift),
                      ("Planned outcome", brief.planned_outcome)]
            return _view(f"Brief · ch{brief.target_ordinal}", meta, brief.synopsis, fields)
        return None
    return None
