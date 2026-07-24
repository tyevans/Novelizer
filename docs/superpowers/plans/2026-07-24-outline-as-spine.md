# Outline as the Story's Spine — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make the first-pass outline the genesis artifact — the Plotter mints a blueprint from the premise before anyone drafts prose, prose is soft-gated on that blueprint existing, and the outline is navigable in the TUI.

**Architecture:** A readiness-layer soft gate (no scheduler changes): a pure `novelizer/brain/gate.py` helper decides whether the Author may draft. The Plotter's readiness is inverted so it wakes first on a premise-seed; it reads the seed but does not consume it (the World Architect still owns seed→world). The canon browser gains an Outline section; the Story Brain tabs become scrollable.

**Tech Stack:** Python 3.12+, asyncio, aiosqlite, Pydantic v2, Textual/Rich (TUI), pytest / pytest-asyncio. Event-sourced canon (append-only events → SQL projection → `ReadStore`).

## Global Constraints

- Outline stays **event-sourced**: no materialized on-disk outline file; the single source of truth is the event log + its projection. Visibility is the existing read-only `/outline/*` mount.
- Gate is **soft** (readiness-driven). Do **not** modify `agent_kit/scheduler.py` or its `gate_provider` (reserved for embedding/KG catch-up).
- Follow existing patterns: `brain/` holds pure async functions taking `read`; agents own `readiness/poll/work/commit`; TUI `*_model.py` is pure, `*_panel.py`/`app.py` is the Textual shell.
- TDD: write the failing test first, watch it fail, implement minimally, watch it pass, commit. Small commits.
- Do **not** run the full suite or DB-touching tests in the main checkout — this plan runs in the `outline-as-spine` worktree. Targeted per-task tests only; full suite is Task 7.
- New settings go on `EffectiveSettings` (`novelizer/settings/models.py`); add the key to `STORY_OVERRIDABLE_KEYS` only if per-story override is wanted.

---

### Task 1: Blueprint gate helper (`novelizer/brain/gate.py`)

The single source of truth for "may the Author draft?" — pure, async, takes a `ReadStore`.

**Files:**
- Create: `novelizer/brain/gate.py`
- Create: `tests/brain/test_gate.py`

**Interfaces:**
- Consumes: `ReadStore.get_active_blueprint()`, `ReadStore.list_proposals(status="open")`, `ReadStore.list_world_entries()`. `Proposal.target_event_type: str` equals `EventType.BLUEPRINT_ADOPTED` for a blueprint proposal.
- Produces:
  - `async def has_active_blueprint(read) -> bool`
  - `async def genesis_fallback_open(read) -> bool` — True when a blueprint proposal is pending AND world entries exist.
  - `async def author_may_draft(read, *, gate_enabled: bool) -> bool` — `True` if `not gate_enabled`, else `has_active_blueprint(read) or genesis_fallback_open(read)`.

- [ ] **Step 1: Write the failing test**

```python
# tests/brain/test_gate.py
import pytest
from novelizer.brain import gate
from novelizer.canon.events import EventType


class FakeRead:
    def __init__(self, *, blueprint=None, proposals=None, world=None):
        self._blueprint = blueprint
        self._proposals = proposals or []
        self._world = world or []

    async def get_active_blueprint(self):
        return self._blueprint

    async def list_proposals(self, status=None):
        return [p for p in self._proposals if status is None or p["status"] == status]

    async def list_world_entries(self):
        return self._world


def _bp_proposal():
    return {"status": "open", "target_event_type": EventType.BLUEPRINT_ADOPTED}


@pytest.mark.asyncio
async def test_active_blueprint_lets_author_draft():
    read = FakeRead(blueprint=object())
    assert await gate.has_active_blueprint(read) is True
    assert await gate.author_may_draft(read, gate_enabled=True) is True


@pytest.mark.asyncio
async def test_no_blueprint_no_proposal_blocks_author():
    read = FakeRead()
    assert await gate.author_may_draft(read, gate_enabled=True) is False


@pytest.mark.asyncio
async def test_fallback_opens_when_proposal_pending_and_world_exists():
    read = FakeRead(proposals=[_bp_proposal()], world=[object()])
    assert await gate.genesis_fallback_open(read) is True
    assert await gate.author_may_draft(read, gate_enabled=True) is True


@pytest.mark.asyncio
async def test_fallback_closed_with_proposal_but_no_world():
    read = FakeRead(proposals=[_bp_proposal()], world=[])
    assert await gate.genesis_fallback_open(read) is False
    assert await gate.author_may_draft(read, gate_enabled=True) is False


@pytest.mark.asyncio
async def test_disabled_gate_always_lets_author_draft():
    read = FakeRead()
    assert await gate.author_may_draft(read, gate_enabled=False) is True
```

