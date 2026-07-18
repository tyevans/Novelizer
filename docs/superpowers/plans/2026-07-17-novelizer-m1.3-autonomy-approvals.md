# M1.3 · Autonomy & Approvals — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax.

**Goal:** Gate an agent → its next canon-changing output queues as a *proposal* instead of entering canon → the director approves (the real event is appended) or rejects (a rejection is recorded) from the TUI and CLI. This is M1's done-criterion and completes the milestone.

**Architecture:** Agents already write canon only through an injected `Committer` (`novelizer/canon/committer.py`) — the seam M1.1 built for exactly this. M1.3 adds a `GatingCommitter` that consults an `AutonomyPolicy` (itself reading a materialized `autonomy_state` projection) to decide, per `(agent_name, event_type)`, whether to append the real event or wrap it as a `proposal.created` event. `Runtime` swaps `Committer` for `GatingCommitter` in one place; **no agent code changes**. A `ProposalService` turns an open proposal into either the real event + `proposal.approved`, or `proposal.rejected`. The shared director command layer (`novelizer/director/commands.py`) grows `autonomy`/`approve`/`reject`, used identically by the CLI and the TUI's command input — the same DRY seam M1.2 established.

**Tech Stack:** Python 3.13, `aiosqlite`, `pydantic` v2, `click`+`rich`, `textual` (Static/Input/Tree, `run_test` pilot), `pytest`+`pytest-asyncio` (`asyncio_mode=auto`).

**Context — current state after M1.2 (on `m1.2-mission-control`):**

- `novelizer/canon/events.py` — `EventType` string-constant class (`WORLD_ENTRY_CREATED`, `WORLD_ENTRY_SUPERSEDED`, `CHARACTER_CREATED`, `CHARACTER_UPDATED`, `CHAPTER_CREATED`, `CHAPTER_STATUS_CHANGED`, `DIRECTOR_SIGNAL_CREATED`, `DIRECTOR_SIGNAL_CONSUMED`, `RETCON_REQUEST_CREATED`, `RETCON_REQUEST_RESOLVED`, `RETCON_REQUEST_REJECTED`); `StoredEvent(sequence, id, event_type, aggregate_id, payload: dict, created_at)`.
- `novelizer/canon/committer.py` — `Committer(event_store)` with `async def commit(self, agent_name: str, event_type: str, aggregate_id: str, payload: BaseModel) -> None`, currently `await self._events.append(event_type, aggregate_id, payload)`. Docstring explicitly earmarks this seam for M1.3 gating.
- `novelizer/canon/event_store.py` — `EventStore(path)`; `async def append(self, event_type: str, aggregate_id: str, payload: BaseModel) -> StoredEvent` (calls `payload.model_dump_json()` — **requires a pydantic `BaseModel`**, not a raw dict); `async def events_since(self, sequence, event_types=None) -> list[StoredEvent]`.
- `novelizer/canon/projector.py` — `Projector(event_store, path)`; `_CREATE` DDL for `chapters/world_entries/characters/director_signals/retcon_requests/projector_state`; `_reset_state()` deletes rows from those tables and resets `last_sequence`; `_apply(ev)` is an `if/elif` dispatch keyed on `ev.event_type`; `catch_up()`/`run(interval)`/`stop()`.
- `novelizer/canon/read_store.py` — `ReadStore(path)` with `list_chapters(status=None)`, `get_chapter(id)`, `list_world_entries(domain=None)`, `list_characters()`, `get_character(id)`, `list_unconsumed_signals(target_agent=None)`, `list_retcon_requests(status=None)`.
- `novelizer/runtime.py` — `Runtime(settings, runner=None, runners=None)`; builds `self.committer = Committer(self.events)` in `__init__`, then in `start()` builds all six agents with `self._runner_for(name, builder)` and wires `self.scheduler = Scheduler(self.agents, self.read)`. Exposes `.events/.projector/.read/.committer/.agents/.scheduler` + named agent attrs.
- `novelizer/agents/base.py` — `BaseAgent(runner, read_store, committer, interval, name=None)`; agents call `self._committer.commit(self.name, EventType.X, aggregate_id, payload)`; `_consume_signals` commits `DIRECTOR_SIGNAL_CONSUMED` — **must never be gated**.
- `novelizer/scheduler.py` — `Scheduler(agents, read_store, tick_sleep=1.0, clock=time.monotonic)`; `status() -> list[{"name","paused","running"}]`; `pause_agent(name)`/`resume_agent(name)`; `tick()`/`run()`/`stop()`.
- `novelizer/director/commands.py` — `async def seed(events, text)`, `async def focus(events, entity)`, `def pause(scheduler, name)`, `def resume(scheduler, name)`, `async def dispatch(runtime, line) -> str` parsing `seed/focus/pause/resume` off a runtime-like object exposing `.events`/`.scheduler`.
- `novelizer/director/cli.py` — `click` group; `_with_runtime(settings, fn)` boots events/projector/read (no agents); commands `seed/chapters/read/retcons`; bare invocation launches the TUI via `_launch_tui`.
- `novelizer/tui/app.py` — `NovelizerApp(runtime)`; `compose()` yields `Header`, `Horizontal#body` (`Vertical#left`: `RichLog#feed` + `AgentRoster#roster`; `Vertical#right`: `StoryBrowser#browser` + `Static#detail`), `Static#statusbar` (currently hard-coded `"AUTONOMY: full-auto   ·   ..."`), `Input#command`, `Footer`; `on_mount` starts five `run_worker` loops (`_projector_loop/_scheduler_loop/_feed_loop/_roster_loop/_browser_loop`), each guarded via `_report_worker_error`; `_run_command`/`on_input_submitted` route through `commands.dispatch`; `on_tree_node_selected` renders via `browser_model.detail_text`. `BINDINGS` include `ctrl+k` (focus command, since Textual 5.3.0 rejects `"colon"` as a key name), `r` (toggle Room), `q` (quit).
- `novelizer/store/models.py` — `Domain/CanonStatus/EditorialStatus/RetconStatus/SignalKind` `StrEnum`s; `WorldEntry/Character/Chapter/RetconRequest/DirectorSignal` pydantic models using module-level `_uuid()`/`_now()` factories. **Not edited in this plan** except a note below — new models go in `novelizer/canon/autonomy.py`.
- `novelizer/tui/widgets/roster.py` — pure `roster_line(status_row) -> str` + `AgentRoster(Static)` with `update_from(status)`.
- `novelizer/tui/widgets/browser_model.py` — pure `async def browser_sections(read) -> list[section-dicts]`, `async def detail_text(read, section_key, item_id) -> str`.
- Test layout: `tests/canon/`, `tests/director/`, `tests/tui/`, `tests/test_scheduler.py`, `tests/test_runtime.py`. Fixtures build a fresh SQLite file per test via `tempfile.mkstemp`.

## Global Constraints

- **Python** `>=3.13`.
- **Event sourcing is absolute.** The event log is the sole source of truth; only the `Projector` writes projection tables; agents/CLI/TUI change canon only by appending events. The `GatingCommitter` still only *appends* — it never mutates existing rows, and a rejected/approved proposal is itself recorded as new events, never edited in place.
- **`EventType` constants only** — no magic strings for event types anywhere outside `events.py`.
- **All LLM access via deepagents/OpenAI-compat**; agents take injected runners. Unaffected by this plan.
- **TDD, black-box first:** failing test → fail → implement → pass → commit, for every task. Parametrized/property-based tests wherever an invariant generalizes (the autonomy level → gated-event-type mapping is a prime target).
- **`asyncio_mode = "auto"`.**
- **`novelizer/store/models.py` is effectively unchanged.** New domain models (`Proposal`, `AutonomyState`, `AutonomyLevel`) live in `novelizer/canon/autonomy.py`, reusing the same `_uuid()`/`_now()`-style factories and `StrEnum` conventions.
- **DRY via pydantic** — no hand-rolled dict validation where a `BaseModel` will do.
- **`director_signal.created`/`director_signal.consumed` are NEVER gated**, at any autonomy level — the dial must not be able to lock out the director's own steering channel or the agents' signal-consumption bookkeeping.
- **The gating swap requires zero agent edits.** `GatingCommitter` presents the exact same `commit(agent_name, event_type, aggregate_id, payload)` coroutine signature as `Committer`; `Runtime` is the only place that changes which one gets constructed.

---

### Task 1: Autonomy domain models — `Proposal`, `AutonomyLevel`, `AutonomyState`

**Files:**
- Create: `novelizer/canon/autonomy.py`
- Test: `tests/canon/test_autonomy_models.py`

**Interfaces:**
- Produces:
  - `class AutonomyLevel(StrEnum)`: `full_auto`, `gated_retcons`, `gated_canon`, `gated_all`.
  - `class ProposalStatus(StrEnum)`: `open`, `approved`, `rejected`.
  - `class Proposal(BaseModel)`: `id: str` (uuid factory), `created_at: datetime` (now factory), `proposing_agent: str`, `target_event_type: str`, `target_aggregate_id: str`, `payload: dict`, `status: ProposalStatus = ProposalStatus.open`.
  - `class AutonomyState(BaseModel)`: `global_level: AutonomyLevel = AutonomyLevel.full_auto`, `overrides: dict[str, AutonomyLevel] = {}` (per-agent), `def level_for(self, agent_name: str) -> AutonomyLevel` returning the override if present else `global_level`.

- [ ] **Step 1: Write the failing test**

Create `tests/canon/test_autonomy_models.py`:
```python
from novelizer.canon.autonomy import AutonomyLevel, AutonomyState, Proposal, ProposalStatus


def test_proposal_defaults():
    p = Proposal(
        proposing_agent="author",
        target_event_type="chapter.created",
        target_aggregate_id="c1",
        payload={"title": "One", "prose": "p"},
    )
    assert p.status == ProposalStatus.open
    assert p.id and p.created_at is not None


def test_autonomy_state_level_for_uses_global_by_default():
    st = AutonomyState(global_level=AutonomyLevel.gated_canon)
    assert st.level_for("author") == AutonomyLevel.gated_canon
    assert st.level_for("editor") == AutonomyLevel.gated_canon


def test_autonomy_state_level_for_prefers_override():
    st = AutonomyState(
        global_level=AutonomyLevel.full_auto,
        overrides={"retconner": AutonomyLevel.gated_all},
    )
    assert st.level_for("retconner") == AutonomyLevel.gated_all
    assert st.level_for("author") == AutonomyLevel.full_auto


def test_autonomy_state_default_is_full_auto():
    st = AutonomyState()
    assert st.global_level == AutonomyLevel.full_auto
    assert st.overrides == {}
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/canon/test_autonomy_models.py -v`
Expected: FAIL (`ModuleNotFoundError: No module named 'novelizer.canon.autonomy'`).

