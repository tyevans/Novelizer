# M8b "Comparators & Outline Board" Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** The plan-vs-actual half of M8: `beat_drift` + `tension_target` Brain comparators, a target-curve overlay on the Shape tab, the Outline board Brain tab (threads × chapters grid with beat markers and marching briefs), and the `/outline/` virtual filesystem for agents — completing the M8 milestone (docs/MILESTONES.md).

**Architecture:** Two new pure Brain faculties compare authored targets (beat windows from the active blueprint; a target tension curve interpolated from beat positions/polarities) against actuals (chapter ordinals; StructureScore tensions), surfacing as prompt notes + alarms. The Outline board is a new Brain tab (pure model + thin widget, keyed `5`). `OutlineBackend` is a separate read-only `BackendProtocol` implementation mounted at `/outline/` via deepagents' `CompositeBackend` (routes strip the prefix before delegating and re-prepend it on results; `CanonBackend` stays untouched as the default route). Also lands the M8a re-review deferrals (stale-brief reaping via Plotter guidance, runway upper bound, docstring/style nits).

**Tech Stack:** Python 3.13, pydantic v2, Hypothesis, pytest asyncio_mode=auto, deepagents 0.6.12 (`CompositeBackend`), Textual pure-model widgets.

## Global Constraints

- Spec: `docs/superpowers/specs/2026-07-19-authoring-skills-blueprint-design.md` §"Story Brain — plan-vs-actual faculties", §"deepagents surface", §"TUI — Mission Control". Roadmap: docs/MILESTONES.md M8 row (M8b completes it).
- Brain faculties are pure functions over ReadStore data, never persisted; prompt notes return `""` when quiet; TUI reuses faculty decisions, never re-derives them.
- Windows/ordinals: 1-based; `now = len(chapters)`; beat windows via `beat_window(b.ideal_pct, b.tolerance_pct, blueprint.target_chapter_count)` (canon/beat_templates.py) — the single source, never re-derived.
- `OutlineBackend` mirrors `CanonBackend`'s structure (thin path router + pure renderers, writes refused with the intent-path message, async-first). CompositeBackend semantics (verified against installed deepagents 0.6.12): routes match longest-prefix-first; the routed backend receives the path WITH THE PREFIX STRIPPED (e.g. `/outline/beats.md` arrives as `/beats.md`) and the composite re-prepends the prefix on ls/glob/grep result paths; the DEFAULT backend receives original unstripped paths. OutlineBackend therefore builds a root-relative index (`/blueprint.md`, `/beats.md`, `/briefs/NNN-....md`, `/threads-plan.md`, `/ledger.md`).
- Wiring point: `Runtime._phase_a_toolkit` (runtime.py) currently returns `CanonBackend(self.read)`; it becomes `CompositeBackend(default=CanonBackend(self.read), routes={"/outline/": OutlineBackend(self.read)})`. Every tooled agent + chat inherits automatically. Import `CompositeBackend` from `deepagents.backends`.
- M8a deferrals to land here (from the final re-review): (a) Plotter readiness runway counts only briefs with `len < target_ordinal <= len + 3`; (b) stale open briefs (target_ordinal <= len(chapters)) are auto-superseded by the Plotter at commit time (mechanical reap, not LLM-dependent); (c) kishōtenketsu docstring wording in beat_templates.py ("never require conflict or an antagonist; polarity marks the recontextualizing turn, not a battle"); (d) `EventType.BLUEPRINT_ADOPTED` constant instead of the string literal in the Plotter's pending-proposal guard.
- **Run all tests in this worktree, NEVER the main checkout.** Synchronous only.

---

### Task 1: `beat_drift` faculty

**Files:**
- Create: `novelizer/brain/beat_drift.py`
- Test: `tests/brain/test_beat_drift.py`

**Interfaces:**

```python
@dataclass(frozen=True)
class BeatDrift:
    beat_id: str
    name: str
    window_lo: int
    window_hi: int
    kind: str            # "late" | "early" | "off_window"
    detail: str          # human line, e.g. "midpoint not fulfilled by ch 14 (window 9-11)"

def beat_drifts(blueprint, beats, chapters) -> list[BeatDrift]
    # blueprint None or beats empty -> []
    # For each beat (windows via beat_window):
    #   unfulfilled and now > window_hi            -> "late"
    #   fulfilled by chapter at ordinal < window_lo -> "early"
    #   fulfilled by chapter at ordinal > window_hi -> "off_window"
    #   fulfilled inside window, or unfulfilled with now <= window_hi -> no drift
    # Ordinal of the fulfilling chapter = its 1-based position in chapters; a
    # fulfilled_by_chapter_id not present in chapters -> treat as "off_window"
    # with detail noting the unknown chapter.

def next_expected_beat(blueprint, beats, chapters) -> BeatRecord | None
    # earliest unfulfilled beat by ideal_pct, or None
```

