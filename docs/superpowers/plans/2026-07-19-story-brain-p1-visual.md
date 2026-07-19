# Story Brain P1 — Visual Pass Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Deliver Phase 1 of the Story Brain polish spec (`docs/superpowers/specs/2026-07-19-story-brain-polish-design.md`): compact-panel visual upgrades — Shape spark with aligned sag/spike markers, Threads age heat bars, Secrets spread meters, Cause paradox-first sort with dim arrows. No new data, no new surfaces.

**Architecture:** All rendering stays pure functions in `novelizer/tui/widgets/brain_model.py` (records in → `rich.text.Text` out, no Textual imports, no I/O). The one widget change: the `Sparkline` widget in `brain_panel.py` is **removed** and replaced by a model-rendered text spark (one block character per chapter). Rationale, recorded in the spec: `Sparkline` scales its data to widget width, so a marker row can never reliably align under specific chapters; a 1-cell-per-chapter text spark aligns trivially and makes the whole Shape tab pure-function testable. The full-width scaled chart arrives in P2's zoom mode.

**Tech Stack:** Python 3.12, Textual, Rich, pytest + pytest-asyncio (asyncio_mode=auto) + hypothesis, `uv` for running.

## Global Constraints

- **Run tests only in this worktree** (`.claude/worktrees/story-brain-polish-spec`), never in the main checkout (DB-lock incident rule).
- TUI suite invocation: `uv run pytest tests/tui -q -W error`. Never pipe long pytest runs through `tail`/`head` (see `docs/TESTING-TUI.md`).
- `brain_model.py` must keep zero Textual imports and zero I/O.
- No new events, projections, or ReadStore queries in P1.
- Style vocabulary: `ALARM_STYLE` (= `"bold red"`, imported from `feed_model`), `DIM = "dim"`, and new `WARN_STYLE = "yellow"` defined once in `brain_model.py`.
- Names, never ids, on the dashboard (existing rule — every changed line keeps it).
- Alarm *detection* is never re-derived in the model: staleness, sag/spike, paradoxes, knowledge cells keep coming from `novelizer.brain.*` / `novelizer.canon.*` functions.
- Glyphs used in this plan: spark levels `▁▂▃▄▅▆▇█`, bar cells `▰`/`▱`, meter cells `●`/`○`, marker `⚠`.

---

### Task 1: Shape model — text spark + aligned marker row

**Files:**
- Modify: `novelizer/tui/widgets/brain_model.py` (ShapeTab dataclass, `shape_tab`, new `spark_char`)
- Modify: `docs/superpowers/specs/2026-07-19-story-brain-polish-design.md` (record the Sparkline-widget → text-spark decision)
- Test: `tests/tui/test_brain_model.py`

**Interfaces:**
- Consumes: existing `detect_sag_spike`, `chapter_label`, `ALARM_STYLE`, `DIM`.
- Produces (Task 2 relies on these exact names):
  - `SPARK_LEVELS: str = "▁▂▃▄▅▆▇█"`, `SHAPE_GUTTER: str = "tension  "`, `WARN_STYLE: str = "yellow"`
  - `spark_char(tension: float) -> str`
  - `ShapeTab` fields, in order: `tensions: list[float]`, `spark: Text | None`, `markers: Text | None`, `meta: Text`, `callouts: list[Text]`, `alarm_count: int`

- [ ] **Step 1: Write the failing tests**

Append to the shape section of `tests/tui/test_brain_model.py` (after `test_shape_tab_score_for_unknown_chapter_keeps_its_data_at_the_end`), and extend two existing tests:

```python
from novelizer.tui.widgets.brain_model import SHAPE_GUTTER, SPARK_LEVELS, spark_char


def test_spark_char_maps_tension_onto_the_eight_block_levels():
    assert spark_char(0.0) == "▁"
    assert spark_char(1.0) == "█"
    assert spark_char(0.6) == "▅"
    assert spark_char(-3.0) == "▁"   # clamped low
    assert spark_char(9.0) == "█"    # clamped high


def test_shape_tab_spark_is_one_cell_per_chapter_after_the_gutter():
    chs = _chapters("One", "Two", "Three")
    scores = [
        StructureScore(chapter_id=f"c{i + 1}", tension=t, pacing_label="")
        for i, t in enumerate([0.2, 0.5, 0.9])
    ]
    tab = shape_tab(scores, chs)
    assert tab.spark.plain == SHAPE_GUTTER + "▂▅█"
    assert tab.markers is None                    # quiet story: no marker row


def test_shape_tab_marker_row_aligns_alarm_glyphs_under_flagged_chapters():
    chs = _chapters("One", "Two", "The Long Calm")
    scores = [
        StructureScore(chapter_id=f"c{i + 1}", tension=t, pacing_label="")
        for i, t in enumerate([0.6, 0.6, 0.1])    # c3 sags
    ]
    tab = shape_tab(scores, chs)
    assert tab.markers.plain == " " * len(SHAPE_GUTTER) + "  ⚠"
    marker_spans = [
        (tab.markers.plain[s.start:s.end], str(s.style)) for s in tab.markers.spans
    ]
    assert ("⚠", ALARM_STYLE) in marker_spans
```

Extend `test_shape_tab_empty_is_one_dim_line_no_data` with two lines:

```python
    assert tab.spark is None
    assert tab.markers is None
```

Extend the hypothesis property `test_shape_tab_keeps_every_point_and_alarm_count_matches_callouts` with one line:

```python
    assert len(tab.spark.plain) == len(SHAPE_GUTTER) + len(tensions)
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/tui/test_brain_model.py -q -W error`
Expected: FAIL — `ImportError: cannot import name 'SHAPE_GUTTER'`.

- [ ] **Step 3: Implement**

In `novelizer/tui/widgets/brain_model.py`, add below the `DIM = "dim"` line:

```python
WARN_STYLE = "yellow"

SPARK_LEVELS = "▁▂▃▄▅▆▇█"
SHAPE_GUTTER = "tension  "  # the marker row indents by this much to align under the spark


def spark_char(tension: float) -> str:
    """One block-glyph cell for one chapter's tension, clamped to [0, 1]."""
    clamped = min(max(tension, 0.0), 1.0)
    return SPARK_LEVELS[min(int(clamped * len(SPARK_LEVELS)), len(SPARK_LEVELS) - 1)]
```

Replace the `ShapeTab` dataclass:

```python
@dataclass(frozen=True)
class ShapeTab:
    tensions: list[float]   # chapter-order tension values (invariants/tests)
    spark: Text | None      # "tension  ▂▅█" — one cell per chapter; None when empty
    markers: Text | None    # "⚠" cells aligned under flagged chapters; None when no flags
    meta: Text              # axis + pacing line, or the dim empty state
    callouts: list[Text]    # one ALARM_STYLE line per sag/spike, chapter order
    alarm_count: int
```

In `shape_tab`, change the empty return to:

```python
        return ShapeTab([], None, None, Text(SHAPE_EMPTY, style=DIM), [], 0)
```

and, after the existing `flags = detect_sag_spike(scores, delta)` line, build the two rows and thread them into the return:

```python
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
```

Final return becomes:

```python
    return ShapeTab(tensions, spark, markers, meta, callouts, len(callouts))
```

