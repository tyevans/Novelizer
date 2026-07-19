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

from novelizer.brain.paradoxes import find_paradoxes
from novelizer.brain.sag_spike import SAG_SPIKE_DELTA, detect_sag_spike
from novelizer.brain.staleness import STALENESS_THRESHOLD_CHAPTERS, chapters_elapsed_since, is_thread_stale
from novelizer.canon.secrets import knowledge_cell_state
from novelizer.canon.threads import TERMINAL_STATES
from novelizer.store.models import CausalEdgeRecord, Chapter, Character, SecretRecord, StructureScore, ThreadRecord, ThreadState
from novelizer.tui.widgets.feed_model import ALARM_STYLE

DIM = "dim"

WARN_STYLE = "yellow"

SPARK_LEVELS = "▁▂▃▄▅▆▇█"
SHAPE_GUTTER = "tension  "  # the marker row indents by this much to align under the spark


def spark_char(tension: float) -> str:
    """One block-glyph cell for one chapter's tension, clamped to [0, 1]."""
    clamped = min(max(tension, 0.0), 1.0)
    return SPARK_LEVELS[min(int(clamped * len(SPARK_LEVELS)), len(SPARK_LEVELS) - 1)]


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
    tensions: list[float]   # chapter-order tension values (invariants/tests)
    spark: Text | None      # "tension  ▂▅█" — one cell per chapter; None when empty
    markers: Text | None    # "⚠" cells aligned under flagged chapters; None when no flags
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
        return ShapeTab([], None, None, Text(SHAPE_EMPTY, style=DIM), [], 0)
    by_chapter = {s.chapter_id: s for s in scores}  # last score per chapter wins
    chapter_ids = {c.id for c in chapters}
    ordered = [by_chapter[c.id] for c in chapters if c.id in by_chapter]
    ordered += [s for cid, s in by_chapter.items() if cid not in chapter_ids]
    tensions = [s.tension for s in ordered]
    flags = detect_sag_spike(scores, delta)
    spark = Text(SHAPE_GUTTER, style=DIM)
    for s in ordered:
        spark.append(spark_char(s.tension))
    markers: Text | None = None
    if any(s.chapter_id in flags for s in ordered):
        markers = Text(" " * len(SHAPE_GUTTER))
        for s in ordered:
            if s.chapter_id in flags:
                markers.append("⚠", style=ALARM_STYLE)
            else:
                markers.append(" ")
    callouts = [
        Text(f"⚠ {flags[s.chapter_id]}: {chapter_label(s.chapter_id, chapters)}", style=ALARM_STYLE)
        for s in ordered
        if s.chapter_id in flags
    ]
    axis = f"ch 1 ▸ ch {len(tensions)}" if len(tensions) > 1 else "ch 1"
    pacing = ordered[-1].pacing_label
    meta = Text(f"{axis} · pacing: {pacing}" if pacing else axis, style=DIM)
    return ShapeTab(tensions, spark, markers, meta, callouts, len(callouts))


AGE_BAR_CELLS = 5
NAME_WIDTH = 20
WARN_FRACTION = 0.6  # bar warms to WARN_STYLE at this fraction of the staleness threshold


def age_bar(elapsed: int, threshold: int) -> Text:
    """Thread-age heat bar: fill and color scale with elapsed/threshold, so
    staleness is a visible gradient, not a binary flip. Clamped at full."""
    ratio = min(elapsed / max(threshold, 1), 1.0)
    filled = round(ratio * AGE_BAR_CELLS)
    glyphs = "▰" * filled + "▱" * (AGE_BAR_CELLS - filled)
    if ratio >= 1.0:
        style = ALARM_STYLE
    elif ratio >= WARN_FRACTION:
        style = WARN_STYLE
    else:
        style = DIM
    return Text(glyphs, style=style)


def thread_line(
    thread: ThreadRecord, chapters: list[Chapter], threshold: int = STALENESS_THRESHOLD_CHAPTERS
) -> Text:
    """One Threads-tab row: state glyph, padded name, age heat bar, detail.
    Staleness comes from is_thread_stale / chapters_elapsed_since — never
    re-derived; `threshold` arrives from settings.staleness_threshold_chapters
    via the app's _brain_loop. No slugs/ids anywhere."""
    if thread.state.value in TERMINAL_STATES:
        return Text(f"✓ {thread.name} · {thread.state.value}", style=DIM)
    elapsed = chapters_elapsed_since(thread.last_chapter_id, chapters)
    n = chapter_number(thread.last_chapter_id, chapters)
    bar = age_bar(elapsed, threshold)
    name = _clip_title(thread.name, NAME_WIDTH).ljust(NAME_WIDTH)
    if is_thread_stale(thread, chapters, threshold):
        if n is None:
            detail = f"stale — untouched for {elapsed} chapters"
        else:
            detail = f"stale — last touched ch {n}, {elapsed} chapters ago"
        return Text(f"⚠ {name}  {bar.plain}  {detail}", style=ALARM_STYLE)
    detail = thread.state.value + (f" — ch {n}" if n is not None else "")
    row = Text(f"· {name}  ")
    row.append(bar.plain, style=bar.style)
    row.append(f"  {detail}")
    return row


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