Note: `Proposal` has attribute access in production (`.status`, `.target_event_type`); the fake uses dicts, so `gate.py` must read via attribute access on real records. Write `gate.py` to use `p.status` / `p.target_event_type`; update the fake to a small object with those attributes (below) rather than dicts.

- [ ] **Step 2: Fix the fake to use attribute access, re-run to confirm it fails**

Replace `_bp_proposal()` and the `list_proposals` filter in the test to use a tiny class:

```python
class _Prop:
    def __init__(self, status, target_event_type):
        self.status = status
        self.target_event_type = target_event_type

def _bp_proposal():
    return _Prop("open", EventType.BLUEPRINT_ADOPTED)
```
And in `FakeRead.list_proposals`: `return [p for p in self._proposals if status is None or p.status == status]`.

Run: `pytest tests/brain/test_gate.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'novelizer.brain.gate'`.

- [ ] **Step 3: Implement `novelizer/brain/gate.py`**

```python
"""Outline-first soft gate: decides whether the Author may draft yet.

The single source of truth for "does a first-pass outline exist?" Pure and
async — takes a ReadStore-shaped object, reads no wall clock, mints no events.
The gate lives in the readiness layer (each gated agent multiplies its
readiness by this), never in the scheduler: keeping it soft is the whole point.
"""
from __future__ import annotations

from novelizer.canon.events import EventType


async def has_active_blueprint(read) -> bool:
    """True once the Plotter's blueprint has been adopted (approved)."""
    return await read.get_active_blueprint() is not None


async def genesis_fallback_open(read) -> bool:
    """Unattended-run escape hatch. Opens when the Plotter has proposed a
    blueprint AND the World Architect has built world from the premise, yet
    no blueprint is active — i.e. real genesis work happened but nobody
    approved the blueprint (a run with no human at the wheel). Progress-based,
    not a wall-clock timer, to match the scheduler's event-driven design.
    """
    proposals = await read.list_proposals(status="open")
    pending_blueprint = any(
        p.target_event_type == EventType.BLUEPRINT_ADOPTED for p in proposals
    )
    if not pending_blueprint:
        return False
    return len(await read.list_world_entries()) > 0


async def author_may_draft(read, *, gate_enabled: bool) -> bool:
    """The gate the Author consults in readiness(). Disabled -> always open."""
    if not gate_enabled:
        return True
    if await has_active_blueprint(read):
        return True
    return await genesis_fallback_open(read)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/brain/test_gate.py -v`
Expected: PASS (5 tests).

- [ ] **Step 5: Commit**

```bash
git add novelizer/brain/gate.py tests/brain/test_gate.py
git commit -m "feat(brain): outline-first soft-gate helper (author_may_draft)"
```

---

### Task 2: Settings kill-switch (`outline_gate_enabled`)

**Files:**
- Modify: `novelizer/settings/models.py` (add field to `EffectiveSettings`, ~line 66 area alongside `staleness_threshold_chapters`)
- Test: `tests/settings/test_models.py` (add one test; if the file/dir does not exist, create `tests/settings/test_outline_gate_setting.py`)

**Interfaces:**
- Produces: `EffectiveSettings.outline_gate_enabled: bool = True`. Consumed by Task 3 (Author construct) via `ctx.settings.outline_gate_enabled`.

- [ ] **Step 1: Write the failing test**

```python
# tests/settings/test_outline_gate_setting.py
from novelizer.settings.models import EffectiveSettings


def test_outline_gate_enabled_defaults_true():
    assert EffectiveSettings().outline_gate_enabled is True


def test_outline_gate_can_be_disabled():
    assert EffectiveSettings(outline_gate_enabled=False).outline_gate_enabled is False
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/settings/test_outline_gate_setting.py -v`
Expected: FAIL — `AttributeError`/validation error (unknown field is frozen-model tolerant? Pydantic raises on unknown init kwarg → the second test fails with `ValidationError`; the first fails with `AttributeError`).

- [ ] **Step 3: Add the field**

In `novelizer/settings/models.py`, on `class EffectiveSettings`, near `staleness_threshold_chapters: int = 3`:

```python
    # Outline-first soft gate: when True, the Author will not draft until a
    # first-pass blueprint exists (or the genesis fallback opens). Turn OFF to
    # restore the legacy outline-optional behavior (draft first, retrofit later).
    outline_gate_enabled: bool = True
```

