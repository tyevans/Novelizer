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

from novelizer.brain.arc_alignment import arc_findings
from novelizer.brain.beat_drift import beat_drifts
from novelizer.brain.ledger import due_promises, open_promises, overdue_promises
from novelizer.brain.paradoxes import find_paradoxes
from novelizer.brain.resolution_pacing import congested_windows, overdue_reveals, overdue_resolutions
from novelizer.brain.sag_spike import SAG_SPIKE_DELTA, detect_sag_spike
from novelizer.brain.staleness import STALENESS_THRESHOLD_CHAPTERS, chapters_elapsed_since, is_thread_stale
from novelizer.brain.tension_target import tension_deviations, target_curve
from novelizer.canon.beat_templates import beat_window
from novelizer.canon.promises import TERMINAL_PROMISE_STATES
from novelizer.canon.secrets import knowledge_cell_state
from novelizer.canon.threads import TERMINAL_STATES
from novelizer.store.models import (
    ArcRecord,
    BeatRecord,
    BlueprintRecord,
    CausalEdgeRecord,
    Chapter,
    Character,
    ChapterBriefRecord,
    BriefStatus,
    PromiseRecord,
    PromiseState,
    SecretRecord,
    StructureScore,
    ThreadRecord,
    ThreadState,
)
from novelizer.tui.widgets.feed_model import ALARM_STYLE

DIM = "dim"

WARN_STYLE = "yellow"
SUCCESS_STYLE = "bold green"

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
    target: Text | None = None  # "plan     ▂▅█" — target_curve overlay; None with no blueprint


def shape_tab(
    scores: list[StructureScore],
    chapters: list[Chapter],
    delta: float = SAG_SPIKE_DELTA,
    blueprint: BlueprintRecord | None = None,
    beats: list[BeatRecord] | None = None,
) -> ShapeTab:
    """The Shape tab: a rendered tension-by-chapter spark row (rich.text.Text,
    not raw sparkline data) plus sag/spike callouts naming chapter TITLES.
    Flags come from detect_sag_spike over the raw score list — never
    re-derived here. `delta` arrives from settings.sag_spike_delta via the
    app's _brain_loop (M5.3 single-sourcing); the default is the imported
    constant, never a re-typed literal.

    Note: the rendered `plan` row aligns beats by scored-chapter position
    (index into the spark), matching the spark's own existing approximation
    -- it is not a true chapter ordinal. Deviations/alarms below it use true
    ordinals from beat_window, so the two rows are not on the same axis."""
    if not scores:
        return ShapeTab([], None, None, Text(SHAPE_EMPTY, style=DIM), [], 0, None)
    by_chapter = {s.chapter_id: s for s in scores}  # last score per chapter wins
    chapter_ids = {c.id for c in chapters}
    ordered = [by_chapter[c.id] for c in chapters if c.id in by_chapter]
    ordered += [s for cid, s in by_chapter.items() if cid not in chapter_ids]
    tensions = [s.tension for s in ordered]
    flags = detect_sag_spike(scores, delta)
    # no_wrap + ellipsis: spark and markers are two aligned rows keyed by
    # chapter index. Rich's default word-wrap would wrap each row
    # independently on overflow, desyncing the ⚠ markers from their
    # chapters — crop instead.
    spark = Text(no_wrap=True, overflow="ellipsis")
    spark.append(SHAPE_GUTTER, style=DIM)  # only the gutter is dim; glyphs carry the signal
    for s in ordered:
        spark.append(spark_char(s.tension))
    markers: Text | None = None
    if any(s.chapter_id in flags for s in ordered):
        markers = Text(" " * len(SHAPE_GUTTER), no_wrap=True, overflow="ellipsis")
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

    target: Text | None = None
    if blueprint is not None:
        curve = target_curve(blueprint, beats or [])
        target_label = "plan".ljust(len(SHAPE_GUTTER))
        target = Text(no_wrap=True, overflow="ellipsis", style=DIM)
        target.append(target_label)
        # ordinals 1..len(ordered) — truncate the curve to the drafted
        # range; the curve's last value pads any shortfall via
        # tension_deviations, never re-derived here.
        for i in range(len(ordered)):
            value = curve[i] if i < len(curve) else (curve[-1] if curve else 0.0)
            target.append(spark_char(value))

        deviations = tension_deviations(blueprint, beats or [], scores, chapters, delta)
        callouts = callouts + [
            Text(f"⚠ tension off-plan: {chapter_label(cid, chapters)}", style=ALARM_STYLE)
            for cid, _actual, _target in deviations
        ]

    axis = f"ch 1 ▸ ch {len(tensions)}" if len(tensions) > 1 else "ch 1"
    pacing = ordered[-1].pacing_label
    meta = Text(f"{axis} · pacing: {pacing}" if pacing else axis, style=DIM)
    return ShapeTab(tensions, spark, markers, meta, callouts, len(callouts), target)


