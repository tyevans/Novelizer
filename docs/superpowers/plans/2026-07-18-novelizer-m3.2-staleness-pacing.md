# M3.2 · Staleness & Pacing Analysis Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Two deterministic, pure Story Brain functions — `is_thread_stale`/`stale_threads` (a thread is stale once 3 chapters have elapsed since its last `planted`/`touched` event, no terminal event since) and `detect_sag_spike` (flags chapters whose tension deviates from the mean of emitted scores) — plus a new `annotation.structure_scored` event domain fed by a 7th scheduled agent, the **Structure Analyst**, which asks the LLM for a tension/pacing score per unscored recent chapter. Both pure functions live where M3.3's `BrainContext` provider and Thread Board/Story Shape TUI widgets can share them without duplicating logic.

**Architecture:** This is Story Brain's first dedicated module (`novelizer/brain/`), separate from `novelizer/agents/` and `novelizer/canon/` — it houses derived narrative intelligence, matching the vision spec's bounded-context split ("Story Brain: analyzers → brain projections", read-only over canon, never reached into by agents). (1) **Staleness is computed live, never persisted.** `novelizer/brain/staleness.py` exposes `is_thread_stale(thread, chapters) -> bool` and `stale_threads(threads, chapters) -> list[ThreadRecord]`, pure functions over `ThreadRecord`/`Chapter` data already available from `ReadStore.list_threads()`/`list_chapters()` — no new projection field, no LLM call. M3.3's `BrainContext` and Thread Board widget will both import this same module, so "the Thread Board shows it stale" and "the Author was told it's stale" can never disagree (this plan does not build those M3.3 consumers, only the shared function they'll both call). (2) **Sag/spike detection is likewise a pure function**, `novelizer/brain/sag_spike.py`'s `detect_sag_spike(scores) -> dict[str, str]`, operating over `StructureScore` rows already emitted — no LLM call, no judgment beyond a fixed numeric threshold. (3) **Score *production* is judgment, so it's a small LLM agent.** `StructureAnalyst` (`novelizer/agents/structure_analyst.py`) is a 7th `BaseAgent`, following the exact `CharacterKeeper`/`Editor` pattern: `poll()` gathers unscored recent chapters, `work()` asks the LLM for a batch of per-chapter scores in one structured-output call, `commit()` validates each score's `chapter_id` against the chapters it was actually given (dropping unrequested/hallucinated ids with a logged warning, mirroring M3.1's thread-intent validation) and commits `annotation.structure_scored` events through the existing `Committer` seam. (4) **Scheduling requires zero `Scheduler` changes.** `novelizer/scheduler.py`'s `Scheduler` already operates on any list of objects satisfying the `BaseAgent` protocol (`readiness()`, `ready_for_interval()`, `run_once()`, etc.) — confirmed by reading `tests/test_scheduler.py`, which is entirely agent-count-agnostic. `StructureAnalyst` is simply appended to `Runtime.agents` in `novelizer/runtime.py`; the M3.2 decomposition doc's "wired into scheduler.py / runtime.py" is satisfied by the runtime.py change alone, stated explicitly here since it's a design surprise worth flagging.

