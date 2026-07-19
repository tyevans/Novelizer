# Mission Control Phase 2 — Story Brain Panel Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

> **NEVER create a `.env` file at any point in this plan.** Any scratch/temp file goes in
> the job's tmp dir, never the repo. No live-LLM tests are involved in this plan — every
> test here is pure-function or pilot-harness with stub runners.

**Goal:** Replace the four stacked brain panes (`#thread_board`, `#story_shape`, `#who_knows_what`, `#causeway`) with **one tabbed Story Brain panel** (Textual `TabbedContent`, keys 1–4, border title `STORY BRAIN`): a real tension sparkline with titled sag/spike callouts, threads grouped by state with stale pinned on top, the knowledge matrix as an actual glyph matrix, a titled causeway with paradox alarms, a persistent one-line alarm summary strip, and designed one-line empty states. Names, never ids.

**Architecture:** All new rendering logic is pure functions in **new** `novelizer/tui/widgets/brain_model.py` (same seam as `feed_model.py` / `browser_model.py`: records in → dataclasses of `rich.text.Text` out, no Textual imports, no I/O, unit-testable without a terminal). A thin `BrainPanel` widget (**new** `novelizer/tui/widgets/brain_panel.py`) composes `TabbedContent`/`TabPane`/`Sparkline`/`Static` and consumes the models. The four old widget files and their four polling loops are **deleted**; one `_brain_loop` in `app.py` polls `ReadStore` once per second and updates all four tabs plus the strip. Alarm/state detection is **never re-derived**: staleness, sag/spike, paradoxes, and knowledge cells come from the existing brain/canon functions. No new events, projections, or read-model changes. Rendering only — **no row selection/targeting inside tabs** (that is Phase 3); proposals/roster/statusbar/detail are untouched.

**Tech Stack:** Python 3.13, uv, Textual 5.3.0 (`TabbedContent`, `TabPane`, `Sparkline`, `Static`, `Vertical`), `rich.text.Text`, pytest + pytest-asyncio pilot harness, Hypothesis for property tests.

## Global Constraints