AGE_BAR_CELLS = 5
NAME_WIDTH = 20
WARN_FRACTION = 0.6  # bar warms to WARN_STYLE at this fraction of the staleness threshold


def age_bar(elapsed: int, threshold: int) -> Text:
    """Thread-age heat bar: fill and color scale with elapsed/threshold, so
    staleness is a visible gradient, not a binary flip. Clamped at full."""
    ratio = 1.0 if elapsed >= threshold else elapsed / threshold
    filled = round(ratio * AGE_BAR_CELLS)
    glyphs = "▰" * filled + "▱" * (AGE_BAR_CELLS - filled)
    if ratio >= 1.0:
        style = ALARM_STYLE
    elif ratio >= WARN_FRACTION:
        style = WARN_STYLE
    else:
        style = DIM
    return Text(glyphs, style=style)


def _window_badge(window_lo: int, window_hi: int, overdue: bool) -> tuple[str, str] | None:
    """The shared 'due chL-H' / 'OVERDUE chH' badge text+style for both
    thread rows and ledger rows. `overdue` is decided by the caller via the
    relevant brain/canon faculty (overdue_resolutions/overdue_promises) —
    never re-derived here."""
    if window_hi <= 0:
        return None
    if overdue:
        return f"OVERDUE ch{window_hi}", ALARM_STYLE
    return f"due ch{window_lo}-{window_hi}", DIM


def thread_line(
    thread: ThreadRecord, chapters: list[Chapter], threshold: int = STALENESS_THRESHOLD_CHAPTERS
) -> Text:
    """One Threads-tab row: state glyph, padded name, age heat bar, detail.
    Staleness comes from is_thread_stale / chapters_elapsed_since — never
    re-derived; `threshold` arrives from settings.staleness_threshold_chapters
    via the app's _brain_loop. No slugs/ids anywhere. A set window appends a
    'due chL-H' / 'OVERDUE chH' badge, overdue decided via
    resolution_pacing.overdue_resolutions (reused, never reimplemented)."""
    if thread.state.value in TERMINAL_STATES:
        return Text(f"✓ {thread.name} · {thread.state.value}", style=DIM)
    elapsed = chapters_elapsed_since(thread.last_chapter_id, chapters)
    n = chapter_number(thread.last_chapter_id, chapters)
    bar = age_bar(elapsed, threshold)
    name = _clip_title(thread.name, NAME_WIDTH).ljust(NAME_WIDTH)
    overdue = bool(overdue_resolutions([thread], chapters))
    badge = _window_badge(thread.window_lo, thread.window_hi, overdue)
    if is_thread_stale(thread, chapters, threshold):
        if n is None:
            detail = f"stale — untouched for {elapsed} chapters"
        else:
            detail = f"stale — last touched ch {n}, {elapsed} chapters ago"
        line = f"⚠ {name}  {bar.plain}  {detail}"
        if badge is not None:
            line += f" · {badge[0]}"
        return Text(line, style=ALARM_STYLE)
    detail = thread.state.value + (f" — ch {n}" if n is not None else "")
    row = Text(f"· {name}  ")
    row.append(bar.plain, style=bar.style)
    row.append(f"  {detail}")
    if badge is not None:
        row.append(" · ")
        row.append(badge[0], style=badge[1])
    return row