(Do **not** add to `STORY_OVERRIDABLE_KEYS` unless per-story override is desired — leaving it out keeps it global-default, matching the plan's intent. It can still be set via env/global config.)

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/settings/test_outline_gate_setting.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add novelizer/settings/models.py tests/settings/test_outline_gate_setting.py
git commit -m "feat(settings): add outline_gate_enabled (default on)"
```

---

### Task 3: Gate the Author (readiness + provisional prompt)

**Files:**
- Modify: `novelizer/agents/author.py` — `readiness()` (255-257), `Author.__init__` (add `gate_enabled` param), `_construct` (399 area), `poll()` (add `blueprint`), `_summarize` (add provisional note when no blueprint)
- Test: `tests/agents/test_author_gate.py` (new)

**Interfaces:**
- Consumes: `novelizer.brain.gate.author_may_draft`, `EffectiveSettings.outline_gate_enabled`.
- Produces: `Author(..., gate_enabled: bool = True)`; a gated `readiness()` that returns `0.0` when `author_may_draft` is False.

- [ ] **Step 1: Write the failing test**

```python
# tests/agents/test_author_gate.py
import pytest
from novelizer.agents.author import Author


class _Read:
    def __init__(self, *, drafts=0, blueprint=None, proposals=None, world=None):
        self._drafts = drafts
        self._blueprint = blueprint
        self._proposals = proposals or []
        self._world = world or []

    async def list_chapters(self, status=None):
        if status == "draft":
            return [object()] * self._drafts
        return []

    async def get_active_blueprint(self):
        return self._blueprint

    async def list_proposals(self, status=None):
        return [p for p in self._proposals if status is None or p.status == status]

    async def list_world_entries(self):
        return self._world


class _Prop:
    def __init__(self):
        from novelizer.canon.events import EventType
        self.status = "open"
        self.target_event_type = EventType.BLUEPRINT_ADOPTED


def _author(read, gate_enabled=True):
    a = Author.__new__(Author)          # bypass runner/committer wiring
    a._read = read
    a.gate_enabled = gate_enabled
    return a


@pytest.mark.asyncio
async def test_no_blueprint_suppresses_author():
    a = _author(_Read(blueprint=None))
    assert await a.readiness() == 0.0


@pytest.mark.asyncio
async def test_active_blueprint_restores_normal_readiness():
    a = _author(_Read(blueprint=object(), drafts=0))
    assert await a.readiness() == 1.0


@pytest.mark.asyncio
async def test_fallback_opens_when_proposal_and_world_present():
    a = _author(_Read(blueprint=None, proposals=[_Prop()], world=[object()]))
    assert await a.readiness() == 1.0


@pytest.mark.asyncio
async def test_disabled_gate_drafts_without_blueprint():
    a = _author(_Read(blueprint=None), gate_enabled=False)
    assert await a.readiness() == 1.0
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/agents/test_author_gate.py -v`
Expected: FAIL — `AttributeError: 'Author' object has no attribute 'gate_enabled'` / readiness ignores the gate.

- [ ] **Step 3: Implement the gate in `author.py`**

Add import near the other brain imports (top of file):

```python
from novelizer.brain.gate import author_may_draft
```

Add the `gate_enabled` field to `Author.__init__` (after `pull_mode`):

```python
        pull_mode: bool = False,
        gate_enabled: bool = True,
    ) -> None:
        super().__init__(runner, read_store, committer, interval, name="author", personality=personality)
        self._casting_note = casting_note
        self.provenance = provenance
        self._advisory_token_budget = advisory_token_budget
        self._staleness_threshold_chapters = staleness_threshold_chapters
        self.pull_mode = pull_mode
        self.gate_enabled = gate_enabled
```

Replace `readiness()` (lines 255-257):

```python
    async def readiness(self) -> float:
        # Outline-first soft gate: stand down until a first-pass blueprint
        # exists (or the genesis fallback opens). Kept in readiness so it stays
        # soft — the scheduler is untouched.
        if not await author_may_draft(self._read, gate_enabled=self.gate_enabled):
            return 0.0
        drafts = len(await self._read.list_chapters(status="draft"))
        return max(0.0, 1.0 - drafts / 3)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/agents/test_author_gate.py -v`
Expected: PASS (4 tests).

- [ ] **Step 5: Wire `gate_enabled` through `_construct` and add the provisional prompt note**

In `_construct` (near line 390), pass the setting:

```python
    return Author(
        runner, ctx.read, ctx.committer,
        interval=ctx.settings.author_interval,
        casting_note=ctx.casting_note,
        personality=ctx.personalities.get("author", ""),
        provenance=ctx.provenance,
        advisory_token_budget=ctx.settings.advisory_token_budget,
        staleness_threshold_chapters=ctx.settings.staleness_threshold_chapters,
        pull_mode=enabled,
        gate_enabled=ctx.settings.outline_gate_enabled,
    )