- [ ] **Step 1: failing tests** — direct-construction style (mirror tests/brain/test_ledger.py): no blueprint → `[]`; unfulfilled inside window → no drift; unfulfilled past window → late (detail contains window); fulfilled early/inside/late → early/none/off_window; unknown fulfilling chapter id → off_window; next_expected_beat ordering + None when all fulfilled.
- [ ] **Step 2: verify failure.** **Step 3: implement** (pure; import beat_window). **Step 4:** `uv run pytest tests/brain/test_beat_drift.py -q` PASS. **Step 5: commit** `feat(brain): beat-drift faculty`.

---

### Task 2: `tension_target` faculty

**Files:**
- Create: `novelizer/brain/tension_target.py`
- Test: `tests/brain/test_tension_target.py`

**Interfaces:**

```python
# Polarity anchors: a beat's expected_polarity implies a target tension level
# at its center chapter: "up" -> 0.75, "down" -> 0.35, "flip" -> 0.85, "" -> None
# (no anchor). The curve starts at (1, 0.3) and ends at (target_chapter_count,
# 0.5) unless a beat anchors those ordinals.

def target_curve(blueprint, beats) -> list[float]
    # length = blueprint.target_chapter_count; linear interpolation between
    # anchor points (implicit start/end + each polarity-bearing beat at
    # round(ideal_pct * n) clamped 1..n). blueprint None -> [].

def tension_deviations(blueprint, beats, scores, chapters, delta: float = 0.25) -> list[tuple[str, float, float]]
    # (chapter_id, actual, target) for scored chapters whose |actual - target| > delta,
    # comparing each chapter's StructureScore.tension (last-wins by chapter, in
    # chapter order — mirror shape_tab's ordering) against target_curve[ordinal-1].
    # Chapters beyond the target length compare against the curve's last value.
```

- `context.py` additions (empty-when-quiet):

```python
def beat_drift_note(blueprint, beats, chapters) -> str      # lists drifts, "Beat drift:" header
def tension_target_note(blueprint, beats, scores, chapters) -> str
    # only the worst deviation + the next expected beat's polarity guidance,
    # e.g. "Tension vs blueprint: ch 11 actual 0.9 vs target 0.5 — the midpoint
    # flip is planned for ch 9-11."
```

Wire both notes into the Plotter's `_summarize` (it owns re-planning) and `beat_drift_note` additionally into the Editor's prompt assembly (it judges chapters). NOT the Author (briefs already carry beats_to_hit — avoid double-steering).

- [ ] **Step 1: failing tests** — target_curve: no blueprint → []; six-position blueprint n=20 → length 20, monotonic rise into the flip anchor at ch 10 area, value at climax center ≈0.85 ± interpolation; anchors clamp; kishotenketsu (single flip anchor) still produces a full curve. tension_deviations: inside delta → []; outside → tuple with correct target; unscored chapters skipped; chapter beyond n uses last curve value. Note fns: quiet → ""; drift/deviation → pinned strings. Plotter/Editor prompt-inclusion tests (mirror the M7 ledger-note prompt tests).
- [ ] **Step 2: verify failure.** **Step 3: implement.** **Step 4:** `uv run pytest tests/brain/ tests/agents/ -q` PASS. **Step 5: commit** `feat(brain): tension-target faculty with prompt notes`.

---

### Task 3: Shape-tab target overlay

**Files:**
- Modify: `novelizer/tui/widgets/brain_model.py` (`shape_tab` gains `blueprint=None, beats=None` params; target row + deviation markers)
- Modify: `novelizer/tui/widgets/brain_panel.py` (`refresh_from` fetches blueprint/beats, passes through)
- Test: `tests/tui/test_brain_model.py` (append)

Behavior (binding):
- `shape_tab(scores, chapters, delta=..., blueprint=None, beats=None)`; `ShapeTab` dataclass gains `target: Text | None`.
- With an active blueprint: a second sparkline row built from `target_curve` (same `spark_char` mapping, DIM style, `SHAPE_GUTTER`-aligned, labeled `plan` in the gutter), truncated/padded to the actual sparkline's length for the drafted range; deviations from `tension_deviations` add `⚠ tension off-plan: <chapter label>` callouts (ALARM_STYLE) and count into `alarm_count`.
- No blueprint → `target=None`, output byte-identical to today (pinned).
- brain_panel: fetch `get_active_blueprint()` + `list_beats()` in `refresh_from`, pass to `shape_tab`; render `target` row directly under `spark` when present.