@dataclass(frozen=True)
class ThreadsTab:
    lines: list[Text]
    alarm_count: int


def ledger_line(promise: PromiseRecord, chapters: list[Chapter]) -> Text:
    """One Ledger row: glyph, name, kind tag for red herrings, window badge —
    same badge rules as thread_line, overdue decided via
    ledger.overdue_promises (reused, never reimplemented)."""
    overdue = bool(overdue_promises([promise], chapters))
    badge = _window_badge(promise.window_lo, promise.window_hi, overdue)
    text = f"◇ {promise.name}"
    if promise.kind == "red_herring":
        text += " (red herring)"
    if badge is None:
        return Text(text)
    if overdue:
        return Text(f"{text} · {badge[0]}", style=ALARM_STYLE)
    line = Text(f"{text} · ")
    line.append(badge[0], style=badge[1])
    return line


def threads_tab(
    threads: list[ThreadRecord],
    chapters: list[Chapter],
    promises: list[PromiseRecord] | None = None,
    secrets: list[SecretRecord] | None = None,
    threshold: int = STALENESS_THRESHOLD_CHAPTERS,
) -> ThreadsTab:
    """Threads grouped by state: stale pinned first (alarms), then live open
    threads, terminal threads folded to one dim count line. Followed by a
    Ledger section (open promises, overdue pinned first, paid/released
    folded) and congestion warnings — alarm arithmetic sums every source
    from Task 7's faculties, never re-derived here."""
    promises = promises or []
    secrets = secrets or []
    stale = [t for t in threads if is_thread_stale(t, chapters, threshold)]
    stale_ids = {t.id for t in stale}
    live = [
        t for t in threads
        if t.id not in stale_ids and t.state.value not in TERMINAL_STATES
    ]
    terminal = [t for t in threads if t.state.value in TERMINAL_STATES]

    lines: list[Text] = []
    if threads:
        lines += [thread_line(t, chapters, threshold) for t in stale + live]
        if terminal:
            paid = sum(1 for t in terminal if t.state == ThreadState.paid_off)
            abandoned = len(terminal) - paid
            parts = ([f"{paid} paid off"] if paid else []) + (
                [f"{abandoned} abandoned"] if abandoned else []
            )
            lines.append(Text("✓ " + " · ".join(parts), style=DIM))

    open_ = open_promises(promises)
    overdue_p = overdue_promises(promises, chapters)
    overdue_p_ids = {p.id for p in overdue_p}
    due_or_future = [p for p in open_ if p.id not in overdue_p_ids]
    terminal_promises = [p for p in promises if p.state.value in TERMINAL_PROMISE_STATES]
    if open_:
        lines.append(Text("Ledger", style=DIM))
        lines += [ledger_line(p, chapters) for p in overdue_p + due_or_future]
    if terminal_promises:
        paid = sum(1 for p in terminal_promises if p.state == PromiseState.paid)
        released = len(terminal_promises) - paid
        parts = ([f"{paid} paid"] if paid else []) + (
            [f"{released} released"] if released else []
        )
        lines.append(Text("✓ " + " · ".join(parts), style=DIM))

    spans = congested_windows(threads, secrets)
    overdue_resolved = overdue_resolutions(threads, chapters)
    overdue_revealed = overdue_reveals(secrets, chapters)

    if not threads and not promises and not spans and not overdue_revealed:
        return ThreadsTab([Text(THREADS_EMPTY, style=DIM)], 0)

    for secret in overdue_revealed:
        lines.append(
            Text(f"⚠ reveal overdue: '{secret.title}' (ch{secret.reveal_window_hi})", style=ALARM_STYLE)
        )
    for lo, hi, count in spans:
        lines.append(Text(f"⚠ {count} resolutions target ch{lo}-{hi}", style=WARN_STYLE))

    # stale + overdue resolutions/reveals/promises double-count on purpose:
    # a thread/secret can be both stale and overdue at once, and each is its
    # own kind of heat the alarm strip should reflect, not dedupe away.
    alarm_count = (
        len(stale) + len(overdue_resolved) + len(overdue_revealed) + len(overdue_p) + len(spans)
    )
    return ThreadsTab(lines, alarm_count)


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