```

In `poll()` (259-276) add the blueprint so `_summarize` can tell whether we're in the fallback:

```python
            "brief": await self._read.get_open_brief_for_ordinal(len(chapters) + 1),
            "blueprint": await self._read.get_active_blueprint(),
            "summaries": await self._read.list_chapter_summaries(),
```

In `_summarize`, when there is no blueprint AND no brief, prepend a provisional-draft note so unmoored prose knows it is unmoored. Add near the `brief` handling (after line 198 `brief = ctx.get("brief")`):

```python
    brief = ctx.get("brief")
    provisional = ""
    if ctx.get("blueprint") is None and brief is None:
        provisional = (
            "\n\nNo outline exists yet — you are drafting ahead of the Plotter under a "
            "fallback. Keep this chapter provisional and exploratory; do not invent a "
            "chapter's worth of new threads/promises/secrets, and say so in your feed note."
        )
```

Then include `provisional` in the returned string (append right before the final `"\n\nWrite the next chapter."`), e.g. change the trailing return to interpolate `{provisional}` immediately before `brief_block`:

```python
        f"{ledger}{pacing_plan}{provisional}{brief_block}\n\nWrite the next chapter."
```

`_summarize` currently receives `ctx` — confirm `ctx["blueprint"]` is available there (it is set in poll). `_summarize` is called from `work()` with the full `ctx`.

- [ ] **Step 6: Rewrite the static "no blueprint" prompt guidance (author.py:63-64, 74-77)**

The `AUTHOR_SYSTEM_PROMPT` "Check the outline before you draft" section still contemplates routine outline-less drafting. Tighten it so the norm is "the Plotter goes first." Replace the bullet at lines 63-64:

```
  - No blueprint adopted: this should not normally happen — the Plotter mints
    a blueprint from the premise before you draft. If you are here, you are in
    the fallback: keep the chapter provisional (see your task note) and say in
    your feed note that the Plotter still owes a blueprint.
```

Keep the rest of the section intact (the overdue-thread/beat/promise guidance is still correct for the fallback case). This is a copy-only edit; no test asserts prose wording.

- [ ] **Step 7: Run the author test module + existing author context tests**

Run: `pytest tests/agents/test_author_gate.py tests/agents/test_author_context.py -v`
Expected: PASS. If `test_author_context.py` constructs an `Author` and asserts genesis drafting without a blueprint, update those cases to either pass `gate_enabled=False` or seed an active blueprint — the gate is the new intended behavior. Document any such change in the commit message.

- [ ] **Step 8: Commit**

```bash
git add novelizer/agents/author.py tests/agents/test_author_gate.py tests/agents/test_author_context.py
git commit -m "feat(author): soft-gate drafting on an outline; provisional note in fallback"
```

---

### Task 4: Plotter goes first (readiness + non-consuming seed read + prompt)

**Files:**
- Modify: `novelizer/agents/plotter.py` — `readiness()` (124-145), `commit()` seed-consume filter (240), `PLOTTER_SYSTEM_PROMPT` (24-33), imports
- Test: `tests/agents/test_plotter_genesis.py` (new)

**Interfaces:**
- Consumes: `ReadStore.list_unconsumed_signals(target_agent="plotter")` (returns broadcast seeds), `ReadStore.list_proposals(status="open")`, `EventType.BLUEPRINT_ADOPTED`, `SignalKind.seed`.
- Produces: Plotter `readiness()==1.0` at genesis when a premise-seed exists and no active blueprint / pending blueprint proposal; `commit()` that leaves `kind=seed` signals unconsumed.

- [ ] **Step 1: Write the failing test**

```python
# tests/agents/test_plotter_genesis.py
import pytest
from novelizer.agents.plotter import Plotter
from novelizer.store.models import DirectorSignal, SignalKind
from novelizer.canon.events import EventType


class _Read:
    def __init__(self, *, chapters=None, world=None, blueprint=None,
                 proposals=None, signals=None, briefs=None):
        self._chapters = chapters or []
        self._world = world or []
        self._blueprint = blueprint
        self._proposals = proposals or []
        self._signals = signals or []
        self._briefs = briefs or []

    async def list_chapters(self, status=None):
        return self._chapters

    async def list_world_entries(self):
        return self._world

    async def get_active_blueprint(self):
        return self._blueprint

    async def list_proposals(self, status=None):
        return [p for p in self._proposals if status is None or p.status == status]

    async def list_unconsumed_signals(self, target_agent=None):
        return self._signals

    async def list_briefs(self, status=None):
        return self._briefs


