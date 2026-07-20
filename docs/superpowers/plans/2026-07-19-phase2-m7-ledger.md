# M7 "The Ledger" Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** A promise ledger (Chekhov's-gun setups with target payoff windows) and planned resolution windows for threads/secrets, checked by two new Story Brain faculties and visualized on the Threads board — Phase 2's first authored-plan slice (docs/MILESTONES.md M7).

**Architecture:** Net-new `promises` aggregate following the threads pattern exactly: slug module → EventType constants + payload models with locked-decision docstrings → projector branches (first-mint-wins, absorbing terminals) → ReadStore queries → intent helper with commit-time id validation → Author/Editor structured-output intents. Planned windows for threads/secrets fold into the EXISTING ThreadRecord/SecretRecord rows (re-emission supersedes). Windows are 1-based chapter ordinals; `0` = unset; "now" = `len(chapters)`. Brain faculties are pure functions (never persisted); prompt notes return `""` when empty. Director sets windows via new commands (no Plotter until M8).

**Tech Stack:** Python 3.13, aiosqlite, pydantic v2, Hypothesis, pytest asyncio_mode=auto, Textual (pure-model widgets).

## Global Constraints

- Spec: `docs/superpowers/specs/2026-07-19-authoring-skills-blueprint-design.md` §"Thread resolution planning + promise ledger" + §Milestones M7. Roadmap row: docs/MILESTONES.md Phase 2 M7.
- Event sourcing rules: ids minted exactly once at the mint event via slugify; later events cite ids; projector else-branches are silent no-ops for unknown/terminal ids; every new table goes into `Projector._reset_state_locked`'s tuple (projector.py ~line 125) or rebuild-equivalence property tests fail.
- All six new event types (`promise.made/progressed/paid/released`, `thread.resolution_planned`, `secret.reveal_planned`) are **never gated** (`_NEVER_GATED` in canon/policy.py) — planning/bookkeeping writes, same class as thread/theme events. (Blueprint-adoption proposals are M8's concern.)
- Prompt-note functions must return `""` when they have nothing to say (byte-identical-prompt discipline).
- **Explicitly deferred to M8 (record, don't build):** ContinuityChecker prose-mining of promises; `/promises/` in canon_fs renderers; `search_canon` promise kind; any new Brain tab (ledger lives on the Threads tab in M7).
- **Run all tests in this worktree, NEVER the main checkout.** `uv run pytest <path> -v`, synchronously (no background runs).
- Window semantics everywhere: `window_lo`/`window_hi` ints, 1-based chapter ordinals, `0` = unset, valid plan has `1 <= lo <= hi`; "overdue" means `window_hi > 0 and len(chapters) > window_hi` and not terminal.

---

### Task 1: Promise slug + event types + payload models

**Files:**
- Create: `novelizer/canon/promises.py`
- Modify: `novelizer/canon/events.py` (EventType block end ~line 42; payload models appended at end)
- Test: `tests/canon/test_promises.py` (new), `tests/canon/test_events.py` (append)

**Interfaces:**
- Produces: `slugify_promise_name(name) -> str`; `TERMINAL_PROMISE_STATES: set[str] = {"paid", "released"}`; EventType constants `PROMISE_MADE/PROMISE_PROGRESSED/PROMISE_PAID/PROMISE_RELEASED/THREAD_RESOLUTION_PLANNED/SECRET_REVEAL_PLANNED`; payload models `PromiseMade, PromiseProgressed, PromisePaid, PromiseReleased, ThreadResolutionPlanned, SecretRevealPlanned`. Every later task cites these names exactly.

- [ ] **Step 1: Write the failing tests**

`tests/canon/test_promises.py` (mirror tests/canon/test_threads.py's style — read it first for the slug test shape):

```python
from novelizer.canon.promises import TERMINAL_PROMISE_STATES, slugify_promise_name


def test_slugify_promise_name_basic():
    assert slugify_promise_name("The Sealed Letter") == "the-sealed-letter"


def test_slugify_promise_name_collapses_and_strips():
    assert slugify_promise_name("  A -- rusty!! KEY  ") == "a-rusty-key"


def test_slugify_promise_name_empty_falls_back():
    assert slugify_promise_name("!!!") == "promise"


def test_terminal_promise_states():
    assert TERMINAL_PROMISE_STATES == {"paid", "released"}
```

Append to `tests/canon/test_events.py` (mirror its existing payload-model tests):

```python
def test_promise_event_payloads_construct_with_defaults():
    from novelizer.canon.events import (
        EventType, PromiseMade, PromiseProgressed, PromisePaid, PromiseReleased,
        ThreadResolutionPlanned, SecretRevealPlanned,
    )
    made = PromiseMade(id="the-sealed-letter", name="The Sealed Letter")
    assert made.kind == "foreshadow" and made.window_lo == 0 and made.window_hi == 0
    assert PromiseProgressed(id="x").note == ""
    assert PromisePaid(id="x").chapter_id == ""
    assert PromiseReleased(id="x").reason == ""
    trp = ThreadResolutionPlanned(id="t", window_lo=18, window_hi=20)
    assert trp.planned_payoff_note == ""
    srp = SecretRevealPlanned(id="s", window_lo=5, window_hi=9)
    assert srp.window_hi == 9
    assert EventType.PROMISE_MADE == "promise.made"
    assert EventType.THREAD_RESOLUTION_PLANNED == "thread.resolution_planned"
    assert EventType.SECRET_REVEAL_PLANNED == "secret.reveal_planned"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/canon/test_promises.py tests/canon/test_events.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'novelizer.canon.promises'` / ImportError on new payload names.

- [ ] **Step 3: Implement**

`novelizer/canon/promises.py` (byte-mirror of canon/threads.py's shape):

```python
import re

TERMINAL_PROMISE_STATES: set[str] = {"paid", "released"}

_SLUG_RE = re.compile(r"[^a-z0-9]+")


def slugify_promise_name(name: str) -> str:
    slug = _SLUG_RE.sub("-", name.lower()).strip("-")
    return slug or "promise"
```

`novelizer/canon/events.py` — append to the EventType constants (after the INSPIRATION_* block):

```python
    PROMISE_MADE = "promise.made"
    PROMISE_PROGRESSED = "promise.progressed"
    PROMISE_PAID = "promise.paid"
    PROMISE_RELEASED = "promise.released"
    THREAD_RESOLUTION_PLANNED = "thread.resolution_planned"
    SECRET_REVEAL_PLANNED = "secret.reveal_planned"
```

and append the payload models at the end of the file:

```python
class PromiseMade(BaseModel):
    """Payload for promise.made — mints a new promise's identity.

    A promise is a discrete planted expectation (Chekhov's gun, foreshadowed
    image, red herring) with a discrete payoff — below thread scale. `id` is
    the slug minted from `name` (see novelizer.canon.promises
    .slugify_promise_name) at make time; every later promise.* event must
    cite this id, never re-derive it (Locked decision #1: first-make-wins,
    same as threads).

    `window_lo`/`window_hi` are 1-based chapter ordinals bounding the target
    payoff window; 0 means unset. `kind` is one of foreshadow|plant|
    red_herring — red herrings exit via promise.released without alarm.
    """

    id: str
    name: str
    description: str = ""
    kind: str = "foreshadow"
    chapter_id: str = ""
    thread_id: str = ""
    window_lo: int = 0
    window_hi: int = 0
    note: str = ""
    source: str = "declared"


class PromiseProgressed(BaseModel):
    """Payload for promise.progressed — an existing promise advances, cited
    by id. Progress on a terminal promise is a no-op in projection."""

    id: str
    chapter_id: str = ""
    note: str = ""
    source: str = "declared"


class PromisePaid(BaseModel):
    """Payload for promise.paid — the planted expectation is fulfilled,
    cited by id. Terminal: the PromisesProjection treats this id as
    absorbing thereafter (Locked decision #2)."""

    id: str
    chapter_id: str = ""
    note: str = ""
    source: str = "declared"


class PromiseReleased(BaseModel):
    """Payload for promise.released — the sanctioned exit for red herrings
    and deliberate abandonment, cited by id. Terminal and absorbing, like
    promise.paid; released promises never alarm."""

    id: str
    reason: str = ""
    chapter_id: str = ""
    source: str = "declared"


class ThreadResolutionPlanned(BaseModel):
    """Payload for thread.resolution_planned — pins a target resolution
    window on an existing, non-terminal thread, cited by id.

    Re-emission supersedes (Locked decision: the event history IS the record
    of schedule slips). Unknown or terminal thread ids are projection no-ops.
    `window_lo`/`window_hi` are 1-based chapter ordinals; 0 clears the plan.
    """

    id: str
    window_lo: int = 0
    window_hi: int = 0
    planned_payoff_note: str = ""


class SecretRevealPlanned(BaseModel):
    """Payload for secret.reveal_planned — pins a target reveal window on an
    existing, unrevealed secret, cited by id. Re-emission supersedes; unknown
    or already-revealed secret ids are projection no-ops. Windows are 1-based
    chapter ordinals; 0 clears the plan."""

    id: str
    window_lo: int = 0
    window_hi: int = 0
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/canon/test_promises.py tests/canon/test_events.py -v`
Expected: all PASS

- [ ] **Step 5: Commit**

```bash
git add novelizer/canon/promises.py novelizer/canon/events.py tests/canon/
git commit -m "feat(canon): promise ledger + resolution-window event types and payloads"
```

---

### Task 2: Read models — PromiseRecord + window fields on ThreadRecord/SecretRecord

**Files:**
- Modify: `novelizer/store/models.py` (PromiseState + PromiseRecord beside ThreadRecord ~line 89; ThreadRecord + SecretRecord gain window fields)
- Test: `tests/canon/test_promises.py` (append)

**Interfaces:**
- Produces: `PromiseState(StrEnum)` = `open|paid|released`; `PromiseRecord(id, name, description, kind, state, thread_id, setup_chapter_id, window_lo, window_hi, progress_count, last_note, last_chapter_id)`; `ThreadRecord` gains `window_lo: int = 0, window_hi: int = 0, planned_payoff_note: str = ""`; `SecretRecord` gains `reveal_window_lo: int = 0, reveal_window_hi: int = 0`. Defaults keep every existing serialized row valid (back-compat).

- [ ] **Step 1: Write the failing tests**

Append to `tests/canon/test_promises.py`:

```python
def test_promise_record_defaults():
    from novelizer.store.models import PromiseRecord, PromiseState
    p = PromiseRecord(id="p", name="P")
    assert p.state == PromiseState.open
    assert p.kind == "foreshadow"
    assert p.progress_count == 0 and p.window_lo == 0 and p.window_hi == 0


def test_thread_and_secret_records_accept_window_fields_with_back_compat_defaults():
    from novelizer.store.models import SecretRecord, ThreadRecord
    t = ThreadRecord(id="t", name="T")
    assert t.window_lo == 0 and t.window_hi == 0 and t.planned_payoff_note == ""
    # pre-M7 serialized rows must still validate
    assert ThreadRecord.model_validate_json(t.model_dump_json()).window_hi == 0
    s = SecretRecord(id="s", title="S")
    assert s.reveal_window_lo == 0 and s.reveal_window_hi == 0
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/canon/test_promises.py -v -k "record or window_fields"`
Expected: FAIL — ImportError `PromiseRecord`

- [ ] **Step 3: Implement**

In `novelizer/store/models.py`, after `ThreadRecord`:

```python
class PromiseState(StrEnum):
    open = "open"
    paid = "paid"
    released = "released"


class PromiseRecord(BaseModel):
    """Read-side row for a ledger promise, built and rebuilt by the Projector
    from the promise.* event log. Promise events after the first carry only
    deltas (id + note), so fields accumulate across events. `paid` and
    `released` are absorbing (see canon.promises.TERMINAL_PROMISE_STATES);
    released is the alarm-free exit for red herrings."""

    id: str
    name: str
    description: str = ""
    kind: str = "foreshadow"
    state: PromiseState = PromiseState.open
    thread_id: str = ""
    setup_chapter_id: str = ""
    window_lo: int = 0
    window_hi: int = 0
    progress_count: int = 0
    last_note: str = ""
    last_chapter_id: str = ""
```

Add to `ThreadRecord` (after `last_chapter_id`):

```python
    window_lo: int = 0
    window_hi: int = 0
    planned_payoff_note: str = ""
```

Add to `SecretRecord` (after `revealed`):

```python
    reveal_window_lo: int = 0
    reveal_window_hi: int = 0
```

- [ ] **Step 4: Run tests + models regression**

Run: `uv run pytest tests/canon/test_promises.py tests/canon/ -q`
Expected: all PASS

- [ ] **Step 5: Commit**

```bash
git add novelizer/store/models.py tests/canon/test_promises.py
git commit -m "feat(store): PromiseRecord + planned-window fields on thread/secret records"
```

---

### Task 3: Projection + ReadStore queries + property tests

**Files:**
- Modify: `novelizer/canon/projector.py` (`_CREATE` table string ~line 21; `_reset_state_locked` tuple ~line 125; `_project()` new elif branches after the thread block ~line 290; import PromiseRecord/PromiseState + TERMINAL_PROMISE_STATES)
- Modify: `novelizer/canon/read_store.py` (`list_promises`/`get_promise` beside `list_threads` ~line 99)
- Test: `tests/canon/test_promises_projection_property.py` (new), `tests/canon/test_projector.py` (append window-folding tests)

**Interfaces:**
- Consumes: Task 1 events, Task 2 models.
- Produces: `promises` table (`id TEXT PRIMARY KEY, data TEXT NOT NULL, state TEXT NOT NULL`); `ReadStore.list_promises() -> list[PromiseRecord]`, `ReadStore.get_promise(promise_id) -> Optional[PromiseRecord]`. Semantics later tasks rely on: first-make-wins; progress increments `progress_count` and updates last_note/last_chapter_id; paid/released absorbing; `thread.resolution_planned` folds `window_lo/hi/planned_payoff_note` into the thread row (no-op for unknown/terminal threads); `secret.reveal_planned` folds `reveal_window_lo/hi` into the secret row (no-op for unknown/revealed secrets).

- [ ] **Step 1: Write the failing property test**

`tests/canon/test_promises_projection_property.py` — copy the structure of `tests/canon/test_threads_projection_property.py` wholesale (read it first; keep its `asyncio.run` + rebuild-equivalence shape). The oracle and sequence runner:

```python
import asyncio
import os
import tempfile

from hypothesis import given, settings
from hypothesis import strategies as st

from novelizer.canon.event_store import EventStore
from novelizer.canon.events import (
    EventType, PromiseMade, PromiseProgressed, PromisePaid, PromiseReleased,
)
from novelizer.canon.projector import Projector
from novelizer.canon.read_store import ReadStore

ACTIONS = ["made", "progressed", "paid", "released"]
_EVENT_BY_ACTION = {
    "made": (EventType.PROMISE_MADE, lambda: PromiseMade(id="p1", name="P One")),
    "progressed": (EventType.PROMISE_PROGRESSED, lambda: PromiseProgressed(id="p1", note="n")),
    "paid": (EventType.PROMISE_PAID, lambda: PromisePaid(id="p1")),
    "released": (EventType.PROMISE_RELEASED, lambda: PromiseReleased(id="p1", reason="r")),
}


def _expected(actions: list[str]):
    """Pure oracle for the p1 promise state machine, independent of SQL."""
    state, progress, exists = None, 0, False
    for a in actions:
        if not exists:
            if a == "made":
                exists, state = True, "open"
            continue
        if state in ("paid", "released"):
            continue
        if a == "progressed":
            progress += 1
        elif a == "paid":
            state = "paid"
        elif a == "released":
            state = "released"
        # a second "made" for an existing id: first-make-wins no-op
    return exists, state, progress


async def _run(actions: list[str]):
    fd, path = tempfile.mkstemp(suffix=".db"); os.close(fd)
    try:
        events = EventStore(path); await events.init()
        proj = Projector(events, path); await proj.init()
        read = ReadStore(path); await read.init()
        for a in actions:
            etype, mk = _EVENT_BY_ACTION[a]
            await events.append(etype, "p1", mk())
        await proj.catch_up()
        incremental = await read.list_promises()
        # rebuild equivalence: reset + full replay must agree
        await proj._reset_state()
        await proj.catch_up()
        rebuilt = await read.list_promises()
        await read.close(); await proj.close(); await events.close()
        return incremental, rebuilt
    finally:
        os.unlink(path)


@settings(max_examples=50, deadline=None)
@given(st.lists(st.sampled_from(ACTIONS), max_size=8))
def test_promise_state_machine_holds_for_any_event_sequence(actions):
    incremental, rebuilt = asyncio.run(_run(actions))
    exists, state, progress = _expected(actions)
    assert [(p.id, p.state.value, p.progress_count) for p in incremental] == \
           [(p.id, p.state.value, p.progress_count) for p in rebuilt]
    if not exists:
        assert incremental == []
    else:
        assert len(incremental) == 1
        p = incremental[0]
        assert p.state.value == state and p.progress_count == progress
```

**Adapt to the real threads property-test file's helpers** — if it exposes `_reset_state` differently (e.g. a locked variant or a fresh-Projector pattern), copy exactly what it does; the assertion contract above is what matters.

Append to `tests/canon/test_projector.py` (mirror its fixture style):

```python
async def test_resolution_planned_folds_window_into_thread_and_supersedes(stack_or_equivalent):
    # plant thread t1; append THREAD_RESOLUTION_PLANNED(id="t1", 18, 20, "pay at the gate")
    # catch_up: thread t1 has window_lo=18, window_hi=20, planned_payoff_note set
    # re-emit with (21, 23): fields superseded
    # append for unknown id "zz": no crash, no new row
    ...


async def test_resolution_planned_noop_on_terminal_thread(...):
    # plant t1, pay it off, then resolution_planned(id="t1") -> window stays 0


async def test_reveal_planned_folds_window_into_secret_and_noop_when_revealed(...):
    # secret s1 created; reveal_planned(5, 9) folds in; secret revealed; reveal_planned(10, 12) is a no-op
```

Write these three as REAL tests following the file's existing seeded-event style (the `...` bodies above are your requirements checklist, not literal code — the file's existing thread/secret tests show the exact append + catch_up + assert shape to copy).

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/canon/test_promises_projection_property.py tests/canon/test_projector.py -v -k "promise or planned"`
Expected: FAIL — promises table/branches missing (`list_promises` AttributeError first).

- [ ] **Step 3: Implement**

Projector `_CREATE` string — add beside the threads table:

```sql
CREATE TABLE IF NOT EXISTS promises (
    id TEXT PRIMARY KEY, data TEXT NOT NULL, state TEXT NOT NULL
);
```

`_reset_state_locked` tuple — add `"promises"`.

Imports — extend the `novelizer.store.models` import with `PromiseRecord, PromiseState`; add `from novelizer.canon.promises import TERMINAL_PROMISE_STATES`.

`_project()` — new branches after the thread block, mirroring it exactly:

```python
        elif t == EventType.PROMISE_MADE:
            cur = await self._conn.execute("SELECT id FROM promises WHERE id=?", (p["id"],))
            if await cur.fetchone() is None:
                record = PromiseRecord(
                    id=p["id"], name=p["name"], description=p.get("description", ""),
                    kind=p.get("kind", "foreshadow"), thread_id=p.get("thread_id", ""),
                    setup_chapter_id=p.get("chapter_id", ""),
                    window_lo=p.get("window_lo", 0), window_hi=p.get("window_hi", 0),
                    last_note=p.get("note", ""), last_chapter_id=p.get("chapter_id", ""),
                )
                await self._conn.execute(
                    "INSERT OR REPLACE INTO promises (id, data, state) VALUES (?,?,?)",
                    (record.id, record.model_dump_json(), record.state.value),
                )
            # else: a promise id is minted exactly once — first-make-wins.
        elif t in (EventType.PROMISE_PROGRESSED, EventType.PROMISE_PAID, EventType.PROMISE_RELEASED):
            cur = await self._conn.execute("SELECT data FROM promises WHERE id=?", (p["id"],))
            row = await cur.fetchone()
            if row is not None:
                record = PromiseRecord.model_validate_json(row[0])
                if record.state.value not in TERMINAL_PROMISE_STATES:
                    new_state = {
                        EventType.PROMISE_PROGRESSED: PromiseState.open,
                        EventType.PROMISE_PAID: PromiseState.paid,
                        EventType.PROMISE_RELEASED: PromiseState.released,
                    }[t]
                    progress = record.progress_count + (1 if t == EventType.PROMISE_PROGRESSED else 0)
                    updated = record.model_copy(update={
                        "state": new_state, "progress_count": progress,
                        "last_note": p.get("note", p.get("reason", "")),
                        "last_chapter_id": p.get("chapter_id", ""),
                    })
                    await self._conn.execute(
                        "INSERT OR REPLACE INTO promises (id, data, state) VALUES (?,?,?)",
                        (updated.id, updated.model_dump_json(), updated.state.value),
                    )
        elif t == EventType.THREAD_RESOLUTION_PLANNED:
            cur = await self._conn.execute("SELECT data FROM threads WHERE id=?", (p["id"],))
            row = await cur.fetchone()
            if row is not None:
                record = ThreadRecord.model_validate_json(row[0])
                if record.state.value not in TERMINAL_STATES:
                    updated = record.model_copy(update={
                        "window_lo": p.get("window_lo", 0), "window_hi": p.get("window_hi", 0),
                        "planned_payoff_note": p.get("planned_payoff_note", ""),
                    })
                    await self._conn.execute(
                        "INSERT OR REPLACE INTO threads (id, data, state) VALUES (?,?,?)",
                        (updated.id, updated.model_dump_json(), updated.state.value),
                    )
        elif t == EventType.SECRET_REVEAL_PLANNED:
            cur = await self._conn.execute("SELECT data FROM secrets WHERE id=?", (p["id"],))
            row = await cur.fetchone()
            if row is not None:
                record = SecretRecord.model_validate_json(row[0])
                if not record.revealed:
                    updated = record.model_copy(update={
                        "reveal_window_lo": p.get("window_lo", 0),
                        "reveal_window_hi": p.get("window_hi", 0),
                    })
                    await self._conn.execute(
                        "INSERT OR REPLACE INTO secrets (id, data) VALUES (?,?)",
                        (updated.id, updated.model_dump_json()),
                    )
```

(Check the real secrets INSERT shape in the existing secret branches and match column count exactly.)

ReadStore — beside `list_threads`:

```python
    async def list_promises(self) -> list[PromiseRecord]:
        cur = await self._conn.execute("SELECT data FROM promises ORDER BY rowid")
        return [PromiseRecord.model_validate_json(r[0]) for r in await cur.fetchall()]

    async def get_promise(self, promise_id: str) -> Optional[PromiseRecord]:
        cur = await self._conn.execute("SELECT data FROM promises WHERE id=?", (promise_id,))
        row = await cur.fetchone()
        return PromiseRecord.model_validate_json(row[0]) if row else None
```

- [ ] **Step 4: Run the canon suite**

Run: `uv run pytest tests/canon/ -q`
Expected: all PASS (including every pre-existing projection property test — the `_reset_state` tuple addition is what keeps them honest).

- [ ] **Step 5: Commit**

```bash
git add novelizer/canon/projector.py novelizer/canon/read_store.py tests/canon/
git commit -m "feat(canon): promises projection + planned-window folding with rebuild-equivalence property tests"
```

---

### Task 4: Policy — never-gate the six new event types

**Files:**
- Modify: `novelizer/canon/policy.py` (`_NEVER_GATED` set)
- Test: `tests/canon/test_policy.py` (append)

**Interfaces:** none new — behavior only.

- [ ] **Step 1: Write the failing test** (mirror the file's existing is_gated tests)

```python
def test_promise_and_planning_events_are_never_gated():
    from novelizer.canon.autonomy import AutonomyLevel
    from novelizer.canon.events import EventType
    from novelizer.canon.policy import is_gated_event_types_helper_or_equivalent
    for et in (EventType.PROMISE_MADE, EventType.PROMISE_PROGRESSED, EventType.PROMISE_PAID,
               EventType.PROMISE_RELEASED, EventType.THREAD_RESOLUTION_PLANNED,
               EventType.SECRET_REVEAL_PLANNED):
        for level in AutonomyLevel:
            assert not is_gated(et, level), (et, level)
```

Adapt the call shape to the file's real API — read tests/canon/test_policy.py first and copy exactly how existing tests invoke gating (module-level `is_gated(event_type, level)` vs a policy object); the requirement is: all six types ungated at every level including `gated_all`.

- [ ] **Step 2: Run to verify failure**

Run: `uv run pytest tests/canon/test_policy.py -v -k never_gated`
Expected: FAIL at `gated_all` (new types not in `_NEVER_GATED` fall through to gated).

- [ ] **Step 3: Implement** — add to `_NEVER_GATED`:

```python
    EventType.PROMISE_MADE, EventType.PROMISE_PROGRESSED,
    EventType.PROMISE_PAID, EventType.PROMISE_RELEASED,
    EventType.THREAD_RESOLUTION_PLANNED, EventType.SECRET_REVEAL_PLANNED,
```

- [ ] **Step 4: Run** `uv run pytest tests/canon/test_policy.py -v` — all PASS

- [ ] **Step 5: Commit**

```bash
git add novelizer/canon/policy.py tests/canon/test_policy.py
git commit -m "feat(policy): promise + planning events are never gated"
```

---

### Task 5: PromiseIntent + commit helper + BaseAgent wrapper

**Files:**
- Modify: `novelizer/agents/schemas.py` (PromiseIntent beside ThreadIntent ~line 69)
- Modify: `novelizer/agents/intents.py` (`commit_promise_intents` beside `commit_thread_intents`)
- Modify: `novelizer/agents/base.py` (`_commit_promise_intents` wrapper beside `_commit_thread_intents` ~line 183)
- Test: `tests/agents/test_intents.py` (append)

**Interfaces:**
- Produces:

```python
class PromiseIntent(BaseModel):
    action: Literal["make", "progress", "pay", "release"]
    name: str = ""
    id: str = ""
    kind: Literal["foreshadow", "plant", "red_herring"] = "foreshadow"
    description: str = ""
    thread_id: str = ""
    note: str = ""
```

```python
async def commit_promise_intents(
    committer, agent_name: str, intents: list[PromiseIntent],
    active_promise_ids: set[str], active_thread_ids: set[str],
    chapter_id: str = "", source: str = "declared",
) -> None
```

and `BaseAgent._commit_promise_intents(intents, active_promise_ids, active_thread_ids, chapter_id="", source="declared")`. Task 6 calls the wrapper from Author/Editor commit.

- [ ] **Step 1: Write the failing tests**

Read `tests/agents/test_intents.py` first and mirror its thread-intent tests (fixture, fake committer capture). Required behaviors, each its own test:

```python
async def test_make_mints_slug_and_commits_promise_made(...):
    # PromiseIntent(action="make", name="The Sealed Letter", kind="plant", thread_id="t1")
    # with active_thread_ids={"t1"} -> PROMISE_MADE with id="the-sealed-letter",
    # kind="plant", thread_id="t1", chapter_id passed through

async def test_make_with_unknown_thread_id_drops_the_link_but_keeps_the_promise(...):
    # thread_id="ghost" not in active_thread_ids -> PROMISE_MADE committed with thread_id=""

async def test_make_collision_downgrades_to_progress(...):
    # name slugs to an id already in active_promise_ids -> PROMISE_PROGRESSED, not MADE

async def test_citing_actions_drop_unknown_ids_with_no_commit(...):
    # progress/pay/release with id not in active_promise_ids -> nothing committed

async def test_pay_and_release_commit_terminal_events(...):
    # pay -> PROMISE_PAID; release -> PROMISE_RELEASED (note carried into reason)

async def test_blank_name_make_is_dropped(...):
    # action="make", name="" -> nothing committed
```

- [ ] **Step 2: Run to verify failure** — `uv run pytest tests/agents/test_intents.py -v -k promise` — ImportError.

- [ ] **Step 3: Implement**

`schemas.py` — the PromiseIntent model above, with a docstring following ThreadIntent's ("One agent-declared ledger-promise action from structured output. `make` mints (name required); progress/pay/release cite an existing id exactly. `release` is the red-herring exit."). 

`intents.py` — mirror `commit_thread_intents`'s body exactly (blank-drop, `_normalize_id`, plant-collision downgrade, action→(payload, event) map):

```python
_PROMISE_EVENT_BY_ACTION = {
    "progress": (PromiseProgressed, EventType.PROMISE_PROGRESSED),
    "pay": (PromisePaid, EventType.PROMISE_PAID),
    "release": (PromiseReleased, EventType.PROMISE_RELEASED),
}


async def commit_promise_intents(
    committer, agent_name: str, intents: list[PromiseIntent],
    active_promise_ids: set[str], active_thread_ids: set[str],
    chapter_id: str = "", source: str = "declared",
) -> None:
    for intent in intents:
        if intent.action == "make":
            if not intent.name.strip():
                logger.warning("%s: dropped promise make with blank name", agent_name)
                continue
            promise_id = slugify_promise_name(intent.name)
            if promise_id in active_promise_ids:
                await committer.commit(
                    agent_name, EventType.PROMISE_PROGRESSED, promise_id,
                    PromiseProgressed(id=promise_id, chapter_id=chapter_id, note=intent.note, source=source),
                )
                continue
            thread_id = _normalize_id(intent.thread_id)
            if thread_id and thread_id not in active_thread_ids:
                logger.warning("%s: promise '%s' cited unknown thread %r — link dropped", agent_name, promise_id, thread_id)
                thread_id = ""
            await committer.commit(
                agent_name, EventType.PROMISE_MADE, promise_id,
                PromiseMade(
                    id=promise_id, name=intent.name.strip(), description=intent.description,
                    kind=intent.kind, chapter_id=chapter_id, thread_id=thread_id,
                    note=intent.note, source=source,
                ),
            )
            active_promise_ids.add(promise_id)
            continue
        promise_id = _normalize_id(intent.id)
        if promise_id not in active_promise_ids:
            logger.warning("%s: dropped promise %s citing unknown/terminal id %r", agent_name, intent.action, intent.id)
            continue
        payload_cls, event_type = _PROMISE_EVENT_BY_ACTION[intent.action]
        if payload_cls is PromiseReleased:
            payload = PromiseReleased(id=promise_id, reason=intent.note, chapter_id=chapter_id, source=source)
        else:
            payload = payload_cls(id=promise_id, chapter_id=chapter_id, note=intent.note, source=source)
        await committer.commit(agent_name, event_type, promise_id, payload)
```

(Match the real `commit_thread_intents` logging/import style exactly; if it does NOT mutate its active-id set after plant, drop the `active_promise_ids.add(...)` line and match — consistency with the sibling wins over this listing.)

`base.py` wrapper — mirror `_commit_thread_intents`:

```python
    async def _commit_promise_intents(
        self, intents: list[PromiseIntent], active_promise_ids: set[str],
        active_thread_ids: set[str], chapter_id: str = "", source: str = "declared",
    ) -> None:
        await intent_helpers.commit_promise_intents(
            self._committer, self.name, intents, active_promise_ids, active_thread_ids,
            chapter_id=chapter_id, source=source,
        )
```

- [ ] **Step 4: Run** `uv run pytest tests/agents/test_intents.py -q` — all PASS

- [ ] **Step 5: Commit**

```bash
git add novelizer/agents/schemas.py novelizer/agents/intents.py novelizer/agents/base.py tests/agents/test_intents.py
git commit -m "feat(agents): PromiseIntent with commit-time id validation"
```

---

### Task 6: Author + Editor carry promise intents

**Files:**
- Modify: `novelizer/agents/base.py` (`ChapterDraft` gains `promise_intents` ~line 37)
- Modify: `novelizer/agents/schemas.py` (`EditorVerdict` gains `promise_intents` ~line 158)
- Modify: `novelizer/agents/author.py` (poll gains `"promises"`; commit calls `_commit_promise_intents`; AUTHOR_SYSTEM_PROMPT gains one sentence)
- Modify: `novelizer/agents/editor.py` (same pattern; read its poll/commit first)
- Test: `tests/agents/test_author.py`, `tests/agents/test_editor.py` (append)

**Interfaces:**
- Consumes: Task 5. `TERMINAL_PROMISE_STATES` from `novelizer.canon.promises`.
- Produces: `ChapterDraft.promise_intents: list[PromiseIntent]`, `EditorVerdict.promise_intents: list[PromiseIntent]`; both agents' ctx dicts carry `"promises"`.

- [ ] **Step 1: Write the failing tests**

Append to `tests/agents/test_author.py` (reuse its `stack` fixture + FakeRunner):

```python
async def test_author_commits_promise_intents_with_validation(stack):
    events, proj, read, committer = stack
    from novelizer.agents.schemas import PromiseIntent
    draft = ChapterDraft(
        title="T", prose="P",
        promise_intents=[
            PromiseIntent(action="make", name="The Sealed Letter", kind="plant"),
            PromiseIntent(action="pay", id="never-made"),
        ],
    )
    author = Author(FakeRunner(draft), read, committer)
    await author.run_once()
    await proj.catch_up()
    promises = await read.list_promises()
    assert [p.id for p in promises] == ["the-sealed-letter"]
    assert promises[0].state.value == "open"
```

Append the equivalent to `tests/agents/test_editor.py` following that file's existing verdict-fixture pattern (an approving EditorVerdict carrying the same two intents; assert the same projection outcome).

Also pin the prompt change deliberately:

```python
async def test_author_system_prompt_mentions_promises():
    from novelizer.agents.author import AUTHOR_SYSTEM_PROMPT
    assert "promise" in AUTHOR_SYSTEM_PROMPT.lower()
```

- [ ] **Step 2: Run to verify failure** — `uv run pytest tests/agents/test_author.py tests/agents/test_editor.py -v -k promise` — ValidationError/AttributeError.

- [ ] **Step 3: Implement**

`ChapterDraft` and `EditorVerdict` each gain:

```python
    promise_intents: list[PromiseIntent] = Field(default_factory=list)
```

`Author.poll()` adds `"promises": await self._read.list_promises(),`. `Author.commit()` adds, beside the thread-intent call:

```python
        active_promise_ids = {
            p.id for p in ctx["promises"] if p.state.value not in TERMINAL_PROMISE_STATES
        }
        await self._commit_promise_intents(
            draft.promise_intents, active_promise_ids, active_thread_ids, chapter_id=chapter_id
        )
```

(import `TERMINAL_PROMISE_STATES` from `novelizer.canon.promises`; `active_thread_ids` already exists in that scope — place the promise call after it). Editor: same additions in its poll/commit (read the file; `EditorVerdict` commits happen in `Editor.commit` beside its thread-intent call).

`AUTHOR_SYSTEM_PROMPT` — append one sentence to the existing string (before AI_TELL_BAN_NOTE):

```python
You may declare promise intents: 'make' plants a discrete setup (a Chekhov's gun, foreshadowing, or red herring); progress/pay/release cite an existing promise id exactly.
```

Editor's SYSTEM_PROMPT gains the equivalent sentence adapted to review ("You may declare promise intents when the chapter plants or pays off a setup..."). If any existing test pins these prompt strings byte-for-byte, update those pins in the same commit — this is a deliberate prompt change.

- [ ] **Step 4: Run** `uv run pytest tests/agents/ -q` — all PASS

- [ ] **Step 5: Commit**

```bash
git add novelizer/agents/ tests/agents/
git commit -m "feat(agents): Author and Editor declare ledger promises"
```

---

### Task 7: Brain faculties — ledger + resolution pacing + prompt notes

**Files:**
- Create: `novelizer/brain/ledger.py`, `novelizer/brain/resolution_pacing.py`
- Modify: `novelizer/brain/context.py` (two note fns), `novelizer/agents/author.py` (`_summarize` includes `ledger_note` + `resolution_pacing_note`), `novelizer/agents/editor.py` (same)
- Test: `tests/brain/test_ledger.py`, `tests/brain/test_resolution_pacing.py`, `tests/brain/test_context.py` (append), plus prompt-inclusion tests in `tests/agents/test_author.py`

**Interfaces:**
- Produces (pure functions, ReadStore data in, never persisted):

```python
# ledger.py
def open_promises(promises) -> list[PromiseRecord]            # state == open
def overdue_promises(promises, chapters) -> list[PromiseRecord]  # open, window_hi>0, len(chapters) > window_hi
def due_promises(promises, chapters) -> list[PromiseRecord]      # open, window active: lo<=len(chapters)<=hi
# resolution_pacing.py
def overdue_resolutions(threads, chapters) -> list[ThreadRecord]     # non-terminal, window_hi>0, len(chapters) > window_hi
def overdue_reveals(secrets, chapters) -> list[SecretRecord]         # unrevealed, reveal_window_hi>0, past
def congested_windows(threads, secrets, max_per_window=2) -> list[tuple[int, int, int]]
    # merge all set windows (thread + unrevealed secret reveal windows) into overlapping spans;
    # return (span_lo, span_hi, count) for spans with count > max_per_window
```

- `context.py` note fns (empty-string-when-nothing contract):

```python
def ledger_note(promises, chapters) -> str
    # "\n\nPromise ledger (pay or release these, citing ids exactly):" listing
    # overdue first ("OVERDUE — window closed ch N"), then due ("due ch L-H"); "" if neither
def resolution_pacing_note(threads, secrets, chapters) -> str
    # overdue thread resolutions + overdue reveals + congestion warnings; "" when nothing
```

- [ ] **Step 1: Write the failing tests**

`tests/brain/test_ledger.py` (mirror test_staleness.py's direct-construction style):

```python
from novelizer.brain.ledger import due_promises, open_promises, overdue_promises
from novelizer.store.models import Chapter, PromiseRecord, PromiseState


def _chapters(n):
    return [Chapter(id=f"c{i}", title=str(i), prose="p") for i in range(n)]


def test_open_promises_excludes_terminal_states():
    ps = [PromiseRecord(id="a", name="A"),
          PromiseRecord(id="b", name="B", state=PromiseState.paid),
          PromiseRecord(id="c", name="C", state=PromiseState.released)]
    assert [p.id for p in open_promises(ps)] == ["a"]


def test_overdue_promise_past_window_hi():
    p = PromiseRecord(id="a", name="A", window_lo=2, window_hi=3)
    assert overdue_promises([p], _chapters(4)) == [p]
    assert overdue_promises([p], _chapters(3)) == []


def test_unset_window_never_overdue_or_due():
    p = PromiseRecord(id="a", name="A")
    assert overdue_promises([p], _chapters(10)) == []
    assert due_promises([p], _chapters(10)) == []


def test_due_promise_inside_window():
    p = PromiseRecord(id="a", name="A", window_lo=2, window_hi=4)
    assert due_promises([p], _chapters(1)) == []
    assert due_promises([p], _chapters(3)) == [p]


def test_terminal_promise_never_overdue():
    p = PromiseRecord(id="a", name="A", window_hi=1, state=PromiseState.paid)
    assert overdue_promises([p], _chapters(5)) == []
```

`tests/brain/test_resolution_pacing.py`:

```python
from novelizer.brain.resolution_pacing import congested_windows, overdue_resolutions, overdue_reveals
from novelizer.store.models import Chapter, SecretRecord, ThreadRecord, ThreadState


def _chapters(n):
    return [Chapter(id=f"c{i}", title=str(i), prose="p") for i in range(n)]


def test_overdue_resolution_past_window():
    t = ThreadRecord(id="t", name="T", window_lo=2, window_hi=3)
    assert overdue_resolutions([t], _chapters(4)) == [t]
    assert overdue_resolutions([t], _chapters(3)) == []


def test_terminal_thread_never_overdue():
    t = ThreadRecord(id="t", name="T", state=ThreadState.paid_off, window_hi=1)
    assert overdue_resolutions([t], _chapters(9)) == []


def test_overdue_reveal_only_when_unrevealed():
    s = SecretRecord(id="s", title="S", reveal_window_lo=1, reveal_window_hi=2)
    assert overdue_reveals([s], _chapters(3)) == [s]
    revealed = s.model_copy(update={"revealed": True})
    assert overdue_reveals([revealed], _chapters(3)) == []


def test_congestion_groups_overlapping_windows():
    ts = [ThreadRecord(id=f"t{i}", name=str(i), window_lo=19, window_hi=21) for i in range(3)]
    spans = congested_windows(ts, [], max_per_window=2)
    assert spans == [(19, 21, 3)]


def test_no_congestion_below_threshold_or_disjoint():
    ts = [ThreadRecord(id="a", name="a", window_lo=1, window_hi=2),
          ThreadRecord(id="b", name="b", window_lo=5, window_hi=6)]
    assert congested_windows(ts, [], max_per_window=2) == []
```

`tests/brain/test_context.py` — append: `ledger_note` returns `""` for empty/no-window inputs; contains "OVERDUE" + the promise id when past window; `resolution_pacing_note` `""` when quiet, mentions thread name + "window" when overdue, mentions "resolve in the same window" (or your chosen phrasing) for congestion. Pin exact strings in the tests you write, then implement to match.

Prompt inclusion (append to tests/agents/test_author.py, mirroring `test_author_prompt_includes_causal_flags_when_edges_flagged`): seed one overdue promise via events (PROMISE_MADE with window_hi=1, then two chapters), run the author with a FakeRunner, assert `"Promise ledger"` appears in `runner.calls[0]["messages"][0]["content"]`; and the no-promises case leaves the prompt free of `"Promise ledger"`.

- [ ] **Step 2: Run to verify failure** — `uv run pytest tests/brain/ -v -k "ledger or pacing"` — ModuleNotFoundError.

- [ ] **Step 3: Implement**

`ledger.py`:

```python
from novelizer.store.models import Chapter, PromiseRecord, PromiseState


def open_promises(promises: list[PromiseRecord]) -> list[PromiseRecord]:
    return [p for p in promises if p.state == PromiseState.open]


def overdue_promises(promises: list[PromiseRecord], chapters: list[Chapter]) -> list[PromiseRecord]:
    now = len(chapters)
    return [p for p in open_promises(promises) if p.window_hi > 0 and now > p.window_hi]


def due_promises(promises: list[PromiseRecord], chapters: list[Chapter]) -> list[PromiseRecord]:
    now = len(chapters)
    return [
        p for p in open_promises(promises)
        if p.window_hi > 0 and p.window_lo <= now <= p.window_hi
    ]
```

`resolution_pacing.py`:

```python
from novelizer.canon.threads import TERMINAL_STATES
from novelizer.store.models import Chapter, SecretRecord, ThreadRecord


def overdue_resolutions(threads: list[ThreadRecord], chapters: list[Chapter]) -> list[ThreadRecord]:
    now = len(chapters)
    return [
        t for t in threads
        if t.state.value not in TERMINAL_STATES and t.window_hi > 0 and now > t.window_hi
    ]


def overdue_reveals(secrets: list[SecretRecord], chapters: list[Chapter]) -> list[SecretRecord]:
    now = len(chapters)
    return [
        s for s in secrets
        if not s.revealed and s.reveal_window_hi > 0 and now > s.reveal_window_hi
    ]


def congested_windows(
    threads: list[ThreadRecord], secrets: list[SecretRecord], max_per_window: int = 2,
) -> list[tuple[int, int, int]]:
    """Merge every set window (non-terminal threads + unrevealed secrets)
    into overlapping spans; report spans holding more than max_per_window."""
    windows = [
        (t.window_lo, t.window_hi) for t in threads
        if t.state.value not in TERMINAL_STATES and t.window_hi > 0
    ] + [
        (s.reveal_window_lo, s.reveal_window_hi) for s in secrets
        if not s.revealed and s.reveal_window_hi > 0
    ]
    if not windows:
        return []
    windows.sort()
    spans: list[tuple[int, int, int]] = []
    lo, hi, count = *windows[0], 1
    for w_lo, w_hi in windows[1:]:
        if w_lo <= hi:
            hi, count = max(hi, w_hi), count + 1
        else:
            spans.append((lo, hi, count))
            lo, hi, count = w_lo, w_hi, 1
    spans.append((lo, hi, count))
    return [s for s in spans if s[2] > max_per_window]
```

`context.py` — two note fns following `stale_threads_note`'s shape; compose the exact strings your Step-1 tests pinned. Wire into `author.py::_summarize` (new locals `ledger = ledger_note(ctx["promises"], ctx["chapters"])`, `pacing_plan = resolution_pacing_note(ctx["threads"], ctx["secrets"], ctx["chapters"])`, appended into the return f-string beside `{brain}{secrets}{causal}`) and the Editor's prompt assembly equivalently. Author.poll already gains `"promises"` in Task 6.

- [ ] **Step 4: Run** `uv run pytest tests/brain/ tests/agents/ -q` — all PASS

- [ ] **Step 5: Commit**

```bash
git add novelizer/brain/ novelizer/agents/author.py novelizer/agents/editor.py tests/brain/ tests/agents/
git commit -m "feat(brain): ledger + resolution-pacing faculties with prompt notes"
```

---

### Task 8: Director commands — set resolution/reveal windows

**Files:**
- Modify: `novelizer/director/commands.py` (two new commands beside seed/focus)
- Modify: `novelizer/director/cli.py` (two subcommands mirroring how seed/focus are exposed — read the file first)
- Test: `tests/director/` (append to the existing commands test file — find it with `ls tests/director/ tests/test_director*.py 2>/dev/null` and mirror; if commands tests live elsewhere, follow that location)

**Interfaces:**
- Consumes: Task 1 events; `ReadStore.get_thread`/`get_secret`.
- Produces:

```python
async def plan_thread_resolution(events, read, thread_id: str, window_lo: int, window_hi: int, note: str = "") -> str
async def plan_secret_reveal(events, read, secret_id: str, window_lo: int, window_hi: int) -> str
```

Each returns a human-readable confirmation string (or raises/returns an error string matching how seed/focus report — mirror the file). Validation: id must exist and be non-terminal/unrevealed; `1 <= lo <= hi` (or both 0 to clear).

- [ ] **Step 1: Write the failing tests**

Mirror the existing seed/focus command tests exactly (fixture + call + projection assert). Required cases:

```python
async def test_plan_thread_resolution_appends_event_and_projects_window(...):
    # plant thread t1 -> plan_thread_resolution(events, read, "t1", 18, 20, "gate scene")
    # catch_up -> thread.window_lo == 18, window_hi == 20

async def test_plan_thread_resolution_rejects_unknown_and_terminal_ids(...):
    # unknown id -> error string, no event appended; paid-off thread -> same

async def test_plan_thread_resolution_rejects_inverted_window(...):
    # lo=20, hi=18 -> error string, no event

async def test_plan_secret_reveal_appends_and_rejects_revealed(...):
```

- [ ] **Step 2: Run to verify failure** — ImportError on the new names.

- [ ] **Step 3: Implement**

```python
async def plan_thread_resolution(events, read, thread_id: str, window_lo: int, window_hi: int, note: str = "") -> str:
    thread = await read.get_thread(thread_id)
    if thread is None:
        return f"no such thread: {thread_id}"
    if thread.state.value in TERMINAL_STATES:
        return f"thread {thread_id} is already {thread.state.value}"
    if not ((window_lo == 0 and window_hi == 0) or (1 <= window_lo <= window_hi)):
        return f"invalid window {window_lo}-{window_hi} (need 1 <= lo <= hi, or 0 0 to clear)"
    await events.append(
        EventType.THREAD_RESOLUTION_PLANNED, thread_id,
        ThreadResolutionPlanned(id=thread_id, window_lo=window_lo, window_hi=window_hi, planned_payoff_note=note),
    )
    return f"resolution window ch{window_lo}-{window_hi} planned for '{thread.name}'"
```

`plan_secret_reveal` mirrors it (`get_secret`, `revealed` check, `SecretRevealPlanned`). Match the ACTUAL return/reporting convention of seed/focus in the real file — if they return the created object or print via a console abstraction, mirror that instead of bare strings, and adjust the tests you wrote in Step 1 accordingly BEFORE implementing. CLI: add subcommands the same way seed/focus are registered (click decorators per pyproject's click dep), accepting `THREAD_ID LO HI [--note]` / `SECRET_ID LO HI`.

- [ ] **Step 4: Run** the director tests + `uv run pytest tests/ -q -k director` — all PASS

- [ ] **Step 5: Commit**

```bash
git add novelizer/director/ tests/
git commit -m "feat(director): plan-resolution and plan-reveal commands"
```

---

### Task 9: TUI — Threads board windows + ledger section + alarms

**Files:**
- Modify: `novelizer/tui/widgets/brain_model.py` (`thread_line` gains window badge; `threads_tab` gains `promises` + `secrets` params, ledger section, alarm arithmetic)
- Modify: `novelizer/tui/widgets/brain_panel.py` (`refresh_from` passes promises/secrets into `threads_tab`)
- Modify: `novelizer/tui/app.py` (`_brain_loop` fetches `read.list_promises()` and passes through — mirror how threads/secrets already flow)
- Test: `tests/tui/test_brain_model.py` (append), `tests/tui/test_brain_panel.py` or equivalent wiring test (mirror existing)

**Interfaces:**
- Consumes: Tasks 2-3 records/queries; `overdue_promises`/`due_promises`/`overdue_resolutions`/`overdue_reveals`/`congested_windows` from Task 7 (reuse — do not reimplement checks in the TUI).
- Produces: `threads_tab(threads, chapters, promises, secrets, threshold=...) -> ThreadsTab` (extended signature; keep `ThreadsTab` dataclass shape `lines + alarm_count`).

Board behavior to implement and pin in tests:
- A non-terminal thread with a set window shows a badge appended to its detail: `· due ch18-20` (DIM) when now < lo or inside window, `· OVERDUE ch20` (ALARM_STYLE) when past `window_hi` (reuse `overdue_resolutions` for the decision).
- After the thread groups, a ledger section header `Ledger` (only when any open promise exists): one line per open promise — glyph `◇`, name, kind tag for red herrings `(red herring)`, window badge same rules as threads; overdue lines in ALARM_STYLE pinned first; paid/released promises fold into one dim count line like terminal threads.
- `alarm_count` = stale threads + overdue resolutions + overdue reveals + overdue promises + number of congested spans (each span = 1 alarm). Congested spans render one warning line under the ledger: `⚠ 3 resolutions target ch19-21` (WARN_STYLE).
- Empty-state line unchanged when there are neither threads nor promises.

- [ ] **Step 1: Write the failing tests** — append to tests/tui/test_brain_model.py, mirroring the existing threads_tab tests' construction style (direct record construction, assert on `Text` plain strings + alarm_count). Cover: window badge (due + overdue), ledger section presence/ordering (overdue-first), red-herring tag, congestion warning line + its alarm contribution, terminal-promise fold line, empty-state unchanged, alarm_count arithmetic across all sources.

- [ ] **Step 2: Run to verify failure** — TypeError (threads_tab signature) / assertions.

- [ ] **Step 3: Implement** in brain_model.py using the layout constants already there (`NAME_WIDTH`, `ALARM_STYLE`, `WARN_STYLE`, `DIM`), then thread the new params through `brain_panel.refresh_from` and `app._brain_loop` exactly the way threads/secrets already flow (read those call sites and mirror; the panel's `#threads_body` Static is reused — no new tab in M7).

- [ ] **Step 4: Run** `uv run pytest tests/tui/ -q` — all PASS

- [ ] **Step 5: Commit**

```bash
git add novelizer/tui/ tests/tui/
git commit -m "feat(tui): Threads board planned-window badges + promise ledger section + alarms"
```

---

### Task 10: Docs + full-suite gate

**Files:**
- Modify: `docs/MILESTONES.md` (M7 row Status → `✅ complete (pending review)` — final wording set by the controller at merge)
- Modify: `docs/QUICKSTART.md` (short "Promise ledger & resolution windows" note: what agents do automatically, the two director commands, where it shows in the TUI)
- Modify: `docs/superpowers/specs/2026-07-19-authoring-skills-blueprint-design.md` (M7 mapping bullet: annotate "checker mining, canon_fs rendering, and search kind deferred to M8")

- [ ] **Step 1: Update the three docs** as above.
- [ ] **Step 2: Full suite** — `uv run pytest -q` (≈6 min) — all pass.
- [ ] **Step 3: Commit**

```bash
git add docs/
git commit -m "docs: M7 The Ledger delivered — promises, windows, pacing brain, board badges"
```
