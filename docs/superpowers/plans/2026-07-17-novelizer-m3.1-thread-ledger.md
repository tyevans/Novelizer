# M3.1 · Thread Ledger Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** A new `thread.*` canon event domain (`planted`, `touched`, `paid_off`, `abandoned`) lets the Author and Editor declare plot-thread bookkeeping in their existing structured output; those intents flow through the existing `Committer` seam into the log, a `ThreadsProjection` rebuilds a `threads` read table honoring the state machine `planted → touched* → paid_off|abandoned` with absorbing terminal states, and `ReadStore.list_threads()`/`get_thread()` expose it — with `thread.*` classified as never-gated so this bookkeeping always flows regardless of autonomy level.

**Architecture:** Thread bookkeeping is additive canon, following the exact M2.1–M2.3 precedent: it flows through the existing `Committer`/`Projector`/`ReadStore` seams, not new ones. (1) **Thread identity is minted once, at plant time.** The Author/Editor structured output gains an optional `ThreadIntent` list; a `plant` intent carries a freeform `name` which `novelizer.canon.threads.slugify_thread_name` turns into the thread's `aggregate_id` — no id is ever invented by an agent. `touch`/`pay_off`/`abandon` intents must cite an `id`; each agent validates that id against the set of non-terminal thread ids returned by `ReadStore.list_threads()` at poll time (the `BrainContext`/active-thread-list provider that will supersede this validation seam is M3.3's concern — M3.1 uses the read store directly, which is available today and sufficient for the M3.1 done-when). An intent citing an unknown or already-terminal id is dropped with a logged warning and no event is committed. (2) **A single shared commit helper, not duplicated logic.** `BaseAgent._commit_thread_intents()` turns a validated `list[ThreadIntent]` into `Committer.commit()` calls; `Author.commit()` and `Editor.commit()` both call it after their existing chapter-related commits, exactly as `_remark()` and `_consume_signals()` are already shared. (3) **The projection accumulates state, not just the latest payload.** Unlike `chapters`/`characters` (whose events always carry a full snapshot, so `INSERT OR REPLACE` on the raw payload is correct), `thread.touched`/`paid_off`/`abandoned` payloads are small deltas (just an `id` and a note) — the `Projector` reads the current `ThreadRecord` row, applies the state transition only if the thread is not already in a terminal state (absorbing-terminal-state contract), and writes the merged record back. `thread.planted` always inserts a fresh row (thread ids are minted once, so no prior row can exist for a given id under correct agent behavior). (4) **No cross-event transaction.** A single `run_once()` that both creates a chapter and declares thread intents performs N+1 independent `Committer.commit()` appends — the existing precedent (chapter + remark + signal consumption as separate appends) is unchanged and extended, not replaced.

**Tech Stack:** Python 3.13, `pydantic` v2, `aiosqlite`, `pytest`+`pytest-asyncio` (`asyncio_mode=auto`), `hypothesis>=6.156.6` (already a dependency, first use in this codebase — Task 5 is the first Hypothesis-based test).

## Global Constraints