def spread_meter(known: int, total: int) -> Text:
    """Per-secret spread meter: one ● per knower, ○ per still-dark character,
    'k/N'. Heats as spread approaches everyone — ALARM_STYLE when at most one
    character is left in the dark (the Keeper's leak-proximity signal, shared
    with the P3 Pulse card), WARN_STYLE once half the cast knows."""
    glyphs = "●" * known + "○" * (total - known)
    if known and total - known <= 1:
        style = ALARM_STYLE
    elif known and known / total >= 0.5:
        style = WARN_STYLE
    else:
        style = DIM
    return Text(f"{glyphs} {known}/{total}", style=style)


def secret_row(secret: SecretRecord, characters: list[Character], matrix: dict[str, dict]) -> Text:
    """One matrix row: clipped secret TITLE, one glyph cell per character
    (state from knowledge_cell_state — never re-derived), heat-colored spread
    meter. No ids anywhere."""
    row = Text(_clip_title(secret.title).ljust(TITLE_WIDTH))
    known = 0
    cells = []
    for c in characters:
        state = knowledge_cell_state(matrix, secret.id, c.id)
        known += state == "known"
        cells.append(CELL_GLYPHS[state].ljust(_COL))
    row.append(" ".join(cells).rstrip())
    if characters:
        meter = spread_meter(known, len(characters))
        row.append("   ")
        row.append(meter.plain, style=meter.style)
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
    unknown), sorted by chapter position with paradox edges pinned first;
    paradox edges — per find_paradoxes, never re-derived — in alarm color with
    '⚠ PARADOX'; normal edges render their '──▶' arrow in dim style."""
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
            (e.cause_chapter_id, e.effect_chapter_id) not in paradox_pairs,
            pos.get(e.cause_chapter_id, len(order)),
            pos.get(e.effect_chapter_id, len(order)),
        ),
    )
    for e in ordered:
        cause = chapter_label(e.cause_chapter_id, chapters)
        effect = chapter_label(e.effect_chapter_id, chapters)
        note = f": {e.note}" if e.note else ""
        if (e.cause_chapter_id, e.effect_chapter_id) in paradox_pairs:
            alarms += 1
            lines.append(
                Text(f"{cause} ──▶ {effect}{note}  ⚠ PARADOX", style=ALARM_STYLE)
            )
        else:
            line = Text(f"{cause} ")
            line.append("──▶", style=DIM)
            line.append(f" {effect}{note}")
            lines.append(line)
    return CausewayTab(lines, alarms)


OUTLINE_EMPTY = "No blueprint adopted — the Plotter will propose one."


@dataclass(frozen=True)
class OutlineTab:
    lines: list[Text]
    alarm_count: int


def _beat_line(beat: BeatRecord, blueprint: BlueprintRecord, drifts_by_id: dict[str, "object"]) -> tuple[Text, bool]:
    """One beat-strip row. Status glyph comes from beat_drifts (reused, never
    re-derived): '!' ALARM_STYLE for a late drift, '≈' for a fulfilled beat
    whose drift is early/off_window, '✓' for fulfilled with no drift entry
    (i.e. inside its window), '·' for pending. Returns (line, is_late) so the
    caller can sum alarm_count without re-scanning."""
    window_lo, window_hi = beat_window(beat.ideal_pct, beat.tolerance_pct, blueprint.target_chapter_count)
    drift = drifts_by_id.get(beat.id)
    is_late = drift is not None and drift.kind == "late"
    if is_late:
        glyph, style = "!", ALARM_STYLE
    elif beat.fulfilled_by_chapter_id and drift is not None:
        glyph, style = "≈", DIM
    elif beat.fulfilled_by_chapter_id:
        glyph, style = "✓", None
    else:
        glyph, style = "·", DIM
    text = f"{glyph} {beat.name} @ch {window_lo}-{window_hi}"
    return (Text(text, style=style) if style else Text(text)), is_late


def _outline_grid_row(thread: ThreadRecord, chapters: list[Chapter], max_col: int) -> Text:
    """One outline-grid row: NAME_WIDTH-padded name, then one cell per
    chapter ordinal 1..max_col. '●' marks the thread's last-touch column
    (last_chapter_id ordinal — the only per-thread column data the model
    carries); '░' fills a planned-resolution window span; '·' otherwise.
    Columns beyond the drafted chapters (the future) render DIM."""
    name = _clip_title(thread.name, NAME_WIDTH).ljust(NAME_WIDTH)
    row = Text(name)
    touched_col = chapter_number(thread.last_chapter_id, chapters) if thread.last_chapter_id else None
    drafted = len(chapters)
    for col in range(1, max_col + 1):
        future = col > drafted
        if col == touched_col:
            glyph = "●"
        elif thread.window_hi > 0 and thread.window_lo <= col <= thread.window_hi:
            glyph = "░"
        else:
            glyph = "·"
        row.append(" ")
        row.append(glyph, style=DIM if future else None)
    return row


def _brief_line(brief: ChapterBriefRecord, drafted: int) -> tuple[Text, bool]:
    """One briefs-strip row: 'ch N: goal', dim for a future chapter, '!'
    ALARM_STYLE when target_ordinal <= drafted (the brief should have been
    fulfilled/reaped by now but is still open — stale)."""
    stale = brief.target_ordinal <= drafted
    text = f"ch {brief.target_ordinal}: {brief.goal}"
    if stale:
        return Text(f"! {text}", style=ALARM_STYLE), True
    if brief.target_ordinal > drafted:
        return Text(text, style=DIM), False
    return Text(text), False


def outline_tab(
    blueprint: BlueprintRecord | None,
    beats: list[BeatRecord],
    briefs: list[ChapterBriefRecord],
    threads: list[ThreadRecord],
    chapters: list[Chapter],
) -> OutlineTab:
    """The Outline board: framework header, beat strip (glyphs from
    beat_drifts, reused not re-derived), a threads×chapters grid, and an
    open-briefs strip. The grid's per-thread column data is coarse —
    ThreadRecord only carries a last-touch chapter, not full per-chapter
    touch history — so per-chapter touch history is an M9+ refinement; this
    board shows current state, windows, and the future runway, not a replay
    of every touch."""
    if blueprint is None:
        return OutlineTab([Text(OUTLINE_EMPTY, style=DIM)], 0)

    header_text = f"{blueprint.framework} · ch {len(chapters)}/{blueprint.target_chapter_count}"
    if blueprint.genre:
        header_text += f" · {blueprint.genre}"
    if blueprint.completed:
        header_line = Text()
        header_line.append("✓ COMPLETE · ", style=SUCCESS_STYLE)
        header_line.append(header_text, style=DIM)
    else:
        header_line = Text(header_text, style=DIM)
    lines: list[Text] = [header_line]

    drifts_by_id = {d.beat_id: d for d in beat_drifts(blueprint, beats, chapters)}
    late_count = 0
    for beat in beats:
        line, is_late = _beat_line(beat, blueprint, drifts_by_id)
        lines.append(line)
        late_count += is_late

    open_threads = [t for t in threads if t.state.value not in TERMINAL_STATES]
    open_briefs = [b for b in briefs if b.status == BriefStatus.open]
    max_brief_ordinal = max((b.target_ordinal for b in open_briefs), default=0)
    max_col = max(len(chapters), max_brief_ordinal)
    if open_threads and max_col > 0:
        lines += [_outline_grid_row(t, chapters, max_col) for t in open_threads]

    stale_count = 0
    if open_briefs:
        for brief in sorted(open_briefs, key=lambda b: b.target_ordinal):
            line, stale = _brief_line(brief, len(chapters))
            lines.append(line)
            stale_count += stale

    return OutlineTab(lines, late_count + stale_count)


ARCS_EMPTY = "No arcs declared — the Character Keeper will find the cast's spines."


@dataclass(frozen=True)
class ArcsTab:
    lines: list[Text]
    alarm_count: int


def _arc_pivot_line(
    beat: BeatRecord, blueprint: BlueprintRecord, missed: bool,
) -> Text:
    """One pivot row: ' ◈ {beat name} @ch lo-hi {status}'. missed comes from
    arc_findings' pivot_missed findings — never re-derived here."""
    window_lo, window_hi = beat_window(beat.ideal_pct, beat.tolerance_pct, blueprint.target_chapter_count)
    if missed:
        glyph, style = "missed", ALARM_STYLE
    elif beat.fulfilled_by_chapter_id:
        glyph, style = "✓", None
    else:
        glyph, style = "pending", DIM
    text = f"  ◈ {beat.name} @ch {window_lo}-{window_hi} {glyph}"
    return Text(text, style=style) if style else Text(text)