- [ ] **Step 1: failing tests** — no-blueprint byte-identity; with-blueprint target row present (plain text contains `plan`), deviation callout + alarm arithmetic; reuse (not re-derivation) is structural: monkeypatch-free, assert against faculty outputs.
- [ ] **Step 2: verify failure.** **Step 3: implement.** **Step 4:** `uv run pytest tests/tui/ -q` PASS. **Step 5: commit** `feat(tui): target tension overlay on the Shape tab`.

---

### Task 4: Outline board tab

**Files:**
- Modify: `novelizer/tui/widgets/brain_model.py` (new `outline_tab` + `OutlineTab` dataclass)
- Modify: `novelizer/tui/widgets/brain_panel.py` (5th TabPane `id="tab_outline"`, `#outline_body`, refresh wiring)
- Modify: `novelizer/tui/app.py` (binding `("5", "brain_tab('tab_outline')", "Outline")` — mirror bindings 1-4)
- Test: `tests/tui/test_brain_model.py`, `tests/tui/test_brain_panel.py` (or the panel's existing test home), app-binding test if bindings are tested (check `tests/tui/test_app_layout.py`-style files and mirror)

**Interfaces:**

```python
@dataclass(frozen=True)
class OutlineTab:
    lines: list[Text]
    alarm_count: int

def outline_tab(blueprint, beats, briefs, threads, chapters) -> OutlineTab
```

Board behavior (binding):
- No blueprint → single dim line `"No blueprint adopted — the Plotter will propose one."`, alarm 0.
- Header block: `framework · target N ch · genre` then a beat strip: one line per beat `name @ch lo-hi` with status glyph (`✓` fulfilled inside window, `≈` fulfilled off-window, `!` late (ALARM_STYLE), `·` pending) — status from `beat_drifts`/fulfillment, reused not re-derived.
- Grid: one row per non-terminal thread (NAME_WIDTH-padded name), columns = chapter ordinals 1..max(len(chapters), highest open brief ordinal); cell glyphs from thread history: `●` planted (thread.last events aren't per-chapter — use what the ThreadRecord carries: mark the planted/last-touch columns only via `last_chapter_id` ordinal, `◆` on the payoff chapter for terminal threads if its ordinal is known; else leave `·`); planned-resolution window rendered as `░` across its column span; columns beyond `len(chapters)` (the future) render DIM. Keep it honest: the model has coarse per-thread column data (last touch only) — the docstring must say so ("per-chapter touch history is an M9+ refinement; this board shows current state, windows, and the future runway").
- Briefs strip below the grid: one line per open brief `ch N: goal` (DIM for future), `!` ALARM_STYLE if `target_ordinal <= len(chapters)` (stale — should have been reaped).
- `alarm_count` = late beats + stale open briefs.

- [ ] **Step 1: failing tests** — empty state; header + beat strip statuses (one of each glyph); grid row count = non-terminal threads; window span glyphs; future columns dim; briefs strip with stale alarm; alarm arithmetic; panel wiring test (tab exists, body populated); binding test if precedent exists.
- [ ] **Step 2: verify failure.** **Step 3: implement.** **Step 4:** `uv run pytest tests/tui/ -q` PASS. **Step 5: commit** `feat(tui): Outline board — Brain tab 5`.

---

### Task 5: `OutlineBackend` + composite wiring

**Files:**
- Create: `novelizer/canon_fs/outline.py` (backend + path index)
- Create: `novelizer/canon_fs/outline_render.py` (pure renderers)
- Modify: `novelizer/runtime.py` (`_phase_a_toolkit` returns the CompositeBackend)
- Test: `tests/canon_fs/test_outline_backend.py`, `tests/canon_fs/test_outline_render.py`, `tests/test_runtime.py` (toolkit shape test)

**Interfaces:**
- Renderers (pure, frontmatter style mirrors canon_fs/render.py `_frontmatter`):

```python
def render_blueprint(blueprint, beats) -> str        # fm: id, kind: blueprint, framework, target_chapter_count; body: genre, obligatory scenes, beat table with windows+status
def render_beats(blueprint, beats, chapters) -> str  # fm: kind: beats; body: one line per beat with window, polarity, fulfilled-by
def render_brief(brief) -> str                       # fm: id, kind: chapter_brief, target_ordinal, status; body: goal/pov/threads/beats/promises/value shift/outcome/synopsis
def render_threads_plan(threads, chapters) -> str    # fm: kind: threads_plan; body: per non-terminal thread: state, window or '—', planned payoff note
def render_ledger(promises, chapters) -> str         # fm: kind: ledger; body: open promises w/ windows + overdue flag, then paid/released counts
```

- `OutlineBackend(read_store)` implementing the same `BackendProtocol` surface as `CanonBackend` (read the class: als/aread/aglob/agrep async-first, sync raise NotImplementedError, writes refused with the canonical intent-path message — reuse/mirror its error strings verbatim). Root-relative tree (composite strips `/outline/`):

```
/blueprint.md
/beats.md
/threads-plan.md
/ledger.md
/briefs/NNN-<slug-of-goal>.md     # NNN = target_ordinal zero-padded 3; open briefs only
```

  Empty story / no blueprint: files still exist with "No blueprint adopted." bodies (ls never errors).
- `Runtime._phase_a_toolkit`:

```python
from deepagents.backends import CompositeBackend
from novelizer.canon_fs.outline import OutlineBackend
backend = CompositeBackend(
    default=CanonBackend(self.read),
    routes={"/outline/": OutlineBackend(self.read)},
)
```

- [ ] **Step 1: failing tests** — renderers: pure unit tests (frontmatter keys, body content, windows, no-blueprint fallback). Backend over a seeded ReadStore (mirror tests/canon_fs/test_backend.py's fixture): als("/") lists the four files + briefs dir; aread("/blueprint.md") returns rendered content with frontmatter id; aread("/briefs/004-*.md") via aglob; write refused with the canonical message; agrep finds a goal string. Composite integration: build the real `CompositeBackend` as `_phase_a_toolkit` will and assert `await composite.aread("/outline/blueprint.md")` succeeds AND `await composite.aread("/chapters/001-*.md"-style canon path)` still routes to CanonBackend (pick an exact seeded path); ls("/outline") results carry the `/outline/` prefix re-prepended. Runtime test: `_phase_a_toolkit` returns a CompositeBackend whose routes contain "/outline/".
- [ ] **Step 2: verify failure.** **Step 3: implement.** **Step 4:** `uv run pytest tests/canon_fs/ tests/test_runtime.py -q` PASS. **Step 5: commit** `feat(canon_fs): /outline/ virtual filesystem via CompositeBackend`.

---

### Task 6: M8a deferrals — Plotter runway bound + stale-brief reaping + nits

**Files:**
- Modify: `novelizer/agents/plotter.py` (readiness runway; commit-time reap; EventType constant)
- Modify: `novelizer/canon/beat_templates.py` (docstring wording)
- Test: `tests/agents/test_plotter.py` (append)

Behavior (binding):
- `readiness()`: `open_briefs_ahead` counts only briefs with `len(chapters) < target_ordinal <= len(chapters) + 3`.
- `commit()`: BEFORE committing the LLM's intents, mechanically supersede stale open briefs (`target_ordinal <= len(ctx["chapters"])`) via the committer (CHAPTER_BRIEF_SUPERSEDED, superseded_by="") with an info log — deterministic reap, not LLM-dependent. Use ctx's open-brief list.
- Pending-proposal guard compares `EventType.BLUEPRINT_ADOPTED` (constant).
- beat_templates.py module docstring: replace the "must never require ... non-empty polarity" phrasing with "must never require conflict or an antagonist; a polarity mark on the turn denotes recontextualization, not battle."

- [ ] **Step 1: failing tests** — readiness ignores a far-future brief (ordinal 40 at 3 chapters → doesn't count toward runway); run_once with a stale open brief (ordinal <= len) in ctx → CHAPTER_BRIEF_SUPERSEDED committed for it even when the LLM output has no intents.
- [ ] **Step 2: verify failure.** **Step 3: implement.** **Step 4:** `uv run pytest tests/agents/test_plotter.py tests/agents/ tests/canon/ -q` PASS. **Step 5: commit** `fix(plotter): runway bound + stale-brief reaping + review nits`.

---

### Task 7: Docs + full-suite gate

**Files:** `docs/MILESTONES.md` (M8 row → `✅ complete (checker prose-mining of promises + search_canon promise/brief kinds remain deferred — tracked for M9/M10)`), `docs/QUICKSTART.md` (extend §10: Outline board on key `5`, the plan-vs-actual overlay on Shape, `/outline/` files agents can read).

- [ ] **Step 1:** doc edits. **Step 2:** `uv run pytest -q` full suite green. **Step 3: commit** `docs: M8 complete — comparators, Outline board, /outline/ filesystem`.