# Glyphs cover knowledge_cell_state's exact codomain. "revealed" is
# secret-level state and never appears as a matrix cell in practice —
# revealed secrets fold to the "✓ revealed (N)" line — but the mapping is
# total so secret_row can render any state the canon function returns.
CELL_GLYPHS: dict[str, str] = {"known": "●", "unknown": "○", "revealed": "✓"}
TITLE_WIDTH = 24
_COL = 2  # every matrix column is 2 cells wide (initials cap at 2 chars)


def char_initials(name: str) -> str:
    """Column header: first letter of up to the first two words of the name
    ('Elara' -> 'E', 'The Boy' -> 'TB'); '?' for an empty name."""
    words = [w for w in name.split() if w]
    return "".join(w[0].upper() for w in words[:2]) or "?"


def _clip_title(title: str, width: int = TITLE_WIDTH) -> str:
    return title if len(title) <= width else title[: width - 1] + "…"


def matrix_header(characters: list[Character]) -> Text:
    """Dim column-header row: a TITLE_WIDTH gutter, then one 2-cell column
    of initials per character, in list_characters() order."""
    cells = " ".join(char_initials(c.name).ljust(_COL) for c in characters)
    return Text(" " * TITLE_WIDTH + cells.rstrip(), style=DIM)


def secret_row(secret: SecretRecord, characters: list[Character], matrix: dict[str, dict]) -> Text:
    """One matrix row: clipped secret TITLE, one glyph cell per character
    (state from knowledge_cell_state — never re-derived), dim who-knows
    summary. No ids anywhere."""
    row = Text(_clip_title(secret.title).ljust(TITLE_WIDTH))
    known = 0
    cells = []
    for c in characters:
        state = knowledge_cell_state(matrix, secret.id, c.id)
        known += state == "known"
        cells.append(CELL_GLYPHS[state].ljust(_COL))
    row.append(" ".join(cells).rstrip())
    if known == 0:
        summary = "no one knows"
    elif known == 1:
        summary = "1 knows"
    else:
        summary = f"{known} know"
    row.append(f"   {summary}", style=DIM)
    return row


@dataclass(frozen=True)
class SecretsTab:
    lines: list[Text]
    alarm_count: int  # always 0 — leak alarms live in the feed/retcon queue


def secrets_tab(
    secrets: list[SecretRecord], characters: list[Character], matrix: dict[str, dict]
) -> SecretsTab:
    """The knowledge matrix: header of character initials, one row per
    unrevealed secret, revealed secrets folded to one dim count line."""
    if not secrets:
        return SecretsTab([Text(SECRETS_EMPTY, style=DIM)], 0)
    unrevealed = [s for s in secrets if not s.revealed]
    revealed = len(secrets) - len(unrevealed)
    lines: list[Text] = []
    if unrevealed and characters:
        lines.append(matrix_header(characters))
    lines += [secret_row(s, characters, matrix) for s in unrevealed]
    if revealed:
        lines.append(Text(f"✓ revealed ({revealed})", style=DIM))
    return SecretsTab(lines, 0)


@dataclass(frozen=True)
class CausewayTab:
    lines: list[Text]
    alarm_count: int


def causeway_tab(edges: list[CausalEdgeRecord], chapters: list[Chapter]) -> CausewayTab:
    """Causal edges with chapter TITLES (raw id only when a chapter id is
    unknown), sorted by chapter position; paradox edges — per find_paradoxes,
    never re-derived — in alarm color with '⚠ PARADOX'."""
    if not edges:
        return CausewayTab([Text(CAUSEWAY_EMPTY, style=DIM)], 0)
    order = [c.id for c in chapters]
    pos = {cid: i for i, cid in enumerate(order)}
    paradox_pairs = {
        (p.cause_chapter_id, p.effect_chapter_id) for p in find_paradoxes(edges, order)
    }
    lines: list[Text] = []
    alarms = 0
    ordered = sorted(
        edges,
        key=lambda e: (
            pos.get(e.cause_chapter_id, len(order)),
            pos.get(e.effect_chapter_id, len(order)),
        ),
    )
    for e in ordered:
        body = f"{chapter_label(e.cause_chapter_id, chapters)} ──▶ {chapter_label(e.effect_chapter_id, chapters)}"
        if e.note:
            body += f": {e.note}"
        if (e.cause_chapter_id, e.effect_chapter_id) in paradox_pairs:
            alarms += 1
            lines.append(Text(f"{body}  ⚠ PARADOX", style=ALARM_STYLE))
        else:
            lines.append(Text(body))
    return CausewayTab(lines, alarms)


def alarm_strip(shape: int, threads: int, secrets: int, cause: int) -> Text:
    """The panel's persistent one-line summary of every tab's alarm state,
    so nothing is missed while another tab is open:
    'Shape ⚠1 · Threads ⚠2 · Secrets · Cause ⚠1'."""
    strip = Text()
    for i, (label, count) in enumerate(
        [("Shape", shape), ("Threads", threads), ("Secrets", secrets), ("Cause", cause)]
    ):
        if i:
            strip.append(" · ", style=DIM)
        strip.append(label, style=DIM)
        if count:
            strip.append(f" ⚠{count}", style=ALARM_STYLE)
    return strip