- [ ] **Step 3: Implement**

Create `novelizer/canon/autonomy.py`:
```python
from __future__ import annotations
import uuid
from datetime import datetime, timezone
from enum import StrEnum
from pydantic import BaseModel, Field


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _uuid() -> str:
    return str(uuid.uuid4())


class AutonomyLevel(StrEnum):
    full_auto = "full_auto"
    gated_retcons = "gated_retcons"
    gated_canon = "gated_canon"
    gated_all = "gated_all"


class ProposalStatus(StrEnum):
    open = "open"
    approved = "approved"
    rejected = "rejected"


class Proposal(BaseModel):
    id: str = Field(default_factory=_uuid)
    created_at: datetime = Field(default_factory=_now)
    proposing_agent: str
    target_event_type: str
    target_aggregate_id: str
    payload: dict
    status: ProposalStatus = ProposalStatus.open


class AutonomyState(BaseModel):
    global_level: AutonomyLevel = AutonomyLevel.full_auto
    overrides: dict[str, AutonomyLevel] = Field(default_factory=dict)

    def level_for(self, agent_name: str) -> AutonomyLevel:
        return self.overrides.get(agent_name, self.global_level)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/canon/test_autonomy_models.py -v`
Expected: PASS (4 passed).

- [ ] **Step 5: Commit**

```bash
git add novelizer/canon/autonomy.py tests/canon/test_autonomy_models.py
git commit -m "feat: add Proposal/AutonomyLevel/AutonomyState domain models"
```

---

### Task 2: Autonomy & proposal event types

**Files:**
- Modify: `novelizer/canon/events.py`
- Test: `tests/canon/test_events.py` (extend)

**Interfaces:**
- Produces (constants on `EventType`): `PROPOSAL_CREATED = "proposal.created"`, `PROPOSAL_APPROVED = "proposal.approved"`, `PROPOSAL_REJECTED = "proposal.rejected"`, `AUTONOMY_CHANGED = "autonomy.changed"`.

- [ ] **Step 1: Write the failing test**

Append to `tests/canon/test_events.py`:
```python
def test_autonomy_and_proposal_event_types_exist():
    from novelizer.canon.events import EventType
    assert EventType.PROPOSAL_CREATED == "proposal.created"
    assert EventType.PROPOSAL_APPROVED == "proposal.approved"
    assert EventType.PROPOSAL_REJECTED == "proposal.rejected"
    assert EventType.AUTONOMY_CHANGED == "autonomy.changed"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/canon/test_events.py -v`
Expected: FAIL (`AttributeError: type object 'EventType' has no attribute 'PROPOSAL_CREATED'`).

- [ ] **Step 3: Implement**

In `novelizer/canon/events.py`, add to `EventType`:
```python
    PROPOSAL_CREATED = "proposal.created"
    PROPOSAL_APPROVED = "proposal.approved"
    PROPOSAL_REJECTED = "proposal.rejected"
    AUTONOMY_CHANGED = "autonomy.changed"
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/canon/test_events.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add novelizer/canon/events.py tests/canon/test_events.py
git commit -m "feat: add proposal.* and autonomy.changed event types"
```

---

### Task 3: Projector — `proposals` and `autonomy_state` tables

**Files:**
- Modify: `novelizer/canon/projector.py`
- Test: `tests/canon/test_projector.py` (extend)

**Interfaces:**
- Produces: two new tables materialized by `_apply`:
  - `proposals(id TEXT PRIMARY KEY, data TEXT NOT NULL, status TEXT NOT NULL, proposing_agent TEXT NOT NULL)` — written on `PROPOSAL_CREATED` (insert, `status='open'`) and updated (status only, data left as the original proposal) on `PROPOSAL_APPROVED`/`PROPOSAL_REJECTED`.
  - `autonomy_state(id TEXT PRIMARY KEY, data TEXT NOT NULL)` — a singleton row (`id='singleton'`) written/replaced on `AUTONOMY_CHANGED`, storing the full resolved `AutonomyState` JSON (global level + overrides dict) that the event payload carries.
  - Both tables added to `_reset_state`'s cleared-table list (autonomy_state is deleted, not reset to a default — `ReadStore.get_autonomy_state` supplies the full-auto default when the table is empty, per Task 4).

- [ ] **Step 1: Write the failing test**

Append to `tests/canon/test_projector.py`:
```python
async def test_proposal_created_is_projected_open(wired):
    from novelizer.canon.autonomy import Proposal
    events, proj, _ = wired
    prop = Proposal(proposing_agent="author", target_event_type="chapter.created",
                     target_aggregate_id="c1", payload={"title": "One", "prose": "p"})
    await events.append(EventType.PROPOSAL_CREATED, prop.id, prop)
    await proj.catch_up()
    cur = await proj._conn.execute("SELECT status, proposing_agent FROM proposals WHERE id=?", (prop.id,))
    row = await cur.fetchone()
    assert row == ("open", "author")


async def test_proposal_approved_flips_status(wired):
    from novelizer.canon.autonomy import Proposal
    events, proj, _ = wired
    prop = Proposal(proposing_agent="author", target_event_type="chapter.created",
                     target_aggregate_id="c1", payload={"title": "One", "prose": "p"})
    await events.append(EventType.PROPOSAL_CREATED, prop.id, prop)
    await proj.catch_up()
    approved = prop.model_copy(update={"status": "approved"})
    await events.append(EventType.PROPOSAL_APPROVED, prop.id, approved)
    await proj.catch_up()
    cur = await proj._conn.execute("SELECT status FROM proposals WHERE id=?", (prop.id,))
    assert (await cur.fetchone())[0] == "approved"


async def test_proposal_rejected_flips_status(wired):
    from novelizer.canon.autonomy import Proposal
    events, proj, _ = wired
    prop = Proposal(proposing_agent="editor", target_event_type="chapter.status_changed",
                     target_aggregate_id="c1", payload={})
    await events.append(EventType.PROPOSAL_CREATED, prop.id, prop)
    await proj.catch_up()
    rejected = prop.model_copy(update={"status": "rejected"})
    await events.append(EventType.PROPOSAL_REJECTED, prop.id, rejected)
    await proj.catch_up()
    cur = await proj._conn.execute("SELECT status FROM proposals WHERE id=?", (prop.id,))
    assert (await cur.fetchone())[0] == "rejected"


async def test_autonomy_changed_is_projected_singleton(wired):
    from novelizer.canon.autonomy import AutonomyState, AutonomyLevel
    events, proj, _ = wired
    st = AutonomyState(global_level=AutonomyLevel.gated_canon, overrides={"retconner": AutonomyLevel.gated_all})
    await events.append(EventType.AUTONOMY_CHANGED, "singleton", st)
    await proj.catch_up()
    cur = await proj._conn.execute("SELECT data FROM autonomy_state WHERE id='singleton'")
    row = await cur.fetchone()
    loaded = AutonomyState.model_validate_json(row[0])
    assert loaded.global_level == AutonomyLevel.gated_canon
    assert loaded.overrides["retconner"] == AutonomyLevel.gated_all


async def test_reset_state_clears_proposals_and_autonomy(wired):
    from novelizer.canon.autonomy import Proposal, AutonomyState, AutonomyLevel
    events, proj, _ = wired
    prop = Proposal(proposing_agent="author", target_event_type="chapter.created",
                     target_aggregate_id="c1", payload={})
    await events.append(EventType.PROPOSAL_CREATED, prop.id, prop)
    await events.append(EventType.AUTONOMY_CHANGED, "singleton", AutonomyState(global_level=AutonomyLevel.gated_all))
    await proj.catch_up()
    await proj._reset_state()
    cur = await proj._conn.execute("SELECT COUNT(*) FROM proposals")
    assert (await cur.fetchone())[0] == 0
    cur = await proj._conn.execute("SELECT COUNT(*) FROM autonomy_state")
    assert (await cur.fetchone())[0] == 0
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/canon/test_projector.py -v`
Expected: FAIL (`sqlite3.OperationalError: no such table: proposals`).

- [ ] **Step 3: Implement**

In `novelizer/canon/projector.py`:

1. Extend `_CREATE`:
```python
_CREATE = """
CREATE TABLE IF NOT EXISTS chapters (
    id TEXT PRIMARY KEY, data TEXT NOT NULL, editorial_status TEXT NOT NULL, supersedes_id TEXT
);
CREATE TABLE IF NOT EXISTS world_entries (
    id TEXT PRIMARY KEY, data TEXT NOT NULL, canon_status TEXT NOT NULL, supersedes_id TEXT
);
CREATE TABLE IF NOT EXISTS characters (
    id TEXT PRIMARY KEY, data TEXT NOT NULL, canon_status TEXT NOT NULL, supersedes_id TEXT
);
CREATE TABLE IF NOT EXISTS director_signals (
    id TEXT PRIMARY KEY, data TEXT NOT NULL, consumed INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS retcon_requests (
    id TEXT PRIMARY KEY, data TEXT NOT NULL, status TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS proposals (
    id TEXT PRIMARY KEY, data TEXT NOT NULL, status TEXT NOT NULL, proposing_agent TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS autonomy_state (
    id TEXT PRIMARY KEY, data TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS projector_state (
    id TEXT PRIMARY KEY, last_sequence INTEGER NOT NULL
);
"""
```

2. Extend `_reset_state`'s table tuple:
```python
    async def _reset_state(self) -> None:
        """Testing/rebuild helper: forget position and clear projections."""
        for table in ("chapters", "world_entries", "characters", "director_signals",
                      "retcon_requests", "proposals", "autonomy_state"):
            await self._conn.execute(f"DELETE FROM {table}")
        await self._set_last_sequence(0)
```