- **Single-sourcing of brain logic (non-negotiable):** staleness only via `novelizer/brain/staleness.py::is_thread_stale` and `chapters_elapsed_since` (threshold `STALENESS_THRESHOLD_CHAPTERS` stays theirs); sag/spike only via `novelizer/brain/sag_spike.py::detect_sag_spike` (`SAG_SPIKE_DELTA` stays its); paradoxes only via `novelizer/brain/paradoxes.py::find_paradoxes`; per-cell knowledge state only via `novelizer/canon/secrets.py::knowledge_cell_state`; terminal thread states only via `novelizer/canon/threads.py::TERMINAL_STATES`. `brain_model.py` imports these — it never re-types a threshold or re-implements a rule.
- **Real cell states** (read from `knowledge_cell_state`): exactly `"unknown" | "known" | "revealed"`. There is **no** "suspected" state in the data (the spec's `◍` sketch has no backing state and is dropped). Glyph mapping, total over the codomain: `CELL_GLYPHS = {"known": "●", "unknown": "○", "revealed": "✓"}`. Revealed is secret-level, so `✓` never appears as a matrix cell — revealed secrets fold to the one dim line `✓ revealed (N)`.
- **No ids on the dashboard.** Chapter/thread/secret/character *titles and names* only. Chapter number = 1-based position in `ReadStore.list_chapters()` order. The **only** permitted raw-id rendering is the causeway/callout fallback when a chapter id is not in `list_chapters()` (`chapter_label` returns the raw id string then).
- **Alarm color** is `ALARM_STYLE` imported from `novelizer/tui/widgets/feed_model.py` (`"bold red"`) — never re-typed.
- **Exact callout format (Shape):** `⚠ sag: ch 5 "The Long Calm"` / `⚠ spike: ch 3 "The Break"`.
- **Exact stale format (Threads):** `⚠ {name} · stale — last touched ch {N}, {M} chapters ago`; when the thread's `last_chapter_id` is unknown/empty: `⚠ {name} · stale — untouched for {M} chapters`. `M` is `chapters_elapsed_since(...)`, `N` is the 1-based chapter position. Live rows: `· {name} · {state}`; terminal threads fold to one dim line `✓ {p} paid off · {a} abandoned` (zero parts omitted).
- **Exact causeway format:** `ch 2 "The Gift" ──▶ ch 5 "The Price": note` (no `: note` suffix when the note is empty); paradox edges rendered in `ALARM_STYLE` with suffix `  ⚠ PARADOX`.
- **Exact alarm-strip format:** labels `Shape`, `Threads`, `Secrets`, `Cause` joined by ` · `; a label with alarms gains ` ⚠{n}` in `ALARM_STYLE`. Examples: `Shape ⚠1 · Threads ⚠2 · Secrets · Cause ⚠1`; quiet: `Shape · Threads · Secrets · Cause`. Alarm counts: Shape = sag/spike callout count, Threads = stale count, Secrets = always 0 (leaks surface as retcon alarms in the feed, not here), Cause = paradox-edge count.
- **Empty states, one dim line each, verbatim:**
  - Shape: `No chapters scored yet — the story has no shape until the Analyst reads it.`
  - Threads: `No threads yet — nothing planted, nothing owed.`
  - Secrets: `No secrets yet. The room is still honest.` (spec-given)
  - Causeway: `No causal edges yet — nothing has consequences until the Analyst says so.` (spec-given)
- **Tabs and keys:** pane ids `tab_shape` / `tab_threads` / `tab_secrets` / `tab_causeway`; tab titles `1 Shape` / `2 Threads` / `3 Secrets` / `4 Cause`; app-level `BINDINGS` for keys `1`–`4` call `action_brain_tab(pane_id)`. Panel id `#brain`, `border_title = "STORY BRAIN"`. Keys only fire when no focused widget consumes them — typing digits in the `#command` Input must keep working (Input consumes printable keys; no `priority` bindings).
- **Old surface removed in the same task that replaces it:** the four widget files, their four `on_mount` workers/loops, their four `compose()` blocks, their four tcss id rules, their four `tests/tui/test_*.py` files, and every cross-reference (`tests/tui/test_app_layout.py`, `tests/agents/test_author.py`, `tests/agents/test_continuity_checker.py`) — all in Task 5, never left dangling between tasks.
- **`#body.room` / `#body.reading` CSS is not modified.** `#brain` lives inside `#left`, so reading mode ('v') hides it via the existing `#body.reading #left { display: none; }` rule and room mode ('r') keeps it; Task 5 pilot-tests both toggles against the new panel.
- **`app.messages` untouched** — the brain panel never writes to the feed; worker errors from `_brain_loop` go through the existing `_report_worker_error("brain", e)` path.
- Full suite must pass with `uv run pytest -q`, **zero warnings**, before the final commit.

## Existing-test inventory (what breaks, where it is fixed)

| Test | References | Fixed in |
|---|---|---|
| `tests/tui/test_thread_board.py` (3 tests on `thread_board_line`) | deleted widget | Task 5: file deleted; behavior covered by Task 2's `thread_line` tests |
| `tests/tui/test_story_shape.py` (3 tests on `story_shape_line`) | deleted widget | Task 5: file deleted; behavior covered by Task 1's `shape_tab` tests |
| `tests/tui/test_who_knows_what.py` (3 tests on `who_knows_what_line`) | deleted widget | Task 5: file deleted; behavior covered by Task 3's `secret_row`/`secrets_tab` tests |
| `tests/tui/test_causeway.py` (2 tests on `causeway_line`) | deleted widget | Task 5: file deleted; behavior covered by Task 4's `causeway_tab` tests |
| `tests/tui/test_app_layout.py::test_mission_control_shows_thread_board_and_story_shape_panes` | `#thread_board`, `#story_shape` | Task 5: rewritten as `test_story_brain_threads_and_shape_tabs_populate` |
| `tests/tui/test_app_layout.py::test_mission_control_shows_who_knows_what_and_causeway_panes` | `#who_knows_what`, `#causeway` | Task 5: rewritten as `test_story_brain_secrets_matrix_and_causeway_tabs_populate` |
| `tests/tui/test_app_layout.py::test_every_pane_has_its_border_title` | four pane titles | Task 5: four entries replaced with `"#brain": "STORY BRAIN"` |
| `tests/agents/test_author.py::test_m3_done_when_...` (lines ~263–303) | imports `thread_board_line` | Task 5: switched to `brain_model.thread_line(...).plain`, asserts `"stale"` |
| `tests/agents/test_continuity_checker.py::test_m4_3_done_when_...` (lines ~195–250) | imports `who_knows_what_line` | Task 5: switched to `brain_model.secret_row(...).plain`, asserts `"no one knows"` / no `●` |
| `tests/tui/test_reading_mode.py`, `test_app_commands.py` room/reading toggles | `#body`/`#left` only | unaffected (CSS rules unchanged); Task 5 adds a brain-specific toggle test |
| `novelizer/brain/context.py`, `novelizer/brain/paradoxes.py` docstrings naming the old widgets | prose only, no imports | not touched (no code dependency) |

---

### Task 1: `brain_model.py` — chapter helpers + Shape tab model

**Files:**
- Create: `novelizer/tui/widgets/brain_model.py`
- Test: `tests/tui/test_brain_model.py` (new)

**Interfaces:**
- Consumes: `detect_sag_spike` (`novelizer.brain.sag_spike`), `ALARM_STYLE` (`novelizer.tui.widgets.feed_model`), `Chapter`, `StructureScore` (`novelizer.store.models`), `rich.text.Text`.
- Produces: `DIM = "dim"`, the four `*_EMPTY` constants (all defined here once; Tasks 2–4 test theirs), `chapter_number(chapter_id: str, chapters: list[Chapter]) -> int | None`, `chapter_label(chapter_id: str, chapters: list[Chapter]) -> str`, `ShapeTab` (frozen dataclass: `tensions: list[float]`, `meta: Text`, `callouts: list[Text]`, `alarm_count: int`), `shape_tab(scores: list[StructureScore], chapters: list[Chapter]) -> ShapeTab`. Tasks 2–5 consume exactly these names.

Design decisions locked here:
- **Sparkline order:** tensions follow chapter position, not score-append order. One score per chapter (the projection keys `structure_scores` by chapter id); if the list ever carries duplicates for a chapter, the last one wins. A score whose `chapter_id` is not a known chapter (shouldn't occur; defensive) keeps its data, appended after the ordered points in first-seen order — data is never dropped.
- **Meta line:** `ch 1 ▸ ch {n}` axis (just `ch 1` when a single point) plus ` · pacing: {label}` from the **last chapter-ordered** score, omitted when that label is empty. Dim style.
- **Callouts:** one `ALARM_STYLE` line per flagged chapter from `detect_sag_spike(scores)` (called with the raw score list — single-sourcing), in chapter order, format `⚠ {sag|spike}: {chapter_label(...)}`. `alarm_count = len(callouts)`.

- [ ] **Step 1: Write the failing test**

```python
# tests/tui/test_brain_model.py
from hypothesis import given, strategies as st

from novelizer.store.models import Chapter, StructureScore
from novelizer.tui.widgets.brain_model import (
    SHAPE_EMPTY,
    chapter_label,
    chapter_number,
    shape_tab,
)
from novelizer.tui.widgets.feed_model import ALARM_STYLE


def _chapters(*titles: str) -> list[Chapter]:
    return [Chapter(id=f"c{i + 1}", title=t, prose="p") for i, t in enumerate(titles)]


def test_chapter_number_is_one_based_position_in_chapter_order():
    chs = _chapters("One", "Two", "Three")
    assert chapter_number("c1", chs) == 1
    assert chapter_number("c3", chs) == 3
    assert chapter_number("ghost", chs) is None


def test_chapter_label_uses_number_and_title_never_the_id():
    chs = _chapters("One", "The Long Calm")
    assert chapter_label("c2", chs) == 'ch 2 "The Long Calm"'


def test_chapter_label_falls_back_to_raw_id_only_when_unknown():
    assert chapter_label("ghost", _chapters("One")) == "ghost"


def test_shape_tab_empty_is_one_dim_line_no_data():
    tab = shape_tab([], _chapters("One"))
    assert tab.tensions == []
    assert tab.meta.plain == SHAPE_EMPTY
    assert str(tab.meta.style) == "dim"
    assert tab.callouts == [] and tab.alarm_count == 0


def test_shape_tab_single_score_axis_and_pacing():
    tab = shape_tab(
        [StructureScore(chapter_id="c1", tension=0.6, pacing_label="rising")],
        _chapters("One"),
    )
    assert tab.tensions == [0.6]
    assert tab.meta.plain == "ch 1 · pacing: rising"
    assert tab.callouts == [] and tab.alarm_count == 0


def test_shape_tab_orders_tensions_by_chapter_position_not_score_order():
    chs = _chapters("One", "Two", "Three")
    scores = [
        StructureScore(chapter_id="c3", tension=0.9, pacing_label="climax"),
        StructureScore(chapter_id="c1", tension=0.2, pacing_label="calm"),
        StructureScore(chapter_id="c2", tension=0.5, pacing_label="rising"),
    ]
    tab = shape_tab(scores, chs)
    assert tab.tensions == [0.2, 0.5, 0.9]
    assert tab.meta.plain == "ch 1 ▸ ch 3 · pacing: climax"


def test_shape_tab_sag_callout_names_chapter_title_in_alarm_style():
    chs = _chapters("One", "Two", "The Long Calm")
    scores = [
        StructureScore(chapter_id=f"c{i + 1}", tension=t, pacing_label="")
        for i, t in enumerate([0.6, 0.6, 0.1])
    ]
    tab = shape_tab(scores, chs)
    assert len(tab.callouts) == 1 and tab.alarm_count == 1
    assert tab.callouts[0].plain == '⚠ sag: ch 3 "The Long Calm"'
    assert str(tab.callouts[0].style) == ALARM_STYLE


def test_shape_tab_spike_callout():
    chs = _chapters("One", "Two", "The Break")
    scores = [
        StructureScore(chapter_id=f"c{i + 1}", tension=t, pacing_label="")
        for i, t in enumerate([0.2, 0.2, 0.9])
    ]
    tab = shape_tab(scores, chs)
    assert tab.callouts[0].plain == '⚠ spike: ch 3 "The Break"'


def test_shape_tab_score_for_unknown_chapter_keeps_its_data_at_the_end():
    chs = _chapters("One")
    scores = [
        StructureScore(chapter_id="ghost", tension=0.9, pacing_label=""),
        StructureScore(chapter_id="c1", tension=0.2, pacing_label="calm"),
    ]
    tab = shape_tab(scores, chs)
    assert tab.tensions == [0.2, 0.9]


@given(st.lists(st.floats(min_value=0.0, max_value=1.0), min_size=1, max_size=12))
def test_shape_tab_keeps_every_point_and_alarm_count_matches_callouts(tensions):
    chs = [Chapter(id=f"c{i}", title=f"T{i}", prose="p") for i in range(len(tensions))]
    scores = [
        StructureScore(chapter_id=f"c{i}", tension=t, pacing_label="")
        for i, t in enumerate(tensions)
    ]
    tab = shape_tab(scores, chs)
    assert len(tab.tensions) == len(tensions)
    assert tab.alarm_count == len(tab.callouts)
```

- [ ] **Step 2: Run test to verify it fails**

```
uv run pytest tests/tui/test_brain_model.py -v
```
Expected: `ModuleNotFoundError: No module named 'novelizer.tui.widgets.brain_model'` (collection error).

- [ ] **Step 3: Write minimal implementation**

```python
# novelizer/tui/widgets/brain_model.py
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

from novelizer.brain.sag_spike import detect_sag_spike
from novelizer.store.models import Chapter, StructureScore
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


def shape_tab(scores: list[StructureScore], chapters: list[Chapter]) -> ShapeTab:
    """The Shape tab: tension-by-chapter sparkline data plus sag/spike
    callouts naming chapter TITLES. Flags come from detect_sag_spike over the
    raw score list — never re-derived here."""
    if not scores:
        return ShapeTab([], Text(SHAPE_EMPTY, style=DIM), [], 0)
    by_chapter = {s.chapter_id: s for s in scores}  # last score per chapter wins
    chapter_ids = {c.id for c in chapters}
    ordered = [by_chapter[c.id] for c in chapters if c.id in by_chapter]
    ordered += [s for cid, s in by_chapter.items() if cid not in chapter_ids]
    tensions = [s.tension for s in ordered]
    flags = detect_sag_spike(scores)
    callouts = [
        Text(f"⚠ {flags[s.chapter_id]}: {chapter_label(s.chapter_id, chapters)}", style=ALARM_STYLE)
        for s in ordered
        if s.chapter_id in flags
    ]
    axis = f"ch 1 ▸ ch {len(tensions)}" if len(tensions) > 1 else "ch 1"
    pacing = ordered[-1].pacing_label
    meta = Text(f"{axis} · pacing: {pacing}" if pacing else axis, style=DIM)
    return ShapeTab(tensions, meta, callouts, len(callouts))
```

- [ ] **Step 4: Run test to verify it passes**

```
uv run pytest tests/tui/test_brain_model.py -v
```
Expected: 9 passed (the Hypothesis test counts as one).

- [ ] **Step 5: Commit**

```
git add novelizer/tui/widgets/brain_model.py tests/tui/test_brain_model.py
git commit -m "feat: brain_model — chapter-title helpers + Shape tab model (sparkline data, titled sag/spike callouts)"
```

---

### Task 2: Threads tab model (`brain_model.py`, part 2)

**Files:**
- Modify: `novelizer/tui/widgets/brain_model.py` (append)
- Test: `tests/tui/test_brain_model.py` (append)

**Interfaces:**
- Consumes: `is_thread_stale`, `chapters_elapsed_since` (`novelizer.brain.staleness`); `TERMINAL_STATES` (`novelizer.canon.threads`); `ThreadRecord`, `ThreadState` (`novelizer.store.models`); Task 1's `chapter_number`, `THREADS_EMPTY`, `DIM`, `ALARM_STYLE`.
- Produces: `thread_line(thread: ThreadRecord, chapters: list[Chapter]) -> Text`, `ThreadsTab` (frozen dataclass: `lines: list[Text]`, `alarm_count: int`), `threads_tab(threads: list[ThreadRecord], chapters: list[Chapter]) -> ThreadsTab`. Task 5 consumes `threads_tab`; the updated `tests/agents/test_author.py` consumes `thread_line` directly (the same seam its docstring already describes).

Design decisions locked here:
- ThreadRecord state vocabulary (from `novelizer/store/models.py::ThreadState`): `planted | touched | paid_off | abandoned`; "open" = not in `TERMINAL_STATES` (`{"paid_off", "abandoned"}`).
- Grouping: stale first (each an `ALARM_STYLE` line), then live open threads in projection order, then one dim fold line for terminal threads (only when any exist). `alarm_count` = stale count.
- `thread_line` also defines a dim `✓ {name} · {state}` rendering for a terminal thread so the helper is total, even though `threads_tab` folds terminals to a count instead of rendering them per-row.

- [ ] **Step 1: Write the failing test** (append to `tests/tui/test_brain_model.py`)

```python
from novelizer.store.models import ThreadRecord, ThreadState
from novelizer.tui.widgets.brain_model import THREADS_EMPTY, thread_line, threads_tab


def test_thread_line_stale_names_last_touched_chapter_and_gap():
    chs = _chapters("One", "Two", "Three", "Four", "Five")
    t = ThreadRecord(id="the-locket", name="The Locket", state=ThreadState.planted, last_chapter_id="c1")
    line = thread_line(t, chs)
    assert line.plain == "⚠ The Locket · stale — last touched ch 1, 4 chapters ago"
    assert str(line.style) == ALARM_STYLE
    assert "the-locket" not in line.plain


def test_thread_line_stale_with_no_known_chapter_reads_untouched():
    chs = _chapters("One", "Two", "Three")
    t = ThreadRecord(id="t", name="The Boy's Gift", state=ThreadState.planted, last_chapter_id="")
    assert thread_line(t, chs).plain == "⚠ The Boy's Gift · stale — untouched for 3 chapters"


def test_thread_line_live_shows_name_and_state_no_id():
    chs = _chapters("One")
    t = ThreadRecord(id="t", name="Fresh", state=ThreadState.touched, last_chapter_id="c1")
    assert thread_line(t, chs).plain == "· Fresh · touched"


def test_thread_line_terminal_is_dim_and_never_stale():
    chs = _chapters("One", "Two", "Three", "Four", "Five")
    t = ThreadRecord(id="t", name="Closed", state=ThreadState.paid_off, last_chapter_id="c1")
    line = thread_line(t, chs)
    assert line.plain == "✓ Closed · paid_off"
    assert "stale" not in line.plain
    assert str(line.style) == "dim"


def test_threads_tab_pins_stale_first_then_open_then_folds_terminal():
    chs = _chapters("One", "Two", "Three", "Four", "Five")
    threads = [
        ThreadRecord(id="a", name="Open A", state=ThreadState.touched, last_chapter_id="c5"),
        ThreadRecord(id="b", name="Done B", state=ThreadState.paid_off, last_chapter_id="c2"),
        ThreadRecord(id="c", name="Stale C", state=ThreadState.planted, last_chapter_id="c1"),
        ThreadRecord(id="d", name="Gone D", state=ThreadState.abandoned, last_chapter_id="c1"),
        ThreadRecord(id="e", name="Done E", state=ThreadState.paid_off, last_chapter_id="c3"),
    ]
    tab = threads_tab(threads, chs)
    plains = [line.plain for line in tab.lines]
    assert plains[0] == "⚠ Stale C · stale — last touched ch 1, 4 chapters ago"
    assert plains[1] == "· Open A · touched"
    assert plains[2] == "✓ 2 paid off · 1 abandoned"
    assert len(plains) == 3
    assert tab.alarm_count == 1
    assert str(tab.lines[2].style) == "dim"


def test_threads_tab_fold_line_omits_zero_parts():
    chs = _chapters("One")
    threads = [ThreadRecord(id="b", name="Done B", state=ThreadState.paid_off, last_chapter_id="c1")]
    tab = threads_tab(threads, chs)
    assert [line.plain for line in tab.lines] == ["✓ 1 paid off"]


def test_threads_tab_empty_state():
    tab = threads_tab([], [])
    assert [line.plain for line in tab.lines] == [THREADS_EMPTY]
    assert str(tab.lines[0].style) == "dim"
    assert tab.alarm_count == 0


@given(st.lists(st.sampled_from(list(ThreadState)), max_size=8))
def test_threads_tab_alarm_count_matches_alarm_lines_and_stale_pinned_first(states):
    chs = _chapters("One", "Two", "Three", "Four")
    threads = [
        ThreadRecord(id=f"t{i}", name=f"T{i}", state=s, last_chapter_id="")
        for i, s in enumerate(states)
    ]
    tab = threads_tab(threads, chs)
    alarm_flags = [line.plain.startswith("⚠") for line in tab.lines]
    assert tab.alarm_count == sum(alarm_flags)
    # every alarm line precedes every non-alarm line
    assert alarm_flags == sorted(alarm_flags, reverse=True)
```

- [ ] **Step 2: Run test to verify it fails**

```
uv run pytest tests/tui/test_brain_model.py -v
```
Expected: `ImportError: cannot import name 'thread_line' from 'novelizer.tui.widgets.brain_model'` (collection error); Task 1's tests still pass when run alone.

- [ ] **Step 3: Write minimal implementation** (append to `novelizer/tui/widgets/brain_model.py`; extend the imports block with `from novelizer.brain.staleness import chapters_elapsed_since, is_thread_stale`, `from novelizer.canon.threads import TERMINAL_STATES`, and add `ThreadRecord, ThreadState` to the `novelizer.store.models` import)

```python
def thread_line(thread: ThreadRecord, chapters: list[Chapter]) -> Text:
    """One Threads-tab row. Staleness comes from is_thread_stale /
    chapters_elapsed_since — never re-derived. No slugs/ids anywhere."""
    if is_thread_stale(thread, chapters):
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


def threads_tab(threads: list[ThreadRecord], chapters: list[Chapter]) -> ThreadsTab:
    """Threads grouped by state: stale pinned first (alarms), then live open
    threads, terminal threads folded to one dim count line."""
    if not threads:
        return ThreadsTab([Text(THREADS_EMPTY, style=DIM)], 0)
    stale = [t for t in threads if is_thread_stale(t, chapters)]
    stale_ids = {t.id for t in stale}
    live = [
        t for t in threads
        if t.id not in stale_ids and t.state.value not in TERMINAL_STATES
    ]
    terminal = [t for t in threads if t.state.value in TERMINAL_STATES]
    lines = [thread_line(t, chapters) for t in stale + live]
    if terminal:
        paid = sum(1 for t in terminal if t.state == ThreadState.paid_off)
        abandoned = len(terminal) - paid
        parts = ([f"{paid} paid off"] if paid else []) + (
            [f"{abandoned} abandoned"] if abandoned else []
        )
        lines.append(Text("✓ " + " · ".join(parts), style=DIM))
    return ThreadsTab(lines, len(stale))
```

- [ ] **Step 4: Run test to verify it passes**

```
uv run pytest tests/tui/test_brain_model.py -v
```
Expected: 17 passed.

- [ ] **Step 5: Commit**

```
git add novelizer/tui/widgets/brain_model.py tests/tui/test_brain_model.py
git commit -m "feat: brain_model Threads tab — stale pinned with titled last-touch, terminal folded to a dim count"
```

---

### Task 3: Secrets matrix model (`brain_model.py`, part 3)

**Files:**
- Modify: `novelizer/tui/widgets/brain_model.py` (append)
- Test: `tests/tui/test_brain_model.py` (append)

**Interfaces:**
- Consumes: `knowledge_cell_state` (`novelizer.canon.secrets` — the REAL cell states `"unknown" | "known" | "revealed"`); `Character`, `SecretRecord` (`novelizer.store.models`); Task 1's `SECRETS_EMPTY`, `DIM`.
- Produces: `CELL_GLYPHS: dict[str, str]`, `TITLE_WIDTH: int = 24`, `char_initials(name: str) -> str`, `matrix_header(characters: list[Character]) -> Text`, `secret_row(secret: SecretRecord, characters: list[Character], matrix: dict[str, dict]) -> Text`, `SecretsTab` (frozen dataclass: `lines: list[Text]`, `alarm_count: int`), `secrets_tab(secrets: list[SecretRecord], characters: list[Character], matrix: dict[str, dict]) -> SecretsTab`. Task 5 consumes `secrets_tab`; the updated `tests/agents/test_continuity_checker.py` consumes `secret_row`.

Design decisions locked here:
- **Column headers:** `char_initials` = first letter of up to the first two words of the name, uppercased (`"Elara"→"E"`, `"The Boy"→"TB"`, empty name → `"?"`). Every column is width 2 (`ljust(2)`), columns joined by a single space, so a glyph sits exactly under its character's first initial. Columns follow `list_characters()` order.
- **Row layout:** secret title clipped to `TITLE_WIDTH` (24; longer titles truncate to 23 + `…`) and `ljust`-padded, then the glyph cells, then a dim summary three spaces later: `no one knows` / `1 knows` / `{n} know` (n ≥ 2 — the spec's own `2 know` sketch).
- **Folding:** only unrevealed secrets get matrix rows; the header renders only when there are characters *and* unrevealed secrets; revealed secrets collapse to one dim `✓ revealed (N)` line. `alarm_count` is always 0 (leak alarms live in the feed/retcon queue).

- [ ] **Step 1: Write the failing test** (append to `tests/tui/test_brain_model.py`)

```python
from novelizer.store.models import Character, SecretRecord
from novelizer.tui.widgets.brain_model import (
    CELL_GLYPHS,
    SECRETS_EMPTY,
    TITLE_WIDTH,
    char_initials,
    matrix_header,
    secret_row,
    secrets_tab,
)


def test_cell_glyphs_cover_exactly_the_real_cell_states():
    # knowledge_cell_state's actual codomain — there is no "suspected" state.
    assert CELL_GLYPHS == {"known": "●", "unknown": "○", "revealed": "✓"}


def test_char_initials_short_names_from_words():
    assert char_initials("Elara") == "E"
    assert char_initials("The Boy") == "TB"
    assert char_initials("Mara Vane Kestrel") == "MV"
    assert char_initials("") == "?"


def test_matrix_header_aligns_initials_after_title_gutter():
    header = matrix_header([Character(id="elara", name="Elara"), Character(id="boy", name="The Boy")])
    assert header.plain == " " * TITLE_WIDTH + "E  TB"
    assert str(header.style) == "dim"


def test_secret_row_glyph_cells_align_under_header_and_count_knowers():
    chars = [Character(id="elara", name="Elara"), Character(id="boy", name="The Boy")]
    secret = SecretRecord(id="the-heir-lives", title="The Heir Lives")
    matrix = {"the-heir-lives": {"revealed": False, "known_by": {"elara"}}}
    row = secret_row(secret, chars, matrix)
    assert row.plain == "The Heir Lives".ljust(TITLE_WIDTH) + "●  ○" + "   1 knows"
    assert "the-heir-lives" not in row.plain


def test_secret_row_known_to_no_one():
    secret = SecretRecord(id="s", title="The Map Is Forged")
    matrix = {"s": {"revealed": False, "known_by": set()}}
    row = secret_row(secret, [Character(id="k", name="Kestrel")], matrix)
    assert row.plain.endswith("no one knows")
    assert "●" not in row.plain and "○" in row.plain


def test_secret_row_plural_summary_matches_spec_sketch():
    chars = [Character(id="a", name="Ana"), Character(id="b", name="Bram"), Character(id="c", name="Cole")]
    secret = SecretRecord(id="s", title="The Tide Debt")
    matrix = {"s": {"revealed": False, "known_by": {"a", "b"}}}
    assert secret_row(secret, chars, matrix).plain.endswith("2 know")


def test_secret_row_clips_long_titles():
    secret = SecretRecord(id="s", title="A" * 40)
    row = secret_row(secret, [], {"s": {"revealed": False, "known_by": set()}})
    assert row.plain.startswith("A" * (TITLE_WIDTH - 1) + "…")


def test_secrets_tab_folds_revealed_and_renders_matrix_for_unrevealed():
    chars = [Character(id="elara", name="Elara")]
    secrets = [
        SecretRecord(id="s1", title="The Heir Lives"),
        SecretRecord(id="s2", title="The Map Is Forged", revealed=True),
        SecretRecord(id="s3", title="The Tide Debt", revealed=True),
    ]
    matrix = {
        "s1": {"revealed": False, "known_by": {"elara"}},
        "s2": {"revealed": True, "known_by": set()},
        "s3": {"revealed": True, "known_by": set()},
    }
    tab = secrets_tab(secrets, chars, matrix)
    plains = [line.plain for line in tab.lines]
    assert plains[0] == " " * TITLE_WIDTH + "E"
    assert plains[1].startswith("The Heir Lives")
    assert plains[2] == "✓ revealed (2)"
    assert len(plains) == 3
    assert tab.alarm_count == 0
    assert str(tab.lines[2].style) == "dim"


def test_secrets_tab_all_revealed_is_just_the_fold_line():
    secrets = [SecretRecord(id="s", title="Old News", revealed=True)]
    tab = secrets_tab(secrets, [], {"s": {"revealed": True, "known_by": set()}})
    assert [line.plain for line in tab.lines] == ["✓ revealed (1)"]


def test_secrets_tab_empty_state():
    tab = secrets_tab([], [], {})
    assert [line.plain for line in tab.lines] == [SECRETS_EMPTY]
    assert str(tab.lines[0].style) == "dim"


@given(n_secrets=st.integers(0, 5), n_chars=st.integers(0, 5))
def test_matrix_rows_cover_every_secret_by_character_pair(n_secrets, n_chars):
    chars = [Character(id=f"ch{i}", name=f"N{i}") for i in range(n_chars)]
    secrets = [SecretRecord(id=f"s{i}", title=f"S{i}") for i in range(n_secrets)]
    matrix = {s.id: {"revealed": False, "known_by": set()} for s in secrets}
    tab = secrets_tab(secrets, chars, matrix)
    rows = [line for line in tab.lines if line.plain.startswith("S")]
    assert len(rows) == n_secrets
    for row in rows:
        assert row.plain.count("○") + row.plain.count("●") == n_chars
```

- [ ] **Step 2: Run test to verify it fails**

```
uv run pytest tests/tui/test_brain_model.py -v
```
Expected: `ImportError: cannot import name 'CELL_GLYPHS' from 'novelizer.tui.widgets.brain_model'` (collection error).

- [ ] **Step 3: Write minimal implementation** (append to `novelizer/tui/widgets/brain_model.py`; extend imports with `from novelizer.canon.secrets import knowledge_cell_state` and add `Character, SecretRecord` to the `novelizer.store.models` import)

```python
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
```

- [ ] **Step 4: Run test to verify it passes**

```
uv run pytest tests/tui/test_brain_model.py -v
```
Expected: 28 passed.

- [ ] **Step 5: Commit**

```
git add novelizer/tui/widgets/brain_model.py tests/tui/test_brain_model.py
git commit -m "feat: brain_model Secrets tab — knowledge matrix (initial columns, real cell-state glyphs, revealed folded)"
```

---

### Task 4: Causeway tab model + alarm strip (`brain_model.py`, part 4)

**Files:**
- Modify: `novelizer/tui/widgets/brain_model.py` (append)
- Test: `tests/tui/test_brain_model.py` (append)

**Interfaces:**
- Consumes: `find_paradoxes` (`novelizer.brain.paradoxes` — single-sourcing; flags both `ordering` and `cycle` paradoxes); `CausalEdgeRecord` (`novelizer.store.models`); Task 1's `chapter_label`, `CAUSEWAY_EMPTY`, `DIM`, `ALARM_STYLE`.
- Produces: `CausewayTab` (frozen dataclass: `lines: list[Text]`, `alarm_count: int`), `causeway_tab(edges: list[CausalEdgeRecord], chapters: list[Chapter]) -> CausewayTab`, `alarm_strip(shape: int, threads: int, secrets: int, cause: int) -> Text`. Task 5 consumes both.

Design decisions locked here:
- Edges sort by (cause chapter position, effect chapter position) in `list_chapters()` order; edges citing unknown chapter ids sort last (stable, so duplicates keep declaration order).
- `alarm_count` counts paradox **edge rows** (a duplicate declared paradox edge counts each time — matching one alarm row each; a 2-cycle yields 2).
- Strip segments are dim labels; only the ` ⚠{n}` suffix takes `ALARM_STYLE`.

- [ ] **Step 1: Write the failing test** (append to `tests/tui/test_brain_model.py`)

```python
from novelizer.store.models import CausalEdgeRecord
from novelizer.tui.widgets.brain_model import CAUSEWAY_EMPTY, alarm_strip, causeway_tab


def test_causeway_line_uses_chapter_titles_and_arrow_never_ids():
    chs = _chapters("The Gift", "The Price")
    edges = [CausalEdgeRecord(cause_chapter_id="c1", effect_chapter_id="c2", note="sets up the reveal")]
    tab = causeway_tab(edges, chs)
    assert tab.lines[0].plain == 'ch 1 "The Gift" ──▶ ch 2 "The Price": sets up the reveal'
    assert tab.alarm_count == 0
    assert "c1" not in tab.lines[0].plain and "c2" not in tab.lines[0].plain


def test_causeway_edge_without_note_has_no_colon():
    chs = _chapters("One", "Two")
    tab = causeway_tab([CausalEdgeRecord(cause_chapter_id="c1", effect_chapter_id="c2")], chs)
    assert tab.lines[0].plain == 'ch 1 "One" ──▶ ch 2 "Two"'


def test_causeway_ordering_paradox_edge_is_alarm_with_marker():
    chs = _chapters("One", "Two")
    tab = causeway_tab(
        [CausalEdgeRecord(cause_chapter_id="c2", effect_chapter_id="c1", note="the fall")], chs
    )
    assert tab.lines[0].plain == 'ch 2 "Two" ──▶ ch 1 "One": the fall  ⚠ PARADOX'
    assert str(tab.lines[0].style) == ALARM_STYLE
    assert tab.alarm_count == 1


def test_causeway_cycle_paradox_flags_both_directions():
    chs = _chapters("One", "Two", "Three")
    edges = [
        CausalEdgeRecord(cause_chapter_id="c1", effect_chapter_id="c2"),
        CausalEdgeRecord(cause_chapter_id="c2", effect_chapter_id="c1"),
    ]
    tab = causeway_tab(edges, chs)
    assert tab.alarm_count == 2
    assert all("⚠ PARADOX" in line.plain for line in tab.lines)


def test_causeway_unknown_chapter_id_falls_back_to_raw_id():
    chs = _chapters("One")
    tab = causeway_tab([CausalEdgeRecord(cause_chapter_id="ghost", effect_chapter_id="c1")], chs)
    assert tab.lines[0].plain == 'ghost ──▶ ch 1 "One"'


def test_causeway_sorts_by_chapter_position():
    chs = _chapters("One", "Two", "Three")
    edges = [
        CausalEdgeRecord(cause_chapter_id="c2", effect_chapter_id="c3"),
        CausalEdgeRecord(cause_chapter_id="c1", effect_chapter_id="c2"),
    ]
    tab = causeway_tab(edges, chs)
    assert tab.lines[0].plain.startswith('ch 1 "One"')
    assert tab.lines[1].plain.startswith('ch 2 "Two"')


def test_causeway_empty_state():
    tab = causeway_tab([], [])
    assert [line.plain for line in tab.lines] == [CAUSEWAY_EMPTY]
    assert str(tab.lines[0].style) == "dim"


def test_alarm_strip_matches_spec_format():
    assert alarm_strip(1, 2, 0, 1).plain == "Shape ⚠1 · Threads ⚠2 · Secrets · Cause ⚠1"


def test_alarm_strip_quiet_shows_bare_labels():
    assert alarm_strip(0, 0, 0, 0).plain == "Shape · Threads · Secrets · Cause"


def test_alarm_strip_alarm_segments_are_alarm_styled():
    strip = alarm_strip(1, 0, 0, 0)
    spans = [(strip.plain[s.start:s.end], str(s.style)) for s in strip.spans]
    assert (" ⚠1", ALARM_STYLE) in spans
```

- [ ] **Step 2: Run test to verify it fails**

```
uv run pytest tests/tui/test_brain_model.py -v
```
Expected: `ImportError: cannot import name 'alarm_strip' from 'novelizer.tui.widgets.brain_model'` (collection error).

- [ ] **Step 3: Write minimal implementation** (append to `novelizer/tui/widgets/brain_model.py`; extend imports with `from novelizer.brain.paradoxes import find_paradoxes` and add `CausalEdgeRecord` to the `novelizer.store.models` import)

```python
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
```

- [ ] **Step 4: Run test to verify it passes**

```
uv run pytest tests/tui/test_brain_model.py -v
```
Expected: 38 passed.

- [ ] **Step 5: Commit**

```
git add novelizer/tui/widgets/brain_model.py tests/tui/test_brain_model.py
git commit -m "feat: brain_model Causeway tab (titled edges, paradox alarms) + alarm summary strip"
```

---

### Task 5: `BrainPanel` widget, app wiring, tcss, key bindings, old-widget deletion, test updates

**Files:**
- Create: `novelizer/tui/widgets/brain_panel.py`
- Create: `tests/tui/test_brain_panel.py`
- Modify: `novelizer/tui/app.py` (imports, `BINDINGS`, `compose`, `on_mount`, new `_brain_loop` + `action_brain_tab`; delete the four old loops)
- Modify: `novelizer/tui/app.tcss` (full replacement below)
- Modify: `tests/tui/test_app_layout.py` (two pane tests rewritten; border-title test updated)
- Modify: `tests/agents/test_author.py` (swap `thread_board_line` → `thread_line`)
- Modify: `tests/agents/test_continuity_checker.py` (swap `who_knows_what_line` → `secret_row`)
- Delete: `novelizer/tui/widgets/thread_board.py`, `novelizer/tui/widgets/story_shape.py`, `novelizer/tui/widgets/who_knows_what.py`, `novelizer/tui/widgets/causeway.py`
- Delete: `tests/tui/test_thread_board.py`, `tests/tui/test_story_shape.py`, `tests/tui/test_who_knows_what.py`, `tests/tui/test_causeway.py`

**Interfaces:**
- Consumes: `shape_tab`, `threads_tab`, `secrets_tab`, `causeway_tab`, `alarm_strip` (Tasks 1–4); `ReadStore.list_chapters/list_threads/list_structure_scores/list_secrets/list_characters/knowledge_matrix/list_causal_edges` (existing signatures, no args except `list_chapters`' optional `status`, unused); Textual `TabbedContent(id=...)` / `TabPane(title, id=...)` / `Sparkline(data)` (`data` is a reactive `Sequence[float] | None`) / `Static` / `Vertical`.
- Produces: `BrainPanel(Vertical)` with `compose()`, `async refresh_from(read) -> None`, `activate_tab(pane_id: str) -> None`; widget ids `#brain`, `#brain_tabs`, `#shape_spark`, `#shape_body`, `#threads_body`, `#secrets_body`, `#causeway_body`, `#brain_strip`; app additions `_brain_loop`, `action_brain_tab`.

Notes locked here:
- One poll (`await asyncio.sleep(1.0)` loop, first refresh immediately on mount) updates **all four tabs and the strip** each cycle — hidden tabs stay current, so the strip can never lie.
- `chapters` is fetched **once** per refresh and shared by shape/threads/causeway (consistent snapshot within a cycle).
- The `Sparkline` is hidden (`display = False`) when there is no data (its empty render is a blank row, not a designed empty state) and shown otherwise; the empty-state line lives in `#shape_body` via `ShapeTab.meta`.
- Multi-line `Static` content = `Text("\n").join(lines)`.
- `activate_tab` (not `show_tab`, which is an unrelated `TabbedContent` method name) sets `TabbedContent.active`.
- `#brain { height: 16; max-height: 45%; }` — fixed height on real terminals, percentage-capped on tiny ones so the feed's `3fr` always survives.
- Footer will show the four new bindings; the footer/palette cleanup is Phase 3's job — do not add `show=False`.

- [ ] **Step 1: Write the failing tests and update the cross-referencing tests**

Create `tests/tui/test_brain_panel.py`:

```python
import os
import tempfile
import pytest
from novelizer.settings import EffectiveSettings as Settings
from novelizer.runtime import Runtime
from novelizer.tui.app import NovelizerApp
from novelizer.tui.widgets.brain_model import (
    CAUSEWAY_EMPTY, SECRETS_EMPTY, SHAPE_EMPTY, THREADS_EMPTY,
)


async def _app():
    fd, path = tempfile.mkstemp(suffix=".db"); os.close(fd)
    settings = Settings(db_path=path, projector_interval=0.1)
    rt = Runtime(settings, runners={})
    await rt.start()
    for a in rt.scheduler.status():
        rt.scheduler.pause_agent(a["name"])
    return NovelizerApp(rt), rt, path


@pytest.mark.asyncio
async def test_brain_panel_replaces_the_four_stacked_panes():
    from novelizer.tui.widgets.brain_panel import BrainPanel
    from textual.widgets import TabbedContent

    app, rt, path = await _app()
    try:
        async with app.run_test() as pilot:
            await pilot.pause()
            panel = app.query_one("#brain", BrainPanel)
            assert str(panel.border_title) == "STORY BRAIN"
            tabs = app.query_one("#brain_tabs", TabbedContent)
            assert tabs.active == "tab_shape"
            for pane_id in ("tab_shape", "tab_threads", "tab_secrets", "tab_causeway"):
                assert tabs.get_pane(pane_id) is not None
            for old_id in ("#thread_board", "#story_shape", "#who_knows_what", "#causeway"):
                assert not app.query(old_id)
    finally:
        await rt.close(); os.unlink(path)


@pytest.mark.asyncio
async def test_keys_1_to_4_switch_brain_tabs():
    from textual.widgets import TabbedContent

    app, rt, path = await _app()
    try:
        async with app.run_test() as pilot:
            await pilot.pause()
            tabs = app.query_one("#brain_tabs", TabbedContent)
            await pilot.press("2")
            assert tabs.active == "tab_threads"
            await pilot.press("3")
            assert tabs.active == "tab_secrets"
            await pilot.press("4")
            assert tabs.active == "tab_causeway"
            await pilot.press("1")
            assert tabs.active == "tab_shape"
    finally:
        await rt.close(); os.unlink(path)


@pytest.mark.asyncio
async def test_fresh_story_shows_designed_empty_states_and_quiet_strip():
    from textual.widgets import Sparkline, Static

    app, rt, path = await _app()
    try:
        async with app.run_test() as pilot:
            await pilot.pause(0.5)  # first _brain_loop refresh
            assert str(app.query_one("#shape_body", Static).renderable) == SHAPE_EMPTY
            assert str(app.query_one("#threads_body", Static).renderable) == THREADS_EMPTY
            assert str(app.query_one("#secrets_body", Static).renderable) == SECRETS_EMPTY
            assert str(app.query_one("#causeway_body", Static).renderable) == CAUSEWAY_EMPTY
            assert not app.query_one("#shape_spark", Sparkline).display
            assert str(app.query_one("#brain_strip", Static).renderable) == "Shape · Threads · Secrets · Cause"
    finally:
        await rt.close(); os.unlink(path)


@pytest.mark.asyncio
async def test_strip_flags_stale_thread_alarm_while_another_tab_is_open():
    from textual.widgets import Static
    from novelizer.canon.events import EventType, ThreadPlanted
    from novelizer.store.models import Chapter

    app, rt, path = await _app()
    try:
        await rt.events.append(EventType.THREAD_PLANTED, "the-boys-gift",
                               ThreadPlanted(id="the-boys-gift", name="The Boy's Gift"))
        for i in range(4):
            await rt.events.append(EventType.CHAPTER_CREATED, f"c{i}",
                                   Chapter(id=f"c{i}", title=f"Ch {i}", prose="p"))
        await rt.projector.catch_up()
        async with app.run_test() as pilot:
            await pilot.pause(0.5)
            strip = str(app.query_one("#brain_strip", Static).renderable)
            assert "Threads ⚠1" in strip   # visible regardless of the active tab (Shape)
    finally:
        await rt.close(); os.unlink(path)


@pytest.mark.asyncio
async def test_brain_panel_hides_in_reading_mode_and_survives_room_mode():
    from textual.widgets import TabbedContent

    app, rt, path = await _app()
    try:
        async with app.run_test(size=(120, 40)) as pilot:
            await pilot.pause()
            brain = app.query_one("#brain")
            assert brain.region.width > 0
            await pilot.press("v")
            await pilot.pause()
            assert brain.region.width == 0       # #left is display:none in reading mode
            await pilot.press("v")
            await pilot.pause()
            assert brain.region.width > 0        # back on the home screen
            await pilot.press("r")
            await pilot.pause()
            assert brain.region.width > 0        # room mode keeps the left column
            await pilot.press("2")               # keys still switch tabs after toggling
            assert app.query_one("#brain_tabs", TabbedContent).active == "tab_threads"
    finally:
        await rt.close(); os.unlink(path)
```

In `tests/tui/test_app_layout.py`, **replace** `test_mission_control_shows_thread_board_and_story_shape_panes` with:

```python
@pytest.mark.asyncio
async def test_story_brain_threads_and_shape_tabs_populate():
    from novelizer.canon.events import EventType, ThreadPlanted, AnnotationStructureScored
    from novelizer.store.models import Chapter

    fd, path = tempfile.mkstemp(suffix=".db"); os.close(fd)
    settings = Settings(db_path=path, projector_interval=0.1)
    rt = Runtime(settings, runners=_runners())
    await rt.start()
    for name in ["world_architect", "character_keeper", "author", "editor", "continuity_checker", "retconner", "structure_analyst"]:
        rt.scheduler.pause_agent(name)
    try:
        await rt.events.append(EventType.CHAPTER_CREATED, "c1", Chapter(id="c1", title="The Salt Road", prose="p"))
        await rt.events.append(EventType.THREAD_PLANTED, "the-locket", ThreadPlanted(id="the-locket", name="The Locket"))
        await rt.events.append(EventType.ANNOTATION_STRUCTURE_SCORED, "c1",
                               AnnotationStructureScored(chapter_id="c1", tension=0.6, pacing_label="rising"))
        await rt.projector.catch_up()
        app = NovelizerApp(rt)
        async with app.run_test() as pilot:
            from textual.widgets import Sparkline, Static
            await pilot.pause(0.5)
            threads_text = str(app.query_one("#threads_body", Static).renderable)
            shape_text = str(app.query_one("#shape_body", Static).renderable)
            spark = app.query_one("#shape_spark", Sparkline)
            assert "The Locket" in threads_text
            assert "the-locket" not in threads_text          # no ids on the dashboard
            assert list(spark.data) == [0.6] and spark.display
            assert "pacing: rising" in shape_text
            assert "c1" not in shape_text                    # no ids on the dashboard
    finally:
        await rt.close(); os.unlink(path)
```

**Replace** `test_mission_control_shows_who_knows_what_and_causeway_panes` with:

```python
@pytest.mark.asyncio
async def test_story_brain_secrets_matrix_and_causeway_tabs_populate():
    from novelizer.canon.events import EventType, SecretCreated, SecretLearned, CausalEdgeDeclared
    from novelizer.store.models import Chapter, Character

    fd, path = tempfile.mkstemp(suffix=".db"); os.close(fd)
    settings = Settings(db_path=path, projector_interval=0.1)
    rt = Runtime(settings, runners=_runners())
    await rt.start()
    for name in ["world_architect", "character_keeper", "author", "editor", "continuity_checker", "retconner", "structure_analyst"]:
        rt.scheduler.pause_agent(name)
    try:
        await rt.events.append(EventType.CHAPTER_CREATED, "c1", Chapter(id="c1", title="One", prose="p"))
        await rt.events.append(EventType.CHARACTER_CREATED, "mara", Character(id="mara", name="Mara"))
        await rt.events.append(EventType.SECRET_CREATED, "the-heir-lives", SecretCreated(id="the-heir-lives", title="The Heir Lives"))
        await rt.events.append(EventType.SECRET_LEARNED, "the-heir-lives", SecretLearned(id="the-heir-lives", character_id="mara"))
        await rt.events.append(EventType.CHAPTER_CREATED, "c2", Chapter(id="c2", title="Two", prose="p"))
        await rt.events.append(EventType.CAUSAL_EDGE_DECLARED, "c1",
                               CausalEdgeDeclared(cause_chapter_id="c2", effect_chapter_id="c1"))
        await rt.projector.catch_up()
        app = NovelizerApp(rt)
        async with app.run_test() as pilot:
            from textual.widgets import Static
            await pilot.pause(0.5)
            secrets_text = str(app.query_one("#secrets_body", Static).renderable)
            causeway_text = str(app.query_one("#causeway_body", Static).renderable)
            strip_text = str(app.query_one("#brain_strip", Static).renderable)
            assert "The Heir Lives" in secrets_text
            assert "M" in secrets_text.splitlines()[0]       # Mara's initial in the header
            assert "●" in secrets_text and "1 knows" in secrets_text
            assert "the-heir-lives" not in secrets_text      # no ids on the dashboard
            assert 'ch 2 "Two" ──▶ ch 1 "One"' in causeway_text
            assert "⚠ PARADOX" in causeway_text
            assert "Cause ⚠1" in strip_text
    finally:
        await rt.close(); os.unlink(path)
```

In `test_every_pane_has_its_border_title` (same file), replace the four brain entries so `expected` becomes:

```python
            expected = {
                "#feed": "THE ROOM",
                "#proposals": "PROPOSALS",
                "#brain": "STORY BRAIN",
                "#browser": "STORY",
                "#detail_scroll": "DETAIL",
            }
```

In `tests/agents/test_author.py` (lines ~263–303):
- change the import `from novelizer.tui.widgets.thread_board import thread_board_line` to `from novelizer.tui.widgets.brain_model import thread_line`
- change `assert "STALE" in thread_board_line(thread_before, chapters)` to `assert "stale" in thread_line(thread_before, chapters).plain`
- change `assert "STALE" not in thread_board_line(thread_after, chapters_after)` to `assert "stale" not in thread_line(thread_after, chapters_after).plain`
- in the docstring, change `(thread_board_line, via is_thread_stale)` to `(brain_model.thread_line, via is_thread_stale)`

In `tests/agents/test_continuity_checker.py` (lines ~195–250):
- change the import `from novelizer.tui.widgets.who_knows_what import who_knows_what_line` to `from novelizer.tui.widgets.brain_model import secret_row`
- replace the final three lines

```python
    line = who_knows_what_line(secret, characters, matrix)
    assert "Kestrel" not in line
    assert "known to no one" in line
```

  with

```python
    row = secret_row(secret, characters, matrix).plain
    assert "●" not in row          # no filled cell — Kestrel hasn't learned it
    assert "no one knows" in row
```

- in the docstring, change `the Who-Knows-What widget's render-time helper` to `the Secrets matrix render-time helper (brain_model.secret_row)` (both occurrences in the docstring/step-4 comment).

- [ ] **Step 2: Run tests to verify the new ones fail and the updated ones' state is understood**

```
uv run pytest tests/tui/test_brain_panel.py tests/tui/test_app_layout.py -v
uv run pytest tests/agents/test_author.py tests/agents/test_continuity_checker.py -v
```
Expected: `test_brain_panel.py` fails at collection (`ModuleNotFoundError: No module named 'novelizer.tui.widgets.brain_panel'`); the two rewritten `test_app_layout` tests fail (`NoMatches` for `#threads_body` etc.) and the border-title test fails (`NoMatches` / `KeyError` for `#brain`); the two **agents** tests PASS already (they now consume Task 2/3's pure helpers — that is the point: their surface was migrated before the old one is deleted).

- [ ] **Step 3: Write minimal implementation**

Create `novelizer/tui/widgets/brain_panel.py`:

```python
"""The Story Brain panel: one TabbedContent over the four brain views.

Thin Textual shell — every rendered string/Text comes from the pure
brain_model functions; this widget only fetches ReadStore data once per
refresh and places the results. No selection/targeting inside tabs (Phase 3).
"""
from __future__ import annotations

from rich.text import Text
from textual.app import ComposeResult
from textual.containers import Vertical
from textual.widgets import Sparkline, Static, TabbedContent, TabPane

from novelizer.tui.widgets.brain_model import (
    alarm_strip,
    causeway_tab,
    secrets_tab,
    shape_tab,
    threads_tab,
)

TAB_IDS = ("tab_shape", "tab_threads", "tab_secrets", "tab_causeway")


def _joined(lines: list[Text]) -> Text:
    return Text("\n").join(lines)


class BrainPanel(Vertical):
    """One panel, four tabs, one persistent alarm strip. Polled by the app's
    _brain_loop once per second; every refresh updates all four tabs plus the
    strip so nothing is missed while another tab is open."""

    def compose(self) -> ComposeResult:
        with TabbedContent(id="brain_tabs"):
            with TabPane("1 Shape", id="tab_shape"):
                yield Sparkline([], id="shape_spark")
                yield Static("", id="shape_body")
            with TabPane("2 Threads", id="tab_threads"):
                yield Static("", id="threads_body")
            with TabPane("3 Secrets", id="tab_secrets"):
                yield Static("", id="secrets_body")
            with TabPane("4 Cause", id="tab_causeway"):
                yield Static("", id="causeway_body")
        yield Static("", id="brain_strip")

    async def refresh_from(self, read) -> None:
        chapters = await read.list_chapters()  # one snapshot shared by three tabs
        shape = shape_tab(await read.list_structure_scores(), chapters)
        threads = threads_tab(await read.list_threads(), chapters)
        secrets = secrets_tab(
            await read.list_secrets(), await read.list_characters(), await read.knowledge_matrix()
        )
        cause = causeway_tab(await read.list_causal_edges(), chapters)

        spark = self.query_one("#shape_spark", Sparkline)
        spark.display = bool(shape.tensions)
        spark.data = shape.tensions
        self.query_one("#shape_body", Static).update(_joined([shape.meta, *shape.callouts]))
        self.query_one("#threads_body", Static).update(_joined(threads.lines))
        self.query_one("#secrets_body", Static).update(_joined(secrets.lines))
        self.query_one("#causeway_body", Static).update(_joined(cause.lines))
        self.query_one("#brain_strip", Static).update(
            alarm_strip(shape.alarm_count, threads.alarm_count, secrets.alarm_count, cause.alarm_count)
        )

    def activate_tab(self, pane_id: str) -> None:
        self.query_one("#brain_tabs", TabbedContent).active = pane_id
```

In `novelizer/tui/app.py`:

1. **Imports** — delete these four lines:

```python
from novelizer.tui.widgets.thread_board import ThreadBoard
from novelizer.tui.widgets.story_shape import StoryShape
from novelizer.tui.widgets.who_knows_what import WhoKnowsWhat
from novelizer.tui.widgets.causeway import Causeway
```

and add in their place:

```python
from novelizer.tui.widgets.brain_panel import BrainPanel
```

2. **BINDINGS** — replace the list with:

```python
    BINDINGS = [
        ("ctrl+k", "focus_command", "Command"),
        ("r", "toggle_room", "Room"),
        ("v", "toggle_reading", "Reading"),
        ("1", "brain_tab('tab_shape')", "Shape"),
        ("2", "brain_tab('tab_threads')", "Threads"),
        ("3", "brain_tab('tab_secrets')", "Secrets"),
        ("4", "brain_tab('tab_causeway')", "Cause"),
        ("q", "quit", "Quit"),
    ]
```

3. **`compose()`** — replace the four brain-pane blocks (the `thread_board` / `story_shape` / `who_knows_what` / `causeway` widget constructions and yields, currently lines 75–84) with:

```python
                brain = BrainPanel(id="brain")
                brain.border_title = "STORY BRAIN"
                yield brain
```

4. **`on_mount()`** — replace the four workers

```python
        self.run_worker(self._thread_board_loop(), exclusive=False)
        self.run_worker(self._story_shape_loop(), exclusive=False)
        ...
        self.run_worker(self._who_knows_what_loop(), exclusive=False)
        self.run_worker(self._causeway_loop(), exclusive=False)
```

with one (keep the other workers exactly as they are):

```python
        self.run_worker(self._brain_loop(), exclusive=False)
```

5. **Delete** the four methods `_thread_board_loop`, `_story_shape_loop`, `_who_knows_what_loop`, `_causeway_loop`; **add**:

```python
    async def _brain_loop(self) -> None:
        while True:
            try:
                await self.query_one("#brain", BrainPanel).refresh_from(self.runtime.read)
            except Exception as e:
                self._report_worker_error("brain", e)
            await asyncio.sleep(1.0)
```

6. **Add** the tab action next to the other actions:

```python
    def action_brain_tab(self, pane_id: str) -> None:
        self.query_one("#brain", BrainPanel).activate_tab(pane_id)
```

Replace `novelizer/tui/app.tcss` in full with:

```
#body { height: 1fr; }
#left { width: 3fr; }
#right { width: 2fr; }
#feed { height: 3fr; border: round $primary; }
#proposals { height: auto; max-height: 6; border: round $secondary; }
#brain { height: 16; max-height: 45%; border: round $secondary; }
#brain TabPane { padding: 0 1; }
#shape_spark { height: 1; }
#brain_strip { height: 1; padding: 0 1; }
#browser { height: 2fr; border: round $primary; }
#detail_scroll { height: 1fr; border: round $secondary; padding: 0 1; }
#detail { height: auto; }
#statusbar { height: 1; background: $panel; color: $text; }
#command { height: 1; padding: 0 1; }
#body.room #right { display: none; }
#body.reading #left { display: none; }
#body.reading #right { layout: horizontal; width: 1fr; }
#body.reading #browser { width: 1fr; height: 1fr; }
#body.reading #detail_scroll { width: 3fr; height: 1fr; }
#settings_table { height: 1fr; }
#settings_msg { height: 1; }
#edit_value { height: 3; }
#feed, #browser { border-title-style: bold; }
#proposals, #brain, #detail_scroll { border-title-color: $text-muted; }
```

Delete the replaced files:

```
git rm novelizer/tui/widgets/thread_board.py novelizer/tui/widgets/story_shape.py \
       novelizer/tui/widgets/who_knows_what.py novelizer/tui/widgets/causeway.py \
       tests/tui/test_thread_board.py tests/tui/test_story_shape.py \
       tests/tui/test_who_knows_what.py tests/tui/test_causeway.py
```

- [ ] **Step 4: Run tests to verify green**

```
uv run pytest tests/tui/ tests/agents/test_author.py tests/agents/test_continuity_checker.py -v
```
Expected: all pass — including `test_brain_panel.py` (5), the rewritten `test_app_layout.py`, the untouched `test_reading_mode.py` / `test_app_commands.py` room-reading toggles, and the two migrated agents tests. No test anywhere still imports the deleted modules (`grep -rn "thread_board\|story_shape\|who_knows_what\|widgets.causeway" tests/ novelizer/` returns only docstring prose in `novelizer/brain/`).

- [ ] **Step 5: Commit**

```
git add -A
git commit -m "feat: Story Brain panel — TabbedContent over sparkline/threads/matrix/causeway with alarm strip, replacing the four stacked panes"
```

---

### Task 6: Full-suite verification, zero warnings

**Files:**
- Modify: only whatever the suite run reveals (expected: nothing — Task 5 migrated every known cross-reference; the inventory table above is the checklist).

**Interfaces:**
- Consumes: everything above. Produces: a green suite.

- [ ] **Step 1: Run the full suite**

```
uv run pytest -q
```
Expected: all tests pass, `0 warnings` in the summary line. (Live-LLM tests skip themselves without the env; that's normal.)

- [ ] **Step 2: If anything fails, fix it** — expected fallout class: a stray assertion on the old pane ids or `who_knows_what_line`-style helpers somewhere the inventory missed. Fix by migrating the assertion to the brain_model surface (`thread_line`/`secret_row`/tab bodies), never by resurrecting a deleted module and never by weakening the assertion to bare truthiness. If a *warning* appears, fix its source (e.g. an unawaited coroutine or an un-closed pilot), not the filter config.

- [ ] **Step 3: Re-run to verify green + zero warnings**

```
uv run pytest -q
```
Expected: `... passed` with no warnings summary block.

- [ ] **Step 4: Sanity-run the TUI subset alone**

```
uv run pytest tests/tui -q
```
Expected: green.

- [ ] **Step 5: Commit** (only if Step 2 changed anything)

```
git add -A tests/
git commit -m "test: migrate remaining assertions to the Story Brain surface"
```

---

## Self-review notes (spec coverage)

- Spec Phase 2 item ↔ task map: sparkline + pacing + titled sag/spike callouts → Task 1; grouped threads with stale pinned + titled last-touch → Task 2; knowledge matrix with initials header, real cell-state glyphs, revealed fold → Task 3; titled causeway with `⚠ PARADOX` + alarm summary strip → Task 4; TabbedContent panel, keys 1–4, `STORY BRAIN` border title, one poll loop, old-pane deletion, tcss, and every cross-referencing test updated in the same task → Task 5; suite green, zero warnings → Task 6.
- Names-not-ids checked per tab: shape callouts and causeway use `chapter_label` (title; raw id only for unknown chapter ids), thread rows use `thread.name`, matrix rows use `secret.title` + character initials; explicit "no id" assertions in Tasks 1, 2, 3 and both rewritten layout tests.
- Single-sourcing checked: `brain_model.py` imports `is_thread_stale`, `chapters_elapsed_since`, `TERMINAL_STATES`, `detect_sag_spike`, `find_paradoxes`, `knowledge_cell_state`, `ALARM_STYLE`; no threshold constant or state rule is re-typed anywhere in the new code.
- Type/name consistency verified across tasks: Task 5's `BrainPanel.refresh_from` consumes exactly `shape_tab(scores, chapters) -> ShapeTab(tensions, meta, callouts, alarm_count)`, `threads_tab(threads, chapters) -> ThreadsTab(lines, alarm_count)`, `secrets_tab(secrets, characters, matrix) -> SecretsTab(lines, alarm_count)`, `causeway_tab(edges, chapters) -> CausewayTab(lines, alarm_count)`, `alarm_strip(int, int, int, int) -> Text`; the agents tests consume exactly `thread_line(thread, chapters) -> Text` (Task 2) and `secret_row(secret, characters, matrix) -> Text` (Task 3).
- The spec's `◍ suspected` cell has no backing state in `knowledge_cell_state` (`"unknown" | "known" | "revealed"` only) — deliberately dropped; documented in Global Constraints and pinned by `test_cell_glyphs_cover_exactly_the_real_cell_states`.
- Room/reading interaction: no changes to `#body.room` / `#body.reading` rules; `#brain` inherits `#left`'s hide in reading mode; pilot-tested both directions plus key handling after toggles (Task 5).
- `app.messages`, proposals pane, roster/statusbar, detail pane, and `format_event` are untouched — Phase 3 surfaces stay exactly as Phase 1 left them.