def arcs_tab(
    arcs: list[ArcRecord],
    characters: list[Character],
    chapters: list[Chapter],
    beats: list[BeatRecord],
    blueprint: BlueprintRecord | None,
) -> ArcsTab:
    """One lane per active arc (header + dim detail + pivot rows), resolved
    arcs folded to a single line each (dim if consistent, full + ALARM if
    contradictory). Glyphs and alarm_count come entirely from arc_findings —
    never re-derived here."""
    if not arcs:
        return ArcsTab([Text(ARCS_EMPTY, style=DIM)], 0)

    names = {c.id: c.name for c in characters}
    beats_by_id = {b.id: b for b in beats}
    findings = arc_findings(arcs, characters, chapters, beats, blueprint)
    contradiction_arc_ids = {f.arc_id for f in findings if f.kind == "contradiction"}
    stagnant_arc_ids = {f.arc_id for f in findings if f.kind == "stagnant"}
    missed_beat_ids_by_arc: dict[str, set[str]] = {}
    for f in findings:
        if f.kind == "pivot_missed":
            missed_beat_ids_by_arc.setdefault(f.arc_id, set()).add(f.beat_id)

    lines: list[Text] = []
    for arc in arcs:
        name = names.get(arc.character_id, arc.character_id)
        if arc.resolved:
            if arc.id in contradiction_arc_ids:
                lines.append(Text(f"⚠ {name} · {arc.arc_type}", style=ALARM_STYLE))
            else:
                lines.append(Text(f"✓ {name} · {arc.arc_type}", style=DIM))
            continue

        if not arc.active:
            continue

        if arc.id in stagnant_arc_ids:
            glyph, style = "!", ALARM_STYLE
        else:
            glyph, style = "·", None
        header_text = f"{glyph} {name} · {arc.arc_type}"
        lines.append(Text(header_text, style=style) if style else Text(header_text))
        last = chapter_label(arc.last_chapter_id, chapters) if arc.last_chapter_id else "—"
        detail = f"lie '{arc.lie}' → truth '{arc.truth}' · advances {arc.advance_count} · last {last}"
        lines.append(Text(detail, style=DIM))
        if blueprint is not None:
            missed_beat_ids = missed_beat_ids_by_arc.get(arc.id, set())
            for pivot in arc.pivots:
                beat = beats_by_id.get(pivot.beat_id)
                if beat is None:
                    # Beat no longer exists in the current blueprint (e.g.
                    # superseded/re-outlined): the pivot citation is orphaned
                    # -- named by arc_findings' pivot_orphaned kind, never
                    # re-derived here. A pivot is orphaned XOR missed XOR
                    # fulfilled/pending -- never double-reported.
                    lines.append(Text(
                        f"  ◈ (beat {pivot.beat_id} superseded — re-pin)", style=DIM,
                    ))
                    continue
                missed = pivot.beat_id in missed_beat_ids
                lines.append(_arc_pivot_line(beat, blueprint, missed))

    return ArcsTab(lines, len(findings))