3. Extend `_apply` (append these branches before the final `await self._conn.commit()`):
```python
        elif t == EventType.PROPOSAL_CREATED:
            await self._conn.execute(
                "INSERT OR REPLACE INTO proposals (id, data, status, proposing_agent) VALUES (?,?,?,?)",
                (p["id"], data, p.get("status", "open"), p["proposing_agent"]),
            )
        elif t == EventType.PROPOSAL_APPROVED or t == EventType.PROPOSAL_REJECTED:
            new_status = "approved" if t == EventType.PROPOSAL_APPROVED else "rejected"
            await self._conn.execute(
                "UPDATE proposals SET status=? WHERE id=?", (new_status, p["id"])
            )
        elif t == EventType.AUTONOMY_CHANGED:
            await self._conn.execute(
                "INSERT OR REPLACE INTO autonomy_state (id, data) VALUES ('singleton', ?)", (data,)
            )
```
(These are additional `elif` branches on the existing chain in `_apply`; the trailing `await self._conn.commit()` after the chain is unchanged and covers them.)

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/canon/test_projector.py -v`
Expected: PASS (all prior + 5 new).

- [ ] **Step 5: Commit**

```bash
git add novelizer/canon/projector.py tests/canon/test_projector.py
git commit -m "feat: project proposal.* and autonomy.changed into proposals/autonomy_state tables"
```

---

### Task 4: ReadStore — proposals and autonomy state reads

**Files:**
- Modify: `novelizer/canon/read_store.py`
- Test: `tests/canon/test_read_store.py` (extend)

**Interfaces:**
- Produces:
  - `async def list_proposals(self, status: Optional[str] = None) -> list[Proposal]`
  - `async def get_proposal(self, proposal_id: str) -> Optional[Proposal]`
  - `async def get_autonomy_state(self) -> AutonomyState` — returns `AutonomyState()` (full-auto default) if the `autonomy_state` table is empty.

- [ ] **Step 1: Write the failing test**

Append to `tests/canon/test_read_store.py` (reuse that file's existing `wired`/fixture pattern — inspect the file first for the exact fixture name; assume `stack` yielding `(events, proj, read)` as used elsewhere in this suite, otherwise adapt to the local fixture):
```python
from novelizer.canon.autonomy import Proposal, AutonomyState, AutonomyLevel
from novelizer.canon.events import EventType


async def test_list_and_get_proposals(stack):
    events, proj, read = stack
    prop = Proposal(proposing_agent="author", target_event_type="chapter.created",
                     target_aggregate_id="c1", payload={"title": "One"})
    await events.append(EventType.PROPOSAL_CREATED, prop.id, prop)
    await proj.catch_up()
    open_props = await read.list_proposals(status="open")
    assert len(open_props) == 1 and open_props[0].proposing_agent == "author"
    fetched = await read.get_proposal(prop.id)
    assert fetched is not None and fetched.target_aggregate_id == "c1"
    assert await read.get_proposal("missing") is None


async def test_get_autonomy_state_defaults_to_full_auto(stack):
    _, _, read = stack
    st = await read.get_autonomy_state()
    assert st.global_level == AutonomyLevel.full_auto
    assert st.overrides == {}


async def test_get_autonomy_state_reflects_latest_change(stack):
    events, proj, read = stack
    await events.append(
        EventType.AUTONOMY_CHANGED, "singleton",
        AutonomyState(global_level=AutonomyLevel.gated_all, overrides={"author": AutonomyLevel.full_auto}),
    )
    await proj.catch_up()
    st = await read.get_autonomy_state()
    assert st.global_level == AutonomyLevel.gated_all
    assert st.overrides["author"] == AutonomyLevel.full_auto
```

If `tests/canon/test_read_store.py`'s existing fixture is not named `stack` or does not yield `(events, proj, read)`, adapt these three tests' fixture argument and unpacking to match the file's established fixture exactly — do not introduce a second fixture.

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/canon/test_read_store.py -v`
Expected: FAIL (`AttributeError: 'ReadStore' object has no attribute 'list_proposals'`).

- [ ] **Step 3: Implement**

In `novelizer/canon/read_store.py`, add the import and methods:
```python
from novelizer.canon.autonomy import Proposal, AutonomyState
```
```python
    async def list_proposals(self, status: Optional[str] = None) -> list[Proposal]:
        if status:
            cur = await self._conn.execute(
                "SELECT data FROM proposals WHERE status=? ORDER BY rowid", (status,)
            )
        else:
            cur = await self._conn.execute("SELECT data FROM proposals ORDER BY rowid")
        return [Proposal.model_validate_json(r[0]) for r in await cur.fetchall()]

    async def get_proposal(self, proposal_id: str) -> Optional[Proposal]:
        cur = await self._conn.execute("SELECT data FROM proposals WHERE id=?", (proposal_id,))
        row = await cur.fetchone()
        return Proposal.model_validate_json(row[0]) if row else None

    async def get_autonomy_state(self) -> AutonomyState:
        cur = await self._conn.execute("SELECT data FROM autonomy_state WHERE id='singleton'")
        row = await cur.fetchone()
        return AutonomyState.model_validate_json(row[0]) if row else AutonomyState()
```

Note: `list_proposals`/`get_proposal` return the *original* `Proposal` payload as stored at `proposal.created` time (status field inside the JSON blob may lag the `proposals.status` column, since Task 3 only updates the column on approve/reject, not the stored JSON). This is acceptable for M1.3 — the source of truth for status is the `status` column, which these queries do not currently expose alongside the parsed model. Extend the query if a caller needs both; for this plan, `ProposalService` (Task 6) and the UI (Tasks 9-10) only need `list_proposals(status="open")` to find pending items and `get_proposal(id)` to fetch one by id, both satisfied here.

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/canon/test_read_store.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add novelizer/canon/read_store.py tests/canon/test_read_store.py
git commit -m "feat: add ReadStore.list_proposals/get_proposal/get_autonomy_state"
```

---

### Task 5: `AutonomyPolicy` — level → gated-event-type mapping

**Files:**
- Create: `novelizer/canon/policy.py`
- Test: `tests/canon/test_policy.py`

**Interfaces:**
- Produces: `class AutonomyPolicy` with `def __init__(self, read_store) -> None` and `async def is_gated(self, agent_name: str, event_type: str) -> bool`.
- Gating table (module-level, tested directly and via `is_gated`):
  ```python
  _RETCON_EVENTS = {EventType.WORLD_ENTRY_SUPERSEDED, EventType.RETCON_REQUEST_RESOLVED}
  _CANON_EVENTS = _RETCON_EVENTS | {
      EventType.WORLD_ENTRY_CREATED, EventType.CHARACTER_CREATED, EventType.CHARACTER_UPDATED,
      EventType.CHAPTER_CREATED, EventType.CHAPTER_STATUS_CHANGED,
  }
  _NEVER_GATED = {EventType.DIRECTOR_SIGNAL_CREATED, EventType.DIRECTOR_SIGNAL_CONSUMED}
  ```
  `AutonomyLevel.full_auto` gates nothing; `gated_retcons` gates `_RETCON_EVENTS`; `gated_canon` gates `_CANON_EVENTS`; `gated_all` gates everything except `_NEVER_GATED`. `_NEVER_GATED` overrides every level.

- [ ] **Step 1: Write the failing test**

Create `tests/canon/test_policy.py`:
```python
import pytest
from novelizer.canon.autonomy import AutonomyLevel, AutonomyState
from novelizer.canon.events import EventType
from novelizer.canon.policy import AutonomyPolicy


class FakeRead:
    def __init__(self, state: AutonomyState):
        self._state = state

    async def get_autonomy_state(self):
        return self._state


GATED_CASES = [
    # (level, event_type, expected_gated)
    (AutonomyLevel.full_auto, EventType.CHAPTER_CREATED, False),
    (AutonomyLevel.full_auto, EventType.RETCON_REQUEST_RESOLVED, False),
    (AutonomyLevel.gated_retcons, EventType.RETCON_REQUEST_RESOLVED, True),
    (AutonomyLevel.gated_retcons, EventType.WORLD_ENTRY_SUPERSEDED, True),
    (AutonomyLevel.gated_retcons, EventType.CHAPTER_CREATED, False),
    (AutonomyLevel.gated_retcons, EventType.WORLD_ENTRY_CREATED, False),
    (AutonomyLevel.gated_canon, EventType.WORLD_ENTRY_CREATED, True),
    (AutonomyLevel.gated_canon, EventType.CHARACTER_UPDATED, True),
    (AutonomyLevel.gated_canon, EventType.CHAPTER_CREATED, True),
    (AutonomyLevel.gated_canon, EventType.CHAPTER_STATUS_CHANGED, True),
    (AutonomyLevel.gated_canon, EventType.RETCON_REQUEST_RESOLVED, True),
    (AutonomyLevel.gated_all, EventType.CHAPTER_CREATED, True),
    (AutonomyLevel.gated_all, EventType.RETCON_REQUEST_CREATED, True),
]


@pytest.mark.parametrize("level,event_type,expected", GATED_CASES)
async def test_is_gated_by_level(level, event_type, expected):
    policy = AutonomyPolicy(FakeRead(AutonomyState(global_level=level)))
    assert await policy.is_gated("author", event_type) is expected


@pytest.mark.parametrize("level", list(AutonomyLevel))
async def test_director_signals_are_never_gated(level):
    policy = AutonomyPolicy(FakeRead(AutonomyState(global_level=level)))
    assert await policy.is_gated("author", EventType.DIRECTOR_SIGNAL_CREATED) is False
    assert await policy.is_gated("any_agent", EventType.DIRECTOR_SIGNAL_CONSUMED) is False


async def test_per_agent_override_takes_precedence():
    state = AutonomyState(global_level=AutonomyLevel.full_auto,
                           overrides={"retconner": AutonomyLevel.gated_all})
    policy = AutonomyPolicy(FakeRead(state))
    assert await policy.is_gated("retconner", EventType.RETCON_REQUEST_RESOLVED) is True
    assert await policy.is_gated("author", EventType.CHAPTER_CREATED) is False
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/canon/test_policy.py -v`
Expected: FAIL (`ModuleNotFoundError: No module named 'novelizer.canon.policy'`).

- [ ] **Step 3: Implement**

Create `novelizer/canon/policy.py`:
```python
from __future__ import annotations
from novelizer.canon.autonomy import AutonomyLevel
from novelizer.canon.events import EventType

_RETCON_EVENTS = {EventType.WORLD_ENTRY_SUPERSEDED, EventType.RETCON_REQUEST_RESOLVED}
_CANON_EVENTS = _RETCON_EVENTS | {
    EventType.WORLD_ENTRY_CREATED,
    EventType.CHARACTER_CREATED,
    EventType.CHARACTER_UPDATED,
    EventType.CHAPTER_CREATED,
    EventType.CHAPTER_STATUS_CHANGED,
}
_NEVER_GATED = {EventType.DIRECTOR_SIGNAL_CREATED, EventType.DIRECTOR_SIGNAL_CONSUMED}