(The existing `callouts`/`axis`/`meta` code is unchanged.)

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/tui/test_brain_model.py -q -W error`
Expected: PASS (panel/layout tests are touched in Task 2 — do not run `tests/tui` wholesale yet).

- [ ] **Step 5: Amend the spec with the locked decision**

In `docs/superpowers/specs/2026-07-19-story-brain-polish-design.md`, Section 3, replace the sentence `Compact keeps the `Sparkline` and gains a marker row beneath it —` with:

```markdown
Compact replaces the `Sparkline` *widget* with a model-rendered text spark — one
block cell per chapter, so the marker row beneath it aligns exactly (the widget
scales data to its width, which makes per-chapter alignment impossible; the
scaled full-width chart lives in P2's zoom mode) —
```

- [ ] **Step 6: Commit**

```bash
git add novelizer/tui/widgets/brain_model.py tests/tui/test_brain_model.py docs/superpowers/specs/2026-07-19-story-brain-polish-design.md
git commit -m "feat(brain): shape tab renders text spark with aligned sag/spike markers"
```

---

### Task 2: Shape panel — remove the Sparkline widget

**Files:**
- Modify: `novelizer/tui/widgets/brain_panel.py`
- Modify: `novelizer/tui/app.tcss` (line 9: `#shape_spark { height: 1; }`)
- Test: `tests/tui/test_brain_panel.py`, `tests/tui/test_app_layout.py`

**Interfaces:**
- Consumes: Task 1's `ShapeTab` (`spark: Text | None`, `markers: Text | None`).
- Produces: `#shape_body` now carries the entire Shape tab (spark, markers, meta, callouts); `#shape_spark` no longer exists. No other widget id changes.

- [ ] **Step 1: Update the two integration tests to the new surface (failing first)**

In `tests/tui/test_brain_panel.py`, `test_fresh_story_shows_designed_empty_states_and_quiet_strip`:
- change the import line `from textual.widgets import Sparkline, Static` to `from textual.widgets import Static`
- replace `assert not app.query_one("#shape_spark", Sparkline).display` with:

```python
            assert not app.query("#shape_spark")   # the widget is gone entirely
```

In `tests/tui/test_app_layout.py`, `test_story_brain_threads_and_shape_tabs_populate`:
- change `from textual.widgets import Sparkline, Static` to `from textual.widgets import Static`
- delete the line `spark = app.query_one("#shape_spark", Sparkline)`
- replace `assert list(spark.data) == [0.6] and spark.display` with:

```python
            assert shape_text.splitlines()[0] == "tension  ▅"   # 0.6 → level 4 of 8
```

- [ ] **Step 2: Run to verify the new assertions fail**

Run: `uv run pytest tests/tui/test_brain_panel.py tests/tui/test_app_layout.py -q -W error`
Expected: FAIL — `#shape_spark` still exists / `shape_text` first line is not the spark.

- [ ] **Step 3: Implement the panel change**

In `novelizer/tui/widgets/brain_panel.py`:

- Change the widgets import to drop `Sparkline`:

```python
from textual.widgets import Static, TabbedContent, TabPane
```

- In `compose`, delete the line `yield Sparkline([], id="shape_spark")`.
- In `refresh_from`, delete the three `spark` lines (`spark = self.query_one(...)`, `spark.display = ...`, `spark.data = ...`) and replace the `#shape_body` update with:

```python
        shape_rows = [r for r in (shape.spark, shape.markers, shape.meta) if r is not None]
        self.query_one("#shape_body", Static).update(_joined([*shape_rows, *shape.callouts]))
```

- Update the module docstring's first paragraph to say the spark is model-rendered text (delete any mention of `Sparkline`).

In `novelizer/tui/app.tcss`, delete line 9: `#shape_spark { height: 1; }`.

- [ ] **Step 4: Run to verify pass**

Run: `uv run pytest tests/tui/test_brain_panel.py tests/tui/test_app_layout.py tests/tui/test_brain_model.py -q -W error`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add novelizer/tui/widgets/brain_panel.py novelizer/tui/app.tcss tests/tui/test_brain_panel.py tests/tui/test_app_layout.py
git commit -m "feat(brain): shape tab is fully model-rendered; Sparkline widget removed"
```

---

### Task 3: Threads — age heat bars

**Files:**
- Modify: `novelizer/tui/widgets/brain_model.py` (`thread_line`, new `age_bar`, new constants)
- Test: `tests/tui/test_brain_model.py`

**Interfaces:**
- Consumes: existing `is_thread_stale`, `chapters_elapsed_since`, `chapter_number`, `_clip_title`, `WARN_STYLE` (Task 1), `TERMINAL_STATES`.
- Produces:
  - `AGE_BAR_CELLS: int = 5`, `NAME_WIDTH: int = 20`, `WARN_FRACTION: float = 0.6`
  - `age_bar(elapsed: int, threshold: int) -> Text`
  - `thread_line` row format (P2's selection models reuse it):
    - stale: `"⚠ " + name.ljust(20) + "  <bar>  stale — last touched ch N, K chapters ago"` (whole line `ALARM_STYLE`; `"stale — untouched for K chapters"` when the chapter is unknown)
    - live: `"· " + name.ljust(20) + "  <bar>  <state>"` plus `" — ch N"` when known (bar span styled `DIM`/`WARN_STYLE`)
    - terminal: unchanged `"✓ {name} · {state}"`, dim
  - `threads_tab` grouping/fold behavior unchanged.

- [ ] **Step 1: Write the failing tests**

In `tests/tui/test_brain_model.py`, add after the shape tests:

```python
from novelizer.tui.widgets.brain_model import WARN_STYLE, age_bar


def test_age_bar_scales_fill_and_heat_with_elapsed_over_threshold():
    assert age_bar(0, 3).plain == "▱▱▱▱▱"
    assert str(age_bar(0, 3).style) == "dim"
    assert age_bar(1, 3).plain == "▰▰▱▱▱"          # round(1/3 · 5) = 2
    assert age_bar(2, 3).plain == "▰▰▰▱▱"          # round(2/3 · 5) = 3
    assert str(age_bar(2, 3).style) == WARN_STYLE  # 2/3 ≥ 0.6: warming
    assert age_bar(3, 3).plain == "▰▰▰▰▰"
    assert str(age_bar(3, 3).style) == ALARM_STYLE
    assert age_bar(9, 3).plain == "▰▰▰▰▰"          # clamped past threshold
    assert age_bar(0, 0).plain == "▱▱▱▱▱"          # degenerate threshold guarded
```

Rewrite these existing tests' expectations (same names, new bodies):

```python
def test_thread_line_stale_names_last_touched_chapter_and_gap():
    chs = _chapters("One", "Two", "Three", "Four", "Five")
    t = ThreadRecord(id="the-locket", name="The Locket", state=ThreadState.planted, last_chapter_id="c1")
    line = thread_line(t, chs)
    assert line.plain == (
        "⚠ " + "The Locket".ljust(20) + "  ▰▰▰▰▰  stale — last touched ch 1, 4 chapters ago"
    )
    assert str(line.style) == ALARM_STYLE
    assert "the-locket" not in line.plain


def test_thread_line_stale_with_no_known_chapter_reads_untouched():
    chs = _chapters("One", "Two", "Three")
    t = ThreadRecord(id="t", name="The Boy's Gift", state=ThreadState.planted, last_chapter_id="")
    line = thread_line(t, chs)
    assert line.plain == (
        "⚠ " + "The Boy's Gift".ljust(20) + "  ▰▰▰▰▰  stale — untouched for 3 chapters"
    )


def test_thread_line_live_shows_name_bar_state_and_chapter_no_id():
    chs = _chapters("One")
    t = ThreadRecord(id="t", name="Fresh", state=ThreadState.touched, last_chapter_id="c1")
    line = thread_line(t, chs)
    assert line.plain == "· " + "Fresh".ljust(20) + "  ▱▱▱▱▱  touched — ch 1"
    bar_spans = [(line.plain[s.start:s.end], str(s.style)) for s in line.spans]
    assert ("▱▱▱▱▱", "dim") in bar_spans
```

Add one new test for the warming band and the long-name clip:

```python
def test_thread_line_live_warming_bar_is_warn_styled_and_long_names_clip():
    chs = _chapters("One", "Two", "Three")          # elapsed since c1 = 2, threshold 3
    t = ThreadRecord(id="t", name="The Unraveling of Everything",
                     state=ThreadState.touched, last_chapter_id="c1")
    line = thread_line(t, chs)
    assert line.plain.startswith("· The Unraveling of E…")   # clipped to NAME_WIDTH
    bar_spans = [(line.plain[s.start:s.end], str(s.style)) for s in line.spans]
    assert ("▰▰▰▱▱", WARN_STYLE) in bar_spans
```

Update the two `threads_tab` expectation tests to the new row format:

- `test_threads_tab_pins_stale_first_then_open_then_folds_terminal`: replace the two row assertions with

```python
    assert plains[0] == "⚠ " + "Stale C".ljust(20) + "  ▰▰▰▰▰  stale — last touched ch 1, 4 chapters ago"
    assert plains[1] == "· " + "Open A".ljust(20) + "  ▱▱▱▱▱  touched — ch 5"
```

- `test_thread_line_and_threads_tab_respect_explicit_threshold`: replace the final assertion with

```python
    assert tab.lines[0].plain == "⚠ " + "T".ljust(20) + "  ▰▰▰▰▰  stale — last touched ch 1, 2 chapters ago"
```

(`test_thread_line_terminal_is_dim_and_never_stale`, the fold-line test, the empty-state test, and the hypothesis property need no changes — terminal lines, fold line, and `⚠`-prefix ordering are untouched.)

- [ ] **Step 2: Run to verify failure**

Run: `uv run pytest tests/tui/test_brain_model.py -q -W error`
Expected: FAIL — `ImportError: cannot import name 'age_bar'`.

- [ ] **Step 3: Implement**

In `novelizer/tui/widgets/brain_model.py`, add above `thread_line`:

```python
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
```

Replace `thread_line` entirely:

```python
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
```

- [ ] **Step 4: Run to verify pass**

Run: `uv run pytest tests/tui/test_brain_model.py -q -W error`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add novelizer/tui/widgets/brain_model.py tests/tui/test_brain_model.py
git commit -m "feat(brain): thread rows carry age heat bars — staleness as a gradient"
```

---

### Task 4: Secrets — spread meters

**Files:**
- Modify: `novelizer/tui/widgets/brain_model.py` (`secret_row`, new `spread_meter`)
- Test: `tests/tui/test_brain_model.py`

**Interfaces:**
- Consumes: `knowledge_cell_state`, `WARN_STYLE`, `ALARM_STYLE`, `DIM`.
- Produces:
  - `spread_meter(known: int, total: int) -> Text` — `"●●○ 2/3"`; `ALARM_STYLE` when ≤1 character is left in the dark (the same leak-proximity signal P3's Pulse card will reuse), `WARN_STYLE` when ≥ half know, else `DIM`.
  - `secret_row` format: `title.ljust(TITLE_WIDTH) + cells + "   " + meter` (meter omitted when there are no characters). The `"N know"` / `"no one knows"` text summaries are **gone**.

- [ ] **Step 1: Write the failing tests**

Add after the existing secrets tests in `tests/tui/test_brain_model.py`:

```python
from novelizer.tui.widgets.brain_model import spread_meter


def test_spread_meter_heats_as_spread_approaches_everyone():
    assert spread_meter(0, 4).plain == "○○○○ 0/4"
    assert str(spread_meter(0, 4).style) == "dim"
    assert spread_meter(2, 4).plain == "●●○○ 2/4"
    assert str(spread_meter(2, 4).style) == WARN_STYLE   # half know: warming
    assert spread_meter(3, 4).plain == "●●●○ 3/4"
    assert str(spread_meter(3, 4).style) == ALARM_STYLE  # one reveal from public
    assert str(spread_meter(4, 4).style) == ALARM_STYLE  # everyone knows
    assert str(spread_meter(1, 4).style) == "dim"        # 1/4: still quiet
    assert str(spread_meter(1, 1).style) == ALARM_STYLE  # the whole cast of one knows
```

Rewrite these existing tests' expectations (same names, new bodies):

```python
def test_secret_row_glyph_cells_align_under_header_and_show_spread_meter():
    chars = [Character(id="elara", name="Elara"), Character(id="boy", name="The Boy")]
    secret = SecretRecord(id="the-heir-lives", title="The Heir Lives")
    matrix = {"the-heir-lives": {"revealed": False, "known_by": {"elara"}}}
    row = secret_row(secret, chars, matrix)
    assert row.plain == "The Heir Lives".ljust(TITLE_WIDTH) + "●  ○" + "   ●○ 1/2"
    assert "the-heir-lives" not in row.plain


def test_secret_row_known_to_no_one_has_a_cold_meter():
    secret = SecretRecord(id="s", title="The Map Is Forged")
    matrix = {"s": {"revealed": False, "known_by": set()}}
    row = secret_row(secret, [Character(id="k", name="Kestrel")], matrix)
    assert row.plain.endswith("○ 0/1")
    meter_spans = [(row.plain[s.start:s.end], str(s.style)) for s in row.spans]
    assert ("○ 0/1", "dim") in meter_spans


def test_secret_row_two_of_three_know_is_leak_hot():
    chars = [Character(id="a", name="Ana"), Character(id="b", name="Bram"), Character(id="c", name="Cole")]
    secret = SecretRecord(id="s", title="The Tide Debt")
    matrix = {"s": {"revealed": False, "known_by": {"a", "b"}}}
    row = secret_row(secret, chars, matrix)
    assert row.plain.endswith("●●○ 2/3")
    meter_spans = [(row.plain[s.start:s.end], str(s.style)) for s in row.spans]
    assert ("●●○ 2/3", ALARM_STYLE) in meter_spans
```

(The old `test_secret_row_plural_summary_matches_spec_sketch` is replaced by the leak-hot test above — delete it. `test_secret_row_clips_long_titles` passes `characters=[]`, so no meter is appended and it needs no change.)

Update the hypothesis property `test_matrix_rows_cover_every_secret_by_character_pair`'s final loop — meter glyphs now double the count when characters exist:

```python
    for row in rows:
        cells = row.plain.count("○") + row.plain.count("●")
        assert cells == (2 * n_chars if n_chars else 0)   # matrix cells + meter cells
        if n_chars:
            assert row.plain.endswith(f"0/{n_chars}")
```

- [ ] **Step 2: Run to verify failure**

Run: `uv run pytest tests/tui/test_brain_model.py -q -W error`
Expected: FAIL — `ImportError: cannot import name 'spread_meter'`.

- [ ] **Step 3: Implement**

In `novelizer/tui/widgets/brain_model.py`, add above `secret_row`:

```python
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
```

In `secret_row`, replace everything from `if known == 0:` through `row.append(f"   {summary}", style=DIM)` with:

```python
    if characters:
        meter = spread_meter(known, len(characters))
        row.append("   ")
        row.append(meter.plain, style=meter.style)
```

and update `secret_row`'s docstring: the row ends in a spread meter, not a who-knows summary.

- [ ] **Step 4: Run to verify pass**

Run: `uv run pytest tests/tui/test_brain_model.py -q -W error`
Expected: PASS.

- [ ] **Step 5: Update the one integration assertion that mentioned the old summary**

`tests/tui/test_app_layout.py`, `test_story_brain_secrets_matrix_and_causeway_tabs_populate`: replace `assert "●" in secrets_text and "1 knows" in secrets_text` with:

```python
            assert "●" in secrets_text and "1/1" in secrets_text
```

Run: `uv run pytest tests/tui/test_app_layout.py -q -W error`
Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add novelizer/tui/widgets/brain_model.py tests/tui/test_brain_model.py tests/tui/test_app_layout.py
git commit -m "feat(brain): secret rows end in a heat-colored spread meter"
```

---

### Task 5: Cause — paradox-first sort, dim arrows

**Files:**
- Modify: `novelizer/tui/widgets/brain_model.py` (`causeway_tab`)
- Test: `tests/tui/test_brain_model.py`

**Interfaces:**
- Consumes: `find_paradoxes`, `chapter_label`, `ALARM_STYLE`, `DIM`.
- Produces: `causeway_tab` line *plain text* is unchanged (every existing plain-string assertion keeps passing); ordering now puts paradox edges first; non-paradox lines carry a `DIM` span over the `──▶` arrow.

- [ ] **Step 1: Write the failing tests**

Add after the existing causeway tests:

```python
def test_causeway_paradoxes_sort_above_normal_edges():
    chs = _chapters("One", "Two", "Three")
    edges = [
        CausalEdgeRecord(cause_chapter_id="c1", effect_chapter_id="c2"),   # normal, earliest
        CausalEdgeRecord(cause_chapter_id="c3", effect_chapter_id="c2"),   # paradox, latest
    ]
    tab = causeway_tab(edges, chs)
    assert "⚠ PARADOX" in tab.lines[0].plain
    assert tab.lines[0].plain.startswith('ch 3 "Three"')
    assert tab.lines[1].plain.startswith('ch 1 "One"')


def test_causeway_normal_edge_arrow_is_dim():
    chs = _chapters("One", "Two")
    tab = causeway_tab([CausalEdgeRecord(cause_chapter_id="c1", effect_chapter_id="c2")], chs)
    spans = [(tab.lines[0].plain[s.start:s.end], str(s.style)) for s in tab.lines[0].spans]
    assert ("──▶", "dim") in spans
```

- [ ] **Step 2: Run to verify failure**

Run: `uv run pytest tests/tui/test_brain_model.py -q -W error`
Expected: FAIL — paradox edge sorts second; no dim span on the arrow.

- [ ] **Step 3: Implement**

In `causeway_tab`, add a paradox-first key ahead of the position keys in the existing `sorted` call:

```python
    ordered = sorted(
        edges,
        key=lambda e: (
            (e.cause_chapter_id, e.effect_chapter_id) not in paradox_pairs,
            pos.get(e.cause_chapter_id, len(order)),
            pos.get(e.effect_chapter_id, len(order)),
        ),
    )
```

Replace the loop body so normal lines are assembled with a dim arrow span (paradox lines unchanged):

```python
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
```

Update `causeway_tab`'s docstring: paradox edges sort first; normal arrows render dim.

- [ ] **Step 4: Run to verify pass**

Run: `uv run pytest tests/tui/test_brain_model.py -q -W error`
Expected: PASS — including every pre-existing causeway plain-string test, unchanged.

- [ ] **Step 5: Commit**

```bash
git add novelizer/tui/widgets/brain_model.py tests/tui/test_brain_model.py
git commit -m "feat(brain): causeway pins paradoxes first, dims routine arrows"
```

---

### Task 6: Full TUI suite verification

**Files:**
- None modified (verification only; fixes fold back into the owning task's files if anything surfaces).

**Interfaces:**
- Consumes: everything above.
- Produces: a green `tests/tui` suite — P1 done.

- [ ] **Step 1: Run the whole TUI suite**

Run: `uv run pytest tests/tui -q -W error`
Expected: PASS, no warnings-as-errors. If a test outside the four touched files fails, fix forward in the task that owns that surface and re-run.

- [ ] **Step 2: Run the brain-domain unit suites (guard against accidental coupling)**

Run: `uv run pytest tests/brain tests/canon -q -W error`
Expected: PASS untouched — P1 changed rendering only, never detection.

- [ ] **Step 3: Commit (only if fixes were needed), otherwise done**

```bash
git status --short   # expect: clean
```