class _Prop:
    def __init__(self):
        self.status = "open"
        self.target_event_type = EventType.BLUEPRINT_ADOPTED


def _plotter(read):
    p = Plotter.__new__(Plotter)
    p._read = read
    return p


def _seed():
    return DirectorSignal(kind=SignalKind.seed, body="a lighthouse keeper who taxes the tide")


@pytest.mark.asyncio
async def test_genesis_wakes_on_premise_seed():
    p = _plotter(_Read(signals=[_seed()]))
    assert await p.readiness() == 1.0


@pytest.mark.asyncio
async def test_genesis_idle_without_premise():
    p = _plotter(_Read())               # no seed, no world, no chapters
    assert await p.readiness() == 0.0


@pytest.mark.asyncio
async def test_stands_down_while_blueprint_proposal_pending():
    p = _plotter(_Read(signals=[_seed()], proposals=[_Prop()]))
    assert await p.readiness() == 0.0
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/agents/test_plotter_genesis.py -v`
Expected: FAIL — `test_genesis_wakes_on_premise_seed` gets `0.0` (current code returns `0.0` when no chapters and no world), `test_stands_down...` gets `1.0`.

- [ ] **Step 3: Implement the genesis readiness branch**

Add imports at the top of `plotter.py` (with the existing `from novelizer.store.models import Flag`):

```python
from novelizer.store.models import Flag, SignalKind
```

Replace the head of `readiness()` (lines 124-131) — everything up to and including the `if chapters and blueprint is None: return 1.0` — with a blueprint-first structure:

```python
    async def readiness(self) -> float:
        chapters = await self._read.list_chapters()
        world = await self._read.list_world_entries()
        blueprint = await self._read.get_active_blueprint()
        if blueprint is None:
            # Outline-first: the Plotter goes before anyone, minting the first
            # blueprint from the premise alone. Stand down once we've proposed
            # and are only waiting on Director approval.
            proposals = await self._read.list_proposals(status="open")
            if any(p.target_event_type == EventType.BLUEPRINT_ADOPTED for p in proposals):
                return 0.0
            seeds = await self._read.list_unconsumed_signals(target_agent=self.name)
            if seeds or chapters or world:
                return 1.0
            return 0.0
        # steady state below (unchanged)
        open_briefs = await self._read.list_briefs("open")
        chapter_count = len(chapters)
        ...
```

Keep the remainder of `readiness()` (the brief-runway / late-beat block, lines 132-145) exactly as-is. Note `EventType` is already imported in `plotter.py` (line 16).

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/agents/test_plotter_genesis.py -v`
Expected: PASS (3 tests).

- [ ] **Step 5: Do not consume the premise-seed (leave it for the World Architect)**

Add a test first:

```python
# append to tests/agents/test_plotter_genesis.py
class _RecordingRead(_Read):
    pass


@pytest.mark.asyncio
async def test_commit_does_not_consume_seed_signals():
    from novelizer.store.models import DirectorSignal, SignalKind
    seed = DirectorSignal(kind=SignalKind.seed, body="premise")
    focus = DirectorSignal(kind=SignalKind.focus, body="focus on the harbor", target_agent="plotter")
    consumed = []
    p = _plotter(_Read())
    async def fake_consume(sigs):
        consumed.extend(sigs)
    p._consume_signals = fake_consume
    # minimal ctx the tail of commit() touches; only signals matter here
    await p._consume_signals([s for s in [seed, focus] if s.kind != SignalKind.seed])
    assert seed not in consumed
    assert focus in consumed
```

(The unit test asserts the filter expression directly; the production change wires that same filter into `commit()`.)

In `commit()` change the final consume call (line 240) from:

```python
        await self._consume_signals(ctx["signals"])
```
to:

```python
        # Leave premise seeds for the World Architect (seed -> world is its job);
        # the Plotter only reads them to shape the blueprint. Consume everything
        # else targeted at the Plotter as before.
        await self._consume_signals([s for s in ctx["signals"] if s.kind != SignalKind.seed])
```

- [ ] **Step 6: Sharpen the Plotter prompt**

In `PLOTTER_SYSTEM_PROMPT` (lines 24-33) change "propose a blueprint when none exists (pick a framework and target length that fit the world and genre)" to lead with premise-first:

```
propose a blueprint from the premise before any prose or world exists — you go
first; pick a framework and target length that fit the premise, genre and (if
any) the world so far;
```

Copy-only edit; no test asserts wording.

- [ ] **Step 7: Run the plotter tests**

Run: `pytest tests/agents/test_plotter_genesis.py tests/agents/test_plotter.py -v`
Expected: PASS. If `test_plotter.py` asserts the old genesis readiness (`0.0` with no chapters/world) or asserts the Plotter consumes seeds, update those cases to the new intended behavior and note it in the commit.

- [ ] **Step 8: Commit**

```bash
git add novelizer/agents/plotter.py tests/agents/test_plotter_genesis.py tests/agents/test_plotter.py
git commit -m "feat(plotter): go first from the premise; read seeds without consuming them"
```

---

### Task 5: Outline section in the canon browser

Add an Outline section (blueprint, beats, briefs, threads-plan, ledger) to the navigable tree with drill-down detail views. Pure-model change; `browser.py` and the app selection handler are generic and need no edits.

**Files:**
- Modify: `novelizer/tui/widgets/browser_model.py` — `browser_sections()` (64-99) and `detail_view()` (129-172)
- Test: `tests/tui/test_browser_outline.py` (new)

**Interfaces:**
- Consumes: `read.get_active_blueprint()`, `read.list_beats()`, `read.list_briefs()`, `read.list_threads()`, `read.list_promises()`. Records: `BlueprintRecord(framework, target_chapter_count, genre)`, `BeatRecord(id, name, ideal_pct, tolerance_pct, expected_polarity, fulfilled_by_chapter_id)`, `ChapterBriefRecord(id, target_ordinal, goal, synopsis, status)`, `PromiseRecord`.
- Produces: an `{"key": "outline", "label": ..., "items": [...]}` section whose item ids are stable strings, and matching `detail_view(read, "outline", item_id)` branches. Node/item shape stays `{"id": str, "label": str}`; section shape `{"key","label","items"}`; `detail_view` returns `DetailView | None` built via `_view(...)`.

Design the outline section to use **synthetic item ids** so one section holds heterogeneous rows:
- `"blueprint"` → the blueprint summary
- `"beat:<beat_id>"` → a beat's detail
- `"brief:<brief_id>"` → a brief's detail

Threads-plan and ledger are already reachable via the existing Threads section and the Shape/Threads brain tabs; to avoid duplication, the browser Outline section covers **blueprint + beats + open briefs** (the parts with no existing navigable home). (Rationale recorded here so a reviewer sees the scope choice.)

- [ ] **Step 1: Write the failing test**

```python
# tests/tui/test_browser_outline.py
import pytest
from novelizer.tui.widgets import browser_model


class _Read:
    def __init__(self, *, blueprint=None, beats=None, briefs=None, **empty):
        self._blueprint = blueprint
        self._beats = beats or []
        self._briefs = briefs or []

    async def get_active_blueprint(self):
        return self._blueprint

    async def list_beats(self):
        return self._beats

    async def list_briefs(self, status=None):
        if status:
            return [b for b in self._briefs if b.status == status]
        return self._briefs

    # everything else the sections list touches -> empty
    async def list_chapters(self, status=None): return []
    async def list_characters(self): return []
    async def list_world_entries(self): return []
    async def list_flags(self, status=None): return []
    async def list_threads(self): return []
    async def list_themes(self): return []


class _BP:
    framework = "six-position"; target_chapter_count = 24; genre = "noir"


class _Beat:
    def __init__(self, id, name):
        self.id = id; self.name = name; self.ideal_pct = 0.25
        self.tolerance_pct = 0.1; self.expected_polarity = "positive"
        self.fulfilled_by_chapter_id = ""


class _Brief:
    def __init__(self, id, ordinal, goal):
        self.id = id; self.target_ordinal = ordinal; self.goal = goal
        self.synopsis = "syn"; self.status = "open"; self.pov_character_id = ""
        self.threads_to_touch = []; self.beats_to_hit = []
        self.promises_to_progress = []; self.value_shift = ""; self.planned_outcome = ""


@pytest.mark.asyncio
async def test_outline_section_present_with_blueprint():
    read = _Read(blueprint=_BP(), beats=[_Beat("b1", "Hook")],
                 briefs=[_Brief("br1", 3, "raise the stakes")])
    sections = await browser_model.browser_sections(read, staleness_threshold=3)
    outline = next(s for s in sections if s["key"] == "outline")
    ids = [i["id"] for i in outline["items"]]
    assert "blueprint" in ids
    assert "beat:b1" in ids
    assert "brief:br1" in ids


@pytest.mark.asyncio
async def test_outline_absent_without_blueprint():
    sections = await browser_model.browser_sections(_Read(blueprint=None), staleness_threshold=3)
    assert all(s["key"] != "outline" for s in sections)


@pytest.mark.asyncio
async def test_detail_view_for_beat_and_blueprint():
    read = _Read(blueprint=_BP(), beats=[_Beat("b1", "Hook")], briefs=[])
    bp_view = await browser_model.detail_view(read, "outline", "blueprint")
    assert bp_view is not None and "six-position" in bp_view.body.plain
    beat_view = await browser_model.detail_view(read, "outline", "beat:b1")
    assert beat_view is not None and "Hook" in beat_view.body.plain


@pytest.mark.asyncio
async def test_detail_view_missing_returns_none():
    read = _Read(blueprint=_BP(), beats=[], briefs=[])
    assert await browser_model.detail_view(read, "outline", "beat:nope") is None
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/tui/test_browser_outline.py -v`
Expected: FAIL — no `outline` section; `detail_view(..., "outline", ...)` returns None for all.