_GATED_SETS: dict[AutonomyLevel, set[str]] = {
    AutonomyLevel.full_auto: set(),
    AutonomyLevel.gated_retcons: _RETCON_EVENTS,
    AutonomyLevel.gated_canon: _CANON_EVENTS,
    # gated_all is resolved dynamically in is_gated: everything not in _NEVER_GATED.
}


class AutonomyPolicy:
    """Reads the live AutonomyState from canon and decides what an agent may commit directly."""

    def __init__(self, read_store) -> None:
        self._read = read_store

    async def is_gated(self, agent_name: str, event_type: str) -> bool:
        if event_type in _NEVER_GATED:
            return False
        state = await self._read.get_autonomy_state()
        level = state.level_for(agent_name)
        if level == AutonomyLevel.gated_all:
            return True
        return event_type in _GATED_SETS.get(level, set())
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/canon/test_policy.py -v`
Expected: PASS (all parametrized cases green).

- [ ] **Step 5: Commit**

```bash
git add novelizer/canon/policy.py tests/canon/test_policy.py
git commit -m "feat: add AutonomyPolicy mapping autonomy levels to gated event types"
```

---

### Task 6: `GatingCommitter`

**Files:**
- Modify: `novelizer/canon/committer.py`
- Test: `tests/canon/test_committer.py` (extend)

**Interfaces:**
- Produces: `class GatingCommitter` in `committer.py`, same public shape as `Committer`:
  `def __init__(self, event_store, policy) -> None`; `async def commit(self, agent_name: str, event_type: str, aggregate_id: str, payload: BaseModel) -> None`.
  - If `await self._policy.is_gated(agent_name, event_type)`: append `EventType.PROPOSAL_CREATED` with a `Proposal(proposing_agent=agent_name, target_event_type=event_type, target_aggregate_id=aggregate_id, payload=payload.model_dump(mode="json"))`.
  - Else: delegate to the same real-append behavior as `Committer` (`await self._events.append(event_type, aggregate_id, payload)`).

- [ ] **Step 1: Write the failing test**

Append to `tests/canon/test_committer.py` (reuse the file's existing `EventStore`/tempfile fixture pattern; if the file defines a fixture, use it — otherwise inline setup as below matches the sibling test files' style):
```python
import os
import tempfile
import pytest
from novelizer.canon.event_store import EventStore
from novelizer.canon.projector import Projector
from novelizer.canon.read_store import ReadStore
from novelizer.canon.events import EventType
from novelizer.canon.policy import AutonomyPolicy
from novelizer.canon.autonomy import AutonomyLevel, AutonomyState
from novelizer.canon.committer import GatingCommitter
from novelizer.store.models import Chapter


class AlwaysGate:
    async def is_gated(self, agent_name, event_type):
        return True


class NeverGate:
    async def is_gated(self, agent_name, event_type):
        return False


@pytest.fixture
async def gating_stack():
    fd, path = tempfile.mkstemp(suffix=".db"); os.close(fd)
    events = EventStore(path); await events.init()
    proj = Projector(events, path); await proj.init()
    read = ReadStore(path); await read.init()
    yield events, proj, read
    await read.close(); await proj.close(); await events.close(); os.unlink(path)


async def test_gating_committer_queues_proposal_when_gated(gating_stack):
    events, proj, read = gating_stack
    committer = GatingCommitter(events, AlwaysGate())
    ch = Chapter(id="c1", title="One", prose="p")
    await committer.commit("author", EventType.CHAPTER_CREATED, ch.id, ch)
    await proj.catch_up()
    assert await read.list_chapters() == []
    props = await read.list_proposals(status="open")
    assert len(props) == 1
    assert props[0].proposing_agent == "author"
    assert props[0].target_event_type == EventType.CHAPTER_CREATED
    assert props[0].target_aggregate_id == "c1"
    assert props[0].payload["title"] == "One"


async def test_gating_committer_commits_directly_when_not_gated(gating_stack):
    events, proj, read = gating_stack
    committer = GatingCommitter(events, NeverGate())
    ch = Chapter(id="c2", title="Two", prose="p")
    await committer.commit("author", EventType.CHAPTER_CREATED, ch.id, ch)
    await proj.catch_up()
    chapters = await read.list_chapters()
    assert len(chapters) == 1 and chapters[0].title == "Two"
    assert await read.list_proposals() == []


async def test_gating_committer_with_real_policy_gates_by_level(gating_stack):
    events, proj, read = gating_stack
    await events.append(EventType.AUTONOMY_CHANGED, "singleton",
                         AutonomyState(global_level=AutonomyLevel.gated_canon))
    await proj.catch_up()
    committer = GatingCommitter(events, AutonomyPolicy(read))
    ch = Chapter(id="c3", title="Three", prose="p")
    await committer.commit("author", EventType.CHAPTER_CREATED, ch.id, ch)
    await proj.catch_up()
    assert await read.list_chapters() == []
    assert len(await read.list_proposals(status="open")) == 1
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/canon/test_committer.py -v`
Expected: FAIL (`ImportError: cannot import name 'GatingCommitter'`).

- [ ] **Step 3: Implement**

Replace `novelizer/canon/committer.py` with:
```python
from __future__ import annotations
from pydantic import BaseModel
from novelizer.canon.event_store import EventStore
from novelizer.canon.events import EventType
from novelizer.canon.autonomy import Proposal


class Committer:
    """The single seam through which agents write canon.

    M1.1 appends the event directly (full-auto). GatingCommitter (below) is
    the M1.3 replacement that may append a proposal instead, keyed on
    ``agent_name`` and ``event_type`` — without any agent changing.
    """

    def __init__(self, event_store: EventStore) -> None:
        self._events = event_store

    async def commit(self, agent_name: str, event_type: str, aggregate_id: str, payload: BaseModel) -> None:
        await self._events.append(event_type, aggregate_id, payload)


class GatingCommitter:
    """Drop-in replacement for Committer that consults an AutonomyPolicy.

    Same public `commit` signature as Committer, so Runtime can swap the
    implementation with zero agent-code changes.
    """

    def __init__(self, event_store: EventStore, policy) -> None:
        self._events = event_store
        self._policy = policy

    async def commit(self, agent_name: str, event_type: str, aggregate_id: str, payload: BaseModel) -> None:
        if await self._policy.is_gated(agent_name, event_type):
            proposal = Proposal(
                proposing_agent=agent_name,
                target_event_type=event_type,
                target_aggregate_id=aggregate_id,
                payload=payload.model_dump(mode="json"),
            )
            await self._events.append(EventType.PROPOSAL_CREATED, proposal.id, proposal)
            return
        await self._events.append(event_type, aggregate_id, payload)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/canon/test_committer.py -v`
Expected: PASS (existing `Committer` tests + 3 new `GatingCommitter` tests).

- [ ] **Step 5: Commit**

```bash
git add novelizer/canon/committer.py tests/canon/test_committer.py
git commit -m "feat: add GatingCommitter — queues proposals for gated (agent, event_type) pairs"
```

---

### Task 7: `EventStore.append_raw` + `ProposalService`

**Files:**
- Modify: `novelizer/canon/event_store.py` (add `append_raw`)
- Create: `novelizer/canon/proposal_service.py`
- Test: `tests/canon/test_event_store.py` (extend), `tests/canon/test_proposal_service.py` (new)

**Design decision — re-appending a stored dict payload:** `EventStore.append` requires a pydantic `BaseModel` (it calls `payload.model_dump_json()`). A `Proposal`'s `payload` field is already a plain `dict` (JSON-round-tripped through `Proposal.payload: dict`), so approving it means appending that dict as the target event's payload — there is no `BaseModel` to hand back. Rather than force callers to reconstruct an untyped model, `EventStore` grows a small `append_raw(event_type, aggregate_id, payload: dict) -> StoredEvent` that skips the `model_dump_json()` step and serializes the dict directly with `json.dumps`. This keeps `EventStore.append`'s existing typed contract untouched (still `BaseModel` in, for every existing call site) and adds one clearly-named escape hatch for the one case that needs it — re-appending an already-JSON payload rescued from canon.

**Interfaces:**
- Produces:
  - `EventStore.append_raw(self, event_type: str, aggregate_id: str, payload: dict) -> StoredEvent`.
  - `class ProposalService`: `def __init__(self, event_store) -> None`; `async def approve(self, proposal: Proposal) -> None` — appends the target event via `append_raw(proposal.target_event_type, proposal.target_aggregate_id, proposal.payload)`, then appends `EventType.PROPOSAL_APPROVED` with `proposal.model_copy(update={"status": ProposalStatus.approved})`; `async def reject(self, proposal: Proposal) -> None` — appends `EventType.PROPOSAL_REJECTED` with `proposal.model_copy(update={"status": ProposalStatus.rejected})` only (no target event).

- [ ] **Step 1: Write the failing tests**

Append to `tests/canon/test_event_store.py`:
```python
async def test_append_raw_stores_dict_payload_without_a_model():
    fd, path = tempfile.mkstemp(suffix=".db"); os.close(fd)
    store = EventStore(path); await store.init()
    try:
        stored = await store.append_raw(EventType.CHAPTER_CREATED, "c1", {"id": "c1", "title": "One", "prose": "p"})
        assert stored.event_type == EventType.CHAPTER_CREATED
        assert stored.payload["title"] == "One"
        fetched = await store.events_since(0)
        assert fetched[0].payload["title"] == "One"
    finally:
        await store.close(); os.unlink(path)
```
(Add `import os, tempfile` at the top of the file if not already present, and `from novelizer.canon.events import EventType` if missing — match whatever the file already imports.)

Create `tests/canon/test_proposal_service.py`:
```python
import os
import tempfile
import pytest
from novelizer.canon.event_store import EventStore
from novelizer.canon.projector import Projector
from novelizer.canon.read_store import ReadStore
from novelizer.canon.events import EventType
from novelizer.canon.autonomy import Proposal, ProposalStatus
from novelizer.canon.proposal_service import ProposalService
from novelizer.store.models import Chapter


@pytest.fixture
async def stack():
    fd, path = tempfile.mkstemp(suffix=".db"); os.close(fd)
    events = EventStore(path); await events.init()
    proj = Projector(events, path); await proj.init()
    read = ReadStore(path); await read.init()
    yield events, proj, read
    await read.close(); await proj.close(); await events.close(); os.unlink(path)