# A document count is never negative, so -1 marks "no count was passed at all"
# (call sites predating this readout) -- distinct from None, which means the
# count WAS asked for and could not be read.
DOCS_UNREPORTED = -1


def index_segment(docs: int | None = DOCS_UNREPORTED, lag: int = 0) -> Text:
    """The 'Index …' tail of the alarm strip: the semantic index's size, then
    its staleness. Three honest states for the size -- 'Index 1284' (dim: it
    holds documents), 'Index ⚠empty' (alarm: zero documents, so every
    search_canon answers a confident miss and agents read that as canon being
    empty), 'Index ?' (dim: unknown). Unknown is never drawn as empty:
    manufacturing a false alarm is the mirror of the bug this readout exists
    to expose.

    The size is what makes a DEAD index visible, because `lag` cannot: when
    embedding fails the drain abandons each aggregate and jumps the cursor
    past it, after which lag() reads 0 forever and actively reassures.

    Empty Text when there is nothing to say (no count passed AND the index is
    caught up), keeping the quiet strip unchanged."""
    if docs == DOCS_UNREPORTED and not lag:
        return Text()
    segment = Text("Index", style=DIM)
    if docs is None:
        segment.append(" ?", style=DIM)
    elif docs == 0:
        segment.append(" ⚠empty", style=ALARM_STYLE)
    elif docs > 0:
        segment.append(f" {docs}", style=DIM)
    if lag:
        segment.append(f" ⚠{lag} behind", style=ALARM_STYLE)
    return segment