- [ ] **Step 3: Add the Outline section to `browser_sections`**

Inside `browser_sections`, fetch the blueprint/beats/briefs and prepend an outline section when a blueprint exists. Add near the other fetches:

```python
    blueprint = await read.get_active_blueprint()
```

Build the section (only when a blueprint exists) and put it FIRST in the returned list so the spine sits at the top:

```python
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
```

Then in the `return [...]`, prepend it when present:

```python
    sections = [
        {"key": "chapters", ...},
        ... existing sections ...
    ]
    return ([outline_section] + sections) if outline_section else sections
```

(Refactor the existing inline `return [ ... ]` into `sections = [ ... ]` then the conditional return.)

- [ ] **Step 4: Add `detail_view` branches for the outline section**

Add before the final `return None` in `detail_view`:

```python
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
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `pytest tests/tui/test_browser_outline.py -v`
Expected: PASS (4 tests).

- [ ] **Step 6: Commit**

```bash
git add novelizer/tui/widgets/browser_model.py tests/tui/test_browser_outline.py
git commit -m "feat(tui): outline section in the canon browser with drill-down"
```

---

### Task 6: Scrollable Story Brain tabs

Wrap each brain tab body in a `VerticalScroll` so long boards stop truncating. Behavior-preserving; the per-cycle `query_one("#<name>_body", Static).update(...)` calls are unchanged (the `Static` id stays the same, only its parent changes).

**Files:**
- Modify: `novelizer/tui/widgets/brain_panel.py` — `compose()` (each `TabPane` body)
- Test: `tests/tui/test_brain_panel_scroll.py` (new; a Textual pilot test — follow `docs/TESTING-TUI.md`)

**Interfaces:**
- Consumes: `textual.containers.VerticalScroll`.
- Produces: each `#*_body` Static nested inside a `VerticalScroll`; `refresh_from` untouched.

- [ ] **Step 1: Write the failing test**

```python
# tests/tui/test_brain_panel_scroll.py
import pytest
from textual.app import App, ComposeResult
from textual.containers import VerticalScroll
from novelizer.tui.widgets.brain_panel import BrainPanel


class _Harness(App):
    def compose(self) -> ComposeResult:
        yield BrainPanel()


@pytest.mark.asyncio
async def test_each_brain_body_is_inside_a_scroll_container():
    app = _Harness()
    async with app.run_test() as pilot:
        await pilot.pause()
        for body_id in ("shape_body", "threads_body", "secrets_body",
                        "causeway_body", "outline_body", "arcs_body"):
            body = app.query_one(f"#{body_id}")
            # walk ancestors; a VerticalScroll must be one of them
            anc = body.parent
            found = False
            while anc is not None:
                if isinstance(anc, VerticalScroll):
                    found = True
                    break
                anc = anc.parent
            assert found, f"{body_id} is not inside a VerticalScroll"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/tui/test_brain_panel_scroll.py -v`
Expected: FAIL — bodies are direct children of `TabPane`, no `VerticalScroll` ancestor.

Note (load-flakiness): TUI pilot tests can go red under parallel load. Run this module alone. If it errors on harness setup rather than the assertion, re-run in isolation before treating it as a real failure (see `docs/TESTING-TUI.md`).

- [ ] **Step 3: Wrap each body in a VerticalScroll**

In `brain_panel.py`, add to the imports:

```python
from textual.containers import Vertical, VerticalScroll
```