async def test_approve_appends_target_event_and_marks_approved(stack):
    events, proj, read = stack
    ch = Chapter(id="c1", title="One", prose="p")
    proposal = Proposal(proposing_agent="author", target_event_type=EventType.CHAPTER_CREATED,
                         target_aggregate_id="c1", payload=ch.model_dump(mode="json"))
    await events.append(EventType.PROPOSAL_CREATED, proposal.id, proposal)
    await proj.catch_up()
    service = ProposalService(events)
    await service.approve(proposal)
    await proj.catch_up()
    chapters = await read.list_chapters()
    assert len(chapters) == 1 and chapters[0].title == "One"
    props = await read.list_proposals(status="approved")
    assert len(props) == 1 and props[0].id == proposal.id


async def test_reject_marks_rejected_without_target_event(stack):
    events, proj, read = stack
    proposal = Proposal(proposing_agent="editor", target_event_type=EventType.CHAPTER_STATUS_CHANGED,
                         target_aggregate_id="c1", payload={"id": "c1", "editorial_status": "reviewed"})
    await events.append(EventType.PROPOSAL_CREATED, proposal.id, proposal)
    await proj.catch_up()
    service = ProposalService(events)
    await service.reject(proposal)
    await proj.catch_up()
    assert await read.list_chapters() == []
    props = await read.list_proposals(status="rejected")
    assert len(props) == 1 and props[0].id == proposal.id
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/canon/test_event_store.py tests/canon/test_proposal_service.py -v`
Expected: FAIL (`AttributeError: 'EventStore' object has no attribute 'append_raw'`; `ModuleNotFoundError: novelizer.canon.proposal_service`).

- [ ] **Step 3: Implement**

In `novelizer/canon/event_store.py`, add below `append`:
```python
    async def append_raw(self, event_type: str, aggregate_id: str, payload: dict) -> StoredEvent:
        """Append a payload that is already a plain dict (e.g. rescued from a Proposal)."""
        eid = str(uuid.uuid4())
        created_at = datetime.now(timezone.utc).isoformat()
        payload_json = json.dumps(payload)
        cur = await self._conn.execute(
            "INSERT INTO events (id, event_type, aggregate_id, payload, created_at) VALUES (?,?,?,?,?)",
            (eid, event_type, aggregate_id, payload_json, created_at),
        )
        await self._conn.commit()
        return StoredEvent(
            sequence=cur.lastrowid, id=eid, event_type=event_type,
            aggregate_id=aggregate_id, payload=json.loads(payload_json), created_at=created_at,
        )
```

Create `novelizer/canon/proposal_service.py`:
```python
from __future__ import annotations
from novelizer.canon.event_store import EventStore
from novelizer.canon.events import EventType
from novelizer.canon.autonomy import Proposal, ProposalStatus


class ProposalService:
    """Turns an open Proposal into either its real target event + proposal.approved,
    or a proposal.rejected — the only two ways a proposal leaves the 'open' state.
    """

    def __init__(self, event_store: EventStore) -> None:
        self._events = event_store

    async def approve(self, proposal: Proposal) -> None:
        await self._events.append_raw(proposal.target_event_type, proposal.target_aggregate_id, proposal.payload)
        approved = proposal.model_copy(update={"status": ProposalStatus.approved})
        await self._events.append(EventType.PROPOSAL_APPROVED, proposal.id, approved)

    async def reject(self, proposal: Proposal) -> None:
        rejected = proposal.model_copy(update={"status": ProposalStatus.rejected})
        await self._events.append(EventType.PROPOSAL_REJECTED, proposal.id, rejected)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/canon/test_event_store.py tests/canon/test_proposal_service.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add novelizer/canon/event_store.py novelizer/canon/proposal_service.py \
        tests/canon/test_event_store.py tests/canon/test_proposal_service.py
git commit -m "feat: add EventStore.append_raw and ProposalService.approve/reject"
```

---

### Task 8: Command layer — `autonomy` / `approve` / `reject`

**Files:**
- Modify: `novelizer/director/commands.py`
- Modify: `novelizer/director/cli.py` (add `autonomy`, `proposals`, `approve`, `reject` CLI commands)
- Test: `tests/director/test_commands.py` (extend), `tests/director/test_cli.py` (extend)

**Interfaces:**
- Produces in `commands.py`:
  - `async def autonomy(events, level: str, agent: str | None = None) -> None` — validates `level` against `AutonomyLevel`, reads the CURRENT state via a passed-in `read_store` is avoided (keep `autonomy()` symmetrical with `seed`/`focus`, which only take `events`) — instead `autonomy` takes the full desired `AutonomyState` shape as arguments and appends `AUTONOMY_CHANGED` directly: `async def autonomy(events, level: str, agent: str | None = None) -> None` builds `AutonomyState(global_level=AutonomyLevel(level))` when `agent is None`, else raises — **per-agent overrides need the existing state to merge into**, so `dispatch` (below) is the layer that reads current state and merges; `autonomy()` itself accepts an already-resolved `AutonomyState` to keep `commands.py` handler functions read-free and consistent with `seed`/`focus`/`pause`/`resume`. Concretely:
    - `async def autonomy(events, state: "AutonomyState") -> None` — appends `EventType.AUTONOMY_CHANGED` with `state`.
  - `async def approve(events, read, proposal_id: str) -> str` — looks up the proposal via `read.get_proposal`, calls `ProposalService(events).approve(proposal)` if found and open, returns a human-readable result (or an error string if not found / not open).
  - `async def reject(events, read, proposal_id: str) -> str` — symmetric, calls `.reject`.
  - `dispatch(runtime, line)` extended to parse:
    - `autonomy <level> [agent]` — reads `runtime.read.get_autonomy_state()`, builds the merged next state (sets `global_level` if no agent given, else sets/overwrites `overrides[agent]`), calls `commands.autonomy(runtime.events, next_state)`.
    - `approve <id>` / `reject <id>` — call `commands.approve`/`commands.reject(runtime.events, runtime.read, id)`.
  - `dispatch` now requires `runtime` to expose `.read` in addition to `.events`/`.scheduler` for these three verbs; the `seed`/`focus`/`pause`/`resume` verbs are unchanged and still only touch `.events`/`.scheduler`.

- [ ] **Step 1: Write the failing test**

Append to `tests/director/test_commands.py` (reusing that file's existing `stack` fixture and `FakeRuntime`/`FakeScheduler` — extend `FakeRuntime` to also carry `.read`):
```python
from novelizer.canon.autonomy import AutonomyLevel, AutonomyState, Proposal
from novelizer.store.models import Chapter


async def test_autonomy_appends_global_level_change(stack):
    events, proj, read = stack
    await commands.autonomy(events, AutonomyState(global_level=AutonomyLevel.gated_canon))
    await proj.catch_up()
    st = await read.get_autonomy_state()
    assert st.global_level == AutonomyLevel.gated_canon


async def test_approve_and_reject_via_command_layer(stack):
    events, proj, read = stack
    ch = Chapter(id="c1", title="One", prose="p")
    proposal = Proposal(proposing_agent="author", target_event_type="chapter.created",
                         target_aggregate_id="c1", payload=ch.model_dump(mode="json"))
    from novelizer.canon.events import EventType
    await events.append(EventType.PROPOSAL_CREATED, proposal.id, proposal)
    await proj.catch_up()
    result = await commands.approve(events, read, proposal.id)
    await proj.catch_up()
    assert "approved" in result.lower()
    assert len(await read.list_chapters()) == 1

    proposal2 = Proposal(proposing_agent="editor", target_event_type="chapter.status_changed",
                          target_aggregate_id="c1", payload={"id": "c1", "editorial_status": "reviewed"})
    await events.append(EventType.PROPOSAL_CREATED, proposal2.id, proposal2)
    await proj.catch_up()
    result2 = await commands.reject(events, read, proposal2.id)
    assert "rejected" in result2.lower()

    assert "not found" in (await commands.approve(events, read, "missing-id")).lower()


async def test_dispatch_routes_autonomy_and_approve_reject(stack):
    events, proj, read = stack
    sched = FakeScheduler()
    rt = FakeRuntime(events, sched)
    rt.read = read
    result = await commands.dispatch(rt, "autonomy gated_canon")
    await proj.catch_up()
    assert "gated_canon" in result
    st = await read.get_autonomy_state()
    assert st.global_level == AutonomyLevel.gated_canon

    result_agent = await commands.dispatch(rt, "autonomy full_auto retconner")
    await proj.catch_up()
    st2 = await read.get_autonomy_state()
    assert st2.global_level == AutonomyLevel.gated_canon
    assert st2.overrides["retconner"] == AutonomyLevel.full_auto
    assert "retconner" in result_agent

    ch = Chapter(id="c9", title="Nine", prose="p")
    from novelizer.canon.events import EventType
    proposal = Proposal(proposing_agent="author", target_event_type="chapter.created",
                         target_aggregate_id="c9", payload=ch.model_dump(mode="json"))
    await events.append(EventType.PROPOSAL_CREATED, proposal.id, proposal)
    await proj.catch_up()
    result3 = await commands.dispatch(rt, f"approve {proposal.id}")
    assert "approved" in result3.lower()
```

Update the existing `FakeRuntime` in that file to accept/store `.read` (add `self.read = None` in `__init__` or set it as an attribute after construction, matching whichever is less invasive given the file's current fixture wiring).

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/director/test_commands.py -v`
Expected: FAIL (`AttributeError: module 'novelizer.director.commands' has no attribute 'autonomy'`).

- [ ] **Step 3: Implement**

