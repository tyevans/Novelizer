"""Pure Story Brain rendering: ReadStore records -> per-tab models of rich Text.

Same seam and testability as feed_model.py / browser_model.py — no Textual
imports, no I/O, unit-testable without a terminal. Alarm and state detection
are never re-derived here: staleness, sag/spike, paradoxes, and knowledge
cells all come from the existing brain/canon functions (single-sourcing).
Names, never ids: the only raw-id rendering is chapter_label's fallback for
a chapter id absent from list_chapters().
"""
from __future__ import annotations

from dataclasses import dataclass

from rich.text import Text

from novelizer.brain.sag_spike import SAG_SPIKE_DELTA, detect_sag_spike
from novelizer.brain.staleness import STALENESS_THRESHOLD_CHAPTERS, chapters_elapsed_since, is_thread_stale
from novelizer.canon.threads import TERMINAL_STATES
from novelizer.store.models import Chapter, StructureScore, ThreadRecord, ThreadState
from novelizer.tui.widgets.feed_model import ALARM_STYLE

DIM = "dim"

# One dim line each — the panel's designed quiet states. Secrets and
# Causeway are verbatim from the design spec; Shape and Threads match the
# same voice.
SHAPE_EMPTY = "No chapters scored yet — the story has no shape until the Analyst reads it."
THREADS_EMPTY = "No threads yet — nothing planted, nothing owed."
SECRETS_EMPTY = "No secrets yet. The room is still honest."
CAUSEWAY_EMPTY = "No causal edges yet — nothing has consequences until the Analyst says so."


def chapter_number(chapter_id: str, chapters: list[Chapter]) -> int | None:
    """1-based position of chapter_id in list_chapters() order; None if unknown."""
    for i, c in enumerate(chapters):
        if c.id == chapter_id:
            return i + 1
    return None


def chapter_label(chapter_id: str, chapters: list[Chapter]) -> str:
    """'ch N "Title"' from list_chapters() order — the raw id string only
    when the chapter id is unknown (the one permitted id on the dashboard)."""
    for i, c in enumerate(chapters):
        if c.id == chapter_id:
            return f'ch {i + 1} "{c.title}"'
    return chapter_id


@dataclass(frozen=True)
class ShapeTab:
    tensions: list[float]   # sparkline data, chapter order
    meta: Text              # axis + pacing line, or the dim empty state
    callouts: list[Text]    # one ALARM_STYLE line per sag/spike, chapter order
    alarm_count: int


def shape_tab(
    scores: list[StructureScore], chapters: list[Chapter], delta: float = SAG_SPIKE_DELTA
) -> ShapeTab:
    """The Shape tab: tension-by-chapter sparkline data plus sag/spike
    callouts naming chapter TITLES. Flags come from detect_sag_spike over the
    raw score list — never re-derived here. `delta` arrives from
    settings.sag_spike_delta via the app's _brain_loop (M5.3 single-sourcing);
    the default is the imported constant, never a re-typed literal."""
    if not scores:
        return ShapeTab([], Text(SHAPE_EMPTY, style=DIM), [], 0)
    by_chapter = {s.chapter_id: s for s in scores}  # last score per chapter wins
    chapter_ids = {c.id for c in chapters}
    ordered = [by_chapter[c.id] for c in chapters if c.id in by_chapter]
    ordered += [s for cid, s in by_chapter.items() if cid not in chapter_ids]
    tensions = [s.tension for s in ordered]
    flags = detect_sag_spike(scores, delta)
    callouts = [
        Text(f"⚠ {flags[s.chapter_id]}: {chapter_label(s.chapter_id, chapters)}", style=ALARM_STYLE)
        for s in ordered
        if s.chapter_id in flags
    ]
    axis = f"ch 1 ▸ ch {len(tensions)}" if len(tensions) > 1 else "ch 1"
    pacing = ordered[-1].pacing_label
    meta = Text(f"{axis} · pacing: {pacing}" if pacing else axis, style=DIM)
    return ShapeTab(tensions, meta, callouts, len(callouts))


def thread_line(
    thread: ThreadRecord, chapters: list[Chapter], threshold: int = STALENESS_THRESHOLD_CHAPTERS
) -> Text:
    """One Threads-tab row. Staleness comes from is_thread_stale /
    chapters_elapsed_since — never re-derived; `threshold` arrives from
    settings.staleness_threshold_chapters via the app's _brain_loop (M5.3
    single-sourcing). No slugs/ids anywhere."""
    if is_thread_stale(thread, chapters, threshold):
        elapsed = chapters_elapsed_since(thread.last_chapter_id, chapters)
        n = chapter_number(thread.last_chapter_id, chapters)
        if n is None:
            detail = f"stale — untouched for {elapsed} chapters"
        else:
            detail = f"stale — last touched ch {n}, {elapsed} chapters ago"
        return Text(f"⚠ {thread.name} · {detail}", style=ALARM_STYLE)
    if thread.state.value in TERMINAL_STATES:
        return Text(f"✓ {thread.name} · {thread.state.value}", style=DIM)
    return Text(f"· {thread.name} · {thread.state.value}")


@dataclass(frozen=True)
class ThreadsTab:
    lines: list[Text]
    alarm_count: int


def threads_tab(
    threads: list[ThreadRecord], chapters: list[Chapter], threshold: int = STALENESS_THRESHOLD_CHAPTERS
) -> ThreadsTab:
    """Threads grouped by state: stale pinned first (alarms), then live open
    threads, terminal threads folded to one dim count line."""
    if not threads:
        return ThreadsTab([Text(THREADS_EMPTY, style=DIM)], 0)
    stale = [t for t in threads if is_thread_stale(t, chapters, threshold)]
    stale_ids = {t.id for t in stale}
    live = [
        t for t in threads
        if t.id not in stale_ids and t.state.value not in TERMINAL_STATES
    ]
    terminal = [t for t in threads if t.state.value in TERMINAL_STATES]
    lines = [thread_line(t, chapters, threshold) for t in stale + live]
    if terminal:
        paid = sum(1 for t in terminal if t.state == ThreadState.paid_off)
        abandoned = len(terminal) - paid
        parts = ([f"{paid} paid off"] if paid else []) + (
            [f"{abandoned} abandoned"] if abandoned else []
        )
        lines.append(Text("✓ " + " · ".join(parts), style=DIM))
    return ThreadsTab(lines, len(stale))