- Event sourcing: the `threads` table is a Projector-owned projection, rebuildable from the log exactly like every other projection; only the Projector writes it.
- `thread.*` and no other new event types are introduced in M3.1 (no `annotation.*` — that's M3.2).
- `thread.*` is added to `AutonomyPolicy._NEVER_GATED` — thread commits never enter the proposal queue at any autonomy level.
- Thread identity: an id is minted only once, at `thread.planted`, via `slugify_thread_name(name)`; every other thread event type carries only an `id` reference, never a name.
- Terminal states (`paid_off`, `abandoned`) are absorbing: once reached, further `thread.*` events for that id are appended to the log as facts but are projection no-ops — they never change projected state.
- DRY: thread-intent-to-event translation lives in exactly one place, `BaseAgent._commit_thread_intents`, called identically by `Author.commit()` and `Editor.commit()`.
- TDD, black-box-first: every task starts with a failing test asserting on observable events/projections/output, not internals. Task 5's Hypothesis property test generalizes the state-machine invariant across any valid event sequence, per M3's standing principle of property-based tests where invariants generalize.
- Backward compatibility: `ChapterDraft.thread_intents` and `EditorVerdict.thread_intents` both default to `[]`; when empty, `Author.commit()`/`Editor.commit()` call `_commit_thread_intents([], ...)`, which is a no-op — zero extra events, byte-identical behavior to pre-M3.1. The existing test suite stays green throughout.

---

### Task 1: `thread.*` event types, payload models, and the id-slugging helper

**Files:**
- Modify: `novelizer/canon/events.py`
- Create: `novelizer/canon/threads.py`
- Test: `novelizer/canon/events.py` tests in `tests/canon/test_events.py`; new `tests/canon/test_threads.py`

**Interfaces:**
- Produces: `EventType.THREAD_PLANTED = "thread.planted"`, `EventType.THREAD_TOUCHED = "thread.touched"`, `EventType.THREAD_PAID_OFF = "thread.paid_off"`, `EventType.THREAD_ABANDONED = "thread.abandoned"`; payload models `ThreadPlanted(id, name, chapter_id="", note="")`, `ThreadTouched(id, chapter_id="", note="")`, `ThreadPaidOff(id, chapter_id="", note="")`, `ThreadAbandoned(id, chapter_id="", note="")` in `novelizer/canon/events.py`; `slugify_thread_name(name: str) -> str` and `TERMINAL_STATES: set[str] = {"paid_off", "abandoned"}` in `novelizer/canon/threads.py`.

- [ ] **Step 1: Write the failing tests**

Append to `tests/canon/test_events.py`:

```python
def test_thread_event_types_exist():
    from novelizer.canon.events import EventType
    assert EventType.THREAD_PLANTED == "thread.planted"
    assert EventType.THREAD_TOUCHED == "thread.touched"
    assert EventType.THREAD_PAID_OFF == "thread.paid_off"
    assert EventType.THREAD_ABANDONED == "thread.abandoned"


def test_thread_payload_models_roundtrip():
    from novelizer.canon.events import ThreadPlanted, ThreadTouched, ThreadPaidOff, ThreadAbandoned
    planted = ThreadPlanted(id="the-locket", name="The Locket", chapter_id="c1", note="introduced")
    assert ThreadPlanted.model_validate_json(planted.model_dump_json()) == planted
    for cls in (ThreadTouched, ThreadPaidOff, ThreadAbandoned):
        inst = cls(id="the-locket", chapter_id="c2", note="advanced")
        assert cls.model_validate_json(inst.model_dump_json()) == inst
```

Create `tests/canon/test_threads.py`:

```python
from novelizer.canon.threads import slugify_thread_name, TERMINAL_STATES


def test_slugify_lowercases_and_hyphenates():
    assert slugify_thread_name("The Locket's Secret") == "the-locket-s-secret"


def test_slugify_strips_leading_trailing_punctuation():
    assert slugify_thread_name("  --Mira's Revenge!!--  ") == "mira-s-revenge"


def test_slugify_falls_back_when_name_has_no_alnum_chars():
    assert slugify_thread_name("###") == "thread"


def test_terminal_states_are_paid_off_and_abandoned():
    assert TERMINAL_STATES == {"paid_off", "abandoned"}
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/canon/test_events.py tests/canon/test_threads.py -v`
Expected: FAIL — `AttributeError: type object 'EventType' has no attribute 'THREAD_PLANTED'` and `ModuleNotFoundError: No module named 'novelizer.canon.threads'`.

- [ ] **Step 3: Implement**

In `novelizer/canon/events.py`, add the four event type constants to `EventType` (after `AGENT_REMARKED`) and the four payload models (after `AgentRemark`):

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


class StoredEvent(BaseModel):
    sequence: int
    id: str
    event_type: str
    aggregate_id: str
    payload: dict[str, Any]
    created_at: str


class AgentRemark(BaseModel):
    """Payload for agent.remarked — a short in-personality feed line.

    Feed-flavor only: never gated (see AutonomyPolicy._NEVER_GATED), never
    projected (the Projector has no _apply branch for it, by design).
    """

    agent_name: str
    note: str


class ThreadPlanted(BaseModel):
    """Payload for thread.planted — mints a new thread's identity.

    `id` is the slug minted from `name` (see
    novelizer.canon.threads.slugify_thread_name) at plant time; every later
    thread.* event for this thread must cite this id, never re-derive it.
    """

    id: str
    name: str
    chapter_id: str = ""
    note: str = ""


class ThreadTouched(BaseModel):
    """Payload for thread.touched — an existing thread advances, cited by id."""

    id: str
    chapter_id: str = ""
    note: str = ""


class ThreadPaidOff(BaseModel):
    """Payload for thread.paid_off — an existing thread resolves, cited by id.

    Terminal: the ThreadsProjection treats this id as absorbing thereafter.
    """

    id: str
    chapter_id: str = ""
    note: str = ""


class ThreadAbandoned(BaseModel):
    """Payload for thread.abandoned — an existing thread is dropped, cited by id.

    Terminal: the ThreadsProjection treats this id as absorbing thereafter.
    """

    id: str
    chapter_id: str = ""
    note: str = ""
```

Create `novelizer/canon/threads.py`:

```python
from __future__ import annotations
import re

_SLUG_RE = re.compile(r"[^a-z0-9]+")

TERMINAL_STATES: set[str] = {"paid_off", "abandoned"}


def slugify_thread_name(name: str) -> str:
    """Turn a freeform thread name into a stable, id-safe slug.

    Lowercases, collapses runs of non-alphanumeric characters into single
    hyphens, and strips leading/trailing hyphens. Called exactly once, at
    thread.planted time, to mint a thread's aggregate_id from the Author's
    or Editor's freeform name — see M3.1's thread identity rule: no other
    thread.* event type ever mints or re-derives an id.
    """
    slug = _SLUG_RE.sub("-", name.strip().lower()).strip("-")
    return slug or "thread"
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/canon/test_events.py tests/canon/test_threads.py -v`
Expected: PASS (all prior + 6 new). Then `uv run pytest tests/ -v` for the full suite green.

- [ ] **Step 5: Commit**

```bash
git add novelizer/canon/events.py novelizer/canon/threads.py tests/canon/test_events.py tests/canon/test_threads.py
git commit -m "feat: thread.* event types, payloads, and id-slugging helper"
```

---

### Task 2: `ThreadState` / `ThreadRecord` read-side models

**Files:**
- Modify: `novelizer/store/models.py`
- Test: `tests/store/test_models.py`

**Interfaces:**
- Consumes: nothing new.
- Produces: `ThreadState(StrEnum)` with members `planted`, `touched`, `paid_off`, `abandoned`; `ThreadRecord(BaseModel)` with fields `id: str`, `name: str`, `state: ThreadState = ThreadState.planted`, `touch_count: int = 0`, `last_note: str = ""`, `last_chapter_id: str = ""` — the row shape the `ThreadsProjection` (Task 4) stores and `ReadStore.list_threads()`/`get_thread()` (Task 6) return.

- [ ] **Step 1: Write the failing test**

Append to `tests/store/test_models.py`:

```python
from novelizer.store.models import ThreadState, ThreadRecord


def test_thread_record_defaults():
    t = ThreadRecord(id="the-locket", name="The Locket")
    assert t.state == ThreadState.planted
    assert t.touch_count == 0
    assert t.last_note == ""
    assert t.last_chapter_id == ""


def test_thread_record_roundtrips_through_json():
    t = ThreadRecord(id="the-locket", name="The Locket", state=ThreadState.touched, touch_count=2, last_note="advanced")
    again = ThreadRecord.model_validate_json(t.model_dump_json())
    assert again == t
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/store/test_models.py -v`
Expected: FAIL — `ImportError: cannot import name 'ThreadState' from 'novelizer.store.models'`.

- [ ] **Step 3: Implement**

In `novelizer/store/models.py`, add `ThreadState` after `SignalKind` and `ThreadRecord` after `Character`:

```python
class SignalKind(StrEnum):
    seed = "seed"
    focus = "focus"
    override = "override"
    note = "note"


class ThreadState(StrEnum):
    planted = "planted"
    touched = "touched"
    paid_off = "paid_off"
    abandoned = "abandoned"
```

```python
class ThreadRecord(BaseModel):
    """Read-side row for a plot thread, built and rebuilt by the Projector
    from the thread.* event log (see novelizer/canon/projector.py). Unlike
    Character/Chapter, thread.* events after the first carry only deltas
    (id + note), so this model's fields accumulate state across events
    rather than being replaced wholesale by each event's payload.
    """

    id: str
    name: str
    state: ThreadState = ThreadState.planted
    touch_count: int = 0
    last_note: str = ""
    last_chapter_id: str = ""
```

(Only `ThreadState` and `ThreadRecord` are new; every other class in the file is unchanged.)

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/store/test_models.py -v`
Expected: PASS. Then `uv run pytest tests/ -v` for the full suite green.

- [ ] **Step 5: Commit**

```bash
git add novelizer/store/models.py tests/store/test_models.py
git commit -m "feat: ThreadState/ThreadRecord read-side models for the thread ledger"
```

---

### Task 3: `thread.*` is never gated

**Files:**
- Modify: `novelizer/canon/policy.py`
- Test: `tests/canon/test_policy.py`

**Interfaces:**
- Consumes: `EventType.THREAD_PLANTED/THREAD_TOUCHED/THREAD_PAID_OFF/THREAD_ABANDONED` (Task 1).
- Produces: no new public interface — `AutonomyPolicy._NEVER_GATED` gains the four thread event types.

- [ ] **Step 1: Write the failing test**

Append to `tests/canon/test_policy.py`:

```python
@pytest.mark.parametrize("level", list(AutonomyLevel))
@pytest.mark.parametrize("event_type", [
    EventType.THREAD_PLANTED, EventType.THREAD_TOUCHED,
    EventType.THREAD_PAID_OFF, EventType.THREAD_ABANDONED,
])
async def test_thread_events_are_never_gated(level, event_type):
    policy = AutonomyPolicy(FakeRead(AutonomyState(global_level=level)))
    assert await policy.is_gated("author", event_type) is False
    assert await policy.is_gated("editor", event_type) is False
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/canon/test_policy.py -v`
Expected: FAIL — under `AutonomyLevel.gated_all`, `is_gated` currently returns `True` for `thread.planted` etc. since they aren't yet in `_NEVER_GATED`.

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
}
```

(Only the four `THREAD_*` entries are new; `_RETCON_EVENTS`, `_CANON_EVENTS`, `_GATED_SETS`, and `AutonomyPolicy.is_gated` are unchanged.)

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/canon/test_policy.py -v`
Expected: PASS (all prior + 8 new parametrized cases). Then `uv run pytest tests/ -v` for the full suite green.

- [ ] **Step 5: Commit**

```bash
git add novelizer/canon/policy.py tests/canon/test_policy.py
git commit -m "feat: thread.* events are never gated by AutonomyPolicy"
```

---

### Task 4: `ThreadsProjection` — the `threads` table and its state-machine apply logic

**Files:**
- Modify: `novelizer/canon/projector.py`
- Test: `tests/canon/test_projector.py`

**Interfaces:**
- Consumes: `EventType.THREAD_*` (Task 1); `ThreadRecord`/`ThreadState` (Task 2); `TERMINAL_STATES` (Task 1).
- Produces: a `threads` table (`id TEXT PRIMARY KEY, data TEXT NOT NULL, state TEXT NOT NULL`) maintained by `Projector._apply`; `threads` is added to `Projector._reset_state`'s cleared-tables tuple.

- [ ] **Step 1: Write the failing tests**

Append to `tests/canon/test_projector.py`:

```python
async def _thread_rows(proj):
    cur = await proj._conn.execute("SELECT data FROM threads ORDER BY rowid")
    return [json.loads(r[0]) for r in await cur.fetchall()]


async def test_thread_planted_is_projected(wired):
    from novelizer.canon.events import ThreadPlanted
    events, proj, _ = wired
    await events.append(EventType.THREAD_PLANTED, "the-locket",
                        ThreadPlanted(id="the-locket", name="The Locket"))
    await proj.catch_up()
    rows = await _thread_rows(proj)
    assert len(rows) == 1
    assert rows[0]["id"] == "the-locket" and rows[0]["state"] == "planted"


async def test_thread_touched_increments_count_and_updates_state(wired):
    from novelizer.canon.events import ThreadPlanted, ThreadTouched
    events, proj, _ = wired
    await events.append(EventType.THREAD_PLANTED, "the-locket", ThreadPlanted(id="the-locket", name="The Locket"))
    await events.append(EventType.THREAD_TOUCHED, "the-locket", ThreadTouched(id="the-locket", note="reappears"))
    await proj.catch_up()
    rows = await _thread_rows(proj)
    assert rows[0]["state"] == "touched"
    assert rows[0]["touch_count"] == 1
    assert rows[0]["last_note"] == "reappears"


async def test_thread_paid_off_is_terminal_and_absorbs_later_events(wired):
    from novelizer.canon.events import ThreadPlanted, ThreadTouched, ThreadPaidOff
    events, proj, _ = wired
    await events.append(EventType.THREAD_PLANTED, "the-locket", ThreadPlanted(id="the-locket", name="The Locket"))
    await events.append(EventType.THREAD_PAID_OFF, "the-locket", ThreadPaidOff(id="the-locket", note="resolved"))
    # A late touch after pay-off must be a no-op: the event lands in the log but the projection is unchanged.
    await events.append(EventType.THREAD_TOUCHED, "the-locket", ThreadTouched(id="the-locket", note="should not apply"))
    await proj.catch_up()
    rows = await _thread_rows(proj)
    assert rows[0]["state"] == "paid_off"
    assert rows[0]["touch_count"] == 0
    assert rows[0]["last_note"] == "resolved"
    log = await events.events_since(0)
    assert len(log) == 3  # the late touch is still a fact in the log


async def test_thread_abandoned_is_terminal(wired):
    from novelizer.canon.events import ThreadPlanted, ThreadAbandoned
    events, proj, _ = wired
    await events.append(EventType.THREAD_PLANTED, "the-locket", ThreadPlanted(id="the-locket", name="The Locket"))
    await events.append(EventType.THREAD_ABANDONED, "the-locket", ThreadAbandoned(id="the-locket"))
    await proj.catch_up()
    rows = await _thread_rows(proj)
    assert rows[0]["state"] == "abandoned"


async def test_reprojecting_thread_events_is_equivalent(wired):
    from novelizer.canon.events import ThreadPlanted, ThreadTouched, ThreadPaidOff
    events, proj, path = wired
    await events.append(EventType.THREAD_PLANTED, "the-locket", ThreadPlanted(id="the-locket", name="The Locket"))
    await events.append(EventType.THREAD_TOUCHED, "the-locket", ThreadTouched(id="the-locket"))
    await events.append(EventType.THREAD_PAID_OFF, "the-locket", ThreadPaidOff(id="the-locket"))
    await proj.catch_up()
    incremental = await _thread_rows(proj)
    proj2 = Projector(events, path)
    await proj2.init()
    await proj2._reset_state()
    await proj2.catch_up()
    from_scratch = await _thread_rows(proj2)
    await proj2.close()
    assert incremental == from_scratch


async def test_reset_state_clears_threads(wired):
    from novelizer.canon.events import ThreadPlanted
    events, proj, _ = wired
    await events.append(EventType.THREAD_PLANTED, "the-locket", ThreadPlanted(id="the-locket", name="The Locket"))
    await proj.catch_up()
    await proj._reset_state()
    cur = await proj._conn.execute("SELECT COUNT(*) FROM threads")
    assert (await cur.fetchone())[0] == 0
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/canon/test_projector.py -v`
Expected: FAIL — `sqlite3.OperationalError: no such table: threads` (the table doesn't exist yet).

- [ ] **Step 3: Implement**

In `novelizer/canon/projector.py`, add imports, the `threads` table to `_CREATE`, `"threads"` to `_reset_state`'s tuple, and three new `_apply` branches.

Add to the imports at the top of the file:

```python
from novelizer.store.models import ThreadRecord, ThreadState
from novelizer.canon.threads import TERMINAL_STATES
```

Add the `threads` table to `_CREATE` (after `projector_state`):

```python
CREATE TABLE IF NOT EXISTS threads (
    id TEXT PRIMARY KEY, data TEXT NOT NULL, state TEXT NOT NULL
);
```

Update `_reset_state`:

```python
    async def _reset_state(self) -> None:
        """Testing/rebuild helper: forget position and clear projections."""
        for table in (
            "chapters", "world_entries", "characters", "director_signals",
            "retcon_requests", "proposals", "autonomy_state", "threads",
        ):
            await self._conn.execute(f"DELETE FROM {table}")
        await self._set_last_sequence(0)
```

Add two new `elif` branches to `_apply`, immediately before the final `elif t == EventType.AUTONOMY_CHANGED:` branch:

```python
        elif t == EventType.THREAD_PLANTED:
            record = ThreadRecord(
                id=p["id"], name=p["name"], state=ThreadState.planted,
                last_note=p.get("note", ""), last_chapter_id=p.get("chapter_id", ""),
            )
            await self._conn.execute(
                "INSERT OR REPLACE INTO threads (id, data, state) VALUES (?,?,?)",
                (record.id, record.model_dump_json(), record.state.value),
            )
        elif t in (EventType.THREAD_TOUCHED, EventType.THREAD_PAID_OFF, EventType.THREAD_ABANDONED):
            cur = await self._conn.execute("SELECT data FROM threads WHERE id=?", (p["id"],))
            row = await cur.fetchone()
            if row is not None:
                record = ThreadRecord.model_validate_json(row[0])
                if record.state.value not in TERMINAL_STATES:
                    new_state = {
                        EventType.THREAD_TOUCHED: ThreadState.touched,
                        EventType.THREAD_PAID_OFF: ThreadState.paid_off,
                        EventType.THREAD_ABANDONED: ThreadState.abandoned,
                    }[t]
                    touch_count = record.touch_count + (1 if t == EventType.THREAD_TOUCHED else 0)
                    updated = record.model_copy(update={
                        "state": new_state,
                        "touch_count": touch_count,
                        "last_note": p.get("note", ""),
                        "last_chapter_id": p.get("chapter_id", ""),
                    })
                    await self._conn.execute(
                        "INSERT OR REPLACE INTO threads (id, data, state) VALUES (?,?,?)",
                        (updated.id, updated.model_dump_json(), updated.state.value),
                    )
                # else: absorbing terminal state — the event is a fact in the log,
                # but the threads projection does not change.
            # else: no row for this id yet (shouldn't happen under correct agent
            # behavior, since agents validate intents against known ids before
            # committing) — nothing to project, no error raised.
```

(Only these additions are new; every other branch of `_apply`, `init`, `catch_up`, `run`, `stop`, `_last_sequence`, `_set_last_sequence` are unchanged.)

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/canon/test_projector.py -v`
Expected: PASS (all prior + 6 new). Then `uv run pytest tests/ -v` for the full suite green.

- [ ] **Step 5: Commit**

```bash
git add novelizer/canon/projector.py tests/canon/test_projector.py
git commit -m "feat: ThreadsProjection builds the threads table with absorbing terminal states"
```

---

### Task 5: Hypothesis property test — thread state machine holds for any event sequence

**Files:**
- Create: `tests/canon/test_threads_projection_property.py`

**Interfaces:**
- Consumes: `Projector`, `EventStore`, `ReadStore` (existing); `EventType.THREAD_*`/payload models (Task 1); `ThreadState` (Task 2). No new production code — this task is test-only, exercising Task 4's implementation.

- [ ] **Step 1: Write the property test**

Create `tests/canon/test_threads_projection_property.py`:

```python
import asyncio
import os
import tempfile
from hypothesis import given, settings, strategies as st
from novelizer.canon.event_store import EventStore
from novelizer.canon.projector import Projector
from novelizer.canon.read_store import ReadStore
from novelizer.canon.events import (
    EventType, ThreadPlanted, ThreadTouched, ThreadPaidOff, ThreadAbandoned,
)
from novelizer.store.models import ThreadState

TERMINAL = {ThreadState.paid_off, ThreadState.abandoned}

_ACTION_EVENTS = {
    "touch": (EventType.THREAD_TOUCHED, ThreadTouched, ThreadState.touched),
    "pay_off": (EventType.THREAD_PAID_OFF, ThreadPaidOff, ThreadState.paid_off),
    "abandon": (EventType.THREAD_ABANDONED, ThreadAbandoned, ThreadState.abandoned),
}


def _expected_state(actions: list[str]) -> tuple[ThreadState, int]:
    """Pure re-implementation of the state machine, independent of the
    Projector's SQL, used as the property test's oracle."""
    state = ThreadState.planted
    touch_count = 0
    for action in actions:
        if state in TERMINAL:
            continue  # absorbing: any event after a terminal state is a no-op
        _, _, new_state = _ACTION_EVENTS[action]
        if action == "touch":
            touch_count += 1
        state = new_state
    return state, touch_count


async def _run_sequence(actions: list[str]) -> tuple[ThreadState, int, ThreadState, int]:
    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    try:
        events = EventStore(path)
        await events.init()
        proj = Projector(events, path)
        await proj.init()
        read = ReadStore(path)
        await read.init()

        await events.append(EventType.THREAD_PLANTED, "t1", ThreadPlanted(id="t1", name="The Locket"))
        for action in actions:
            event_type, payload_cls, _ = _ACTION_EVENTS[action]
            await events.append(event_type, "t1", payload_cls(id="t1"))
        await proj.catch_up()
        record = await read.get_thread("t1")
        incremental_state, incremental_count = record.state, record.touch_count

        # Rebuild equivalence: a fresh projector replaying from zero agrees.
        proj2 = Projector(events, path)
        await proj2.init()
        await proj2._reset_state()
        await proj2.catch_up()
        rebuilt = await read.get_thread("t1")
        rebuilt_state, rebuilt_count = rebuilt.state, rebuilt.touch_count
        await proj2.close()

        await read.close()
        await proj.close()
        await events.close()
        return incremental_state, incremental_count, rebuilt_state, rebuilt_count
    finally:
        os.unlink(path)


@given(st.lists(st.sampled_from(["touch", "pay_off", "abandon"]), max_size=8))
@settings(max_examples=50, deadline=None)
def test_thread_state_machine_holds_for_any_event_sequence(actions):
    """For any interleaving of touch/pay_off/abandon events following a plant,
    the projected state and touch count match the pure state-machine oracle
    (including absorbing terminal states), and a from-scratch rebuild agrees
    with the incrementally-projected result (replay idempotence)."""
    incremental_state, incremental_count, rebuilt_state, rebuilt_count = asyncio.run(_run_sequence(actions))
    expected_state, expected_count = _expected_state(actions)
    assert incremental_state == expected_state
    assert incremental_count == expected_count
    assert rebuilt_state == expected_state
    assert rebuilt_count == expected_count
```

- [ ] **Step 2: Run the test to verify it passes**

Run: `uv run pytest tests/canon/test_threads_projection_property.py -v`
Expected: PASS (Hypothesis runs 50 generated sequences; since Task 4's implementation is already correct, this should pass immediately — its value is as a regression guard against future changes to the state-machine logic, and per M3.1's done-when, the property test is required to exist and hold). If it fails, the failure will point at a specific counterexample sequence — fix `Projector._apply`'s thread branches (Task 4) to match `_expected_state`'s oracle, not the other way around, since the oracle directly encodes the spec's stated state machine (`planted → touched* → paid_off|abandoned`, terminal states absorbing).

- [ ] **Step 3: Commit**

```bash
git add tests/canon/test_threads_projection_property.py
git commit -m "test: Hypothesis property test for thread state machine and rebuild equivalence"
```

---

### Task 6: `ReadStore.list_threads()` / `get_thread()`

**Files:**
- Modify: `novelizer/canon/read_store.py`
- Test: `tests/canon/test_read_store.py`

**Interfaces:**
- Consumes: `ThreadRecord` (Task 2); the `threads` table (Task 4).
- Produces: `ReadStore.list_threads() -> list[ThreadRecord]`; `ReadStore.get_thread(thread_id: str) -> Optional[ThreadRecord]`.

- [ ] **Step 1: Write the failing test**

Append to `tests/canon/test_read_store.py`:

```python
async def test_list_and_get_threads(stack):
    from novelizer.canon.events import ThreadPlanted, ThreadTouched
    events, proj, read = stack
    await events.append(EventType.THREAD_PLANTED, "the-locket", ThreadPlanted(id="the-locket", name="The Locket"))
    await events.append(EventType.THREAD_PLANTED, "mira-revenge", ThreadPlanted(id="mira-revenge", name="Mira's Revenge"))
    await events.append(EventType.THREAD_TOUCHED, "the-locket", ThreadTouched(id="the-locket", note="reappears"))
    await proj.catch_up()
    threads = await read.list_threads()
    assert {t.id for t in threads} == {"the-locket", "mira-revenge"}
    fetched = await read.get_thread("the-locket")
    assert fetched is not None and fetched.touch_count == 1
    assert await read.get_thread("missing") is None
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/canon/test_read_store.py::test_list_and_get_threads -v`
Expected: FAIL — `AttributeError: 'ReadStore' object has no attribute 'list_threads'`.

- [ ] **Step 3: Implement**

In `novelizer/canon/read_store.py`, add `ThreadRecord` to the import and two new methods, after `get_autonomy_state`:

```python
from novelizer.store.models import Chapter, WorldEntry, Character, DirectorSignal, RetconRequest, ThreadRecord
```

```python
    async def list_threads(self) -> list[ThreadRecord]:
        cur = await self._conn.execute("SELECT data FROM threads ORDER BY rowid")
        return [ThreadRecord.model_validate_json(r[0]) for r in await cur.fetchall()]

    async def get_thread(self, thread_id: str) -> Optional[ThreadRecord]:
        cur = await self._conn.execute("SELECT data FROM threads WHERE id=?", (thread_id,))
        row = await cur.fetchone()
        return ThreadRecord.model_validate_json(row[0]) if row else None
```

(Only the import addition and these two methods are new; every other method is unchanged.)

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/canon/test_read_store.py -v`
Expected: PASS (all prior + 1 new). Then `uv run pytest tests/ -v` for the full suite green.

- [ ] **Step 5: Commit**

```bash
git add novelizer/canon/read_store.py tests/canon/test_read_store.py
git commit -m "feat: ReadStore.list_threads()/get_thread() expose the thread ledger"
```

---

### Task 7: `ThreadIntent` schema; `ChapterDraft`/`EditorVerdict` gain `thread_intents`

**Files:**
- Modify: `novelizer/agents/schemas.py`
- Modify: `novelizer/agents/base.py` (`ChapterDraft`)
- Test: `tests/agents/test_schemas.py`

**Interfaces:**
- Produces: `ThreadIntent(BaseModel)` in `novelizer/agents/schemas.py` with fields `action: Literal["plant", "touch", "pay_off", "abandon"]`, `name: str = ""` (used only for `plant`), `id: str = ""` (used for `touch`/`pay_off`/`abandon`), `note: str = ""`; `EditorVerdict.thread_intents: list[ThreadIntent] = Field(default_factory=list)`; `ChapterDraft.thread_intents: list[ThreadIntent] = Field(default_factory=list)`.

- [ ] **Step 1: Write the failing tests**

Append to `tests/agents/test_schemas.py`:

```python
def test_thread_intent_plant_defaults():
    from novelizer.agents.schemas import ThreadIntent
    intent = ThreadIntent(action="plant", name="The Locket")
    assert intent.id == "" and intent.note == ""


def test_thread_intent_touch_roundtrips():
    from novelizer.agents.schemas import ThreadIntent
    intent = ThreadIntent(action="touch", id="the-locket", note="reappears")
    again = ThreadIntent.model_validate_json(intent.model_dump_json())
    assert again == intent


def test_editor_verdict_default_thread_intents_empty():
    assert EditorVerdict().thread_intents == []


def test_editor_verdict_carries_thread_intents():
    from novelizer.agents.schemas import ThreadIntent
    v = EditorVerdict(verdict="approve", thread_intents=[ThreadIntent(action="touch", id="the-locket")])
    assert v.thread_intents[0].id == "the-locket"


def test_chapter_draft_default_thread_intents_empty():
    from novelizer.agents.base import ChapterDraft
    assert ChapterDraft(title="T", prose="P").thread_intents == []


def test_chapter_draft_carries_thread_intents():
    from novelizer.agents.base import ChapterDraft
    from novelizer.agents.schemas import ThreadIntent
    d = ChapterDraft(title="T", prose="P", thread_intents=[ThreadIntent(action="plant", name="The Locket")])
    assert d.thread_intents[0].name == "The Locket"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/agents/test_schemas.py -v`
Expected: FAIL — `ImportError: cannot import name 'ThreadIntent' from 'novelizer.agents.schemas'`.

- [ ] **Step 3: Implement**

In `novelizer/agents/schemas.py`, add `ThreadIntent` after `CharacterUpdate` and add `thread_intents` to `EditorVerdict`:

```python
class ThreadIntent(BaseModel):
    """One agent-declared plot-thread action from structured output.

    `plant` mints a new thread from a freeform `name` (the system slugs it
    into an id — see novelizer.canon.threads.slugify_thread_name); `touch`,
    `pay_off`, and `abandon` must cite an existing thread's `id` rather than
    inventing one. `BaseAgent._commit_thread_intents` turns validated
    intents into thread.* commits (see novelizer/agents/base.py).
    """

    action: Literal["plant", "touch", "pay_off", "abandon"]
    name: str = ""
    id: str = ""
    note: str = ""


class EditorVerdict(BaseModel):
    verdict: Literal["approve", "revise"] = "approve"
    notes: str = ""
    feed_note: str = ""
    thread_intents: list[ThreadIntent] = Field(default_factory=list)
```

In `novelizer/agents/base.py`, add the import and the field to `ChapterDraft`:

```python
from novelizer.agents.schemas import ThreadIntent


class ChapterDraft(BaseModel):
    title: str
    prose: str
    character_ids: list[str] = Field(default_factory=list)
    feed_note: str = ""
    thread_intents: list[ThreadIntent] = Field(default_factory=list)
```

(Only `ThreadIntent`, the `thread_intents` field, and the one new import are new; `WorldEntryDraft`, `WorldEntriesDraft`, `CharacterUpdate`, `RetconDraft`, `KeeperOutput`, `ContinuityOutput`, `RetconAmendments` in `schemas.py`, and `Runner`/`BaseAgent` in `base.py`, are unchanged.)

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/agents/test_schemas.py -v`
Expected: PASS (all prior + 6 new). Then `uv run pytest tests/ -v` for the full suite green.

- [ ] **Step 5: Commit**

```bash
git add novelizer/agents/schemas.py novelizer/agents/base.py tests/agents/test_schemas.py
git commit -m "feat: ThreadIntent schema; ChapterDraft/EditorVerdict gain thread_intents"
```

---

### Task 8: `BaseAgent._commit_thread_intents` — the shared intent-to-event translator

**Files:**
- Modify: `novelizer/agents/base.py`
- Test: `tests/agents/test_base.py`

**Interfaces:**
- Consumes: `ThreadIntent` (Task 7); `EventType.THREAD_*`/payload models (Task 1); `slugify_thread_name` (Task 1).
- Produces: `BaseAgent._commit_thread_intents(self, intents: list[ThreadIntent], active_thread_ids: set[str], chapter_id: str = "") -> None`.

- [ ] **Step 1: Write the failing tests**

Append to `tests/agents/test_base.py`:

```python
from novelizer.agents.schemas import ThreadIntent
from novelizer.canon.events import ThreadPlanted


async def test_commit_thread_intents_plant_mints_slugged_id(stack):
    events, proj, read, committer = stack
    agent = BaseAgent(None, read, committer, interval=60, name="author")
    await agent._commit_thread_intents([ThreadIntent(action="plant", name="The Locket's Secret")], active_thread_ids=set())
    await proj.catch_up()
    log = await events.events_since(0)
    assert len(log) == 1
    assert log[0].event_type == EventType.THREAD_PLANTED
    assert log[0].payload["id"] == "the-locket-s-secret"
    assert log[0].payload["name"] == "The Locket's Secret"


async def test_commit_thread_intents_plant_dropped_when_name_blank(stack):
    events, proj, read, committer = stack
    agent = BaseAgent(None, read, committer, interval=60, name="author")
    await agent._commit_thread_intents([ThreadIntent(action="plant", name="   ")], active_thread_ids=set())
    assert await events.events_since(0) == []


async def test_commit_thread_intents_touch_commits_when_id_known(stack):
    events, proj, read, committer = stack
    agent = BaseAgent(None, read, committer, interval=60, name="editor")
    await agent._commit_thread_intents(
        [ThreadIntent(action="touch", id="the-locket", note="reappears")],
        active_thread_ids={"the-locket"}, chapter_id="c1",
    )
    log = await events.events_since(0)
    assert len(log) == 1
    assert log[0].event_type == EventType.THREAD_TOUCHED
    assert log[0].payload == {"id": "the-locket", "chapter_id": "c1", "note": "reappears"}


async def test_commit_thread_intents_drops_unknown_id_with_no_event(stack):
    events, proj, read, committer = stack
    agent = BaseAgent(None, read, committer, interval=60, name="author")
    await agent._commit_thread_intents(
        [ThreadIntent(action="pay_off", id="not-a-real-thread")], active_thread_ids={"the-locket"},
    )
    assert await events.events_since(0) == []


async def test_commit_thread_intents_handles_all_action_kinds(stack):
    events, proj, read, committer = stack
    agent = BaseAgent(None, read, committer, interval=60, name="editor")
    active = {"the-locket", "mira-revenge"}
    await agent._commit_thread_intents(
        [
            ThreadIntent(action="plant", name="New Thread"),
            ThreadIntent(action="touch", id="the-locket"),
            ThreadIntent(action="pay_off", id="mira-revenge"),
        ],
        active_thread_ids=active,
    )
    log = await events.events_since(0)
    assert [e.event_type for e in log] == [
        EventType.THREAD_PLANTED, EventType.THREAD_TOUCHED, EventType.THREAD_PAID_OFF,
    ]


async def test_commit_thread_intents_noop_on_empty_list(stack):
    events, proj, read, committer = stack
    agent = BaseAgent(None, read, committer, interval=60, name="author")
    await agent._commit_thread_intents([], active_thread_ids=set())
    assert await events.events_since(0) == []
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/agents/test_base.py -v`
Expected: FAIL — `AttributeError: 'BaseAgent' object has no attribute '_commit_thread_intents'`.

- [ ] **Step 3: Implement**

In `novelizer/agents/base.py`, add `import logging`, the `logger`, and the new method. Full updated file:

```python
from __future__ import annotations
import logging
from typing import Protocol
from pydantic import BaseModel, Field
from novelizer.canon.events import (
    EventType, AgentRemark, ThreadPlanted, ThreadTouched, ThreadPaidOff, ThreadAbandoned,
)
from novelizer.canon.threads import slugify_thread_name
from novelizer.agents.schemas import ThreadIntent

logger = logging.getLogger(__name__)


class ChapterDraft(BaseModel):
    title: str
    prose: str
    character_ids: list[str] = Field(default_factory=list)
    feed_note: str = ""
    thread_intents: list[ThreadIntent] = Field(default_factory=list)


class Runner(Protocol):
    async def ainvoke(self, inputs: dict) -> dict: ...


class BaseAgent:
    name: str = "agent"

    def __init__(
        self,
        runner,
        read_store,
        committer,
        interval: int,
        name: str | None = None,
        personality: str = "",
    ) -> None:
        self._runner = runner
        self._read = read_store
        self._committer = committer
        self.interval = interval
        if name is not None:
            self.name = name
        self.personality = personality
        self.paused = False
        self._last_run = 0.0

    def pause(self) -> None:
        self.paused = True

    def resume(self) -> None:
        self.paused = False

    def ready_for_interval(self, now: float) -> bool:
        return (now - self._last_run) >= self.interval

    def mark_ran(self, now: float) -> None:
        self._last_run = now

    async def readiness(self) -> float:
        return 0.0

    async def run_once(self) -> None:
        pass

    async def _consume_signals(self, signals) -> None:
        for sig in signals:
            consumed = sig.model_copy(update={"consumed": True})
            await self._committer.commit(self.name, EventType.DIRECTOR_SIGNAL_CONSUMED, sig.id, consumed)

    async def _remark(self, note: str) -> None:
        """Emit a short in-personality feed line as agent.remarked. No-op if note is empty."""
        if not note:
            return
        await self._committer.commit(
            self.name, EventType.AGENT_REMARKED, self.name, AgentRemark(agent_name=self.name, note=note)
        )

    async def _commit_thread_intents(
        self, intents: list[ThreadIntent], active_thread_ids: set[str], chapter_id: str = ""
    ) -> None:
        """Turn agent-declared ThreadIntent entries into thread.* commits.

        `plant` mints a new id via slugify_thread_name(intent.name) and is
        dropped only if the name is blank. `touch`/`pay_off`/`abandon` must
        cite an id present in `active_thread_ids` — the thread's known,
        non-terminal ids at poll time (see Author.poll/Editor.poll); an
        intent naming an unknown or already-terminal id is dropped with a
        logged warning and no event is committed. No-op on an empty list.
        """
        for intent in intents:
            if intent.action == "plant":
                if not intent.name.strip():
                    logger.warning("%s: dropped thread plant intent with empty name", self.name)
                    continue
                thread_id = slugify_thread_name(intent.name)
                await self._committer.commit(
                    self.name, EventType.THREAD_PLANTED, thread_id,
                    ThreadPlanted(id=thread_id, name=intent.name, chapter_id=chapter_id, note=intent.note),
                )
                continue
            if intent.id not in active_thread_ids:
                logger.warning(
                    "%s: dropped thread %s intent for unknown id %r", self.name, intent.action, intent.id
                )
                continue
            payload_cls, event_type = {
                "touch": (ThreadTouched, EventType.THREAD_TOUCHED),
                "pay_off": (ThreadPaidOff, EventType.THREAD_PAID_OFF),
                "abandon": (ThreadAbandoned, EventType.THREAD_ABANDONED),
            }[intent.action]
            await self._committer.commit(
                self.name, event_type, intent.id,
                payload_cls(id=intent.id, chapter_id=chapter_id, note=intent.note),
            )
```

(Only the `logging` import/`logger`, the `ThreadPlanted`/`ThreadTouched`/`ThreadPaidOff`/`ThreadAbandoned`/`slugify_thread_name`/`ThreadIntent` imports, `ChapterDraft.thread_intents` (from Task 7, already applied), and the new `_commit_thread_intents` method are new; every other method is unchanged.)

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/agents/test_base.py -v`
Expected: PASS (all prior + 6 new). Then `uv run pytest tests/ -v` for the full suite green.

- [ ] **Step 5: Commit**

```bash
git add novelizer/agents/base.py tests/agents/test_base.py
git commit -m "feat: BaseAgent._commit_thread_intents translates agent thread declarations into commits"
```

---

### Task 9: Author wires `thread_intents` into `poll()`/`commit()`

**Files:**
- Modify: `novelizer/agents/author.py`
- Test: `tests/agents/test_author.py`

**Interfaces:**
- Consumes: `ReadStore.list_threads()` (Task 6); `ThreadState` (Task 2); `BaseAgent._commit_thread_intents` (Task 8); `ChapterDraft.thread_intents` (Task 7).
- Produces: no new public interface — `Author.poll()`'s returned dict gains a `"threads"` key; `Author.commit()` calls `self._commit_thread_intents(...)` after its existing `chapter.created` commit.

- [ ] **Step 1: Write the failing tests**

Append to `tests/agents/test_author.py`:

```python
from novelizer.agents.schemas import ThreadIntent
from novelizer.canon.events import ThreadPlanted


async def test_author_commit_plants_a_thread_from_structured_output(stack):
    events, proj, read, committer = stack
    draft = ChapterDraft(
        title="T", prose="P",
        thread_intents=[ThreadIntent(action="plant", name="The Locket")],
    )
    author = Author(FakeRunner(draft), read, committer)
    await author.run_once()
    await proj.catch_up()
    thread = await read.get_thread("the-locket")
    assert thread is not None and thread.name == "The Locket"


async def test_author_commit_touches_a_known_active_thread(stack):
    events, proj, read, committer = stack
    await events.append(EventType.THREAD_PLANTED, "the-locket", ThreadPlanted(id="the-locket", name="The Locket"))
    await proj.catch_up()
    draft = ChapterDraft(
        title="T", prose="P",
        thread_intents=[ThreadIntent(action="touch", id="the-locket", note="reappears")],
    )
    author = Author(FakeRunner(draft), read, committer)
    await author.run_once()
    await proj.catch_up()
    thread = await read.get_thread("the-locket")
    assert thread.touch_count == 1


async def test_author_commit_drops_touch_for_unknown_thread_id(stack):
    events, proj, read, committer = stack
    draft = ChapterDraft(
        title="T", prose="P",
        thread_intents=[ThreadIntent(action="touch", id="ghost-thread")],
    )
    author = Author(FakeRunner(draft), read, committer)
    await author.run_once()
    await proj.catch_up()
    log = await events.events_since(0)
    assert [e.event_type for e in log if e.event_type.startswith("thread.")] == []


async def test_author_commit_with_no_thread_intents_emits_no_thread_events(stack):
    events, proj, read, committer = stack
    draft = ChapterDraft(title="T", prose="P")
    author = Author(FakeRunner(draft), read, committer)
    await author.run_once()
    await proj.catch_up()
    log = await events.events_since(0)
    assert [e.event_type for e in log if e.event_type.startswith("thread.")] == []
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/agents/test_author.py -v`
Expected: FAIL — `test_author_commit_plants_a_thread_from_structured_output` fails with `assert thread is None`, since `Author.commit()` doesn't yet call `_commit_thread_intents`.

- [ ] **Step 3: Implement**

In `novelizer/agents/author.py`, add the `ThreadState` import and update `poll()`/`commit()`:

```python
from __future__ import annotations
from novelizer.agents.base import BaseAgent, ChapterDraft, Runner
from novelizer.canon.read_store import ReadStore
from novelizer.canon.committer import Committer
from novelizer.canon.events import EventType
from novelizer.store.models import Chapter, ThreadState
```

```python
    async def poll(self) -> dict:
        chapters = await self._read.list_chapters()
        return {
            "world": await self._read.list_world_entries(),
            "characters": await self._read.list_characters(),
            "previous": chapters[-3:],
            "signals": await self._read.list_unconsumed_signals(target_agent=self.name),
            "threads": await self._read.list_threads(),
        }
```

```python
    async def commit(self, draft: ChapterDraft | None, ctx: dict) -> None:
        if draft is None:
            return
        chapter = Chapter(title=draft.title, prose=draft.prose, character_ids=draft.character_ids)
        await self._committer.commit(self.name, EventType.CHAPTER_CREATED, chapter.id, chapter)
        active_thread_ids = {
            t.id for t in ctx["threads"] if t.state not in (ThreadState.paid_off, ThreadState.abandoned)
        }
        await self._commit_thread_intents(draft.thread_intents, active_thread_ids, chapter_id=chapter.id)
        await self._remark(draft.feed_note)
        await self._consume_signals(ctx["signals"])
```

(Only the `ThreadState` import, the `"threads"` key in `poll()`, and the two new `active_thread_ids`/`_commit_thread_intents` lines in `commit()` are new; `AUTHOR_SYSTEM_PROMPT`, `_summarize`, `Author.__init__`, `readiness`, `work`, `run_once`, `build_author_runner` are unchanged.)

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/agents/test_author.py -v`
Expected: PASS (all prior + 4 new). Then `uv run pytest tests/ -v` for the full suite green.

- [ ] **Step 5: Commit**

```bash
git add novelizer/agents/author.py tests/agents/test_author.py
git commit -m "feat: Author turns declared thread intents into thread.* commits"
```

---

### Task 10: Editor wires `thread_intents` into `poll()`/`commit()`

**Files:**
- Modify: `novelizer/agents/editor.py`
- Test: `tests/agents/test_editor.py`

**Interfaces:**
- Consumes: `ReadStore.list_threads()` (Task 6); `ThreadState` (Task 2); `BaseAgent._commit_thread_intents` (Task 8); `EditorVerdict.thread_intents` (Task 7).
- Produces: no new public interface — `Editor.poll()`'s returned dict gains a `"threads"` key; `Editor.commit()` calls `self._commit_thread_intents(...)` after its existing chapter-status/signal commit.

- [ ] **Step 1: Write the failing tests**

Append to `tests/agents/test_editor.py`:

```python
from novelizer.agents.schemas import ThreadIntent
from novelizer.canon.events import ThreadPlanted


async def test_editor_commit_touches_a_known_active_thread(stack):
    events, proj, read, committer = stack
    await events.append(EventType.CHAPTER_CREATED, "c1", Chapter(id="c1", title="One", prose="p"))
    await events.append(EventType.THREAD_PLANTED, "the-locket", ThreadPlanted(id="the-locket", name="The Locket"))
    await proj.catch_up()
    verdict = EditorVerdict(
        verdict="approve", notes="clean",
        thread_intents=[ThreadIntent(action="touch", id="the-locket", note="resurfaces")],
    )
    agent = Editor(FakeRunner(verdict), read, committer)
    await agent.run_once()
    await proj.catch_up()
    thread = await read.get_thread("the-locket")
    assert thread.touch_count == 1
    assert thread.last_chapter_id == "c1"


async def test_editor_commit_drops_pay_off_for_unknown_thread_id(stack):
    events, proj, read, committer = stack
    await events.append(EventType.CHAPTER_CREATED, "c1", Chapter(id="c1", title="One", prose="p"))
    await proj.catch_up()
    verdict = EditorVerdict(verdict="approve", notes="clean", thread_intents=[ThreadIntent(action="pay_off", id="ghost")])
    agent = Editor(FakeRunner(verdict), read, committer)
    await agent.run_once()
    await proj.catch_up()
    log = await events.events_since(0)
    assert [e.event_type for e in log if e.event_type.startswith("thread.")] == []


async def test_editor_commit_with_no_thread_intents_emits_no_thread_events(stack):
    events, proj, read, committer = stack
    await events.append(EventType.CHAPTER_CREATED, "c1", Chapter(id="c1", title="One", prose="p"))
    await proj.catch_up()
    verdict = EditorVerdict(verdict="approve", notes="clean")
    agent = Editor(FakeRunner(verdict), read, committer)
    await agent.run_once()
    await proj.catch_up()
    log = await events.events_since(0)
    assert [e.event_type for e in log if e.event_type.startswith("thread.")] == []
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/agents/test_editor.py -v`
Expected: FAIL — `test_editor_commit_touches_a_known_active_thread` fails with `assert thread.touch_count == 0`, since `Editor.commit()` doesn't yet call `_commit_thread_intents`.

- [ ] **Step 3: Implement**

In `novelizer/agents/editor.py`, add the `ThreadState` import and update `poll()`/`commit()`:

```python
from __future__ import annotations
from novelizer.agents.base import BaseAgent, Runner
from novelizer.agents.schemas import EditorVerdict
from novelizer.canon.read_store import ReadStore
from novelizer.canon.committer import Committer
from novelizer.canon.events import EventType
from novelizer.store.models import DirectorSignal, SignalKind, EditorialStatus, ThreadState
```

```python
    async def poll(self) -> dict:
        drafts = await self._read.list_chapters(status=EditorialStatus.draft)
        return {
            "target": drafts[0] if drafts else None,
            "threads": await self._read.list_threads(),
        }
```

```python
    async def commit(self, verdict: EditorVerdict | None, ctx: dict) -> None:
        ch = ctx["target"]
        if ch is None or verdict is None:
            return
        if verdict.verdict == "approve":
            updated = ch.model_copy(update={"editorial_status": EditorialStatus.reviewed, "editor_notes": verdict.notes})
            await self._committer.commit(self.name, EventType.CHAPTER_STATUS_CHANGED, updated.id, updated)
        else:
            sig = DirectorSignal(kind=SignalKind.note, body=f"[Editor on '{ch.title}'] {verdict.notes}", target_agent="author")
            await self._committer.commit(self.name, EventType.DIRECTOR_SIGNAL_CREATED, sig.id, sig)
        active_thread_ids = {
            t.id for t in ctx["threads"] if t.state not in (ThreadState.paid_off, ThreadState.abandoned)
        }
        await self._commit_thread_intents(verdict.thread_intents, active_thread_ids, chapter_id=ch.id)
        await self._remark(verdict.feed_note)
```

(Only the `ThreadState` import, the `"threads"` key in `poll()`, and the `active_thread_ids`/`_commit_thread_intents` lines in `commit()` are new; `SYSTEM_PROMPT`, `Editor.__init__`, `readiness`, `_character_voices_block`, `work`, `run_once`, `build_editor_runner` are unchanged. `poll()`'s ctx dict now returns `{"target": ..., "threads": ...}` — the existing `test_editor_prompt_omits_voices_section_when_none_set` test calls `agent.poll()` then `agent.work(ctx)`; `work()` only reads `ctx["target"]`, so the new `"threads"` key is inert there and that test's byte-identical prompt assertion is unaffected.)

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/agents/test_editor.py -v`
Expected: PASS (all prior + 3 new). Then `uv run pytest tests/ -v` for the full suite green.

- [ ] **Step 5: Commit**

```bash
git add novelizer/agents/editor.py tests/agents/test_editor.py
git commit -m "feat: Editor turns declared thread intents into thread.* commits"
```

---

### Task 11: Docs — mark M3.1 complete, document the thread ledger

**Files:**
- Modify: `docs/submilestones/M3-shape-and-threads.md`
- Modify: `README.md`

**Interfaces:** none (documentation only).

- [ ] **Step 1: Update the sub-milestone table**

In `docs/submilestones/M3-shape-and-threads.md`, change the M3.1 row's `Status` cell from `⬜ not started` to `✅ complete`.

- [ ] **Step 2: Add a README section**

In `README.md`, add a new subsection near the existing "Voices" material (after the "Character voices & the voice browser" subsection added in M2.3, before any following top-level heading):

```markdown
### The thread ledger (Story Brain, Phase 1)

The Author and Editor can declare plot-thread bookkeeping alongside their
normal output: `plant` a new thread from a freeform name (the system slugs
it into a stable id — e.g. "The Locket's Secret" becomes `the-locket-s-secret`),
or `touch`/`pay_off`/`abandon` an existing thread by citing its id. Thread
events are never gated by autonomy level — they're narrative bookkeeping,
not proposals — and flow straight into a `threads` read table via the same
event-sourced Projector/ReadStore machinery as chapters and characters.

```bash
novelizer proposals   # thread.* never appears here, at any autonomy level
```

A thread's state machine is `planted → touched* → paid_off|abandoned`, with
`paid_off`/`abandoned` absorbing: once a thread is closed, further events
citing its id are recorded in the log but don't reopen it. Story Brain
surfaces (staleness detection, the Story Shape/Thread Board TUI views, and
prompt injection of stale threads back to the Author) are M3.2/M3.3.
```

- [ ] **Step 3: Commit**

```bash
git add docs/submilestones/M3-shape-and-threads.md README.md
git commit -m "docs: mark M3.1 complete; document the thread ledger"
```

---

## Self-Review

**Spec coverage against the M3.1 row and Load-bearing design decisions in `docs/submilestones/M3-shape-and-threads.md`:**
- "New `thread.*` event domain (planted, touched, paid_off, abandoned)" — Task 1.
- "Author/Editor structured output gains an optional `thread_intents` field" — Task 7 (`ThreadIntent`, `ChapterDraft.thread_intents`, `EditorVerdict.thread_intents`).
- "`work()` turns them into `committer.commit(...)` calls alongside the existing commits" — Task 8 (shared translator) + Tasks 9/10 (wiring into `Author.commit()`/`Editor.commit()`, called after the existing chapter commit, per the doc's explicit N+1-separate-appends precedent).
- "`ThreadsProjection` (new table via the Projector) rebuilding thread state" — Task 4.
- "`ReadStore.list_threads()`/`get_thread()`" — Task 6.
- "Thread identity: id minted only at planted time... freeform name... slugs into aggregate_id... touch/pay_off/abandon must reference an id from the active-thread list already in the agent's context... unknown id is dropped with a logged warning, no event committed" — Task 1 (`slugify_thread_name`) + Task 8 (`_commit_thread_intents`'s validation/logging/no-op-on-unknown-id). The doc explicitly notes the BrainContext/active-thread-list provider is M3.3's concern and directs M3.1 to decide its own validation seam; this plan resolves that as `ReadStore.list_threads()`'s non-terminal ids, fetched in each agent's `poll()` — flagged explicitly in the Architecture section above, not left implicit.
- "`thread.*` added to `AutonomyPolicy._NEVER_GATED`" — Task 3.
- Done-when: "Author declaring a thread intent... results in a `thread.touched` event in the log... and an updated row in the threads read table after `catch_up()`" — Task 9's `test_author_commit_touches_a_known_active_thread`. "never gated, confirmed by a test asserting `is_gated` is False for every thread.* type" — Task 3. "Hypothesis property test asserts the state machine holds under any valid event sequence replay, including the absorbing-terminal-state contract" — Task 5.

**Design decisions the M3.1 row left open, resolved here (flagged per the dispatch instructions):**
1. **Payload shape.** Each of the four events carries `id` (+ `name` only for `planted`), plus `chapter_id: str = ""` and `note: str = ""` on all four, for future traceability (which chapter a touch/pay-off happened in) without requiring it — `chapter_id` defaults to `""` so an intent committed outside chapter-authoring context (hypothetically) still works. This wasn't specified in the decomposition doc; it's the natural minimal shape given the Editor also declares intents (Editor doesn't create chapters, so `chapter_id` there is the chapter under review, not one it authored).
2. **Validation seam for M3.1** (doc explicitly asks the planner to decide this): `ReadStore.list_threads()`'s non-terminal ids, queried fresh in each agent's `poll()` and threaded through to `commit()` via `ctx["threads"]`. This is superseded by the BrainContext provider in M3.3 without requiring `_commit_thread_intents`'s signature to change — it already takes `active_thread_ids: set[str]` as a plain parameter, not a `ReadStore` call, so M3.3 only needs to change what populates that set.
3. **Projection accumulation vs. replace-wholesale.** Unlike every existing projection table (which stores the latest event's full payload verbatim), `threads` accumulates state across events since `touched`/`paid_off`/`abandoned` payloads are deltas. This is called out explicitly in the Architecture section and Task 4's implementation, since it's the one place this plan's `_apply` logic pattern differs from Projector's existing precedent (read-before-write instead of blind insert-or-replace).
4. **`ThreadIntent` as a single polymorphic schema** (one model with an `action` discriminator and unused fields blank per action) rather than four separate intent types. Chosen to keep `ChapterDraft`/`EditorVerdict` at one new field each (`thread_intents: list[ThreadIntent]`) rather than four, matching how a single LLM structured-output call naturally emits a mixed list of actions in one field.

**Placeholder scan:** every task's Step 3 shows complete code — full new files (`threads.py`), full new classes/methods, or exact before/after snippets anchored to the current file contents read during planning (`events.py`, `policy.py`, `projector.py`, `read_store.py`, `schemas.py`, `base.py`, `author.py`, `editor.py` were all read in full before this plan was written). Task 8 shows the complete `base.py` file since the majority of it changes (new imports, new method). No "similar to Task N", no `...` elisions, no TODOs.

**Type consistency:** `ThreadRecord.state: ThreadState` (Task 2) matches `Projector._apply`'s `record.state.value not in TERMINAL_STATES` check (Task 4, `TERMINAL_STATES` is a `set[str]`, so `.value` bridges the enum) and `ReadStore.list_threads() -> list[ThreadRecord]` (Task 6). `ThreadIntent.action: Literal["plant", "touch", "pay_off", "abandon"]` (Task 7) matches the four branches in `BaseAgent._commit_thread_intents` (Task 8) and the `TERMINAL_STATES`/`ThreadState` members' naming (`paid_off`, `abandoned`) exactly — no drift between the intent action string and the resulting `ThreadState` member name save for the deliberate `pay_off`(intent)/`paid_off`(state) distinction, which mirrors natural English and is exercised directly by every cross-referencing test in Tasks 4/5/8/9/10.

**DDD/SOLID:**
- Single Responsibility: `slugify_thread_name` only slugs; `Projector._apply`'s thread branches are the only place that knows the state machine; `BaseAgent._commit_thread_intents` is the only place that turns an intent into a commit; `ReadStore.list_threads`/`get_thread` are the only read path.
- Open/Closed: `ChapterDraft`/`EditorVerdict` each gain one new defaulted field; `Author`/`Editor`'s `poll()`/`commit()` each gain one new dict key and two new lines respectively, following the exact `casting_note`/`personality`/`_remark`/`_consume_signals` precedent — no existing logic branch is modified.
- Dependency Inversion / bounded context: Story Brain's write path (`thread.*` events, `ThreadsProjection`) depends only on the existing `Committer`/`Projector`/`EventStore` seam; agents depend only on `ReadStore.list_threads()` and the shared `BaseAgent` helper, never on Projector internals.
- Event sourcing: `threads` is a disposable, rebuildable projection (Task 4's rebuild-equivalence test + Task 5's property test both assert this); no persistence path bypasses the event log.

**Backward-compatibility check:** `ChapterDraft.thread_intents`/`EditorVerdict.thread_intents` default to `[]`; `_commit_thread_intents([], ...)` iterates zero times and commits nothing (Task 8's `test_commit_thread_intents_noop_on_empty_list` and Tasks 9/10's "with no thread intents emits no thread events" tests pin this directly). Every existing `ChapterDraft(...)`/`EditorVerdict(...)` construction across the pre-M3.1 suite omits `thread_intents`, so none of them are affected. `Author.poll()`/`Editor.poll()` each gain one new dict key (`"threads"`) that no pre-existing test reads, so no existing assertion on `ctx` contents breaks.