In `novelizer/director/commands.py`:
```python
from __future__ import annotations
from novelizer.canon.events import EventType
from novelizer.canon.autonomy import AutonomyLevel, AutonomyState
from novelizer.canon.proposal_service import ProposalService
from novelizer.store.models import DirectorSignal, SignalKind


async def seed(events, text: str) -> None:
    sig = DirectorSignal(kind=SignalKind.seed, body=text)
    await events.append(EventType.DIRECTOR_SIGNAL_CREATED, sig.id, sig)


async def focus(events, entity: str) -> None:
    sig = DirectorSignal(kind=SignalKind.focus, body=entity)
    await events.append(EventType.DIRECTOR_SIGNAL_CREATED, sig.id, sig)


def pause(scheduler, agent_name: str) -> None:
    scheduler.pause_agent(agent_name)


def resume(scheduler, agent_name: str) -> None:
    scheduler.resume_agent(agent_name)


async def autonomy(events, state: AutonomyState) -> None:
    await events.append(EventType.AUTONOMY_CHANGED, "singleton", state)


async def approve(events, read, proposal_id: str) -> str:
    proposal = await read.get_proposal(proposal_id)
    if proposal is None:
        return f"Proposal not found: {proposal_id}"
    if proposal.status != proposal.status.open:
        return f"Proposal {proposal_id} is already {proposal.status.value}."
    await ProposalService(events).approve(proposal)
    return f"Approved proposal {proposal_id} ({proposal.target_event_type})"


async def reject(events, read, proposal_id: str) -> str:
    proposal = await read.get_proposal(proposal_id)
    if proposal is None:
        return f"Proposal not found: {proposal_id}"
    if proposal.status != proposal.status.open:
        return f"Proposal {proposal_id} is already {proposal.status.value}."
    await ProposalService(events).reject(proposal)
    return f"Rejected proposal {proposal_id} ({proposal.target_event_type})"


async def dispatch(runtime, line: str) -> str:
    parts = line.strip().split(maxsplit=2)
    if not parts:
        return "Empty command."
    cmd = parts[0].lower()
    rest = parts[1:]
    if cmd == "seed" and rest:
        text = line.strip().split(maxsplit=1)[1]
        await seed(runtime.events, text)
        return f"Seed injected: {text}"
    if cmd == "focus" and rest:
        text = line.strip().split(maxsplit=1)[1]
        await focus(runtime.events, text)
        return f"Focus set: {text}"
    if cmd == "pause" and rest:
        pause(runtime.scheduler, rest[0])
        return f"Paused: {rest[0]}"
    if cmd == "resume" and rest:
        resume(runtime.scheduler, rest[0])
        return f"Resumed: {rest[0]}"
    if cmd == "autonomy" and rest:
        level_str = rest[0]
        agent = rest[1] if len(rest) > 1 else None
        try:
            level = AutonomyLevel(level_str)
        except ValueError:
            return f"Unknown autonomy level: {level_str}"
        current = await runtime.read.get_autonomy_state()
        if agent:
            overrides = dict(current.overrides)
            overrides[agent] = level
            next_state = AutonomyState(global_level=current.global_level, overrides=overrides)
            await autonomy(runtime.events, next_state)
            return f"Autonomy for {agent} set to {level.value}"
        next_state = AutonomyState(global_level=level, overrides=current.overrides)
        await autonomy(runtime.events, next_state)
        return f"Global autonomy set to {level.value}"
    if cmd == "approve" and rest:
        return await approve(runtime.events, runtime.read, rest[0])
    if cmd == "reject" and rest:
        return await reject(runtime.events, runtime.read, rest[0])
    return f"Unknown command: {line.strip()}"
```

Note: `parts = line.strip().split(maxsplit=2)` is used only to detect `rest` presence/first tokens; the `seed`/`focus` branches re-split with `maxsplit=1` on the original line to preserve multi-word text bodies exactly as the M1.2 implementation did.