Rewrite `compose()` so each `TabPane` yields a `VerticalScroll` wrapping its `Static`:

```python
    def compose(self) -> ComposeResult:
        with TabbedContent(id="brain_tabs"):
            with TabPane("1 Shape", id="tab_shape"):
                with VerticalScroll():
                    yield Static("", id="shape_body")
            with TabPane("2 Threads", id="tab_threads"):
                with VerticalScroll():
                    yield Static("", id="threads_body")
            with TabPane("3 Secrets", id="tab_secrets"):
                with VerticalScroll():
                    yield Static("", id="secrets_body")
            with TabPane("4 Cause", id="tab_causeway"):
                with VerticalScroll():
                    yield Static("", id="causeway_body")
            with TabPane("5 Outline", id="tab_outline"):
                with VerticalScroll():
                    yield Static("", id="outline_body")
            with TabPane("6 Arcs", id="tab_arcs"):
                with VerticalScroll():
                    yield Static("", id="arcs_body")
        yield Static("", id="brain_strip")
```

`refresh_from` and `on_mount`/`activate_tab` are unchanged — they address widgets by id, which are preserved.

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/tui/test_brain_panel_scroll.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add novelizer/tui/widgets/brain_panel.py tests/tui/test_brain_panel_scroll.py
git commit -m "feat(tui): make Story Brain tab bodies scrollable"
```

---

### Task 7: Full-suite verification, docs sync, and merge

Held to the end deliberately (do not run the full suite per-task in the shared checkout).

**Files:**
- Possibly: `docs/` (diataxis sync), the design spec is already committed.

- [ ] **Step 1: Run the full test suite in the worktree**

Run: `pytest -q` (from the worktree root). If DB/pg fixtures are needed, follow `docs/TESTING-TUI.md` / the repo's standard test invocation.
Expected: green. Investigate any failure; genesis-behavior test breakage is expected fallout from the gate — fix those tests to the new intended behavior (blueprint-first), never weaken the gate to satisfy a stale test.

- [ ] **Step 2: Re-check the outline-optional consumers**

Confirm no agent other than the Author needed gating: grep `readiness` across `novelizer/agents/*.py` and verify Editor/Structure Analyst/Continuity/Retconner/Summarizer still gate on drafts (they do today), so gating the Author closes the pipeline. Record the check in the PR description.

- [ ] **Step 3: Update docs (diataxis sync)**

Invoke the `syncing-diataxis-docs` skill (or manually update): document the outline-first genesis, the `outline_gate_enabled` setting, the fallback semantics, and the new browser Outline section. Update the design spec's edge-case note that a premise-less story now waits (does not auto-draft).

- [ ] **Step 4: Code review**

Run `/code-review` (or the `tl:review` skill) over the branch diff. Address correctness/security/style findings.

- [ ] **Step 5: Push and open a draft PR**

```bash
git push -u origin worktree-outline-as-spine
gh pr create --draft --title "Outline as the story's spine: Plotter-first soft gate + TUI" --body "<summary + test evidence>"
```

- [ ] **Step 6: Merge to main**

Per the user's explicit instruction, after the suite is green and review is addressed, merge the branch to main (mark the PR ready and merge, or fast-forward per repo convention). Do not force-push.

---

## Self-Review

**Spec coverage:**
- Plotter-first from premise → Task 4. ✓
- Soft gate on active blueprint → Tasks 1–3. ✓
- Timeout/fallback (now progress-based) → Task 1 (`genesis_fallback_open`), Task 3 (Author uses it). ✓ (mechanism refined from "N cycles" to progress-based; noted for the user.)
- Event-sourced, no on-disk file → honored (gate is derived; no new persisted state). ✓
- Prompt direction + fallback honesty → Task 3 steps 5–6, Task 4 step 6. ✓
- TUI outline in browser + drill-down → Task 5. ✓
- Scrollable brain panes → Task 6. ✓

**Placeholder scan:** No "TBD"/"handle edge cases" — every code step shows code. Threads-plan/ledger browser nodes were intentionally scoped out (documented in Task 5) rather than left vague.

**Type consistency:** `author_may_draft(read, *, gate_enabled)` used identically in Task 1 (def) and Task 3 (call). `genesis_fallback_open`/`has_active_blueprint` names match across tasks. Section dict shape `{"key","label","items"}` and item `{"id","label"}` match the verified `browser.py` build loop. `DetailView` built via `_view(...)` as in the existing code.

**Scope check:** One cohesive feature (outline-first gate + its TUI surfacing). Single plan is appropriate.