def alarm_strip(
    shape: int, threads: int, secrets: int, cause: int, outline: int, arcs: int, lag: int = 0,
    docs: int | None = DOCS_UNREPORTED,
) -> Text:
    """The panel's persistent one-line summary of every tab's alarm state,
    so nothing is missed while another tab is open:
    'Shape ⚠1 · Threads ⚠2 · Secrets · Cause ⚠1 · Outline · Arcs'.

    `lag` is the canon embedding index's staleness (CanonIndexer.lag()) --
    how many indexable events haven't been embedded yet -- and `docs` its
    size (Runtime.index_document_count(); None for unknown). Neither is one
    of the six per-tab counts (there is no Index tab), so both render through
    index_segment as one trailing 'Index …' segment, and the quiet state
    ('Shape · Threads · ... · Arcs') is unchanged when nothing is passed --
    an embed-endpoint outage must never again be silently invisible in the UI."""
    strip = Text()
    for i, (label, count) in enumerate(
        [
            ("Shape", shape), ("Threads", threads), ("Secrets", secrets), ("Cause", cause),
            ("Outline", outline), ("Arcs", arcs),
        ]
    ):
        if i:
            strip.append(" · ", style=DIM)
        strip.append(label, style=DIM)
        if count:
            strip.append(f" ⚠{count}", style=ALARM_STYLE)
    segment = index_segment(docs, lag)
    if segment.plain:
        strip.append(" · ", style=DIM)
        strip.append_text(segment)
    return strip