In `novelizer/director/cli.py`, add three new commands (near `retcons`):
```python
@cli.command()
@click.argument("level")
@click.argument("agent", required=False)
@click.pass_context
def autonomy(ctx, level: str, agent: str | None):
    """Set the global autonomy level, or a per-agent override."""
    from novelizer.canon.autonomy import AutonomyLevel, AutonomyState

    async def _run(rt: Runtime):
        try:
            lvl = AutonomyLevel(level)
        except ValueError:
            console.print(f"[red]Unknown autonomy level:[/red] {level}")
            return
        current = await rt.read.get_autonomy_state()
        if agent:
            overrides = dict(current.overrides)
            overrides[agent] = lvl
            next_state = AutonomyState(global_level=current.global_level, overrides=overrides)
            await commands.autonomy(rt.events, next_state)
            console.print(f"[green]Autonomy for {agent} set to {lvl.value}[/green]")
        else:
            next_state = AutonomyState(global_level=lvl, overrides=current.overrides)
            await commands.autonomy(rt.events, next_state)
            console.print(f"[green]Global autonomy set to {lvl.value}[/green]")
    asyncio.run(_with_runtime(ctx.obj["settings"], _run))


@cli.command()
@click.pass_context
def proposals(ctx):
    """List pending (open) proposals."""
    async def _run(rt: Runtime):
        props = await rt.read.list_proposals(status="open")
        if not props:
            console.print("No pending proposals.")
            return
        table = Table(title="Pending Proposals")
        table.add_column("ID", style="dim", no_wrap=True)
        table.add_column("Agent")
        table.add_column("Target Event")
        for p in props:
            table.add_row(p.id[:8], p.proposing_agent, p.target_event_type)
        console.print(table)
    asyncio.run(_with_runtime(ctx.obj["settings"], _run))


@cli.command()
@click.argument("proposal_id")
@click.pass_context
def approve(ctx, proposal_id: str):
    """Approve a pending proposal — appends its target event + proposal.approved."""
    async def _run(rt: Runtime):
        result = await commands.approve(rt.events, rt.read, proposal_id)
        console.print(f"[green]{result}[/green]")
    asyncio.run(_with_runtime(ctx.obj["settings"], _run))


@cli.command()
@click.argument("proposal_id")
@click.pass_context
def reject(ctx, proposal_id: str):
    """Reject a pending proposal — appends proposal.rejected."""
    async def _run(rt: Runtime):
        result = await commands.reject(rt.events, rt.read, proposal_id)
        console.print(f"[yellow]{result}[/yellow]")
    asyncio.run(_with_runtime(ctx.obj["settings"], _run))
```
Note: `proposal_id` here matches full ids typed by the user (e.g. copy-pasted from `proposals`' truncated table) — if a truncated 8-char id is passed, `get_proposal` will return `None` and the command reports "not found"; full-id matching only is in scope for M1.3 (no prefix matching), consistent with `chapters`/`read` elsewhere in the CLI which also display truncated ids but require full ids for lookups.

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/director/test_commands.py tests/director/test_cli.py -v`
Expected: PASS. Then `uv run pytest -q` green.

- [ ] **Step 5: Commit**

```bash
git add novelizer/director/commands.py novelizer/director/cli.py \
        tests/director/test_commands.py tests/director/test_cli.py
git commit -m "feat: add autonomy/approve/reject to the shared command layer and CLI"
```

---

### Task 9: Runtime — swap in `GatingCommitter` + expose `policy`/`proposals` service

**Files:**
- Modify: `novelizer/runtime.py`
- Test: `tests/test_runtime.py` (extend)

**Interfaces:**
- Produces: `Runtime.__init__` builds `self.committer` lazily now (needs `self.read`, which isn't initialized until `start()`), OR constructs `AutonomyPolicy`/`GatingCommitter` in `start()` after `self.read.init()`. Chosen approach: keep `self.committer` assigned in `start()` (not `__init__`), after `await self.read.init()`, so the policy can read from a live `ReadStore`. `__init__` keeps a placeholder `self.committer = None` for callers that inspect it before `start()`.
  - New attributes after `start()`: `self.policy: AutonomyPolicy`, `self.proposals: ProposalService`, `self.committer: GatingCommitter`.
  - All six agents continue to receive `self.committer` exactly as before (`self._runner_for(...)` construction lines in `start()` are otherwise untouched) — **zero agent code changes**.

- [ ] **Step 1: Write the failing test**

Append to `tests/test_runtime.py` (reuse the file's existing settings/runners fixtures — inspect for the established `_runners()`/similar helper and match it; the test below assumes a `Settings(db_path=path)` + a `runners={...}` dict of stub agents pattern consistent with the M1.2 TUI tests):
```python
import os
import tempfile
import pytest
from novelizer.config import Settings
from novelizer.runtime import Runtime
from novelizer.canon.committer import GatingCommitter
from novelizer.canon.policy import AutonomyPolicy
from novelizer.canon.proposal_service import ProposalService
from novelizer.canon.autonomy import AutonomyLevel, AutonomyState
from novelizer.canon.events import EventType


class _StubRunner:
    async def ainvoke(self, inputs):
        return {}


def _runners():
    return {name: _StubRunner() for name in
            ("author", "world_architect", "character_keeper", "editor", "continuity_checker", "retconner")}


async def test_runtime_wires_gating_committer_and_policy():
    fd, path = tempfile.mkstemp(suffix=".db"); os.close(fd)
    settings = Settings(db_path=path)
    rt = Runtime(settings, runners=_runners())
    try:
        await rt.start()
        assert isinstance(rt.committer, GatingCommitter)
        assert isinstance(rt.policy, AutonomyPolicy)
        assert isinstance(rt.proposals, ProposalService)
        assert rt.author._committer is rt.committer
        assert rt.world_architect._committer is rt.committer
    finally:
        await rt.close(); os.unlink(path)


async def test_runtime_gating_end_to_end_via_scheduler():
    """Set autonomy to gate chapters; author's output queues as a proposal, not a chapter.
    Approving it makes the chapter appear."""
    fd, path = tempfile.mkstemp(suffix=".db"); os.close(fd)
    settings = Settings(db_path=path)
    rt = Runtime(settings, runners=_runners())
    try:
        await rt.start()
        await rt.events.append(EventType.AUTONOMY_CHANGED, "singleton",
                                AutonomyState(global_level=AutonomyLevel.gated_canon))
        await rt.projector.catch_up()
        from novelizer.store.models import Chapter
        ch = Chapter(id="c1", title="Gated One", prose="p")
        await rt.committer.commit("author", EventType.CHAPTER_CREATED, ch.id, ch)
        await rt.projector.catch_up()
        assert await rt.read.list_chapters() == []
        pending = await rt.read.list_proposals(status="open")
        assert len(pending) == 1 and pending[0].payload["title"] == "Gated One"

        await rt.proposals.approve(pending[0])
        await rt.projector.catch_up()
        chapters = await rt.read.list_chapters()
        assert len(chapters) == 1 and chapters[0].title == "Gated One"
    finally:
        await rt.close(); os.unlink(path)
```

If `tests/test_runtime.py` already defines a `_runners()`/stub-runner helper, reuse it verbatim instead of redefining — check the file first and adapt names to match exactly (this plan's names are illustrative, matching the M1.2 plan's own TUI test conventions).

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_runtime.py -v`
Expected: FAIL (`assert isinstance(rt.committer, GatingCommitter)` — currently a plain `Committer`).

- [ ] **Step 3: Implement**

In `novelizer/runtime.py`:
```python
from __future__ import annotations
from typing import Optional
from novelizer.config import Settings
from novelizer.canon.event_store import EventStore
from novelizer.canon.projector import Projector
from novelizer.canon.read_store import ReadStore
from novelizer.canon.committer import GatingCommitter
from novelizer.canon.policy import AutonomyPolicy
from novelizer.canon.proposal_service import ProposalService
from novelizer.scheduler import Scheduler
from novelizer.agents.author import Author, build_author_runner
from novelizer.agents.world_architect import WorldArchitect, build_world_architect_runner
from novelizer.agents.character_keeper import CharacterKeeper, build_character_keeper_runner
from novelizer.agents.editor import Editor, build_editor_runner
from novelizer.agents.continuity_checker import ContinuityChecker, build_continuity_checker_runner
from novelizer.agents.retconner import Retconner, build_retconner_runner


class Runtime:
    def __init__(self, settings: Settings, runner=None, runners: Optional[dict] = None) -> None:
        self.settings = settings
        self.events = EventStore(settings.db_path)
        self.projector = Projector(self.events, settings.db_path)
        self.read = ReadStore(settings.db_path)
        self.policy: Optional[AutonomyPolicy] = None
        self.proposals: Optional[ProposalService] = None
        self.committer = None  # constructed in start(), once self.read is initialized
        self._runner = runner          # back-compat: author-only single runner
        self._runners = runners        # full per-agent override
        self.agents: list = []
        self.author = None
        self.world_architect = None
        self.character_keeper = None
        self.editor = None
        self.continuity_checker = None
        self.retconner = None
        self.scheduler: Optional[Scheduler] = None

    def _runner_for(self, name: str, builder):
        if self._runners is not None:
            return self._runners[name]
        if name == "author" and self._runner is not None:
            return self._runner
        return builder(self.settings)

    async def start(self) -> None:
        await self.events.init()
        await self.projector.init()
        await self.read.init()
        await self.projector.catch_up()
        self.policy = AutonomyPolicy(self.read)
        self.committer = GatingCommitter(self.events, self.policy)
        self.proposals = ProposalService(self.events)
        s = self.settings
        self.author = Author(self._runner_for("author", build_author_runner), self.read, self.committer, interval=s.author_interval)
        self.world_architect = WorldArchitect(self._runner_for("world_architect", build_world_architect_runner), self.read, self.committer, interval=s.default_agent_interval)
        self.character_keeper = CharacterKeeper(self._runner_for("character_keeper", build_character_keeper_runner), self.read, self.committer, interval=s.default_agent_interval)
        self.editor = Editor(self._runner_for("editor", build_editor_runner), self.read, self.committer, interval=s.default_agent_interval)
        self.continuity_checker = ContinuityChecker(self._runner_for("continuity_checker", build_continuity_checker_runner), self.read, self.committer, interval=s.continuity_interval)
        self.retconner = Retconner(self._runner_for("retconner", build_retconner_runner), self.read, self.committer, interval=s.default_agent_interval)
        self.agents = [
            self.world_architect, self.character_keeper, self.author,
            self.editor, self.continuity_checker, self.retconner,
        ]
        self.scheduler = Scheduler(self.agents, self.read)

    async def close(self) -> None:
        await self.read.close()
        await self.projector.close()
        await self.events.close()
```
Note the agent construction lines are byte-for-byte unchanged from M1.2 — only `self.committer`'s construction (now `GatingCommitter(self.events, self.policy)` instead of `Committer(self.events)`) and the two new attributes changed. This is the concrete evidence that the gating swap requires zero agent edits.

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/test_runtime.py -v`
Expected: PASS. Then `uv run pytest -q` — full suite green (existing `Committer`-based tests in `tests/canon/test_committer.py` still pass since `Committer` itself is untouched; only `Runtime` now wires the gating variant).

- [ ] **Step 5: Commit**

```bash
git add novelizer/runtime.py tests/test_runtime.py
git commit -m "feat: wire GatingCommitter/AutonomyPolicy/ProposalService into Runtime"
```

---

### Task 10: TUI — real autonomy status bar + approval queue pane

**Files:**
- Create: `novelizer/tui/widgets/proposals_model.py` (pure helper)
- Modify: `novelizer/tui/app.py` (status bar reads real state; approval-queue pane; wire `approve`/`reject` through the existing command input)
- Test: `tests/tui/test_proposals_model.py` (new), `tests/tui/test_app.py` or `tests/tui/test_app_layout.py` (extend — reuse whichever file already hosts pilot smoke tests per the M1.2 plan's Task 6/7 tests)

**Textual-version fragility note:** as established in the M1.2 plan, this codebase pins around Textual 5.3.0's quirks (`"colon"` is not a valid `BINDINGS` key name — `ctrl+k` is used instead; `Tree`/CSS-class toggling APIs are exercised via pilot but assertions call into pure/handler methods directly rather than simulating keystrokes, to stay deterministic across Textual point releases). This task follows the same discipline: the approval-queue pane is a `Static`/`Vertical` rendered from a pure `proposal_line(proposal) -> str` helper (tested with zero Textual imports), and its pilot test asserts on the rendered text content and on calling `_run_command`/`commands.dispatch` directly — never on simulated key sequences for `approve`/`reject`, since those are typed into the existing `#command` `Input` (no new keybinding is required for M1.3 to be functionally complete; a bonus keybinding is optional and noted below but not required for done-criterion).

**Interfaces:**
- Produces:
  - `proposals_model.py`: `def proposal_line(p: "Proposal") -> str` → e.g. `"◇ <id[:8]> <proposing_agent> → <target_event_type>"`. `async def pending_lines(read) -> list[str]` → `[proposal_line(p) for p in await read.list_proposals(status="open")]` (empty list if none).
  - `app.py`:
    - New `Static(id="proposals")` widget added to `Vertical#left` (below `AgentRoster#roster`), showing one line per pending proposal or `"no pending proposals"`.
    - New `_proposals_loop()` worker (mirrors `_roster_loop`), calling `pending_lines(self.runtime.read)` every 0.5s and updating `#proposals`, guarded via `_report_worker_error`.
    - `_status_line()` pure helper: `def _status_line(state: "AutonomyState") -> str` → `f"AUTONOMY: {state.global_level.value}   ·   :seed <text> · :focus <x> · :pause <agent> · :autonomy <level> [agent] · :approve/:reject <id>"`; overrides are summarized if present, e.g. append `f"  (overrides: {', '.join(f'{k}={v.value}' for k,v in state.overrides.items())})"` when `state.overrides` is non-empty.
    - New `_statusbar_loop()` worker: every 0.5s, `state = await self.runtime.read.get_autonomy_state()`; `self.query_one("#statusbar", Static).update(_status_line(state))`; guarded via `_report_worker_error`.
    - `compose()`'s hard-coded `Static(..., id="statusbar")` initial text becomes a placeholder (`"AUTONOMY: loading…"`) immediately overwritten by the first `_statusbar_loop` tick.
    - `on_mount` gains two more `run_worker(...)` calls for `_proposals_loop` and `_statusbar_loop`.

- [ ] **Step 1: Write the failing tests**

Create `tests/tui/test_proposals_model.py`:
```python
from novelizer.canon.autonomy import Proposal
from novelizer.tui.widgets.proposals_model import proposal_line, pending_lines


def test_proposal_line_renders_id_agent_and_target():
    p = Proposal(id="abcdef12-0000", proposing_agent="author",
                 target_event_type="chapter.created", target_aggregate_id="c1", payload={})
    line = proposal_line(p)
    assert "abcdef12" in line and "author" in line and "chapter.created" in line


class FakeRead:
    def __init__(self, proposals):
        self._proposals = proposals

    async def list_proposals(self, status=None):
        return [p for p in self._proposals if status is None or p.status.value == status]


async def test_pending_lines_lists_open_proposals():
    p1 = Proposal(proposing_agent="author", target_event_type="chapter.created",
                  target_aggregate_id="c1", payload={})
    p2 = Proposal(proposing_agent="editor", target_event_type="chapter.status_changed",
                  target_aggregate_id="c2", payload={})
    p2 = p2.model_copy(update={"status": p2.status.approved})
    lines = await pending_lines(FakeRead([p1, p2]))
    assert len(lines) == 1
    assert "author" in lines[0]


async def test_pending_lines_empty_when_none_open():
    lines = await pending_lines(FakeRead([]))
    assert lines == []
```

Append to `tests/tui/test_app.py` (or the pilot-test file established by the M1.2 plan — check for `test_app_layout.py`/`test_app.py` conventions and place alongside similar pilot tests):
```python
def test_status_line_shows_real_autonomy_level():
    from novelizer.tui.app import _status_line
    from novelizer.canon.autonomy import AutonomyLevel, AutonomyState
    line = _status_line(AutonomyState(global_level=AutonomyLevel.gated_canon))
    assert "gated_canon" in line
    assert "full-auto" not in line


def test_status_line_summarizes_overrides():
    from novelizer.tui.app import _status_line
    from novelizer.canon.autonomy import AutonomyLevel, AutonomyState
    line = _status_line(AutonomyState(global_level=AutonomyLevel.full_auto,
                                       overrides={"retconner": AutonomyLevel.gated_all}))
    assert "retconner=gated_all" in line
```

Add a pilot test (in whichever file hosts `test_room_toggle_hides_right_column` per M1.2's Task 7 — reuse its `_runners()` helper and settings pattern):
```python
@pytest.mark.asyncio
async def test_approval_queue_pane_shows_pending_proposal_and_approve_via_command():
    import os, tempfile
    from novelizer.config import Settings
    from novelizer.runtime import Runtime
    from novelizer.tui.app import NovelizerApp
    from novelizer.canon.events import EventType
    from novelizer.canon.autonomy import AutonomyLevel, AutonomyState
    from novelizer.store.models import Chapter

    fd, path = tempfile.mkstemp(suffix=".db"); os.close(fd)
    settings = Settings(db_path=path, projector_interval=0.1)
    rt = Runtime(settings, runners=_runners())
    await rt.start()
    try:
        await rt.events.append(EventType.AUTONOMY_CHANGED, "singleton",
                                AutonomyState(global_level=AutonomyLevel.gated_canon))
        ch = Chapter(id="c1", title="Pending One", prose="p")
        await rt.committer.commit("author", EventType.CHAPTER_CREATED, ch.id, ch)
        await rt.projector.catch_up()
        app = NovelizerApp(rt)
        async with app.run_test() as pilot:
            await pilot.pause()
            proposals_widget = app.query_one("#proposals", Static)
            pending = await rt.read.list_proposals(status="open")
            assert len(pending) == 1
            proposal_id = pending[0].id
            await app._run_command(f"approve {proposal_id}")
            await rt.projector.catch_up()
            chapters = await rt.read.list_chapters()
            assert len(chapters) == 1 and chapters[0].title == "Pending One"
    finally:
        await rt.close(); os.unlink(path)
```
(Import `Static` and reuse the `_runners()` stub-agent helper already defined earlier in the same test file per the M1.2 plan's Task 6/7 conventions.)

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/tui/test_proposals_model.py tests/tui/test_app.py -v`
Expected: FAIL (`ModuleNotFoundError: novelizer.tui.widgets.proposals_model`; `ImportError: cannot import name '_status_line'`; `#proposals` not found).

- [ ] **Step 3: Implement**

Create `novelizer/tui/widgets/proposals_model.py`:
```python
from __future__ import annotations


def proposal_line(p) -> str:
    return f"◇ {p.id[:8]}  {p.proposing_agent} → {p.target_event_type}"


async def pending_lines(read) -> list[str]:
    props = await read.list_proposals(status="open")
    return [proposal_line(p) for p in props]
```

In `novelizer/tui/app.py`:
1. Add imports:
```python
from novelizer.canon.autonomy import AutonomyState
from novelizer.tui.widgets.proposals_model import pending_lines
```
2. Add the pure status-line helper (module level, near `format_event`):
```python
def _status_line(state: AutonomyState) -> str:
    base = f"AUTONOMY: {state.global_level.value}   ·   :seed <text> · :focus <x> · :pause <agent> · :autonomy <level> [agent] · :approve/:reject <id>"
    if state.overrides:
        summary = ", ".join(f"{k}={v.value}" for k, v in state.overrides.items())
        base += f"  (overrides: {summary})"
    return base
```
3. In `compose()`, add the proposals pane and change the statusbar placeholder:
```python
            with Vertical(id="left"):
                yield RichLog(highlight=False, markup=False, id="feed")
                yield AgentRoster(id="roster")
                yield Static("no pending proposals", id="proposals")
            with Vertical(id="right"):
                yield StoryBrowser("Story", id="browser")
                yield Static("Select an item to view details.", id="detail")
        yield Static("AUTONOMY: loading…", id="statusbar")
```
4. In `on_mount`, add two workers:
```python
        self.run_worker(self._proposals_loop(), exclusive=False)
        self.run_worker(self._statusbar_loop(), exclusive=False)
```
5. Add the two loops:
```python
    async def _proposals_loop(self) -> None:
        while True:
            try:
                lines = await pending_lines(self.runtime.read)
                self.query_one("#proposals", Static).update("\n".join(lines) or "no pending proposals")
            except Exception as e:
                self._report_worker_error("proposals", e)
            await asyncio.sleep(0.5)

    async def _statusbar_loop(self) -> None:
        while True:
            try:
                state = await self.runtime.read.get_autonomy_state()
                self.query_one("#statusbar", Static).update(_status_line(state))
            except Exception as e:
                self._report_worker_error("statusbar", e)
            await asyncio.sleep(0.5)
```

In `novelizer/tui/app.tcss`, add (or confirm present) sizing for the new pane, e.g.:
```css
#proposals {
    height: auto;
    max-height: 6;
}
```
If `app.tcss` does not exist or uses a different selector convention (verify by reading the file before editing), match its existing per-widget block style rather than introducing a new pattern.

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/tui/test_proposals_model.py tests/tui/test_app.py -v`
Expected: PASS. Then `uv run pytest -q` green.

- [ ] **Step 5: Commit**

```bash
git add novelizer/tui/widgets/proposals_model.py novelizer/tui/app.py novelizer/tui/app.tcss \
        tests/tui/test_proposals_model.py tests/tui/test_app.py
git commit -m "feat: TUI shows real autonomy level and a live approval-queue pane"
```

---

### Task 11: Docs — mark M1.3 and M1 complete; README autonomy section

**Files:**
- Modify: `docs/submilestones/M1-the-room-assembles.md`
- Modify: `docs/MILESTONES.md`
- Modify: `README.md`

**Interfaces:** none (docs-only task — no code, no new tests; verified by re-running the full suite and a manual read-through).

- [ ] **Step 1: Update the sub-milestone table**

In `docs/submilestones/M1-the-room-assembles.md`, change the M1.3 row's `Status` cell from `⬜ not started` to `✅ complete`.

- [ ] **Step 2: Update the parent milestone doc**

Open `docs/MILESTONES.md`, find the M1 row/section, and mark it complete, following whatever status convention (`✅`/checkbox/date) that file already uses for M0 — read the file first to match its exact formatting rather than guessing.

- [ ] **Step 3: Add a README section**

Add a short "Autonomy & approvals" subsection to `README.md` (near any existing Mission Control section from M1.2), covering:
- The four autonomy levels (`full_auto`, `gated_retcons`, `gated_canon`, `gated_all`) and what each gates.
- How to change it: TUI command input (`autonomy <level> [agent]`) or CLI (`novelizer autonomy <level> [agent]`).
- How to see and act on pending proposals: TUI approval-queue pane + `approve <id>`/`reject <id>` via the command input, or CLI `novelizer proposals` / `novelizer approve <id>` / `novelizer reject <id>`.
- That `director_signal.*` events (seed/focus/pause/resume) are never gated, at any level.

- [ ] **Step 4: Verify the full suite is green**

Run: `uv run pytest -q`
Expected: all tests pass (no regressions from the docs-only edits).

- [ ] **Step 5: Commit**

```bash
git add docs/submilestones/M1-the-room-assembles.md docs/MILESTONES.md README.md
git commit -m "docs: mark M1.3 (and M1) complete; document autonomy & approvals"
```

---

## Self-Review

**Spec coverage (against the M1.3 row + the load-bearing design decision in `docs/submilestones/M1-the-room-assembles.md`, and the autonomy dial / approval queue sections of the vision doc):**
- Proposal + autonomy events/projections/reads → Tasks 2–4. ✓
- Gating `Committer` implementation swapped in without touching any agent → Tasks 6, 9 (Task 9's Runtime diff touches only the `self.committer =` line and two new attributes; the six agent-construction lines are unchanged, which the plan calls out explicitly as evidence). ✓
- `AutonomyPolicy` → Task 5, parametrized across all four levels plus the never-gated director-signal invariant. ✓
- Approve/reject service → Task 7 (`ProposalService`), with an explicit, justified design decision for the `EventStore.append` (BaseModel) vs. re-appending a stored dict payload tension (`append_raw`). ✓
- Dial + approval-queue UI → Task 10 (status bar from real `AutonomyState`; live proposals pane; approve/reject via the existing command input — no new keybinding required for the done-criterion). ✓
- CLI/TUI `autonomy`/`approve`/`reject` → Task 8 (shared `commands.py`, both CLI and TUI dispatch call it) + Task 10 (TUI wiring) + CLI subcommands in Task 8. ✓
- Milestone done-criterion — "gate an agent → output queues as proposal → approve/reject from TUI" — is proven end-to-end in Task 9's `test_runtime_gating_end_to_end_via_scheduler` (Runtime-level) and Task 10's pilot test (TUI-level, via `_run_command`). ✓
- Docs — M1.3 and M1 marked complete, README section — Task 11. ✓

**Placeholder scan:** no task contains "similar to Task N," `...`, `TODO`, or stubbed function bodies; every `- [ ] Step 3: Implement` shows the complete file/diff content required. The one deliberately-open design note (Task 4's remark about `proposals.status` column vs. stored-JSON status) is not a placeholder — it documents a real, bounded limitation and specifies exactly which two call sites are unaffected by it.

**Type/interface consistency:**
- `Committer.commit` / `GatingCommitter.commit` share the exact signature `(agent_name: str, event_type: str, aggregate_id: str, payload: BaseModel) -> None`, so `BaseAgent`/all six agents are unaffected (Task 6, Task 9).
- `AutonomyPolicy.is_gated(agent_name, event_type) -> bool` is the only thing `GatingCommitter` calls on its policy collaborator; the same interface is satisfiable by test fakes (`AlwaysGate`/`NeverGate` in Task 6) and the real `AutonomyPolicy` (Task 5), proving the seam is a real abstraction, not an accidental coupling.
- `ProposalService.approve/reject(proposal: Proposal) -> None` is used identically by `commands.approve/reject` (Task 8), `Runtime.proposals` (Task 9), and is exercised directly in Task 7's tests.
- `AutonomyState.level_for(agent_name) -> AutonomyLevel` is the single place per-agent-override resolution happens; `AutonomyPolicy` and `commands.dispatch`'s `autonomy` verb both call it (never reimplementing override precedence).
- `ReadStore.list_proposals/get_proposal/get_autonomy_state` signatures declared in Task 4 are consumed unchanged by Tasks 6, 8, 9, 10 — no call site invents a different shape.
- `Proposal.payload: dict` (JSON-compatible, produced via `payload.model_dump(mode="json")` in `GatingCommitter`) round-trips through `EventStore.append_raw` in `ProposalService.approve` without any agent-specific reconstruction step — verified in Task 7's and Task 9's tests using a real `Chapter` model.

**DDD/SOLID:**
- Bounded-context discipline preserved: `GatingCommitter`/`AutonomyPolicy`/`ProposalService` all live under `novelizer/canon/` (World Canon context) since they only append events and read projections; the Direction context (`commands.py`, CLI, TUI) never constructs events itself for approve/reject — it always goes through `ProposalService`/`commands.approve`/`reject`.
- Open/closed: `Runtime` extends behavior by swapping which `Committer`-shaped object it constructs; no agent, and no existing `Committer` consumer, needed modification. `Committer` itself is untouched and remains usable (e.g. in tests) alongside `GatingCommitter`.
- Single responsibility: `AutonomyPolicy` only decides gating; `ProposalService` only resolves proposals; `commands.py` only translates human/CLI/TUI input into calls on those two, plus the existing `EventStore`/`Scheduler`; the Projector only materializes what already happened.
- Event sourcing invariant maintained throughout: `GatingCommitter` still only calls `EventStore.append`/`append_raw` (no in-place row edits anywhere); `ProposalService.reject` deliberately leaves the target event unappended forever — rejection is expressed as the *absence* of a future append, recorded via `proposal.rejected`, never as a mutation of the original `proposal.created`.