**Tech Stack:** Python 3.13, `pydantic` v2, `aiosqlite`, `pytest`+`pytest-asyncio` (`asyncio_mode=auto`), `hypothesis>=6.156.6` (already used for M3.1's thread state-machine property test; this plan adds two more property tests — staleness threshold boundary, sag/spike deviation invariant).

## Global Constraints

- `annotation.structure_scored` is the only new event type in M3.2 (no other `annotation.*` member; `secret.*`/`theme.*` are out of scope, per the M3 doc's Phase-1 note).
- `annotation.structure_scored` is added to `AutonomyPolicy._NEVER_GATED` — never enters the proposal queue at any autonomy level.
- Staleness threshold is a named constant (`STALENESS_THRESHOLD_CHAPTERS = 3` in `novelizer/brain/staleness.py`), not user-configurable in M3.2, per the doc.
- Sag/spike detection is a pure function with a named constant threshold (`SAG_SPIKE_DELTA` in `novelizer/brain/sag_spike.py`), not an LLM call.
- `StructureAnalyst.readiness()` is proportional to the count of unscored recent chapters and returns `0.0` when there are none; it has its own `ready_for_interval` cadence (own `structure_analyst_interval` setting), matching every other agent's pattern.
- Tests drive `StructureAnalyst.run_once()` directly, never via `Scheduler.tick()` — the established pattern for every existing agent's test suite.
- DRY: staleness and sag/spike logic each live in exactly one function, imported (not reimplemented) by every future consumer (M3.3's `BrainContext` and TUI widgets).
- TDD, black-box-first; Hypothesis property tests where invariants generalize (staleness threshold boundary behavior; sag/spike deviation invariant), following the M3.1 precedent test file's async-wrapped pattern.
- Backward compatibility: `StructureAnalyst` is new — zero changes to `Author`/`Editor`/`CharacterKeeper`/`WorldArchitect`/`ContinuityChecker`/`Retconner` behavior; the existing test suite (256 tests after M3.1) stays green throughout.

---

### Task 1: `annotation.structure_scored` event type and bounded payload

**Files:**
- Modify: `novelizer/canon/events.py`
- Test: `tests/canon/test_events.py`

**Interfaces:**
- Produces: `EventType.ANNOTATION_STRUCTURE_SCORED = "annotation.structure_scored"`; `AnnotationStructureScored(BaseModel)` with fields `chapter_id: str`, `tension: float` (bounded `0.0 <= tension <= 1.0` via `Field(ge=0.0, le=1.0)`), `pacing_label: str = ""`.

- [ ] **Step 1: Write the failing tests**

Append to `tests/canon/test_events.py`:

```python
def test_annotation_structure_scored_event_type_exists():
    from novelizer.canon.events import EventType
    assert EventType.ANNOTATION_STRUCTURE_SCORED == "annotation.structure_scored"


def test_annotation_structure_scored_payload_roundtrips():
    from novelizer.canon.events import AnnotationStructureScored
    scored = AnnotationStructureScored(chapter_id="c1", tension=0.7, pacing_label="rising")
    again = AnnotationStructureScored.model_validate_json(scored.model_dump_json())
    assert again == scored


def test_annotation_structure_scored_tension_is_bounded():
    import pytest
    from pydantic import ValidationError
    from novelizer.canon.events import AnnotationStructureScored
    with pytest.raises(ValidationError):
        AnnotationStructureScored(chapter_id="c1", tension=1.5, pacing_label="off the charts")
    with pytest.raises(ValidationError):
        AnnotationStructureScored(chapter_id="c1", tension=-0.1, pacing_label="negative")
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/canon/test_events.py -v`
Expected: FAIL — `AttributeError: type object 'EventType' has no attribute 'ANNOTATION_STRUCTURE_SCORED'`.

- [ ] **Step 3: Implement**

In `novelizer/canon/events.py`, add the event type constant to `EventType` (after `THREAD_ABANDONED`) and the payload model (after `ThreadAbandoned`), and add `Field` to the pydantic import:

```python
from pydantic import BaseModel, Field
```

```python
class EventType:
    WORLD_ENTRY_CREATED = "world_entry.created"
    WORLD_ENTRY_SUPERSEDED = "world_entry.superseded"
    CHARACTER_CREATED = "character.created"
    CHARACTER_UPDATED = "character.updated"
    CHAPTER_CREATED = "chapter.created"
    CHAPTER_STATUS_CHANGED = "chapter.status_changed"
    DIRECTOR_SIGNAL_CREATED = "director_signal.created"
    DIRECTOR_SIGNAL_CONSUMED = "director_signal.consumed"
    RETCON_REQUEST_CREATED = "retcon_request.created"
    RETCON_REQUEST_RESOLVED = "retcon_request.resolved"
    RETCON_REQUEST_REJECTED = "retcon_request.rejected"
    PROPOSAL_CREATED = "proposal.created"
    PROPOSAL_APPROVED = "proposal.approved"
    PROPOSAL_REJECTED = "proposal.rejected"
    AUTONOMY_CHANGED = "autonomy.changed"
    AGENT_REMARKED = "agent.remarked"
    THREAD_PLANTED = "thread.planted"
    THREAD_TOUCHED = "thread.touched"
    THREAD_PAID_OFF = "thread.paid_off"
    THREAD_ABANDONED = "thread.abandoned"
    ANNOTATION_STRUCTURE_SCORED = "annotation.structure_scored"
```

```python
class AnnotationStructureScored(BaseModel):
    """Payload for annotation.structure_scored — one chapter's tension/pacing
    score, emitted by the Structure Analyst. Bounded: tension is a fraction
    in [0.0, 1.0], enforced at construction so an out-of-range LLM score
    fails fast rather than corrupting the projection.
    """

    chapter_id: str
    tension: float = Field(ge=0.0, le=1.0)
    pacing_label: str = ""
```

(Only the new `EventType` constant, the `AnnotationStructureScored` class, and the `Field` import are new; every other class and constant is unchanged.)

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/canon/test_events.py -v`
Expected: PASS (all prior + 3 new). Then `uv run pytest tests/ -v` for the full suite green.

- [ ] **Step 5: Commit**

```bash
git add novelizer/canon/events.py tests/canon/test_events.py
git commit -m "feat: annotation.structure_scored event type with bounded tension payload"
```

---

### Task 2: `StructureScore` read-side model

**Files:**
- Modify: `novelizer/store/models.py`
- Test: `tests/store/test_models.py`

**Interfaces:**
- Produces: `StructureScore(BaseModel)` with fields `chapter_id: str`, `tension: float = Field(ge=0.0, le=1.0)`, `pacing_label: str = ""` — the row shape the `structure_scores` projection (Task 4) stores and `ReadStore.list_structure_scores()`/`get_structure_score()` (Task 5) return, and the type `detect_sag_spike` (Task 7) operates over.

- [ ] **Step 1: Write the failing test**

Append to `tests/store/test_models.py`:

```python
from novelizer.store.models import StructureScore


def test_structure_score_roundtrips_through_json():
    s = StructureScore(chapter_id="c1", tension=0.6, pacing_label="rising")
    again = StructureScore.model_validate_json(s.model_dump_json())
    assert again == s


def test_structure_score_tension_is_bounded():
    import pytest
    from pydantic import ValidationError
    with pytest.raises(ValidationError):
        StructureScore(chapter_id="c1", tension=2.0)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/store/test_models.py -v`
Expected: FAIL — `ImportError: cannot import name 'StructureScore' from 'novelizer.store.models'`.

- [ ] **Step 3: Implement**

In `novelizer/store/models.py`, add `StructureScore` after `ThreadRecord`:

```python
class StructureScore(BaseModel):
    """Read-side row for one chapter's narrative-structure score, built by
    the Projector from annotation.structure_scored events (see
    novelizer/canon/projector.py) and consumed by novelizer/brain/sag_spike.py's
    pure detect_sag_spike function and, in M3.3, the Story Shape TUI view.
    """

    chapter_id: str
    tension: float = Field(ge=0.0, le=1.0)
    pacing_label: str = ""
```

(Only `StructureScore` is new; every other class is unchanged. `Field` is already imported in this file.)

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/store/test_models.py -v`
Expected: PASS. Then `uv run pytest tests/ -v` for the full suite green.

- [ ] **Step 5: Commit**

```bash
git add novelizer/store/models.py tests/store/test_models.py
git commit -m "feat: StructureScore read-side model for chapter tension/pacing rows"
```

---

### Task 3: `annotation.structure_scored` is never gated

**Files:**
- Modify: `novelizer/canon/policy.py`
- Test: `tests/canon/test_policy.py`

**Interfaces:**
- Consumes: `EventType.ANNOTATION_STRUCTURE_SCORED` (Task 1).
- Produces: no new public interface — `AutonomyPolicy._NEVER_GATED` gains one entry.

- [ ] **Step 1: Write the failing test**

Append to `tests/canon/test_policy.py`:

```python
@pytest.mark.parametrize("level", list(AutonomyLevel))
async def test_annotation_structure_scored_is_never_gated(level):
    policy = AutonomyPolicy(FakeRead(AutonomyState(global_level=level)))
    assert await policy.is_gated("structure_analyst", EventType.ANNOTATION_STRUCTURE_SCORED) is False
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/canon/test_policy.py -v`
Expected: FAIL — under `AutonomyLevel.gated_all`, `is_gated` currently returns `True` for `annotation.structure_scored` since it isn't yet in `_NEVER_GATED`.

- [ ] **Step 3: Implement**

In `novelizer/canon/policy.py`, extend `_NEVER_GATED`:

```python
_NEVER_GATED = {
    EventType.DIRECTOR_SIGNAL_CREATED,
    EventType.DIRECTOR_SIGNAL_CONSUMED,
    EventType.AGENT_REMARKED,
    EventType.THREAD_PLANTED,
    EventType.THREAD_TOUCHED,
    EventType.THREAD_PAID_OFF,
    EventType.THREAD_ABANDONED,
    EventType.ANNOTATION_STRUCTURE_SCORED,
}
```

(Only the `ANNOTATION_STRUCTURE_SCORED` entry is new; `_RETCON_EVENTS`, `_CANON_EVENTS`, `_GATED_SETS`, and `AutonomyPolicy.is_gated` are unchanged.)

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/canon/test_policy.py -v`
Expected: PASS (all prior + 4 new parametrized cases). Then `uv run pytest tests/ -v` for the full suite green.

- [ ] **Step 5: Commit**

```bash
git add novelizer/canon/policy.py tests/canon/test_policy.py
git commit -m "feat: annotation.structure_scored is never gated by AutonomyPolicy"
```

---

### Task 4: `structure_scores` projection table

**Files:**
- Modify: `novelizer/canon/projector.py`
- Test: `tests/canon/test_projector.py`

**Interfaces:**
- Consumes: `EventType.ANNOTATION_STRUCTURE_SCORED` (Task 1); `StructureScore` (Task 2).
- Produces: a `structure_scores` table (`id TEXT PRIMARY KEY, data TEXT NOT NULL`, keyed by `chapter_id`) maintained by `Projector._apply`; `structure_scores` is added to `Projector._reset_state`'s cleared-tables tuple.

- [ ] **Step 1: Write the failing tests**

Append to `tests/canon/test_projector.py`:

```python
async def _structure_score_rows(proj):
    cur = await proj._conn.execute("SELECT data FROM structure_scores ORDER BY rowid")
    return [json.loads(r[0]) for r in await cur.fetchall()]


async def test_structure_scored_is_projected(wired):
    from novelizer.canon.events import AnnotationStructureScored
    events, proj, _ = wired
    await events.append(
        EventType.ANNOTATION_STRUCTURE_SCORED, "c1",
        AnnotationStructureScored(chapter_id="c1", tension=0.6, pacing_label="rising"),
    )
    await proj.catch_up()
    rows = await _structure_score_rows(proj)
    assert len(rows) == 1
    assert rows[0]["chapter_id"] == "c1" and rows[0]["tension"] == 0.6


async def test_structure_scored_replaces_prior_score_for_same_chapter(wired):
    from novelizer.canon.events import AnnotationStructureScored
    events, proj, _ = wired
    await events.append(EventType.ANNOTATION_STRUCTURE_SCORED, "c1",
                        AnnotationStructureScored(chapter_id="c1", tension=0.3, pacing_label="lull"))
    await events.append(EventType.ANNOTATION_STRUCTURE_SCORED, "c1",
                        AnnotationStructureScored(chapter_id="c1", tension=0.9, pacing_label="climax"))
    await proj.catch_up()
    rows = await _structure_score_rows(proj)
    assert len(rows) == 1
    assert rows[0]["tension"] == 0.9 and rows[0]["pacing_label"] == "climax"


async def test_reprojecting_structure_scores_is_equivalent(wired):
    from novelizer.canon.events import AnnotationStructureScored
    events, proj, path = wired
    await events.append(EventType.ANNOTATION_STRUCTURE_SCORED, "c1",
                        AnnotationStructureScored(chapter_id="c1", tension=0.6, pacing_label="rising"))
    await events.append(EventType.ANNOTATION_STRUCTURE_SCORED, "c2",
                        AnnotationStructureScored(chapter_id="c2", tension=0.2, pacing_label="lull"))
    await proj.catch_up()
    incremental = await _structure_score_rows(proj)
    proj2 = Projector(events, path)
    await proj2.init()
    await proj2._reset_state()
    await proj2.catch_up()
    from_scratch = await _structure_score_rows(proj2)
    await proj2.close()
    assert incremental == from_scratch


async def test_reset_state_clears_structure_scores(wired):
    from novelizer.canon.events import AnnotationStructureScored
    events, proj, _ = wired
    await events.append(EventType.ANNOTATION_STRUCTURE_SCORED, "c1",
                        AnnotationStructureScored(chapter_id="c1", tension=0.5, pacing_label="steady"))
    await proj.catch_up()
    await proj._reset_state()
    cur = await proj._conn.execute("SELECT COUNT(*) FROM structure_scores")
    assert (await cur.fetchone())[0] == 0
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/canon/test_projector.py -v`
Expected: FAIL — `sqlite3.OperationalError: no such table: structure_scores`.

- [ ] **Step 3: Implement**

In `novelizer/canon/projector.py`, add the `structure_scores` table to `_CREATE` (after `threads`):

```python
CREATE TABLE IF NOT EXISTS structure_scores (
    id TEXT PRIMARY KEY, data TEXT NOT NULL
);
```

Update `_reset_state`:

```python
    async def _reset_state(self) -> None:
        """Testing/rebuild helper: forget position and clear projections."""
        for table in (
            "chapters", "world_entries", "characters", "director_signals",
            "retcon_requests", "proposals", "autonomy_state", "threads",
            "structure_scores",
        ):
            await self._conn.execute(f"DELETE FROM {table}")
        await self._set_last_sequence(0)
```

Add one new `elif` branch to `_apply`, immediately before the final `elif t == EventType.AUTONOMY_CHANGED:` branch:

```python
        elif t == EventType.ANNOTATION_STRUCTURE_SCORED:
            await self._conn.execute(
                "INSERT OR REPLACE INTO structure_scores (id, data) VALUES (?,?)",
                (p["chapter_id"], data),
            )
```

(Only the `structure_scores` table, its `_reset_state` entry, and this one new branch are new; every other branch of `_apply` — including the two `THREAD_*` branches from M3.1 — is unchanged.)

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/canon/test_projector.py -v`
Expected: PASS (all prior + 4 new). Then `uv run pytest tests/ -v` for the full suite green.

- [ ] **Step 5: Commit**

```bash
git add novelizer/canon/projector.py tests/canon/test_projector.py
git commit -m "feat: structure_scores projection table, keyed by chapter_id"
```

---

### Task 5: `ReadStore.list_structure_scores()` / `get_structure_score()`

**Files:**
- Modify: `novelizer/canon/read_store.py`
- Test: `tests/canon/test_read_store.py`

**Interfaces:**
- Consumes: `StructureScore` (Task 2); the `structure_scores` table (Task 4).
- Produces: `ReadStore.list_structure_scores() -> list[StructureScore]`; `ReadStore.get_structure_score(chapter_id: str) -> Optional[StructureScore]`.

- [ ] **Step 1: Write the failing test**

Append to `tests/canon/test_read_store.py`:

```python
async def test_list_and_get_structure_scores(stack):
    from novelizer.canon.events import AnnotationStructureScored
    events, proj, read = stack
    await events.append(EventType.ANNOTATION_STRUCTURE_SCORED, "c1",
                        AnnotationStructureScored(chapter_id="c1", tension=0.6, pacing_label="rising"))
    await events.append(EventType.ANNOTATION_STRUCTURE_SCORED, "c2",
                        AnnotationStructureScored(chapter_id="c2", tension=0.2, pacing_label="lull"))
    await proj.catch_up()
    scores = await read.list_structure_scores()
    assert {s.chapter_id for s in scores} == {"c1", "c2"}
    fetched = await read.get_structure_score("c1")
    assert fetched is not None and fetched.tension == 0.6
    assert await read.get_structure_score("missing") is None
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/canon/test_read_store.py::test_list_and_get_structure_scores -v`
Expected: FAIL — `AttributeError: 'ReadStore' object has no attribute 'list_structure_scores'`.

- [ ] **Step 3: Implement**

In `novelizer/canon/read_store.py`, add `StructureScore` to the import and two new methods, after `get_thread`:

```python
from novelizer.store.models import (
    Chapter, WorldEntry, Character, DirectorSignal, RetconRequest, ThreadRecord, StructureScore,
)
```

```python
    async def list_structure_scores(self) -> list[StructureScore]:
        cur = await self._conn.execute("SELECT data FROM structure_scores ORDER BY rowid")
        return [StructureScore.model_validate_json(r[0]) for r in await cur.fetchall()]

    async def get_structure_score(self, chapter_id: str) -> Optional[StructureScore]:
        cur = await self._conn.execute("SELECT data FROM structure_scores WHERE id=?", (chapter_id,))
        row = await cur.fetchone()
        return StructureScore.model_validate_json(row[0]) if row else None
```

(Only the import addition and these two methods are new; every other method is unchanged.)

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/canon/test_read_store.py -v`
Expected: PASS (all prior + 1 new). Then `uv run pytest tests/ -v` for the full suite green.

- [ ] **Step 5: Commit**

```bash
git add novelizer/canon/read_store.py tests/canon/test_read_store.py
git commit -m "feat: ReadStore.list_structure_scores()/get_structure_score() expose chapter scores"
```

---

### Task 6: `novelizer/brain` package — `StalenessAnalyzer` (`is_thread_stale`/`stale_threads`)

**Files:**
- Create: `novelizer/brain/__init__.py`
- Create: `novelizer/brain/staleness.py`
- Test: `tests/brain/__init__.py`, `tests/brain/test_staleness.py`

**Interfaces:**
- Consumes: `ThreadRecord`/`ThreadState` (`novelizer.store.models`, from M3.1); `Chapter` (`novelizer.store.models`); `TERMINAL_STATES` (`novelizer.canon.threads`, from M3.1).
- Produces: `STALENESS_THRESHOLD_CHAPTERS = 3` (module constant); `chapters_elapsed_since(chapter_id: str, chapters: list[Chapter]) -> int`; `is_thread_stale(thread: ThreadRecord, chapters: list[Chapter], threshold: int = STALENESS_THRESHOLD_CHAPTERS) -> bool`; `stale_threads(threads: list[ThreadRecord], chapters: list[Chapter], threshold: int = STALENESS_THRESHOLD_CHAPTERS) -> list[ThreadRecord]` — all pure functions over in-memory data, no I/O, importable by M3.3's `BrainContext` and Thread Board widget without touching `ReadStore` themselves.

- [ ] **Step 1: Write the failing tests**

Create `tests/brain/__init__.py` (empty file).

Create `tests/brain/test_staleness.py`:

```python
from hypothesis import given, settings, strategies as st
from novelizer.brain.staleness import (
    STALENESS_THRESHOLD_CHAPTERS, chapters_elapsed_since, is_thread_stale, stale_threads,
)
from novelizer.store.models import Chapter, ThreadRecord, ThreadState


def _chapters(n: int) -> list[Chapter]:
    return [Chapter(id=f"c{i}", title=str(i), prose="p") for i in range(n)]


def test_chapters_elapsed_since_counts_chapters_after_the_given_one():
    chs = _chapters(5)  # c0..c4
    assert chapters_elapsed_since("c4", chs) == 0
    assert chapters_elapsed_since("c2", chs) == 2
    assert chapters_elapsed_since("c0", chs) == 4


def test_chapters_elapsed_since_unknown_id_is_maximally_stale():
    chs = _chapters(3)
    assert chapters_elapsed_since("does-not-exist", chs) == 3
    assert chapters_elapsed_since("", chs) == 3


def test_thread_not_stale_before_threshold():
    chs = _chapters(4)  # c0..c3
    thread = ThreadRecord(id="t1", name="T", state=ThreadState.touched, last_chapter_id="c2")
    assert chapters_elapsed_since("c2", chs) == 1
    assert is_thread_stale(thread, chs) is False


def test_thread_stale_once_three_chapters_have_elapsed():
    chs = _chapters(5)  # 4 chapters have elapsed since c0's touch (c1..c4)
    thread = ThreadRecord(id="t1", name="T", state=ThreadState.planted, last_chapter_id="c0")
    assert chapters_elapsed_since("c0", chs) == 4
    assert is_thread_stale(thread, chs) is True


def test_thread_stale_at_exactly_the_threshold():
    chs = _chapters(4)  # c1, c2, c3 elapsed since c0 -> exactly 3
    thread = ThreadRecord(id="t1", name="T", state=ThreadState.touched, last_chapter_id="c0")
    assert chapters_elapsed_since("c0", chs) == 3
    assert is_thread_stale(thread, chs) is True


def test_terminal_threads_are_never_stale_regardless_of_elapsed_chapters():
    chs = _chapters(10)
    for state in (ThreadState.paid_off, ThreadState.abandoned):
        thread = ThreadRecord(id="t1", name="T", state=state, last_chapter_id="c0")
        assert is_thread_stale(thread, chs) is False


def test_stale_threads_filters_a_mixed_list():
    chs = _chapters(5)
    fresh = ThreadRecord(id="fresh", name="Fresh", state=ThreadState.touched, last_chapter_id="c4")
    stale = ThreadRecord(id="stale", name="Stale", state=ThreadState.planted, last_chapter_id="c0")
    closed = ThreadRecord(id="closed", name="Closed", state=ThreadState.paid_off, last_chapter_id="c0")
    assert {t.id for t in stale_threads([fresh, stale, closed], chs)} == {"stale"}


@given(elapsed=st.integers(min_value=0, max_value=20))
@settings(max_examples=50)
def test_staleness_boundary_holds_for_any_elapsed_count(elapsed):
    """For any number of elapsed chapters, a non-terminal thread is stale iff
    elapsed >= STALENESS_THRESHOLD_CHAPTERS -- the boundary is exact, not off
    by one in either direction."""
    chs = _chapters(elapsed + 1)  # thread's last chapter is c0; elapsed chapters follow it
    thread = ThreadRecord(id="t1", name="T", state=ThreadState.touched, last_chapter_id="c0")
    assert chapters_elapsed_since("c0", chs) == elapsed
    assert is_thread_stale(thread, chs) is (elapsed >= STALENESS_THRESHOLD_CHAPTERS)
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/brain/test_staleness.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'novelizer.brain'`.

- [ ] **Step 3: Implement**

Create `novelizer/brain/__init__.py` (empty file — marks the package).

Create `novelizer/brain/staleness.py`:

```python
from __future__ import annotations
from novelizer.canon.threads import TERMINAL_STATES
from novelizer.store.models import Chapter, ThreadRecord

STALENESS_THRESHOLD_CHAPTERS = 3


def chapters_elapsed_since(chapter_id: str, chapters: list[Chapter]) -> int:
    """Count of chapters strictly after `chapter_id` in `chapters`' chronological
    order (the order novelizer.canon.read_store.ReadStore.list_chapters()
    already returns them in). If `chapter_id` isn't found among `chapters`
    (an empty id, or a thread whose last event carried no chapter reference),
    every chapter counts as elapsed -- a conservative, maximally-stale default.
    """
    ids = [c.id for c in chapters]
    if chapter_id not in ids:
        return len(chapters)
    return len(chapters) - 1 - ids.index(chapter_id)


def is_thread_stale(
    thread: ThreadRecord, chapters: list[Chapter], threshold: int = STALENESS_THRESHOLD_CHAPTERS
) -> bool:
    """A thread is stale once `threshold` chapters have elapsed since its last
    planted/touched event, with no terminal (paid_off/abandoned) event since.
    Pure and computed live over ReadStore data -- never persisted -- so that
    M3.3's BrainContext provider and Thread Board widget, which will both
    import this function, can never disagree about which threads are stale.
    """
    if thread.state.value in TERMINAL_STATES:
        return False
    return chapters_elapsed_since(thread.last_chapter_id, chapters) >= threshold


def stale_threads(
    threads: list[ThreadRecord], chapters: list[Chapter], threshold: int = STALENESS_THRESHOLD_CHAPTERS
) -> list[ThreadRecord]:
    """Filter `threads` down to the ones is_thread_stale flags, preserving order."""
    return [t for t in threads if is_thread_stale(t, chapters, threshold)]
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/brain/test_staleness.py -v`
Expected: PASS (all 8, including the Hypothesis property test). Then `uv run pytest tests/ -v` for the full suite green.

- [ ] **Step 5: Commit**

```bash
git add novelizer/brain/__init__.py novelizer/brain/staleness.py tests/brain/__init__.py tests/brain/test_staleness.py
git commit -m "feat: novelizer.brain.staleness — pure thread-staleness detection"
```

---

### Task 7: `novelizer/brain/sag_spike.py` — pure sag/spike detection

**Files:**
- Create: `novelizer/brain/sag_spike.py`
- Test: `tests/brain/test_sag_spike.py`

**Interfaces:**
- Consumes: `StructureScore` (Task 2).
- Produces: `SAG_SPIKE_DELTA = 0.3` (module constant); `detect_sag_spike(scores: list[StructureScore], delta: float = SAG_SPIKE_DELTA) -> dict[str, str]` — maps `chapter_id -> "sag" | "spike"` for chapters whose tension deviates from the mean of the given scores by at least `delta`; chapters within the threshold are omitted from the returned dict.

- [ ] **Step 1: Write the failing tests**

Create `tests/brain/test_sag_spike.py`:

```python
from hypothesis import given, settings, strategies as st
from novelizer.brain.sag_spike import SAG_SPIKE_DELTA, detect_sag_spike
from novelizer.store.models import StructureScore


def test_flat_chapter_amid_high_tension_is_flagged_sag():
    scores = [
        StructureScore(chapter_id="c1", tension=0.8, pacing_label="rising"),
        StructureScore(chapter_id="c2", tension=0.1, pacing_label="flat"),
        StructureScore(chapter_id="c3", tension=0.85, pacing_label="climax"),
    ]
    flags = detect_sag_spike(scores)
    assert flags["c2"] == "sag"
    assert "c1" not in flags and "c3" not in flags


def test_spike_amid_low_tension_is_flagged_spike():
    scores = [
        StructureScore(chapter_id="c1", tension=0.1, pacing_label="lull"),
        StructureScore(chapter_id="c2", tension=0.95, pacing_label="climax"),
        StructureScore(chapter_id="c3", tension=0.15, pacing_label="lull"),
    ]
    flags = detect_sag_spike(scores)
    assert flags["c2"] == "spike"
    assert "c1" not in flags and "c3" not in flags


def test_uniform_tension_flags_nothing():
    scores = [StructureScore(chapter_id=f"c{i}", tension=0.5, pacing_label="steady") for i in range(4)]
    assert detect_sag_spike(scores) == {}


def test_fewer_than_two_scores_flags_nothing():
    assert detect_sag_spike([]) == {}
    assert detect_sag_spike([StructureScore(chapter_id="c1", tension=0.9, pacing_label="climax")]) == {}


@given(
    tensions=st.lists(st.floats(min_value=0.0, max_value=1.0, allow_nan=False), min_size=2, max_size=15),
)
@settings(max_examples=50)
def test_flag_membership_matches_the_deviation_invariant(tensions):
    """For any list of scores, a chapter is flagged iff its tension deviates
    from the mean of all given scores by at least SAG_SPIKE_DELTA, and the
    direction of the flag (sag vs spike) matches the sign of that deviation."""
    scores = [StructureScore(chapter_id=f"c{i}", tension=t, pacing_label="") for i, t in enumerate(tensions)]
    mean = sum(tensions) / len(tensions)
    flags = detect_sag_spike(scores)
    for s in scores:
        diff = s.tension - mean
        if diff <= -SAG_SPIKE_DELTA:
            assert flags.get(s.chapter_id) == "sag"
        elif diff >= SAG_SPIKE_DELTA:
            assert flags.get(s.chapter_id) == "spike"
        else:
            assert s.chapter_id not in flags
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/brain/test_sag_spike.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'novelizer.brain.sag_spike'`.

- [ ] **Step 3: Implement**

Create `novelizer/brain/sag_spike.py`:

```python
from __future__ import annotations
from novelizer.store.models import StructureScore

SAG_SPIKE_DELTA = 0.3


def detect_sag_spike(scores: list[StructureScore], delta: float = SAG_SPIKE_DELTA) -> dict[str, str]:
    """Pure, deterministic sag/spike detection over already-emitted structure
    scores -- no LLM call, no judgment beyond a fixed numeric threshold. A
    chapter whose tension deviates from the mean of `scores` by at least
    `delta` is flagged "sag" (below the mean) or "spike" (above it); chapters
    within the threshold are omitted from the result. Fewer than two scores
    can't establish a mean worth deviating from, so nothing is flagged.
    """
    if len(scores) < 2:
        return {}
    mean = sum(s.tension for s in scores) / len(scores)
    flags: dict[str, str] = {}
    for s in scores:
        diff = s.tension - mean
        if diff <= -delta:
            flags[s.chapter_id] = "sag"
        elif diff >= delta:
            flags[s.chapter_id] = "spike"
    return flags
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/brain/test_sag_spike.py -v`
Expected: PASS (all 5, including the Hypothesis property test). Then `uv run pytest tests/ -v` for the full suite green.

- [ ] **Step 5: Commit**

```bash
git add novelizer/brain/sag_spike.py tests/brain/test_sag_spike.py
git commit -m "feat: novelizer.brain.sag_spike — pure sag/spike detection over structure scores"
```

---

### Task 8: `StructureAnalyst` — the 7th scheduled agent

**Files:**
- Create: `novelizer/agents/structure_analyst.py`
- Modify: `novelizer/agents/schemas.py` (`ChapterScore`, `StructureAnalystOutput`)
- Test: `tests/agents/test_structure_analyst.py`

**Interfaces:**
- Consumes: `BaseAgent` (`novelizer.agents.base`); `EventType.ANNOTATION_STRUCTURE_SCORED`/`AnnotationStructureScored` (Task 1); `ReadStore.list_chapters()`/`list_structure_scores()` (existing + Task 5).
- Produces: `ChapterScore(BaseModel)` with fields `chapter_id: str`, `tension: float = Field(ge=0.0, le=1.0)`, `pacing_label: str = ""`; `StructureAnalystOutput(BaseModel)` with fields `scores: list[ChapterScore] = Field(default_factory=list)`, `feed_note: str = ""`; `StructureAnalyst(BaseAgent)` with `readiness()`, `poll()`, `work()`, `commit()`, `run_once()`; `build_structure_analyst_runner(settings)`.

- [ ] **Step 1: Write the failing tests**

Create `tests/agents/test_structure_analyst.py`:

```python
import os
import tempfile
import pytest
from novelizer.canon.event_store import EventStore
from novelizer.canon.projector import Projector
from novelizer.canon.read_store import ReadStore
from novelizer.canon.committer import Committer
from novelizer.canon.events import EventType, AnnotationStructureScored
from novelizer.agents.structure_analyst import StructureAnalyst
from novelizer.agents.schemas import ChapterScore, StructureAnalystOutput
from novelizer.store.models import Chapter


class FakeRunner:
    def __init__(self, out): self._out = out; self.calls = []
    async def ainvoke(self, inputs):
        self.calls.append(inputs)
        return {"structured_response": self._out}


@pytest.fixture
async def stack():
    fd, path = tempfile.mkstemp(suffix=".db"); os.close(fd)
    events = EventStore(path); await events.init()
    proj = Projector(events, path); await proj.init()
    read = ReadStore(path); await read.init()
    yield events, proj, read, Committer(events)
    await read.close(); await proj.close(); await events.close(); os.unlink(path)


async def test_readiness_is_zero_with_no_unscored_chapters(stack):
    events, proj, read, committer = stack
    analyst = StructureAnalyst(FakeRunner(None), read, committer)
    assert await analyst.readiness() == 0.0


async def test_readiness_is_proportional_to_unscored_chapter_count(stack):
    events, proj, read, committer = stack
    for i in range(2):
        await events.append(EventType.CHAPTER_CREATED, f"c{i}", Chapter(id=f"c{i}", title=str(i), prose="p"))
    await proj.catch_up()
    analyst = StructureAnalyst(FakeRunner(None), read, committer)
    assert await analyst.readiness() == pytest.approx(2 / 3)


async def test_readiness_excludes_already_scored_chapters(stack):
    events, proj, read, committer = stack
    await events.append(EventType.CHAPTER_CREATED, "c1", Chapter(id="c1", title="One", prose="p"))
    await events.append(EventType.ANNOTATION_STRUCTURE_SCORED, "c1",
                        AnnotationStructureScored(chapter_id="c1", tension=0.5, pacing_label="steady"))
    await proj.catch_up()
    analyst = StructureAnalyst(FakeRunner(None), read, committer)
    assert await analyst.readiness() == 0.0


async def test_run_once_emits_a_structure_scored_event_per_chapter(stack):
    events, proj, read, committer = stack
    await events.append(EventType.CHAPTER_CREATED, "c1", Chapter(id="c1", title="One", prose="p"))
    await events.append(EventType.CHAPTER_CREATED, "c2", Chapter(id="c2", title="Two", prose="p"))
    await proj.catch_up()
    out = StructureAnalystOutput(scores=[
        ChapterScore(chapter_id="c1", tension=0.2, pacing_label="lull"),
        ChapterScore(chapter_id="c2", tension=0.9, pacing_label="climax"),
    ])
    analyst = StructureAnalyst(FakeRunner(out), read, committer)
    await analyst.run_once()
    await proj.catch_up()
    scores = {s.chapter_id: s for s in await read.list_structure_scores()}
    assert scores["c1"].tension == 0.2 and scores["c2"].pacing_label == "climax"


async def test_commit_drops_score_for_unrequested_chapter_id(stack):
    events, proj, read, committer = stack
    await events.append(EventType.CHAPTER_CREATED, "c1", Chapter(id="c1", title="One", prose="p"))
    await proj.catch_up()
    out = StructureAnalystOutput(scores=[ChapterScore(chapter_id="not-in-batch", tension=0.5, pacing_label="steady")])
    analyst = StructureAnalyst(FakeRunner(out), read, committer)
    await analyst.run_once()
    log = await events.events_since(0)
    assert [e for e in log if e.event_type == EventType.ANNOTATION_STRUCTURE_SCORED] == []


async def test_work_returns_none_and_commit_is_noop_when_no_unscored_chapters(stack):
    events, proj, read, committer = stack
    analyst = StructureAnalyst(FakeRunner(StructureAnalystOutput()), read, committer)
    ctx = await analyst.poll()
    assert ctx["unscored"] == []
    result = await analyst.work(ctx)
    assert result is None
    await analyst.commit(result, ctx)
    assert await events.events_since(0) == []


async def test_commit_emits_agent_remarked_when_feed_note_present(stack):
    events, proj, read, committer = stack
    await events.append(EventType.CHAPTER_CREATED, "c1", Chapter(id="c1", title="One", prose="p"))
    await proj.catch_up()
    out = StructureAnalystOutput(
        scores=[ChapterScore(chapter_id="c1", tension=0.5, pacing_label="steady")],
        feed_note="Chapter one holds steady.",
    )
    analyst = StructureAnalyst(FakeRunner(out), read, committer)
    await analyst.run_once()
    log = await events.events_since(0)
    remarks = [e for e in log if e.event_type == EventType.AGENT_REMARKED]
    assert len(remarks) == 1 and remarks[0].payload["note"] == "Chapter one holds steady."


async def test_work_prompt_includes_personality_when_set(stack):
    events, proj, read, committer = stack
    await events.append(EventType.CHAPTER_CREATED, "c1", Chapter(id="c1", title="One", prose="p"))
    await proj.catch_up()
    runner = FakeRunner(StructureAnalystOutput())
    analyst = StructureAnalyst(runner, read, committer, personality="A clinical pacing critic.")
    ctx = await analyst.poll()
    await analyst.work(ctx)
    sent = runner.calls[-1]["messages"][0]["content"]
    assert "A clinical pacing critic." in sent
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/agents/test_structure_analyst.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'novelizer.agents.structure_analyst'`.

- [ ] **Step 3: Implement**

In `novelizer/agents/schemas.py`, add `ChapterScore` and `StructureAnalystOutput` after `RetconAmendments`:

```python
class ChapterScore(BaseModel):
    chapter_id: str
    tension: float = Field(ge=0.0, le=1.0)
    pacing_label: str = ""


class StructureAnalystOutput(BaseModel):
    scores: list[ChapterScore] = Field(default_factory=list)
    feed_note: str = ""
```

Create `novelizer/agents/structure_analyst.py`:

```python
from __future__ import annotations
import logging
from novelizer.agents.base import BaseAgent, Runner
from novelizer.agents.schemas import StructureAnalystOutput
from novelizer.canon.read_store import ReadStore
from novelizer.canon.committer import Committer
from novelizer.canon.events import EventType, AnnotationStructureScored

logger = logging.getLogger(__name__)

SYSTEM_PROMPT = """You are the Structure Analyst for a living fictional world's story.
You read recent unscored chapters and score each one's narrative tension and pacing.
For each chapter, return its id, a tension score from 0.0 (slack) to 1.0 (peak intensity),
and a short pacing_label (e.g. "rising", "climax", "lull", "steady").
Return one entry per chapter you were given, no more."""

_BATCH_SIZE = 5
_READINESS_DIVISOR = 3


class StructureAnalyst(BaseAgent):
    def __init__(
        self,
        runner: Runner,
        read_store: ReadStore,
        committer: Committer,
        interval: int = 180,
        personality: str = "",
    ) -> None:
        super().__init__(runner, read_store, committer, interval, name="structure_analyst", personality=personality)

    async def _unscored_recent_chapters(self) -> list:
        chapters = await self._read.list_chapters()
        scored_ids = {s.chapter_id for s in await self._read.list_structure_scores()}
        unscored = [c for c in chapters if c.id not in scored_ids]
        return unscored[-_BATCH_SIZE:]

    async def readiness(self) -> float:
        unscored = await self._unscored_recent_chapters()
        if not unscored:
            return 0.0
        return min(1.0, len(unscored) / _READINESS_DIVISOR)

    async def poll(self) -> dict:
        return {"unscored": await self._unscored_recent_chapters()}

    async def work(self, ctx: dict) -> StructureAnalystOutput | None:
        chapters = ctx["unscored"]
        if not chapters:
            return None
        listing = "\n\n".join(f"Chapter id:{c.id} '{c.title}': {c.prose[:400]}" for c in chapters)
        cast = f"\n\nIn character: {self.personality}" if self.personality else ""
        msg = f"Score these chapters:\n{listing}{cast}"
        result = await self._runner.ainvoke({"messages": [{"role": "user", "content": msg}]})
        return result.get("structured_response")

    async def commit(self, out: StructureAnalystOutput | None, ctx: dict) -> None:
        if out is None:
            return
        valid_ids = {c.id for c in ctx["unscored"]}
        for score in out.scores:
            if score.chapter_id not in valid_ids:
                logger.warning(
                    "structure_analyst: dropped score for unrequested chapter id %r", score.chapter_id
                )
                continue
            payload = AnnotationStructureScored(
                chapter_id=score.chapter_id, tension=score.tension, pacing_label=score.pacing_label
            )
            await self._committer.commit(self.name, EventType.ANNOTATION_STRUCTURE_SCORED, score.chapter_id, payload)
        await self._remark(out.feed_note)

    async def run_once(self) -> None:
        ctx = await self.poll()
        out = await self.work(ctx)
        await self.commit(out, ctx)


def build_structure_analyst_runner(settings):
    from deepagents import create_deep_agent
    from novelizer.agents.llm import build_chat_model
    model = build_chat_model(settings.agent_model, settings.llm_base_url, settings.llm_api_key, settings.agent_temperature)
    return create_deep_agent(model=model, system_prompt=SYSTEM_PROMPT, response_format=StructureAnalystOutput)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/agents/test_structure_analyst.py -v`
Expected: PASS (all 8). Then `uv run pytest tests/ -v` for the full suite green.

- [ ] **Step 5: Commit**

```bash
git add novelizer/agents/structure_analyst.py novelizer/agents/schemas.py tests/agents/test_structure_analyst.py
git commit -m "feat: StructureAnalyst — 7th scheduled agent scoring chapter tension/pacing"
```

---

### Task 9: Wire `StructureAnalyst` into `Runtime`

**Files:**
- Modify: `novelizer/config.py` (`structure_analyst_interval`)
- Modify: `novelizer/runtime.py`
- Test: `tests/test_config.py`, `tests/test_runtime.py`

**Interfaces:**
- Consumes: `StructureAnalyst`/`build_structure_analyst_runner` (Task 8).
- Produces: `Settings.structure_analyst_interval: int = 180`; `Runtime.structure_analyst: StructureAnalyst`; `Runtime.agents` gains a 7th entry. No `novelizer/scheduler.py` change — `Scheduler` already operates on any `Runtime.agents` list (see Architecture section above).

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_config.py`:

```python
def test_structure_analyst_interval_default():
    s = Settings()
    assert s.structure_analyst_interval > 0
```

Append to `tests/test_runtime.py`:

```python
async def test_runtime_wires_structure_analyst_as_a_seventh_agent():
    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    try:
        settings = Settings(db_path=path)
        runners = _all_fake_runners()
        runners["structure_analyst"] = _FakeAgentRunner()
        rt = Runtime(settings, runners=runners)
        await rt.start()
        assert {a.name for a in rt.agents} == {
            "world_architect", "author", "character_keeper", "editor",
            "continuity_checker", "retconner", "structure_analyst",
        }
        assert rt.structure_analyst is not None
        assert rt.structure_analyst._committer is rt.committer
        assert rt.structure_analyst.interval == settings.structure_analyst_interval
        await rt.close()
    finally:
        os.unlink(path)


async def test_runtime_wires_structure_analyst_personality_from_the_pack():
    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    try:
        settings = Settings(db_path=path)
        runners = _all_fake_runners()
        runners["structure_analyst"] = _FakeAgentRunner()
        rt = Runtime(settings, runners=runners)
        await rt.start()
        assert rt.structure_analyst.personality == rt.voice_pack.agent_personalities.get("structure_analyst", "")
        await rt.close()
    finally:
        os.unlink(path)
```

Also update `_all_fake_runners()` in `tests/test_runtime.py` to include `"structure_analyst"`:

```python
def _all_fake_runners():
    return {
        name: _FakeAgentRunner()
        for name in (
            "author", "world_architect", "character_keeper", "editor",
            "continuity_checker", "retconner", "structure_analyst",
        )
    }
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_config.py tests/test_runtime.py -v`
Expected: FAIL — `test_structure_analyst_interval_default` fails with `AttributeError: 'Settings' object has no attribute 'structure_analyst_interval'`; every test using `_all_fake_runners()` (now including `"structure_analyst"`) fails at `Runtime.start()` with a `KeyError: 'structure_analyst'` from `_runner_for`, since `Runtime` doesn't request that runner yet.

- [ ] **Step 3: Implement**

In `novelizer/config.py`, add the new interval setting after `continuity_interval`:

```python
    # Cadence (seconds)
    author_interval: int = 300
    default_agent_interval: int = 120
    continuity_interval: int = 900
    structure_analyst_interval: int = 180
    projector_interval: float = 0.5
```

In `novelizer/runtime.py`, add the import, the `self.structure_analyst = None` init, the agent construction, and the `agents` list entry:

```python
from novelizer.agents.structure_analyst import StructureAnalyst, build_structure_analyst_runner
```

```python
        self.continuity_checker = None
        self.retconner = None
        self.structure_analyst = None
        self.scheduler: Optional[Scheduler] = None
```

```python
        self.retconner = Retconner(
            self._runner_for("retconner", build_retconner_runner), self.read, self.committer,
            interval=s.default_agent_interval, personality=personalities.get("retconner", ""),
        )
        self.structure_analyst = StructureAnalyst(
            self._runner_for("structure_analyst", build_structure_analyst_runner), self.read, self.committer,
            interval=s.structure_analyst_interval, personality=personalities.get("structure_analyst", ""),
        )
        self.agents = [
            self.world_architect, self.character_keeper, self.author,
            self.editor, self.continuity_checker, self.retconner, self.structure_analyst,
        ]
        self.scheduler = Scheduler(self.agents, self.read)
```

(Only the new import, the `self.structure_analyst = None` line in `__init__`, the new `StructureAnalyst(...)` construction, and its addition to the `self.agents` list are new; every other agent's construction, `_runner_for`, `start`, `close` are unchanged. `personalities.get("structure_analyst", "")` follows the exact fallback-to-empty-string pattern every other agent already uses, so a voice pack without a `structure_analyst` personality entry — including the shipped `default.toml` — leaves `rt.structure_analyst.personality == ""`, matching `test_runtime_missing_personality_falls_back_to_empty_string`'s existing pattern for other agents.)

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_config.py tests/test_runtime.py -v`
Expected: PASS (all prior + 3 new). Then `uv run pytest tests/ -v` for the full suite green (this also re-runs every existing `Runtime`/`Scheduler` test, confirming the 7th agent doesn't perturb the deterministic scheduler-driven tests in `test_scheduler_drives_full_retcon_loop_end_to_end`, since that test explicitly pauses all agents except the one under test).

- [ ] **Step 5: Commit**

```bash
git add novelizer/config.py novelizer/runtime.py tests/test_config.py tests/test_runtime.py
git commit -m "feat: wire StructureAnalyst into Runtime as the 7th scheduled agent"
```

---

### Task 10: Docs — mark M3.2 complete, document staleness/pacing analysis

**Files:**
- Modify: `docs/submilestones/M3-shape-and-threads.md`
- Modify: `README.md`

**Interfaces:** none (documentation only).

- [ ] **Step 1: Update the sub-milestone table**

In `docs/submilestones/M3-shape-and-threads.md`, change the M3.2 row's `Status` cell from `⬜ not started` to `✅ complete`.

- [ ] **Step 2: Add a README section**

In `README.md`, add a new subsection immediately after the "The thread ledger (Story Brain, Phase 1)" subsection added in M3.1 (before any following top-level heading):

```markdown
### Staleness & pacing analysis (Story Brain, Phase 1 continued)

Two deterministic functions in `novelizer/brain/` derive narrative signal from
canon with no LLM call: `staleness.is_thread_stale`/`stale_threads` (a thread
is stale once 3 chapters have passed since its last plant/touch, with no
pay-off/abandon in between) and `sag_spike.detect_sag_spike` (flags a chapter
whose tension score deviates sharply from the surrounding average). Both are
pure functions over `ReadStore` data, computed live rather than persisted, so
every consumer — agent prompts and TUI views alike, from M3.3 onward — shares
one answer.

A 7th scheduled agent, the **Structure Analyst**, produces the tension/pacing
scores those functions consume: it reads recently-drafted, not-yet-scored
chapters and asks the LLM for a `tension` (0.0–1.0) and `pacing_label` per
chapter, committing `annotation.structure_scored` events — never gated by
autonomy level, same as thread bookkeeping. It participates in the same
readiness-scored scheduler tick as the other six agents, with its own
interval (`NOVELIZER_STRUCTURE_ANALYST_INTERVAL`, default 180s).

Story Shape/Thread Board TUI views and prompt injection of stale threads and
pacing flags back to the Author/Editor are M3.3.
```

- [ ] **Step 3: Commit**

```bash
git add docs/submilestones/M3-shape-and-threads.md README.md
git commit -m "docs: mark M3.2 complete; document staleness/pacing analysis"
```

---

## Self-Review

**Spec coverage against the M3.2 row and Load-bearing design decisions in `docs/submilestones/M3-shape-and-threads.md`:**
- "Deterministic `StalenessAnalyzer` ... a thread is stale once 3 chapters have elapsed since its last planted/touched event with no terminal event in between; the threshold is a named constant" — Task 6 (`STALENESS_THRESHOLD_CHAPTERS`, `is_thread_stale`, terminal-state short-circuit, boundary property test).
- "no LLM involved, fully unit-testable" — Tasks 6/7 are pure functions, zero I/O, zero LLM calls, exercised entirely by unit + Hypothesis tests.
- "`annotation.*` event domain, single event type `annotation.structure_scored`, bounded numeric payload (tension 0.0–1.0, pacing_label)" — Task 1 (`Field(ge=0.0, le=1.0)`, validated by `test_annotation_structure_scored_tension_is_bounded`).
- "emitted by a new lightweight Structure Analyst scheduled agent that reads recent chapters and asks the LLM for a score/label per chapter" — Task 8.
- "sag/spike detection is a pure function over the emitted scores, not an LLM call" — Task 7.
- "`annotation.structure_scored` added to `AutonomyPolicy._NEVER_GATED`" — Task 3.
- "Analyst wired into `novelizer/scheduler.py`/`novelizer/runtime.py` alongside the six existing agents" — Task 9 wires `runtime.py`; the Architecture section states explicitly, with evidence from reading `tests/test_scheduler.py`, that `scheduler.py` requires no code change since `Scheduler` is already agent-list-generic — flagged as a design surprise, not silently dropped.
- "`readiness()` proportional to the count of unscored recent chapters and returns `0.0` when there are none" — Task 8 (`readiness()`), pinned by `test_readiness_is_zero_with_no_unscored_chapters` and `test_readiness_is_proportional_to_unscored_chapter_count`.
- "its own `ready_for_interval` cadence" — Task 9 (`structure_analyst_interval` setting, distinct from `default_agent_interval`).
- "tests drive `analyst.run_once()` directly ... rather than relying on `Scheduler.tick()`" — every `StructureAnalyst` test in Task 8 calls `run_once()`/`poll()`/`work()`/`commit()` directly; `Scheduler.tick()` is never invoked in that test file.
- Done-when: "Seeding a fixture with 4 chapters and no `thread.touched`/`thread.planted` in the last 3 makes `StalenessAnalyzer` report the thread stale" — Task 6's `test_thread_stale_once_three_chapters_have_elapsed`/`test_thread_stale_at_exactly_the_threshold`. "calling `analyst.run_once()` against a fixture with an artificially flat chapter produces an `annotation.structure_scored` event and the sag-detection pure function flags it" — Task 8's `test_run_once_emits_a_structure_scored_event_per_chapter` (event production) composed with Task 7's `test_flat_chapter_amid_high_tension_is_flagged_sag` (sag detection over the resulting scores) — the two halves of this done-when are each covered by a dedicated test, and Task 7's test uses the exact "flat chapter amid higher-tension neighbors" shape the doc describes. "a test confirms `annotation.structure_scored` is never gated" — Task 3.

**Design decisions the M3.2 row left open, resolved here (flagged per the dispatch instructions):**
1. **"3 chapters elapsed" is measured by chapter list position, not a timestamp or word count.** `ThreadRecord.last_chapter_id` (already populated at plant/touch time by M3.1's Projector) is looked up in `ReadStore.list_chapters()`'s chronological (insertion/rowid) order; `chapters_elapsed_since` counts how many chapters come after it. An id not found in the chapter list (e.g. a thread whose last event carried an empty `chapter_id`) is treated as maximally stale — a conservative default, stated explicitly in the function's docstring and covered by `test_chapters_elapsed_since_unknown_id_is_maximally_stale`.
2. **Module location: `novelizer/brain/`, a new top-level package.** Chosen over adding staleness/sag-spike logic to `novelizer/canon/` (which owns event sourcing plumbing, not derived narrative judgment) or `novelizer/agents/` (which owns LLM-driven work, not pure analysis) because the vision spec's Story Brain bounded context is explicitly "analyzers → brain projections," separate from World Canon and the Agent Roster, and M3.3/M4 add three more faculties (Knowledge, Causality, plus the Threads/Structure ones started here) that will want the same home.
3. **Structure Analyst scores are batched, one LLM call per `run_once()` covering up to 5 unscored chapters** (`_BATCH_SIZE = 5`, mirroring `CharacterKeeper.poll()`'s existing `chapters[-5:]` convention), not one call per chapter. Chosen for consistency with every existing agent's batching pattern (`KeeperOutput.updated_characters`, `WorldEntriesDraft.entries` are all lists returned from a single structured-output call) and because per-chapter calls would multiply LLM round-trips for no stated benefit — the doc's done-when only requires the resulting events and the sag-detection function to work, not a particular call cadence.
4. **Hallucinated/unrequested `chapter_id`s in a `StructureAnalystOutput` are dropped with a logged warning, no event committed** — the same defensive pattern M3.1 established for unknown thread-intent ids (`BaseAgent._commit_thread_intents`). Not stated in the M3.2 row, but consistent with the codebase's one existing precedent for agent-declared-but-invalid references.
5. **`SAG_SPIKE_DELTA = 0.3`, deviation-from-mean over all provided scores** (not a rolling/windowed comparison against immediate neighbors) was chosen for `detect_sag_spike` because it's simpler, still produces the doc's stated "flat chapter amid higher-tension chapters" detection, and is easier to state as a clean, generalizable Hypothesis property (`|tension - mean| >= delta` implies flagged, in the matching direction) than a neighbor-window heuristic would be.

**Placeholder scan:** every task's Step 3 shows complete code — full new files (`brain/__init__.py`, `brain/staleness.py`, `brain/sag_spike.py`, `agents/structure_analyst.py`) or exact before/after snippets anchored to the current file contents (re-read from the post-M3.1-merge `master` branch immediately before writing this plan: `events.py`, `policy.py`, `projector.py`, `read_store.py`, `store/models.py`, `scheduler.py`, `runtime.py`, `config.py`, `character_keeper.py`, `test_runtime.py`, `test_scheduler.py`, `test_config.py`). No "similar to Task N", no `...` elisions, no TODOs.

**Type consistency:** `StructureScore.tension`/`ChapterScore.tension`/`AnnotationStructureScored.tension` all use the identical `Field(ge=0.0, le=1.0)` bound (Tasks 1, 2, 8) — no drift between the canon payload, the read-side row, and the agent's structured-output item type. `detect_sag_spike(scores: list[StructureScore], ...)` (Task 7) matches `ReadStore.list_structure_scores() -> list[StructureScore]` (Task 5) exactly, so a future `BrainContext`/Story-Shape consumer can pipe one into the other with no adaptation. `is_thread_stale(thread: ThreadRecord, chapters: list[Chapter], ...)` (Task 6) matches `ReadStore.list_threads() -> list[ThreadRecord]` (M3.1) and `ReadStore.list_chapters() -> list[Chapter]` (existing) exactly.

**DDD/SOLID:**
- Single Responsibility: `novelizer/brain/staleness.py` only computes staleness; `novelizer/brain/sag_spike.py` only detects deviation; `StructureAnalyst` only produces scores; `Projector`'s new branch and `ReadStore`'s two new methods are each the one place their respective concern lives.
- Open/Closed: `Runtime.__init__`/`start` gain one new agent construction and one new `agents` list entry, following the exact pattern of the six existing agents — no existing agent's wiring is modified. `Scheduler` requires zero changes (see Architecture section).
- Dependency Inversion / bounded context: `novelizer/brain/` depends only on `novelizer.store.models` (plain pydantic data) and `novelizer.canon.threads` (M3.1's shared constant) — never on `ReadStore`, `EventStore`, or agent internals directly, keeping it a pure, framework-free analysis layer that any future caller (agent prompt builder, TUI widget) can invoke without a database connection in hand. `StructureAnalyst` depends only on `ReadStore`/`Committer`, exactly like every other agent.
- Event sourcing: `structure_scores`, like `threads`, is a disposable, rebuildable projection (Task 4's rebuild-equivalence and reset-state tests assert this); no persistence path bypasses the event log; staleness is explicitly never persisted (Task 6's docstring and the Architecture section both state this as a deliberate M3.3-forward-compatibility choice).

**Backward-compatibility check:** `StructureAnalyst` is additive — no existing agent's `poll()`/`work()`/`commit()` signature or prompt content changes. `Runtime.agents` grows from 6 to 7 entries; the one existing test that iterates all agents by name (`test_full_pipeline_runs_under_runtime`'s `{a.name for a in rt.agents}` assertion) is in `tests/test_runtime.py` but asserts a fixed 6-name set built from `runners` that does *not* include `"structure_analyst"` — since `Runtime._runner_for` only special-cases `self._runners is not None` (using `self._runners[name]`, a dict lookup that will `KeyError` if `structure_analyst` is missing from that test's `runners` dict), Task 9's `_all_fake_runners()` update is required for that test (and every other test using it) to keep passing; this is called out explicitly in Task 9's Step 3 note and Step 1's test additions replace `_all_fake_runners()` in place so no test using it is left broken. `test_scheduler_drives_full_retcon_loop_end_to_end` explicitly pauses every agent except the one under test in each phase, so the new 7th agent being unpaused-by-default doesn't perturb its deterministic agent-selection assertions — confirmed by reading that test's `rt.scheduler.pause_agent(name)` loop, which pauses everyone not under test by name, and `structure_analyst` simply isn't named as a target in either phase, but must be running: re-checking the test, phase 1 pauses `("world_architect", "author", "character_keeper", "editor", "retconner")` and leaves `continuity_checker` (the phase-1 target) and `structure_analyst` both unpaused — `structure_analyst`'s `readiness()` is `0.0` whenever there are no unscored chapters (true throughout that test, since no chapters are ever created via the Author in this test), so it never outscores `continuity_checker` and never gets selected, leaving the test's assertions intact.
