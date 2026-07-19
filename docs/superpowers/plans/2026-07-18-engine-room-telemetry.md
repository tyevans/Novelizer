# Engine Room & Telemetry Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make the machinery visible — a live "what is it doing right now" stream (Mission Control strip + Engine Room view) and a durable machinery trace, per `docs/superpowers/specs/2026-07-18-engine-room-telemetry-design.md`.

**Architecture:** Machinery facts are events in their own log: a second `EventStore` on `telemetry.db` records coarse-grained run/call/scheduler events (with full prompt payloads); an in-process `TelemetryBus` carries never-persisted token deltas and mirrors persisted events; a `run_id` ContextVar correlates telemetry with the domain events each run produces.

**Tech Stack:** Python 3.12, aiosqlite, pydantic, LangChain callbacks (`langchain_core.callbacks.AsyncCallbackHandler`), Textual, pytest + pytest-asyncio (`asyncio_mode = "auto"`), Hypothesis.

## Global Constraints

- Event sourcing: the **domain** log stays pure — no domain projection may ever read `telemetry.db`; deleting `telemetry.db` must affect nothing else (spec: "disposable by contract").
- Telemetry writes are fire-and-forget: a telemetry failure logs a warning and drops the event; it must never fail an agent run or the scheduler.
- Token deltas are bus-only, never persisted.
- Prompt inspection is **off by default**, toggled with `p`.
- Red/green TDD, black-box first; Hypothesis where invariants generalize.
- Run tests with `uv run pytest <path> -v`. Full suite: `uv run pytest` (live tests are excluded unless `-m live_llm`).
- Commit after every green task. All work happens in the current worktree branch `worktree-engine-room-telemetry-spec`.
- The scheduler is sequential (one agent in flight at a time); the live view models exactly one run.

## File Structure

New package `novelizer/telemetry/` (infrastructure, not a bounded context — no domain code may import from it except where this plan says):

| File | Responsibility |
|---|---|
| `novelizer/run_context.py` | ContextVars `current_run_id` / `current_agent_name` — tiny, dependency-free; imported by canon and telemetry both (keeps canon from importing telemetry) |
| `novelizer/telemetry/__init__.py` | Re-exports |
| `novelizer/telemetry/events.py` | `TelemetryEventType` constants + pydantic payloads + `TokenDelta` (bus-only) |
| `novelizer/telemetry/bus.py` | `TelemetryBus` — bounded-queue pub/sub, drop-oldest |
| `novelizer/telemetry/recorder.py` | `TelemetryRecorder` — persist (warn-and-drop) + mirror to bus; open-call/call-index tracking |
| `novelizer/telemetry/callbacks.py` | `TelemetryCallbackHandler` (LangChain) — llm.call\_\* events + token deltas |
| `novelizer/tui/widgets/engine_room_model.py` | Pure state machine + formatters: `LiveRunState`, `apply_bus_item`, `seed_state`, `strip_line`, `vitals_line`, `trace_line`, `trace_detail` |
| `novelizer/tui/widgets/activity_strip.py` | `ActivityStrip(Static)` one-liner |
| `novelizer/tui/widgets/engine_room.py` | `EngineRoom` view widget: stream + vitals + prompt pane + trace table + detail |

Modified: `novelizer/canon/event_store.py` (nullable `run_id` column + `events_for_run`), `novelizer/canon/events.py` (`StoredEvent.run_id`), `novelizer/canon/committer.py` (stamp ambient run_id), `novelizer/agents/base.py` (`run_once` template), all seven agent files (`run_once`→`_run` rename), `novelizer/agents/llm.py` (+callbacks/streaming), all seven `build_*_runner` builders (+`callbacks=`), `novelizer/scheduler.py` (picked/eligibility events, `next_ready_in`), `novelizer/runtime.py` (wiring), `novelizer/tui/app.py` + `app.tcss` (strip, view, workers, bindings).

Tests: new `tests/telemetry/` package; additions to `tests/canon/test_event_store.py`, `tests/canon/test_committer.py`, `tests/agents/test_base.py`, `tests/test_scheduler.py`, `tests/test_runtime.py`; new `tests/tui/test_engine_room_model.py`, `tests/tui/test_engine_room.py`; new live smoke `tests/agents/test_telemetry_live_llm.py`.

---

### Task 1: Telemetry event vocabulary

**Files:**
- Create: `novelizer/telemetry/__init__.py`, `novelizer/telemetry/events.py`
- Create: `tests/telemetry/__init__.py`, `tests/telemetry/test_events.py`

**Interfaces:**
- Consumes: nothing.
- Produces: `TelemetryEventType` string constants (`SCHEDULER_PICKED = "scheduler.picked"`, `SCHEDULER_ELIGIBILITY_CHANGED = "scheduler.eligibility_changed"`, `AGENT_RUN_STARTED = "agent.run_started"`, `AGENT_RUN_FINISHED = "agent.run_finished"`, `AGENT_RUN_FAILED = "agent.run_failed"`, `LLM_CALL_STARTED = "llm.call_started"`, `LLM_CALL_FINISHED = "llm.call_finished"`, `LLM_CALL_FAILED = "llm.call_failed"`); pydantic models `SchedulerPicked(agent_name)`, `SchedulerEligibilityChanged(agent_name, eligible: bool, reason: str)`, `AgentRunStarted(run_id, agent_name)`, `AgentRunFinished(run_id, agent_name, duration_s: float)`, `AgentRunFailed(run_id, agent_name, error_type, error_message, phase, duration_s)`, `LlmCallStarted(run_id, agent_name, call_index: int, model: str, prompt: str)`, `LlmCallFinished(run_id, agent_name, call_index, model, duration_s, output_tokens: int)`, `LlmCallFailed(run_id, agent_name, call_index, model, duration_s, error_type, error_message)`, `TokenDelta(run_id, agent_name, text)`.

- [ ] **Step 1: Write the failing test**

`tests/telemetry/__init__.py` — empty file.

```python
# tests/telemetry/test_events.py
from novelizer.telemetry.events import (
    TelemetryEventType, SchedulerPicked, SchedulerEligibilityChanged,
    AgentRunStarted, AgentRunFinished, AgentRunFailed,
    LlmCallStarted, LlmCallFinished, LlmCallFailed, TokenDelta,
)


def test_event_type_constants_are_dotted_strings():
    assert TelemetryEventType.AGENT_RUN_STARTED == "agent.run_started"
    assert TelemetryEventType.AGENT_RUN_FINISHED == "agent.run_finished"
    assert TelemetryEventType.AGENT_RUN_FAILED == "agent.run_failed"
    assert TelemetryEventType.LLM_CALL_STARTED == "llm.call_started"
    assert TelemetryEventType.LLM_CALL_FINISHED == "llm.call_finished"
    assert TelemetryEventType.LLM_CALL_FAILED == "llm.call_failed"
    assert TelemetryEventType.SCHEDULER_PICKED == "scheduler.picked"
    assert TelemetryEventType.SCHEDULER_ELIGIBILITY_CHANGED == "scheduler.eligibility_changed"


def test_payloads_round_trip_through_model_dump():
    started = LlmCallStarted(run_id="r1", agent_name="author", call_index=1,
                             model="m", prompt="[system]\nWrite.")
    assert LlmCallStarted(**started.model_dump()).prompt == "[system]\nWrite."
    failed = AgentRunFailed(run_id="r1", agent_name="author", error_type="ValueError",
                            error_message="boom", phase="llm_call", duration_s=1.5)
    assert AgentRunFailed(**failed.model_dump()).phase == "llm_call"


def test_token_delta_is_a_plain_model_not_a_telemetry_event_type():
    # TokenDelta is bus-only: it has no entry in TelemetryEventType, by design.
    d = TokenDelta(run_id="r1", agent_name="author", text="The ")
    assert d.text == "The "
    assert not hasattr(TelemetryEventType, "TOKEN_DELTA")
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/telemetry/test_events.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'novelizer.telemetry'`

- [ ] **Step 3: Write minimal implementation**

`novelizer/telemetry/__init__.py` — empty for now.

```python
# novelizer/telemetry/events.py
from __future__ import annotations
from pydantic import BaseModel


class TelemetryEventType:
    """Machinery event vocabulary. Persisted to telemetry.db (a separate
    EventStore), never to the domain log."""

    SCHEDULER_PICKED = "scheduler.picked"
    SCHEDULER_ELIGIBILITY_CHANGED = "scheduler.eligibility_changed"
    AGENT_RUN_STARTED = "agent.run_started"
    AGENT_RUN_FINISHED = "agent.run_finished"
    AGENT_RUN_FAILED = "agent.run_failed"
    LLM_CALL_STARTED = "llm.call_started"
    LLM_CALL_FINISHED = "llm.call_finished"
    LLM_CALL_FAILED = "llm.call_failed"


class SchedulerPicked(BaseModel):
    agent_name: str


class SchedulerEligibilityChanged(BaseModel):
    """Emitted on change of an agent's (eligible, reason) pair — never per tick."""

    agent_name: str
    eligible: bool
    reason: str  # "paused" | "interval not elapsed" | "readiness 0" | "ready"


class AgentRunStarted(BaseModel):
    run_id: str
    agent_name: str


class AgentRunFinished(BaseModel):
    run_id: str
    agent_name: str
    duration_s: float


class AgentRunFailed(BaseModel):
    run_id: str
    agent_name: str
    error_type: str
    error_message: str
    phase: str  # "llm_call" if the crash happened inside an open LLM call, else "agent"
    duration_s: float


class LlmCallStarted(BaseModel):
    """Carries the full rendered prompt — this is what powers prompt
    inspection in both the live view and the trace."""

    run_id: str
    agent_name: str
    call_index: int
    model: str
    prompt: str


class LlmCallFinished(BaseModel):
    run_id: str
    agent_name: str
    call_index: int
    model: str
    duration_s: float
    output_tokens: int


class LlmCallFailed(BaseModel):
    run_id: str
    agent_name: str
    call_index: int
    model: str
    duration_s: float
    error_type: str
    error_message: str


class TokenDelta(BaseModel):
    """One streamed chunk of model output. Bus-only: NEVER persisted (the
    finished chapter already lands in the domain log)."""

    run_id: str
    agent_name: str
    text: str
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/telemetry/test_events.py -v`
Expected: PASS (3 tests)

- [ ] **Step 5: Commit**

```bash
git add novelizer/telemetry tests/telemetry
git commit -m "feat(telemetry): machinery event vocabulary"
```

---

### Task 2: TelemetryBus

**Files:**
- Create: `novelizer/telemetry/bus.py`
- Test: `tests/telemetry/test_bus.py`

**Interfaces:**
- Consumes: nothing.
- Produces: `TelemetryBus(maxsize: int = 256)` with `subscribe() -> asyncio.Queue`, `unsubscribe(q: asyncio.Queue) -> None`, `publish(item) -> None` (synchronous, non-blocking; drops the oldest item in any full queue). Items are `StoredEvent` (mirrored persisted telemetry) or `TokenDelta`.

- [ ] **Step 1: Write the failing test**

```python
# tests/telemetry/test_bus.py
import asyncio
from novelizer.telemetry.bus import TelemetryBus


def test_subscriber_receives_published_items_in_order():
    bus = TelemetryBus()
    q = bus.subscribe()
    bus.publish("a")
    bus.publish("b")
    assert q.get_nowait() == "a"
    assert q.get_nowait() == "b"


def test_full_queue_drops_oldest_and_never_blocks_publisher():
    bus = TelemetryBus(maxsize=2)
    q = bus.subscribe()
    bus.publish("a")
    bus.publish("b")
    bus.publish("c")  # queue full: "a" is dropped, publish returns immediately
    assert q.get_nowait() == "b"
    assert q.get_nowait() == "c"


def test_slow_subscriber_does_not_affect_other_subscribers():
    bus = TelemetryBus(maxsize=1)
    slow = bus.subscribe()
    fast = bus.subscribe()
    bus.publish("a")
    bus.publish("b")  # slow's queue overflows (drop-oldest); fast also capped at 1
    assert slow.get_nowait() == "b"
    assert fast.get_nowait() == "b"


def test_unsubscribe_stops_delivery():
    bus = TelemetryBus()
    q = bus.subscribe()
    bus.unsubscribe(q)
    bus.publish("a")
    assert q.empty()


def test_unsubscribe_unknown_queue_is_a_noop():
    bus = TelemetryBus()
    bus.unsubscribe(asyncio.Queue())  # must not raise
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/telemetry/test_bus.py -v`
Expected: FAIL with `ModuleNotFoundError` (no `novelizer.telemetry.bus`)

- [ ] **Step 3: Write minimal implementation**

```python
# novelizer/telemetry/bus.py
from __future__ import annotations
import asyncio


class TelemetryBus:
    """In-process pub/sub for live machinery signals.

    High-frequency items (TokenDelta) and mirrored persisted telemetry
    events both flow through here so live consumers need one subscription.
    Bounded queues with drop-oldest: a slow or dead subscriber never blocks
    a publisher or other subscribers.
    """

    def __init__(self, maxsize: int = 256) -> None:
        self._maxsize = maxsize
        self._queues: list[asyncio.Queue] = []

    def subscribe(self) -> asyncio.Queue:
        q: asyncio.Queue = asyncio.Queue(maxsize=self._maxsize)
        self._queues.append(q)
        return q

    def unsubscribe(self, q: asyncio.Queue) -> None:
        if q in self._queues:
            self._queues.remove(q)

    def publish(self, item) -> None:
        for q in self._queues:
            if q.full():
                try:
                    q.get_nowait()
                except asyncio.QueueEmpty:
                    pass
            q.put_nowait(item)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/telemetry/test_bus.py -v`
Expected: PASS (5 tests)

- [ ] **Step 5: Commit**

```bash
git add novelizer/telemetry/bus.py tests/telemetry/test_bus.py
git commit -m "feat(telemetry): TelemetryBus — bounded drop-oldest pub/sub"
```

---

### Task 3: EventStore run_id column + events_for_run

**Files:**
- Modify: `novelizer/canon/events.py` (StoredEvent), `novelizer/canon/event_store.py`
- Test: `tests/canon/test_event_store.py` (append tests)

**Interfaces:**
- Consumes: existing `EventStore`.
- Produces: `StoredEvent.run_id: Optional[str] = None`; `EventStore.append(event_type, aggregate_id, payload, run_id: str | None = None)`; same optional `run_id` on `append_raw`; `EventStore.events_for_run(run_id: str) -> list[StoredEvent]`. `init()` migrates existing DBs by adding a nullable `run_id` column.

- [ ] **Step 1: Write the failing tests** (append to `tests/canon/test_event_store.py`)

```python
async def test_append_stores_and_returns_run_id(store):
    ev = await store.append(EventType.CHAPTER_CREATED, "c1",
                            Chapter(title="A", prose="a"), run_id="run-42")
    assert ev.run_id == "run-42"
    fetched = await store.events_since(0)
    assert fetched[0].run_id == "run-42"


async def test_append_without_run_id_defaults_to_none(store):
    ev = await store.append(EventType.CHAPTER_CREATED, "c1", Chapter(title="A", prose="a"))
    assert ev.run_id is None
    assert (await store.events_since(0))[0].run_id is None


async def test_events_for_run_returns_only_that_runs_events_in_order(store):
    await store.append(EventType.CHAPTER_CREATED, "c1", Chapter(title="A", prose="a"), run_id="r1")
    await store.append(EventType.CHAPTER_CREATED, "c2", Chapter(title="B", prose="b"), run_id="r2")
    await store.append(EventType.WORLD_ENTRY_CREATED, "w1", WorldEntry(title="W", body="w"), run_id="r1")
    got = await store.events_for_run("r1")
    assert [e.aggregate_id for e in got] == ["c1", "w1"]


async def test_init_migrates_a_pre_run_id_database():
    import aiosqlite
    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    # Build a DB with the pre-migration schema (no run_id column) and one row.
    old_schema = """
    CREATE TABLE IF NOT EXISTS events (
        sequence     INTEGER PRIMARY KEY AUTOINCREMENT,
        id           TEXT NOT NULL UNIQUE,
        event_type   TEXT NOT NULL,
        aggregate_id TEXT NOT NULL,
        payload      TEXT NOT NULL,
        created_at   TEXT NOT NULL
    );
    """
    conn = await aiosqlite.connect(path)
    await conn.executescript(old_schema)
    await conn.execute(
        "INSERT INTO events (id, event_type, aggregate_id, payload, created_at) VALUES (?,?,?,?,?)",
        ("old-1", EventType.CHAPTER_CREATED, "c1", '{"title": "Old", "prose": "p"}', "t"),
    )
    await conn.commit()
    await conn.close()
    s = EventStore(path)
    await s.init()  # must ALTER TABLE, not crash
    try:
        old = await s.events_since(0)
        assert old[0].run_id is None and old[0].payload["title"] == "Old"
        newer = await s.append(EventType.CHAPTER_CREATED, "c2",
                               Chapter(title="New", prose="p"), run_id="r9")
        assert newer.run_id == "r9"
    finally:
        await s.close()
        os.unlink(path)
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/canon/test_event_store.py -v`
Expected: the four new tests FAIL (`TypeError: append() got an unexpected keyword argument 'run_id'`, `AttributeError: events_for_run`); existing tests still PASS.

- [ ] **Step 3: Implement**

In `novelizer/canon/events.py`, add to `StoredEvent`:

```python
class StoredEvent(BaseModel):
    sequence: int
    id: str
    event_type: str
    aggregate_id: str
    payload: dict[str, Any]
    created_at: str
    run_id: str | None = None
```

In `novelizer/canon/event_store.py`, replace `_COLS` and `_row_to_event`, extend `init`, `_insert`, `append`, `append_raw`, and add `events_for_run`:

```python
_COLS = "sequence, id, event_type, aggregate_id, payload, created_at, run_id"


def _row_to_event(row) -> StoredEvent:
    return StoredEvent(
        sequence=row[0], id=row[1], event_type=row[2],
        aggregate_id=row[3], payload=json.loads(row[4]), created_at=row[5],
        run_id=row[6],
    )
```

```python
    async def init(self) -> None:
        self._conn = await aiosqlite.connect(self._path)
        await self._conn.execute("PRAGMA journal_mode=WAL")
        await self._conn.executescript(_CREATE)
        # Additive migration: pre-telemetry DBs lack run_id; existing rows stay NULL.
        cur = await self._conn.execute("PRAGMA table_info(events)")
        cols = [r[1] for r in await cur.fetchall()]
        if "run_id" not in cols:
            await self._conn.execute("ALTER TABLE events ADD COLUMN run_id TEXT")
        await self._conn.commit()

    async def _insert(self, event_type: str, aggregate_id: str, payload_json: str,
                      run_id: Optional[str] = None) -> StoredEvent:
        eid = str(uuid.uuid4())
        created_at = datetime.now(timezone.utc).isoformat()
        cur = await self._conn.execute(
            "INSERT INTO events (id, event_type, aggregate_id, payload, created_at, run_id) VALUES (?,?,?,?,?,?)",
            (eid, event_type, aggregate_id, payload_json, created_at, run_id),
        )
        await self._conn.commit()
        return StoredEvent(
            sequence=cur.lastrowid, id=eid, event_type=event_type,
            aggregate_id=aggregate_id, payload=json.loads(payload_json), created_at=created_at,
            run_id=run_id,
        )

    async def append(self, event_type: str, aggregate_id: str, payload: BaseModel,
                     run_id: Optional[str] = None) -> StoredEvent:
        return await self._insert(event_type, aggregate_id, payload.model_dump_json(), run_id)

    async def append_raw(self, event_type: str, aggregate_id: str, payload: dict,
                         run_id: Optional[str] = None) -> StoredEvent:
        """Append a payload that is already a plain dict (e.g. rescued from a Proposal)."""
        return await self._insert(event_type, aggregate_id, json.dumps(payload), run_id)

    async def events_for_run(self, run_id: str) -> list[StoredEvent]:
        cur = await self._conn.execute(
            f"SELECT {_COLS} FROM events WHERE run_id = ? ORDER BY sequence", (run_id,)
        )
        return [_row_to_event(r) for r in await cur.fetchall()]
```

- [ ] **Step 4: Run the full canon suite to verify green**

Run: `uv run pytest tests/canon -v`
Expected: PASS (all — projector/read-store code never selects `*`, but this run proves it)

- [ ] **Step 5: Commit**

```bash
git add novelizer/canon/events.py novelizer/canon/event_store.py tests/canon/test_event_store.py
git commit -m "feat(canon): nullable run_id envelope on events + events_for_run query"
```

---

### Task 4: Run-context ContextVars + Committer stamping

**Files:**
- Create: `novelizer/run_context.py`
- Modify: `novelizer/canon/committer.py`
- Test: `tests/canon/test_committer.py` (additions)

**Interfaces:**
- Consumes: Task 3's `append(..., run_id=)`.
- Produces: `novelizer.run_context.current_run_id: ContextVar[str | None]` and `current_agent_name: ContextVar[str]`; both `Committer.commit` and `GatingCommitter.commit` stamp `current_run_id.get()` into every append (including the gated `PROPOSAL_CREATED` path). Public signatures unchanged.

- [ ] **Step 1: Write the failing tests** (append to `tests/canon/test_committer.py`; reuse that file's existing fixtures/imports — it already constructs an `EventStore` and committers; add these imports at top if missing: `from novelizer.run_context import current_run_id`)

```python
async def test_commit_stamps_ambient_run_id():
    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    events = EventStore(path)
    await events.init()
    try:
        token = current_run_id.set("run-7")
        try:
            await Committer(events).commit(
                "author", EventType.CHAPTER_CREATED, "c1", Chapter(title="A", prose="a"))
        finally:
            current_run_id.reset(token)
        stored = (await events.events_since(0))[0]
        assert stored.run_id == "run-7"
    finally:
        await events.close()
        os.unlink(path)


async def test_commit_without_ambient_run_id_stores_none():
    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    events = EventStore(path)
    await events.init()
    try:
        await Committer(events).commit(
            "author", EventType.CHAPTER_CREATED, "c1", Chapter(title="A", prose="a"))
        assert (await events.events_since(0))[0].run_id is None
    finally:
        await events.close()
        os.unlink(path)


async def test_gated_proposal_is_also_stamped_with_run_id():
    class GateAll:
        async def is_gated(self, agent_name, event_type):
            return True

    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    events = EventStore(path)
    await events.init()
    try:
        token = current_run_id.set("run-8")
        try:
            await GatingCommitter(events, GateAll()).commit(
                "author", EventType.CHAPTER_CREATED, "c1", Chapter(title="A", prose="a"))
        finally:
            current_run_id.reset(token)
        stored = (await events.events_since(0))[0]
        assert stored.event_type == EventType.PROPOSAL_CREATED
        assert stored.run_id == "run-8"
    finally:
        await events.close()
        os.unlink(path)
```

(If `tests/canon/test_committer.py` lacks `tempfile`/`os`/`Chapter` imports, add them to match `tests/canon/test_event_store.py`'s header.)

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/canon/test_committer.py -v`
Expected: new tests FAIL (`ModuleNotFoundError: novelizer.run_context`, then `assert None == "run-7"`).

- [ ] **Step 3: Implement**

```python
# novelizer/run_context.py
"""Ambient identity of the agent run currently executing.

Deliberately dependency-free: canon (Committer) and telemetry both read
these, and canon must not import the telemetry package.
"""
from __future__ import annotations
from contextvars import ContextVar

current_run_id: ContextVar[str | None] = ContextVar("current_run_id", default=None)
current_agent_name: ContextVar[str] = ContextVar("current_agent_name", default="")
```

In `novelizer/canon/committer.py` add `from novelizer.run_context import current_run_id` and change the three appends:

```python
class Committer:
    # ... docstring/init unchanged ...
    async def commit(self, agent_name: str, event_type: str, aggregate_id: str, payload: BaseModel) -> None:
        await self._events.append(event_type, aggregate_id, payload, run_id=current_run_id.get())


class GatingCommitter:
    # ... docstring/init unchanged ...
    async def commit(self, agent_name: str, event_type: str, aggregate_id: str, payload: BaseModel) -> None:
        if await self._policy.is_gated(agent_name, event_type):
            proposal = Proposal(
                proposing_agent=agent_name,
                target_event_type=event_type,
                target_aggregate_id=aggregate_id,
                payload=payload.model_dump(mode="json"),
            )
            await self._events.append(EventType.PROPOSAL_CREATED, proposal.id, proposal,
                                      run_id=current_run_id.get())
            return
        await self._events.append(event_type, aggregate_id, payload, run_id=current_run_id.get())
```

- [ ] **Step 4: Run tests to verify green**

Run: `uv run pytest tests/canon/test_committer.py -v`
Expected: PASS (all)

- [ ] **Step 5: Commit**

```bash
git add novelizer/run_context.py novelizer/canon/committer.py tests/canon/test_committer.py
git commit -m "feat(canon): committers stamp ambient run_id into every append"
```

---

### Task 5: TelemetryRecorder

**Files:**
- Create: `novelizer/telemetry/recorder.py`
- Modify: `novelizer/telemetry/__init__.py`
- Test: `tests/telemetry/test_recorder.py`

**Interfaces:**
- Consumes: `EventStore` (Task 3), `TelemetryBus` (Task 2), event models (Task 1).
- Produces: `TelemetryRecorder(store: EventStore, bus: TelemetryBus)` with:
  - `async emit(event_type: str, aggregate_id: str, payload: BaseModel) -> None` — persists then mirrors the `StoredEvent` to the bus; on store failure logs a warning and mirrors a synthetic `StoredEvent(sequence=-1, ...)` so live view degrades gracefully.
  - `publish_token(delta: TokenDelta) -> None` — bus only.
  - `in_llm_call(run_id: str) -> bool` — True between an `llm.call_started` and its finish/fail (feeds `AgentRunFailed.phase`).
  - `next_call_index(run_id: str) -> int` — 1-based per-run counter (used by the callback handler); per-run bookkeeping is cleared when `agent.run_finished`/`agent.run_failed` passes through `emit`.

- [ ] **Step 1: Write the failing tests**

```python
# tests/telemetry/test_recorder.py
import os
import tempfile
import pytest
from novelizer.canon.event_store import EventStore
from novelizer.telemetry.bus import TelemetryBus
from novelizer.telemetry.recorder import TelemetryRecorder
from novelizer.telemetry.events import (
    TelemetryEventType, AgentRunStarted, AgentRunFinished, AgentRunFailed,
    LlmCallStarted, LlmCallFinished, TokenDelta,
)


@pytest.fixture
async def rig():
    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    store = EventStore(path)
    await store.init()
    bus = TelemetryBus()
    yield store, bus, TelemetryRecorder(store, bus)
    await store.close()
    os.unlink(path)


async def test_emit_persists_and_mirrors_to_bus(rig):
    store, bus, rec = rig
    q = bus.subscribe()
    await rec.emit(TelemetryEventType.AGENT_RUN_STARTED, "r1",
                   AgentRunStarted(run_id="r1", agent_name="author"))
    persisted = await store.events_since(0)
    assert persisted[0].event_type == TelemetryEventType.AGENT_RUN_STARTED
    mirrored = q.get_nowait()
    assert mirrored.sequence == persisted[0].sequence
    assert mirrored.payload["agent_name"] == "author"


async def test_store_failure_warns_drops_and_still_mirrors(rig, caplog):
    store, bus, rec = rig
    q = bus.subscribe()
    await store.close()  # subsequent appends now raise
    await rec.emit(TelemetryEventType.AGENT_RUN_STARTED, "r1",
                   AgentRunStarted(run_id="r1", agent_name="author"))  # must not raise
    assert any("telemetry" in r.message for r in caplog.records)
    mirrored = q.get_nowait()  # bus mirror still fires (spec: graceful degradation)
    assert mirrored.sequence == -1
    assert mirrored.payload["agent_name"] == "author"
    store._conn = None  # keep the fixture's second close() harmless


async def test_publish_token_reaches_bus_but_never_the_store(rig):
    store, bus, rec = rig
    q = bus.subscribe()
    rec.publish_token(TokenDelta(run_id="r1", agent_name="author", text="The "))
    assert q.get_nowait().text == "The "
    assert await store.events_since(0) == []


async def test_in_llm_call_tracks_open_calls(rig):
    store, bus, rec = rig
    assert rec.in_llm_call("r1") is False
    await rec.emit(TelemetryEventType.LLM_CALL_STARTED, "r1",
                   LlmCallStarted(run_id="r1", agent_name="author", call_index=1,
                                  model="m", prompt="p"))
    assert rec.in_llm_call("r1") is True
    await rec.emit(TelemetryEventType.LLM_CALL_FINISHED, "r1",
                   LlmCallFinished(run_id="r1", agent_name="author", call_index=1,
                                   model="m", duration_s=1.0, output_tokens=5))
    assert rec.in_llm_call("r1") is False


async def test_next_call_index_counts_per_run_and_resets_at_run_end(rig):
    store, bus, rec = rig
    assert rec.next_call_index("r1") == 1
    assert rec.next_call_index("r1") == 2
    assert rec.next_call_index("r2") == 1
    await rec.emit(TelemetryEventType.AGENT_RUN_FINISHED, "r1",
                   AgentRunFinished(run_id="r1", agent_name="author", duration_s=1.0))
    assert rec.next_call_index("r1") == 1  # bookkeeping cleared
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/telemetry/test_recorder.py -v`
Expected: FAIL with `ModuleNotFoundError` (no `novelizer.telemetry.recorder`)

- [ ] **Step 3: Implement**

```python
# novelizer/telemetry/recorder.py
from __future__ import annotations
import logging
import uuid
from datetime import datetime, timezone
from pydantic import BaseModel
from novelizer.canon.event_store import EventStore
from novelizer.canon.events import StoredEvent
from novelizer.telemetry.bus import TelemetryBus
from novelizer.telemetry.events import TelemetryEventType, TokenDelta

logger = logging.getLogger(__name__)

_RUN_END_TYPES = {TelemetryEventType.AGENT_RUN_FINISHED, TelemetryEventType.AGENT_RUN_FAILED}


class TelemetryRecorder:
    """Fire-and-forget machinery recorder: persist to the telemetry log,
    mirror to the bus. A store failure warns and drops — it must never take
    down an agent run or the scheduler; the bus mirror still fires so the
    live view degrades gracefully (the trace just has a gap)."""

    def __init__(self, store: EventStore, bus: TelemetryBus) -> None:
        self._store = store
        self._bus = bus
        self._open_calls: set[str] = set()
        self._call_counts: dict[str, int] = {}

    async def emit(self, event_type: str, aggregate_id: str, payload: BaseModel) -> None:
        self._track(event_type, payload)
        try:
            stored = await self._store.append(event_type, aggregate_id, payload)
        except Exception:
            logger.warning("telemetry: dropped %s (store write failed)", event_type, exc_info=True)
            stored = StoredEvent(
                sequence=-1, id=str(uuid.uuid4()), event_type=event_type,
                aggregate_id=aggregate_id, payload=payload.model_dump(mode="json"),
                created_at=datetime.now(timezone.utc).isoformat(),
            )
        self._bus.publish(stored)

    def publish_token(self, delta: TokenDelta) -> None:
        self._bus.publish(delta)

    def in_llm_call(self, run_id: str) -> bool:
        return run_id in self._open_calls

    def next_call_index(self, run_id: str) -> int:
        idx = self._call_counts.get(run_id, 0) + 1
        self._call_counts[run_id] = idx
        return idx

    def _track(self, event_type: str, payload: BaseModel) -> None:
        run_id = getattr(payload, "run_id", None)
        if run_id is None:
            return
        if event_type == TelemetryEventType.LLM_CALL_STARTED:
            self._open_calls.add(run_id)
        elif event_type in (TelemetryEventType.LLM_CALL_FINISHED, TelemetryEventType.LLM_CALL_FAILED):
            self._open_calls.discard(run_id)
        elif event_type in _RUN_END_TYPES:
            self._open_calls.discard(run_id)
            self._call_counts.pop(run_id, None)
```

Update `novelizer/telemetry/__init__.py`:

```python
from novelizer.telemetry.bus import TelemetryBus
from novelizer.telemetry.events import TelemetryEventType, TokenDelta
from novelizer.telemetry.recorder import TelemetryRecorder

__all__ = ["TelemetryBus", "TelemetryEventType", "TokenDelta", "TelemetryRecorder"]
```

- [ ] **Step 4: Run tests to verify green**

Run: `uv run pytest tests/telemetry -v`
Expected: PASS (all)

- [ ] **Step 5: Commit**

```bash
git add novelizer/telemetry tests/telemetry/test_recorder.py
git commit -m "feat(telemetry): TelemetryRecorder — persist warn-and-drop, mirror to bus"
```

---

### Task 6: BaseAgent run_once template + agent renames

**Files:**
- Modify: `novelizer/agents/base.py`
- Modify (rename `run_once` → `_run` only): `novelizer/agents/author.py:86`, `novelizer/agents/world_architect.py:53`, `novelizer/agents/character_keeper.py:77`, `novelizer/agents/editor.py:105`, `novelizer/agents/continuity_checker.py:264`, `novelizer/agents/retconner.py:56`, `novelizer/agents/structure_analyst.py:73`
- Test: `tests/agents/test_base.py` (additions)

**Interfaces:**
- Consumes: `TelemetryRecorder` (Task 5), `run_context` (Task 4), event models (Task 1).
- Produces: `BaseAgent.telemetry` attribute (default `None`, injected post-construction by Runtime in Task 10); final `BaseAgent.run_once()` template that mints a `run_id` (uuid4), sets/resets `current_run_id` + `current_agent_name`, emits `agent.run_started` / `agent.run_finished` / `agent.run_failed` (re-raising), and calls the new overridable `async def _run(self) -> None`. All subclasses override `_run`, never `run_once`. Callers (Scheduler, tests) still call `run_once()` — behavior with `telemetry=None` is identical to today.

- [ ] **Step 1: Write the failing tests** (append to `tests/agents/test_base.py`)

```python
class _CapturingRecorder:
    """Test double for TelemetryRecorder: records emits, tracks nothing."""

    def __init__(self):
        self.emitted = []  # list of (event_type, aggregate_id, payload)

    async def emit(self, event_type, aggregate_id, payload):
        self.emitted.append((event_type, aggregate_id, payload))

    def in_llm_call(self, run_id):
        return False


async def test_run_once_emits_started_and_finished_with_one_run_id():
    from novelizer.telemetry.events import TelemetryEventType

    class Quiet(BaseAgent):
        async def _run(self):
            pass

    agent = Quiet(runner=None, read_store=None, committer=None, interval=0, name="quiet")
    rec = _CapturingRecorder()
    agent.telemetry = rec
    await agent.run_once()
    types = [t for t, _, _ in rec.emitted]
    assert types == [TelemetryEventType.AGENT_RUN_STARTED, TelemetryEventType.AGENT_RUN_FINISHED]
    started, finished = rec.emitted[0][2], rec.emitted[1][2]
    assert started.run_id == finished.run_id != ""
    assert started.agent_name == "quiet"
    assert finished.duration_s >= 0.0


async def test_run_once_sets_ambient_run_context_during_run_and_resets_after():
    from novelizer.run_context import current_run_id, current_agent_name

    seen = {}

    class Peek(BaseAgent):
        async def _run(self):
            seen["run_id"] = current_run_id.get()
            seen["agent"] = current_agent_name.get()

    agent = Peek(runner=None, read_store=None, committer=None, interval=0, name="peek")
    await agent.run_once()  # works with telemetry=None too
    assert seen["run_id"] is not None
    assert seen["agent"] == "peek"
    assert current_run_id.get() is None
    assert current_agent_name.get() == ""


async def test_run_once_crash_emits_run_failed_and_reraises():
    from novelizer.telemetry.events import TelemetryEventType

    class Boom(BaseAgent):
        async def _run(self):
            raise ValueError("kaboom")

    agent = Boom(runner=None, read_store=None, committer=None, interval=0, name="boom")
    rec = _CapturingRecorder()
    agent.telemetry = rec
    with pytest.raises(ValueError, match="kaboom"):
        await agent.run_once()
    types = [t for t, _, _ in rec.emitted]
    assert types == [TelemetryEventType.AGENT_RUN_STARTED, TelemetryEventType.AGENT_RUN_FAILED]
    failed = rec.emitted[1][2]
    assert failed.error_type == "ValueError" and "kaboom" in failed.error_message
    assert failed.phase == "agent"  # recorder reports no open LLM call


async def test_run_once_crash_inside_open_llm_call_reports_llm_call_phase():
    class InCall(_CapturingRecorder):
        def in_llm_call(self, run_id):
            return True

    class Boom(BaseAgent):
        async def _run(self):
            raise ValueError("mid-call")

    agent = Boom(runner=None, read_store=None, committer=None, interval=0, name="boom")
    rec = InCall()
    agent.telemetry = rec
    with pytest.raises(ValueError):
        await agent.run_once()
    assert rec.emitted[1][2].phase == "llm_call"


async def test_run_once_without_telemetry_is_silent_and_still_runs():
    ran = []

    class Quiet(BaseAgent):
        async def _run(self):
            ran.append(True)

    agent = Quiet(runner=None, read_store=None, committer=None, interval=0, name="quiet")
    await agent.run_once()
    assert ran == [True]
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/agents/test_base.py -v`
Expected: new tests FAIL (`_run` never called / no telemetry emitted — `BaseAgent.run_once` is currently `pass`).

- [ ] **Step 3: Implement the template in `novelizer/agents/base.py`**

Add imports at top: `import time`, `import uuid`, `from novelizer.run_context import current_run_id, current_agent_name`, `from novelizer.telemetry.events import TelemetryEventType, AgentRunStarted, AgentRunFinished, AgentRunFailed`.

In `BaseAgent.__init__`, after `self._last_run = 0.0` add:

```python
        self.telemetry = None  # TelemetryRecorder; injected by Runtime post-construction
```

Replace the current `async def run_once(self) -> None: pass` (lines 73-74) with:

```python
    async def _run(self) -> None:
        """Subclasses put their poll/work/commit body here (M-telemetry:
        run_once became a final template that brackets _run with machinery
        events and ambient run context)."""

    async def run_once(self) -> None:
        run_id = str(uuid.uuid4())
        started = time.monotonic()
        rid_token = current_run_id.set(run_id)
        name_token = current_agent_name.set(self.name)
        await self._emit_telemetry(
            TelemetryEventType.AGENT_RUN_STARTED, run_id,
            AgentRunStarted(run_id=run_id, agent_name=self.name),
        )
        try:
            await self._run()
        except Exception as e:
            phase = "llm_call" if (self.telemetry and self.telemetry.in_llm_call(run_id)) else "agent"
            await self._emit_telemetry(
                TelemetryEventType.AGENT_RUN_FAILED, run_id,
                AgentRunFailed(run_id=run_id, agent_name=self.name,
                               error_type=type(e).__name__, error_message=str(e),
                               phase=phase, duration_s=time.monotonic() - started),
            )
            raise
        else:
            await self._emit_telemetry(
                TelemetryEventType.AGENT_RUN_FINISHED, run_id,
                AgentRunFinished(run_id=run_id, agent_name=self.name,
                                 duration_s=time.monotonic() - started),
            )
        finally:
            current_run_id.reset(rid_token)
            current_agent_name.reset(name_token)

    async def _emit_telemetry(self, event_type: str, aggregate_id: str, payload) -> None:
        if self.telemetry is None:
            return
        await self.telemetry.emit(event_type, aggregate_id, payload)
```

- [ ] **Step 4: Rename `run_once` → `_run` in all seven agents**

Each is the same one-line signature change (bodies untouched):

- `novelizer/agents/author.py:86`: `async def run_once(self) -> None:` → `async def _run(self) -> None:`
- `novelizer/agents/world_architect.py:53`: same change
- `novelizer/agents/character_keeper.py:77`: same change
- `novelizer/agents/editor.py:105`: same change
- `novelizer/agents/continuity_checker.py:264`: same change
- `novelizer/agents/retconner.py:56`: same change
- `novelizer/agents/structure_analyst.py:73`: same change

- [ ] **Step 5: Run the full suite to verify nothing regressed**

Run: `uv run pytest`
Expected: PASS. Agent behavior tests exercise `run_once()` (now the template) which calls the renamed bodies; scheduler stubs define their own `run_once` and are unaffected.

- [ ] **Step 6: Commit**

```bash
git add novelizer/agents tests/agents/test_base.py
git commit -m "feat(agents): run_once telemetry template; agent bodies move to _run"
```

---

### Task 7: Scheduler instrumentation

**Files:**
- Modify: `novelizer/scheduler.py`, `novelizer/agents/base.py` (add `seconds_until_ready`)
- Test: `tests/test_scheduler.py` (additions), `tests/agents/test_base.py` (one addition)

**Interfaces:**
- Consumes: `TelemetryRecorder` (Task 5), event models (Task 1).
- Produces: `Scheduler(..., telemetry=None)`; emits `scheduler.picked` before each `_run`, and `scheduler.eligibility_changed` only when an agent's `(eligible, reason)` pair changes (reasons: `"paused"`, `"interval not elapsed"`, `"readiness 0"`, `"ready"`). `Scheduler.status()` rows gain `"next_ready_in": float` (0.0 when ready or when the agent lacks `seconds_until_ready`). `BaseAgent.seconds_until_ready(now: float) -> float`.

- [ ] **Step 1: Write the failing tests**

Append to `tests/agents/test_base.py`:

```python
def test_seconds_until_ready_counts_down_and_floors_at_zero():
    a = BaseAgent(runner=None, read_store=None, committer=None, interval=10, name="x")
    a.mark_ran(now=100)
    assert a.seconds_until_ready(now=104) == 6
    assert a.seconds_until_ready(now=115) == 0
```

Append to `tests/test_scheduler.py`:

```python
class CapturingRecorder:
    def __init__(self):
        self.emitted = []

    async def emit(self, event_type, aggregate_id, payload):
        self.emitted.append((event_type, payload))

    def in_llm_call(self, run_id):
        return False


async def test_tick_emits_scheduler_picked_for_the_chosen_agent():
    from novelizer.telemetry.events import TelemetryEventType
    a = StubAgent("a", 0.2); b = StubAgent("b", 0.9)
    rec = CapturingRecorder()
    sched = Scheduler([a, b], StubRead(), clock=lambda: 1000.0, telemetry=rec)
    await sched.tick()
    picked = [p for t, p in rec.emitted if t == TelemetryEventType.SCHEDULER_PICKED]
    assert [p.agent_name for p in picked] == ["b"]


async def test_eligibility_changes_emit_once_not_per_tick():
    from novelizer.telemetry.events import TelemetryEventType
    a = StubAgent("a", 0.9, interval=10)
    rec = CapturingRecorder()
    now = [1000.0]
    sched = Scheduler([a], StubRead(), clock=lambda: now[0], telemetry=rec)
    await sched.tick()   # a ready -> runs -> interval consumed
    now[0] = 1001.0
    await sched.tick()   # a ineligible: "interval not elapsed"
    now[0] = 1002.0
    await sched.tick()   # still ineligible: same state -> NO new event
    elig = [p for t, p in rec.emitted if t == TelemetryEventType.SCHEDULER_ELIGIBILITY_CHANGED]
    assert [(p.agent_name, p.eligible, p.reason) for p in elig] == [
        ("a", True, "ready"),
        ("a", False, "interval not elapsed"),
    ]


async def test_paused_and_readiness_zero_reasons_are_reported():
    from novelizer.telemetry.events import TelemetryEventType
    a = StubAgent("a", 0.0)   # eligible by interval but readiness 0
    b = StubAgent("b", 0.5)
    b.pause()
    rec = CapturingRecorder()
    sched = Scheduler([a, b], StubRead(), clock=lambda: 1000.0, telemetry=rec)
    await sched.tick()  # nothing runs: a scores 0, b paused
    elig = {p.agent_name: p for t, p in rec.emitted
            if t == TelemetryEventType.SCHEDULER_ELIGIBILITY_CHANGED}
    assert elig["a"].reason == "readiness 0" and elig["a"].eligible is False
    assert elig["b"].reason == "paused" and elig["b"].eligible is False


async def test_scheduler_without_telemetry_behaves_exactly_as_before():
    a = StubAgent("a", 0.2); b = StubAgent("b", 0.9)
    sched = Scheduler([a, b], StubRead(), clock=lambda: 1000.0)
    assert await sched.tick() == "b"


async def test_status_includes_next_ready_in_and_tolerates_stub_agents():
    a = StubAgent("a", 0.9, interval=10)
    sched = Scheduler([a], StubRead(), clock=lambda: 1000.0)
    st = sched.status()[0]
    assert st["next_ready_in"] == 0.0  # StubAgent has no seconds_until_ready -> 0.0
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_scheduler.py tests/agents/test_base.py -v`
Expected: new tests FAIL (`TypeError: unexpected keyword argument 'telemetry'`, missing `seconds_until_ready`, missing `next_ready_in`).

- [ ] **Step 3: Implement**

In `novelizer/agents/base.py`, after `mark_ran`:

```python
    def seconds_until_ready(self, now: float) -> float:
        return max(0.0, self.interval - (now - self._last_run))
```

In `novelizer/scheduler.py`, add import `from novelizer.telemetry.events import (TelemetryEventType, SchedulerPicked, SchedulerEligibilityChanged)` and rework:

```python
class Scheduler:
    def __init__(self, agents: Sequence, read_store, tick_sleep: float = 1.0,
                 clock=time.monotonic, telemetry=None) -> None:
        self._agents = list(agents)
        self._read = read_store
        self._tick_sleep = tick_sleep
        self._clock = clock
        self._telemetry = telemetry
        self._running = False
        self._last_ran: Optional[str] = None
        self._last_error: dict[str, str] = {}
        self._eligibility: dict[str, tuple[bool, str]] = {}
```

`status()` — add the `next_ready_in` key to the dict comprehension:

```python
    def status(self) -> list:
        now = self._clock()
        return [
            {
                "name": a.name,
                "paused": a.paused,
                "running": a.name == self._last_ran,
                "last_error": self._last_error.get(a.name),
                "next_ready_in": a.seconds_until_ready(now) if hasattr(a, "seconds_until_ready") else 0.0,
            }
            for a in self._agents
        ]
```

`tick()` — same selection logic, plus eligibility snapshot at the end (before returning) and a picked event inside `_run`:

```python
    async def tick(self) -> Optional[str]:
        now = self._clock()
        signals = await self._read.list_unconsumed_signals()
        override = next((s.target_agent for s in signals
                         if s.kind == SignalKind.override and s.target_agent), None)
        eligible = [a for a in self._agents if not a.paused and a.ready_for_interval(now)]
        if not eligible:
            await self._emit_eligibility(now, scores={})
            return None
        if override:
            for a in eligible:
                if a.name == override:
                    await self._emit_eligibility(now, scores={})
                    await self._run(a, now)
                    return a.name
        scored = [(await a.readiness(), a) for a in eligible]
        scored.sort(key=lambda x: x[0], reverse=True)
        best_score, best = scored[0]
        await self._emit_eligibility(now, scores={a.name: s for s, a in scored})
        if best_score > 0.0:
            await self._run(best, now)
            return best.name
        return None

    async def _emit_eligibility(self, now: float, scores: dict[str, float]) -> None:
        """One eligibility_changed per agent per state *change* — quiet log,
        not a per-tick heartbeat. Reasons mirror exactly the predicates tick
        just evaluated."""
        if self._telemetry is None:
            return
        for a in self._agents:
            if a.paused:
                state = (False, "paused")
            elif not a.ready_for_interval(now):
                state = (False, "interval not elapsed")
            elif a.name in scores and scores[a.name] <= 0.0:
                state = (False, "readiness 0")
            else:
                state = (True, "ready")
            if self._eligibility.get(a.name) != state:
                self._eligibility[a.name] = state
                await self._telemetry.emit(
                    TelemetryEventType.SCHEDULER_ELIGIBILITY_CHANGED, a.name,
                    SchedulerEligibilityChanged(agent_name=a.name, eligible=state[0], reason=state[1]),
                )

    async def _run(self, agent, now: float) -> None:
        logger.info("scheduler: running %s", agent.name)
        if self._telemetry is not None:
            await self._telemetry.emit(
                TelemetryEventType.SCHEDULER_PICKED, agent.name,
                SchedulerPicked(agent_name=agent.name),
            )
        try:
            await agent.run_once()
        except Exception as e:
            self._last_error[agent.name] = f"{type(e).__name__}: {e}"
            raise
        else:
            self._last_error.pop(agent.name, None)
            self._last_ran = agent.name
        finally:
            # mark_ran even on failure: a crashing agent must consume its
            # interval (backoff) instead of staying eligible and hot-looping,
            # which starves every other agent of scheduler slots.
            agent.mark_ran(now)
```

(`run()`/`stop()`/`pause_agent`/`resume_agent` unchanged.)

- [ ] **Step 4: Run tests to verify green**

Run: `uv run pytest tests/test_scheduler.py tests/agents/test_base.py -v`
Expected: PASS (all, including all pre-existing scheduler tests)

- [ ] **Step 5: Commit**

```bash
git add novelizer/scheduler.py novelizer/agents/base.py tests/test_scheduler.py tests/agents/test_base.py
git commit -m "feat(scheduler): picked + throttled eligibility telemetry, next_ready_in status"
```

---

### Task 8: LLM callback handler

**Files:**
- Create: `novelizer/telemetry/callbacks.py`
- Test: `tests/telemetry/test_callbacks.py`

**Interfaces:**
- Consumes: `TelemetryRecorder` (Task 5: `emit`, `publish_token`, `next_call_index`), `run_context` (Task 4).
- Produces: `TelemetryCallbackHandler(recorder)` — a `langchain_core.callbacks.AsyncCallbackHandler` implementing `on_chat_model_start` (emits `llm.call_started` with the rendered prompt), `on_llm_new_token` (publishes `TokenDelta`), `on_llm_end` (emits `llm.call_finished`; output tokens from `usage_metadata` when present, else the streamed-chunk count), `on_llm_error` (emits `llm.call_failed`). Also module function `render_messages(messages) -> str`. The LangChain `run_id` kwarg (a UUID per call) keys in-flight bookkeeping; the novelizer run comes from `current_run_id`.

- [ ] **Step 1: Write the failing tests**

```python
# tests/telemetry/test_callbacks.py
import uuid
from types import SimpleNamespace
from langchain_core.messages import HumanMessage, SystemMessage
from novelizer.run_context import current_run_id, current_agent_name
from novelizer.telemetry.callbacks import TelemetryCallbackHandler, render_messages
from novelizer.telemetry.events import TelemetryEventType, TokenDelta


class FakeRecorder:
    def __init__(self):
        self.emitted = []
        self.tokens = []
        self._counts = {}

    async def emit(self, event_type, aggregate_id, payload):
        self.emitted.append((event_type, payload))

    def publish_token(self, delta):
        self.tokens.append(delta)

    def next_call_index(self, run_id):
        self._counts[run_id] = self._counts.get(run_id, 0) + 1
        return self._counts[run_id]


def _in_run(fn):
    """Run coroutine fn under an ambient novelizer run context."""
    import asyncio

    async def wrapper():
        rid = current_run_id.set("nrun-1")
        name = current_agent_name.set("author")
        try:
            return await fn()
        finally:
            current_run_id.reset(rid)
            current_agent_name.reset(name)
    return asyncio.run(wrapper())


def test_render_messages_includes_role_and_content():
    text = render_messages([[SystemMessage(content="Be brief."), HumanMessage(content="Write.")]])
    assert "[system]" in text and "Be brief." in text
    assert "[human]" in text and "Write." in text


def test_chat_model_start_emits_call_started_with_prompt_and_index():
    rec = FakeRecorder()
    h = TelemetryCallbackHandler(rec)
    lc_run = uuid.uuid4()

    async def go():
        await h.on_chat_model_start(
            {"kwargs": {"model_name": "qwen"}},
            [[HumanMessage(content="Write the next chapter.")]],
            run_id=lc_run,
        )
    _in_run(go)
    (etype, payload), = rec.emitted
    assert etype == TelemetryEventType.LLM_CALL_STARTED
    assert payload.run_id == "nrun-1" and payload.agent_name == "author"
    assert payload.call_index == 1 and payload.model == "qwen"
    assert "Write the next chapter." in payload.prompt


def test_new_token_publishes_delta_and_llm_end_reports_usage_tokens():
    rec = FakeRecorder()
    h = TelemetryCallbackHandler(rec)
    lc_run = uuid.uuid4()

    async def go():
        await h.on_chat_model_start({"kwargs": {}}, [[HumanMessage(content="x")]], run_id=lc_run)
        await h.on_llm_new_token("The ", run_id=lc_run)
        await h.on_llm_new_token("sea", run_id=lc_run)
        response = SimpleNamespace(generations=[[SimpleNamespace(
            message=SimpleNamespace(usage_metadata={"output_tokens": 42}))]])
        await h.on_llm_end(response, run_id=lc_run)
    _in_run(go)
    assert [d.text for d in rec.tokens] == ["The ", "sea"]
    assert all(isinstance(d, TokenDelta) and d.run_id == "nrun-1" for d in rec.tokens)
    etype, payload = rec.emitted[-1]
    assert etype == TelemetryEventType.LLM_CALL_FINISHED
    assert payload.output_tokens == 42
    assert payload.duration_s >= 0.0


def test_llm_end_without_usage_falls_back_to_streamed_chunk_count():
    rec = FakeRecorder()
    h = TelemetryCallbackHandler(rec)
    lc_run = uuid.uuid4()

    async def go():
        await h.on_chat_model_start({"kwargs": {}}, [[HumanMessage(content="x")]], run_id=lc_run)
        await h.on_llm_new_token("a", run_id=lc_run)
        await h.on_llm_new_token("b", run_id=lc_run)
        await h.on_llm_end(SimpleNamespace(generations=[[]]), run_id=lc_run)
    _in_run(go)
    assert rec.emitted[-1][1].output_tokens == 2


def test_llm_error_emits_call_failed():
    rec = FakeRecorder()
    h = TelemetryCallbackHandler(rec)
    lc_run = uuid.uuid4()

    async def go():
        await h.on_chat_model_start({"kwargs": {}}, [[HumanMessage(content="x")]], run_id=lc_run)
        await h.on_llm_error(TimeoutError("proxy timeout"), run_id=lc_run)
    _in_run(go)
    etype, payload = rec.emitted[-1]
    assert etype == TelemetryEventType.LLM_CALL_FAILED
    assert payload.error_type == "TimeoutError" and "proxy timeout" in payload.error_message
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/telemetry/test_callbacks.py -v`
Expected: FAIL with `ModuleNotFoundError` (no `novelizer.telemetry.callbacks`)

- [ ] **Step 3: Implement**

```python
# novelizer/telemetry/callbacks.py
from __future__ import annotations
import time
from typing import Any
from uuid import UUID
from langchain_core.callbacks import AsyncCallbackHandler
from novelizer.run_context import current_run_id, current_agent_name
from novelizer.telemetry.events import (
    TelemetryEventType, LlmCallStarted, LlmCallFinished, LlmCallFailed, TokenDelta,
)


def render_messages(messages) -> str:
    """Render LangChain chat batches to the trace's prompt text: one
    [role] header per message, content stringified."""
    parts = []
    for batch in messages:
        for m in batch:
            parts.append(f"[{m.type}]\n{m.content}")
    return "\n\n".join(parts)


class _CallState:
    __slots__ = ("novelizer_run_id", "agent_name", "call_index", "model", "started", "chunks")

    def __init__(self, novelizer_run_id: str, agent_name: str, call_index: int, model: str) -> None:
        self.novelizer_run_id = novelizer_run_id
        self.agent_name = agent_name
        self.call_index = call_index
        self.model = model
        self.started = time.monotonic()
        self.chunks = 0


class TelemetryCallbackHandler(AsyncCallbackHandler):
    """Bridges LangChain model callbacks to telemetry: call_started with the
    full rendered prompt, per-token bus deltas, call_finished/failed with
    vitals. LangChain's own run_id (a UUID per call) keys in-flight state;
    the novelizer run identity is read from run_context at call start."""

    def __init__(self, recorder) -> None:
        self._recorder = recorder
        self._calls: dict[UUID, _CallState] = {}

    async def on_chat_model_start(self, serialized: dict, messages, *, run_id: UUID, **kwargs: Any) -> None:
        await self._start(serialized, render_messages(messages), run_id)

    async def on_llm_start(self, serialized: dict, prompts: list[str], *, run_id: UUID, **kwargs: Any) -> None:
        await self._start(serialized, "\n\n".join(prompts), run_id)

    async def _start(self, serialized: dict, prompt: str, lc_run_id: UUID) -> None:
        nrun = current_run_id.get() or ""
        skw = (serialized or {}).get("kwargs", {})
        model = skw.get("model_name") or skw.get("model") or ""
        state = _CallState(nrun, current_agent_name.get(),
                           self._recorder.next_call_index(nrun), model)
        self._calls[lc_run_id] = state
        await self._recorder.emit(
            TelemetryEventType.LLM_CALL_STARTED, nrun,
            LlmCallStarted(run_id=nrun, agent_name=state.agent_name,
                           call_index=state.call_index, model=model, prompt=prompt),
        )

    async def on_llm_new_token(self, token: str, *, run_id: UUID, **kwargs: Any) -> None:
        state = self._calls.get(run_id)
        if state is None:
            return
        state.chunks += 1
        self._recorder.publish_token(
            TokenDelta(run_id=state.novelizer_run_id, agent_name=state.agent_name, text=token))

    async def on_llm_end(self, response, *, run_id: UUID, **kwargs: Any) -> None:
        state = self._calls.pop(run_id, None)
        if state is None:
            return
        tokens = self._usage_tokens(response)
        await self._recorder.emit(
            TelemetryEventType.LLM_CALL_FINISHED, state.novelizer_run_id,
            LlmCallFinished(run_id=state.novelizer_run_id, agent_name=state.agent_name,
                            call_index=state.call_index, model=state.model,
                            duration_s=time.monotonic() - state.started,
                            output_tokens=tokens if tokens else state.chunks),
        )

    async def on_llm_error(self, error: BaseException, *, run_id: UUID, **kwargs: Any) -> None:
        state = self._calls.pop(run_id, None)
        if state is None:
            return
        await self._recorder.emit(
            TelemetryEventType.LLM_CALL_FAILED, state.novelizer_run_id,
            LlmCallFailed(run_id=state.novelizer_run_id, agent_name=state.agent_name,
                          call_index=state.call_index, model=state.model,
                          duration_s=time.monotonic() - state.started,
                          error_type=type(error).__name__, error_message=str(error)),
        )

    @staticmethod
    def _usage_tokens(response) -> int:
        try:
            gen = response.generations[0][0]
            usage = getattr(getattr(gen, "message", None), "usage_metadata", None) or {}
            return int(usage.get("output_tokens", 0))
        except (IndexError, AttributeError, TypeError, ValueError):
            return 0
```

- [ ] **Step 4: Run tests to verify green**

Run: `uv run pytest tests/telemetry -v`
Expected: PASS (all)

- [ ] **Step 5: Commit**

```bash
git add novelizer/telemetry/callbacks.py tests/telemetry/test_callbacks.py
git commit -m "feat(telemetry): LangChain callback handler — call vitals + token deltas"
```

---

### Task 9: Streaming + callbacks through build_chat_model and the builders

**Files:**
- Modify: `novelizer/agents/llm.py`; `build_author_runner` (`novelizer/agents/author.py:92`), `build_world_architect_runner` (`world_architect.py:59`), `build_character_keeper_runner` (`character_keeper.py:83`), `build_editor_runner` (`editor.py:111`), `build_continuity_checker_runner` (`continuity_checker.py:270`), `build_continuity_mining_runner` (`continuity_checker.py:277`), `build_retconner_runner` (`retconner.py:62`), `build_structure_analyst_runner` (`structure_analyst.py:79`)
- Test: `tests/agents/test_llm.py` (additions)

**Interfaces:**
- Consumes: nothing new (handler instances arrive from Runtime in Task 10).
- Produces: `build_chat_model(model, base_url, api_key, temperature=0.8, max_tokens=None, callbacks=None)` — when `callbacks` is provided, passes `callbacks=callbacks` and `streaming=True` to the model (streaming makes `on_llm_new_token` fire). Every `build_*_runner(settings)` gains a trailing `callbacks=None` parameter forwarded to `build_chat_model`.

- [ ] **Step 1: Write the failing tests** (append to `tests/agents/test_llm.py`)

```python
def test_build_chat_model_with_callbacks_enables_streaming():
    from novelizer.agents.llm import build_chat_model

    handler = object()
    m = build_chat_model("gpt-x", "http://localhost:1", "k", callbacks=[handler])
    assert m.streaming is True
    assert handler in (m.callbacks or [])


def test_build_chat_model_without_callbacks_keeps_current_defaults():
    from novelizer.agents.llm import build_chat_model

    m = build_chat_model("gpt-x", "http://localhost:1", "k")
    assert m.streaming is False
    assert not m.callbacks


def test_every_builder_accepts_a_callbacks_kwarg():
    import inspect
    from novelizer.agents.author import build_author_runner
    from novelizer.agents.world_architect import build_world_architect_runner
    from novelizer.agents.character_keeper import build_character_keeper_runner
    from novelizer.agents.editor import build_editor_runner
    from novelizer.agents.continuity_checker import (
        build_continuity_checker_runner, build_continuity_mining_runner,
    )
    from novelizer.agents.retconner import build_retconner_runner
    from novelizer.agents.structure_analyst import build_structure_analyst_runner

    for builder in [build_author_runner, build_world_architect_runner,
                    build_character_keeper_runner, build_editor_runner,
                    build_continuity_checker_runner, build_continuity_mining_runner,
                    build_retconner_runner, build_structure_analyst_runner]:
        assert "callbacks" in inspect.signature(builder).parameters, builder.__name__
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/agents/test_llm.py -v`
Expected: new tests FAIL (`TypeError: unexpected keyword argument 'callbacks'`).

- [ ] **Step 3: Implement**

Replace `novelizer/agents/llm.py`'s function:

```python
def build_chat_model(
    model: str, base_url: str, api_key: str, temperature: float = 0.8,
    max_tokens: int | None = None, callbacks=None,
):
    """Build a LangChain chat model bound to an OpenAI-compatible endpoint.

    max_tokens caps generation per request: an uncapped local model (especially
    with server-side reasoning enabled) can ramble past a proxy's request
    timeout and never return, which the caller sees as a hang.

    callbacks (telemetry handlers) imply streaming=True — token-by-token
    delivery is what makes on_llm_new_token fire for the live Engine Room view.
    """
    return init_chat_model(
        f"openai:{model}",
        base_url=base_url,
        api_key=api_key,
        temperature=temperature,
        max_tokens=max_tokens,
        callbacks=callbacks,
        streaming=callbacks is not None,
    )
```

Each builder gets the same mechanical change — add the parameter and forward it. All eight, exactly:

```python
# novelizer/agents/author.py
def build_author_runner(settings, callbacks=None):
    from deepagents import create_deep_agent
    from novelizer.agents.llm import build_chat_model
    model = build_chat_model(settings.author_model, settings.llm_base_url, settings.llm_api_key, settings.author_temperature, max_tokens=settings.llm_max_tokens, callbacks=callbacks)
    return create_deep_agent(model=model, system_prompt=AUTHOR_SYSTEM_PROMPT, response_format=ChapterDraft)
```

For the other seven (`build_world_architect_runner`, `build_character_keeper_runner`, `build_editor_runner`, `build_continuity_checker_runner`, `build_continuity_mining_runner`, `build_retconner_runner`, `build_structure_analyst_runner`): add `, callbacks=None` to the signature and `, callbacks=callbacks` to the existing `build_chat_model(...)` call inside — no other line changes. (Their bodies differ only in model/temperature settings keys, prompt constant, and response_format; do not alter those.)

- [ ] **Step 4: Run tests to verify green**

Run: `uv run pytest tests/agents/test_llm.py -v`
Expected: PASS. Then `uv run pytest` — full suite still green.

- [ ] **Step 5: Commit**

```bash
git add novelizer/agents
git commit -m "feat(agents): builders accept telemetry callbacks; streaming on when attached"
```

---

### Task 10: Runtime wiring

**Files:**
- Modify: `novelizer/runtime.py`
- Test: `tests/test_runtime.py` (additions)

**Interfaces:**
- Consumes: everything above.
- Produces: `Runtime.telemetry_store: EventStore` (on `<db_dir>/telemetry.db`), `Runtime.telemetry_bus: TelemetryBus`, `Runtime.telemetry: TelemetryRecorder` — constructed in `__init__`, store `init()`-ed in `start()`, closed in `close()`. `start()` injects `agent.telemetry = self.telemetry` into every agent and passes `telemetry=self.telemetry` to `Scheduler`. `self._llm_callbacks = [TelemetryCallbackHandler(self.telemetry)]` is passed to every real builder call (`_runner_for` fallback paths and the `apply_settings` rebuild block); injected fake runners (tests) are untouched.

- [ ] **Step 1: Write the failing tests** (append to `tests/test_runtime.py`, following that file's existing Runtime-construction pattern — inject `runners=` fakes as `tests/tui/test_app_smoke.py` does, with a tempfile db)

```python
async def test_runtime_wires_telemetry_store_bus_and_recorder(tmp_path):
    from novelizer.settings import EffectiveSettings as Settings
    from novelizer.runtime import Runtime

    class _R:
        async def ainvoke(self, inputs):
            return {"structured_response": None}

    db = tmp_path / "world.db"
    settings = Settings(db_path=str(db))
    rt = Runtime(settings, runners={n: _R() for n in [
        "author", "world_architect", "character_keeper", "editor",
        "continuity_checker", "continuity_checker_mining", "retconner", "structure_analyst"]})
    await rt.start()
    try:
        assert rt.telemetry is not None and rt.telemetry_bus is not None
        # telemetry.db lands beside the domain db — never inside it
        assert (tmp_path / "telemetry.db").exists()
        # every agent got the recorder injected; scheduler too
        assert all(a.telemetry is rt.telemetry for a in rt.agents)
        assert rt.scheduler._telemetry is rt.telemetry
    finally:
        await rt.close()


async def test_agent_run_via_runtime_lands_run_events_in_telemetry_log(tmp_path):
    from novelizer.settings import EffectiveSettings as Settings
    from novelizer.runtime import Runtime
    from novelizer.telemetry.events import TelemetryEventType

    class _R:
        async def ainvoke(self, inputs):
            return {"structured_response": None}

    settings = Settings(db_path=str(tmp_path / "world.db"))
    rt = Runtime(settings, runners={n: _R() for n in [
        "author", "world_architect", "character_keeper", "editor",
        "continuity_checker", "continuity_checker_mining", "retconner", "structure_analyst"]})
    await rt.start()
    try:
        await rt.author.run_once()
        tel = await rt.telemetry_store.events_since(0)
        types = [e.event_type for e in tel]
        assert TelemetryEventType.AGENT_RUN_STARTED in types
        assert TelemetryEventType.AGENT_RUN_FINISHED in types
    finally:
        await rt.close()
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_runtime.py -v`
Expected: new tests FAIL (`AttributeError: 'Runtime' object has no attribute 'telemetry'`).

- [ ] **Step 3: Implement in `novelizer/runtime.py`**

Add imports:

```python
from pathlib import Path
from novelizer.telemetry.bus import TelemetryBus
from novelizer.telemetry.recorder import TelemetryRecorder
from novelizer.telemetry.callbacks import TelemetryCallbackHandler
```

In `__init__`, after `self.read = ReadStore(settings.db_path)`:

```python
        self.telemetry_store = EventStore(str(Path(settings.db_path).with_name("telemetry.db")))
        self.telemetry_bus = TelemetryBus()
        self.telemetry = TelemetryRecorder(self.telemetry_store, self.telemetry_bus)
        self._llm_callbacks = [TelemetryCallbackHandler(self.telemetry)]
```

In `_runner_for`, both fallback `builder(...)` calls become `builder(self.settings, callbacks=self._llm_callbacks)`.

In `start()`: after `await self.read.init()` add `await self.telemetry_store.init()`; after the `self.agents = [...]` list add:

```python
        for agent in self.agents:
            agent.telemetry = self.telemetry
        self.scheduler = Scheduler(self.agents, self.read, telemetry=self.telemetry)
```

(replacing the existing `self.scheduler = Scheduler(self.agents, self.read)` line).

In `apply_settings`, the rebuild block's eight `build_*_runner(stored)` calls each gain `, callbacks=self._llm_callbacks`.

In `close()`, before `await self.events.close()` add `await self.telemetry_store.close()`.

- [ ] **Step 4: Run tests to verify green**

Run: `uv run pytest tests/test_runtime.py tests/test_apply_settings.py -v` then `uv run pytest`
Expected: PASS (all)

- [ ] **Step 5: Commit**

```bash
git add novelizer/runtime.py tests/test_runtime.py
git commit -m "feat(runtime): construct and wire telemetry store, bus, recorder, callbacks"
```

---

### Task 11: Correlation property (Hypothesis)

**Files:**
- Test: `tests/telemetry/test_correlation_property.py` (test-only task — the invariant should already hold; if it fails, that's a bug found, fix it where it lives)

**Interfaces:**
- Consumes: `BaseAgent.run_once` template (Task 6), `Committer` stamping (Task 4), `EventStore.run_id` (Task 3), `TelemetryRecorder` (Task 5).

- [ ] **Step 1: Write the property test**

```python
# tests/telemetry/test_correlation_property.py
"""Spec invariant: every domain event committed during a run carries exactly
that run's run_id, and no run_id appears in the domain log that the telemetry
log doesn't know. Sequential interleavings only — the scheduler runs agents
one at a time by design."""
import os
import tempfile
from hypothesis import given, settings, strategies as st
from novelizer.canon.event_store import EventStore
from novelizer.canon.events import EventType, AgentRemark
from novelizer.canon.committer import Committer
from novelizer.agents.base import BaseAgent
from novelizer.telemetry.bus import TelemetryBus
from novelizer.telemetry.recorder import TelemetryRecorder
from novelizer.telemetry.events import TelemetryEventType


class CommitsN(BaseAgent):
    def __init__(self, committer, name, n):
        super().__init__(runner=None, read_store=None, committer=committer, interval=0, name=name)
        self._n = n

    async def _run(self):
        for i in range(self._n):
            await self._committer.commit(
                self.name, EventType.AGENT_REMARKED, self.name,
                AgentRemark(agent_name=self.name, note=f"note {i}"))


@settings(deadline=None, max_examples=25)
@given(runs=st.lists(
    st.tuples(st.sampled_from(["author", "editor", "retconner"]),
              st.integers(min_value=0, max_value=4)),
    min_size=1, max_size=6))
async def test_every_domain_event_carries_its_own_runs_id(runs):
    fd, dpath = tempfile.mkstemp(suffix=".db"); os.close(fd)
    fd, tpath = tempfile.mkstemp(suffix=".db"); os.close(fd)
    domain = EventStore(dpath); await domain.init()
    tel_store = EventStore(tpath); await tel_store.init()
    recorder = TelemetryRecorder(tel_store, TelemetryBus())
    committer = Committer(domain)
    try:
        for name, n in runs:
            agent = CommitsN(committer, name, n)
            agent.telemetry = recorder
            await agent.run_once()

        tel = await tel_store.events_since(0)
        started = [e for e in tel if e.event_type == TelemetryEventType.AGENT_RUN_STARTED]
        run_ids_in_order = [e.payload["run_id"] for e in started]
        assert len(run_ids_in_order) == len(runs)

        dom = await domain.events_since(0)
        # Domain events, in commit order, must group by run in run order with
        # exactly the declared counts — and cite exactly that run's id.
        expected = [rid for rid, (_, n) in zip(run_ids_in_order, runs) for _ in range(n)]
        assert [e.run_id for e in dom] == expected
        # No domain run_id the telemetry log doesn't know.
        assert {e.run_id for e in dom} <= set(run_ids_in_order)
    finally:
        await domain.close(); await tel_store.close()
        os.unlink(dpath); os.unlink(tpath)
```

- [ ] **Step 2: Run it**

Run: `uv run pytest tests/telemetry/test_correlation_property.py -v`
Expected: PASS. (If it fails, the bug is real — debug where the run_id chain breaks: template sets the ContextVar, committer reads it, store persists it. Do not weaken the property.)

- [ ] **Step 3: Commit**

```bash
git add tests/telemetry/test_correlation_property.py
git commit -m "test(telemetry): Hypothesis property — run_id correlation across interleavings"
```

---

### Task 12: Engine room model — live state machine + formatters

**Files:**
- Create: `novelizer/tui/widgets/engine_room_model.py`
- Test: `tests/tui/test_engine_room_model.py`

**Interfaces:**
- Consumes: `StoredEvent`, `TokenDelta`, `TelemetryEventType`.
- Produces (all pure, no Textual imports):
  - `@dataclass LiveRunState` — fields: `status: str = "idle"` (`"idle" | "running" | "finished" | "failed"`), `run_id: str = ""`, `agent_name: str = ""`, `started_at: float = 0.0` (monotonic), `ended_at: float = 0.0`, `tokens: int = 0`, `text: str = ""` (tail-capped at `TEXT_CAP = 8000` chars), `prompt: str = ""`, `model: str = ""`, `call_index: int = 0`, `error: str = ""`, `stream_attached: bool = True`.
  - `apply_bus_item(state: LiveRunState, item, now: float) -> LiveRunState` — pure transition; unknown/scheduler items return state unchanged.
  - `seed_state(recent: list[StoredEvent], now: float) -> LiveRunState` — fold `apply_bus_item` over persisted events; if the result is `running`, set `stream_attached=False` (restart-mid-run).
  - `strip_line(state, now, next_hint: str = "") -> str`
  - `vitals_line(state, now) -> str`
  - `live_body(state) -> str` — the stream text, or the not-attached / idle / crashed notice.
  - `trace_line(ev: StoredEvent) -> str` — one row per telemetry event, 1:1 (never drops or merges).
  - `trace_detail(ev: StoredEvent, produced: list[StoredEvent]) -> str` — full payload incl. prompt; `produced` lines as `produced: <event_type> <aggregate_id>`.
  - `_VERBS: dict[str, str]` — `{"author": "drafting", "editor": "reviewing", "world_architect": "worldbuilding", "character_keeper": "tending characters", "continuity_checker": "checking continuity", "retconner": "retconning", "structure_analyst": "scoring structure"}`, default `"working"`.

- [ ] **Step 1: Write the failing tests**

```python
# tests/tui/test_engine_room_model.py
from hypothesis import given, strategies as st
from novelizer.canon.events import StoredEvent
from novelizer.telemetry.events import TelemetryEventType, TokenDelta
from novelizer.tui.widgets.engine_room_model import (
    LiveRunState, apply_bus_item, seed_state, strip_line, vitals_line,
    live_body, trace_line, trace_detail,
)


def _ev(seq, etype, payload, created_at="2026-07-18T12:04:32+00:00"):
    return StoredEvent(sequence=seq, id=f"e{seq}", event_type=etype,
                       aggregate_id="r1", payload=payload, created_at=created_at)


def _run_started(seq=1):
    return _ev(seq, TelemetryEventType.AGENT_RUN_STARTED,
               {"run_id": "r1", "agent_name": "author"})


def _call_started(seq=2):
    return _ev(seq, TelemetryEventType.LLM_CALL_STARTED,
               {"run_id": "r1", "agent_name": "author", "call_index": 1,
                "model": "qwen", "prompt": "[system]\nWrite."})


def test_run_started_resets_state_to_a_fresh_running_run():
    s = apply_bus_item(LiveRunState(text="stale", tokens=9), _run_started(), now=100.0)
    assert s.status == "running" and s.agent_name == "author" and s.run_id == "r1"
    assert s.tokens == 0 and s.text == "" and s.started_at == 100.0


def test_call_started_carries_prompt_model_and_index():
    s = apply_bus_item(LiveRunState(status="running", run_id="r1"), _call_started(), now=101.0)
    assert s.prompt == "[system]\nWrite." and s.model == "qwen" and s.call_index == 1


def test_token_deltas_accumulate_text_and_count():
    s = LiveRunState(status="running", run_id="r1", agent_name="author")
    s = apply_bus_item(s, TokenDelta(run_id="r1", agent_name="author", text="The "), now=1.0)
    s = apply_bus_item(s, TokenDelta(run_id="r1", agent_name="author", text="sea"), now=1.1)
    assert s.text == "The sea" and s.tokens == 2


def test_text_is_tail_capped():
    from novelizer.tui.widgets.engine_room_model import TEXT_CAP
    s = LiveRunState(status="running", run_id="r1", text="x" * TEXT_CAP)
    s = apply_bus_item(s, TokenDelta(run_id="r1", agent_name="author", text="END"), now=1.0)
    assert len(s.text) == TEXT_CAP and s.text.endswith("END")


def test_run_failed_marks_failed_with_error():
    s = LiveRunState(status="running", run_id="r1", agent_name="author")
    ev = _ev(3, TelemetryEventType.AGENT_RUN_FAILED,
             {"run_id": "r1", "agent_name": "author", "error_type": "TimeoutError",
              "error_message": "proxy", "phase": "llm_call", "duration_s": 4.0})
    s = apply_bus_item(s, ev, now=104.0)
    assert s.status == "failed" and "TimeoutError" in s.error and s.ended_at == 104.0


def test_strip_line_running_idle_and_failed_forms():
    running = LiveRunState(status="running", agent_name="author", started_at=100.0,
                           tokens=3400, call_index=1)
    line = strip_line(running, now=152.0)
    assert "▶" in line and "author" in line and "drafting" in line
    assert "3.4k tok" in line and "52s" in line
    idle = strip_line(LiveRunState(), now=0.0, next_hint="next: editor in 12s")
    assert idle.startswith("idle") and "next: editor in 12s" in idle
    failed = LiveRunState(status="failed", agent_name="author", ended_at=100.0)
    fline = strip_line(failed, now=220.0)
    assert "✗" in fline and "author" in fline and "Engine Room" in fline and "2m" in fline


def test_live_body_stream_not_attached_notice_after_restart_mid_run():
    s = seed_state([_run_started(), _call_started()], now=10.0)
    assert s.status == "running" and s.stream_attached is False
    assert "stream not attached" in live_body(s)


def test_seed_state_of_a_finished_run_is_not_stuck_running():
    fin = _ev(3, TelemetryEventType.AGENT_RUN_FINISHED,
              {"run_id": "r1", "agent_name": "author", "duration_s": 52.0})
    s = seed_state([_run_started(), _call_started(), fin], now=10.0)
    assert s.status == "finished"


def test_trace_line_formats_key_event_shapes():
    fin = _ev(3, TelemetryEventType.AGENT_RUN_FINISHED,
              {"run_id": "r1", "agent_name": "author", "duration_s": 52.0})
    assert "12:04:32" in trace_line(fin) and "author" in trace_line(fin) and "✓" in trace_line(fin)
    fail = _ev(4, TelemetryEventType.AGENT_RUN_FAILED,
               {"run_id": "r1", "agent_name": "editor", "error_type": "TimeoutError",
                "error_message": "x", "phase": "agent", "duration_s": 1.0})
    assert "✗" in trace_line(fail) and "TimeoutError" in trace_line(fail)
    picked = _ev(5, TelemetryEventType.SCHEDULER_PICKED, {"agent_name": "author"})
    assert "picked author" in trace_line(picked)


@given(st.lists(st.sampled_from([
    TelemetryEventType.AGENT_RUN_STARTED, TelemetryEventType.AGENT_RUN_FINISHED,
    TelemetryEventType.LLM_CALL_STARTED, TelemetryEventType.LLM_CALL_FINISHED,
    TelemetryEventType.SCHEDULER_PICKED, TelemetryEventType.SCHEDULER_ELIGIBILITY_CHANGED,
]), max_size=40))
def test_trace_replay_is_one_to_one_never_drops_or_duplicates(types):
    # Same invariant family as the causal-edge property: replay maps events
    # to rows 1:1 — a trace that dedupes or drops lies about the machinery.
    events = [_ev(i + 1, t, {"run_id": "r", "agent_name": "author", "eligible": True,
                             "reason": "ready", "call_index": 1, "model": "m", "prompt": "p",
                             "duration_s": 1.0, "output_tokens": 1, "error_type": "E",
                             "error_message": "m", "phase": "agent"})
              for i, t in enumerate(types)]
    lines = [trace_line(e) for e in events]
    assert len(lines) == len(events)
    assert all(isinstance(line, str) and line for line in lines)


def test_trace_detail_shows_prompt_and_produced_domain_events():
    call = _call_started()
    produced = [StoredEvent(sequence=9, id="d9", event_type="chapter.created",
                            aggregate_id="ch-12", payload={"title": "T"},
                            created_at="t", run_id="r1")]
    text = trace_detail(call, produced)
    assert "[system]\nWrite." in text
    assert "produced: chapter.created ch-12" in text
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/tui/test_engine_room_model.py -v`
Expected: FAIL with `ModuleNotFoundError` (no `engine_room_model`)

- [ ] **Step 3: Implement**

```python
# novelizer/tui/widgets/engine_room_model.py
"""Pure live-view state machine and formatters for the Engine Room.

No Textual imports: everything here is black-box testable. Widgets render
what these functions return; the app folds bus items through
apply_bus_item and re-renders.
"""
from __future__ import annotations
from dataclasses import dataclass, replace
from novelizer.canon.events import StoredEvent
from novelizer.telemetry.events import TelemetryEventType, TokenDelta

TEXT_CAP = 8000

_VERBS = {
    "author": "drafting",
    "editor": "reviewing",
    "world_architect": "worldbuilding",
    "character_keeper": "tending characters",
    "continuity_checker": "checking continuity",
    "retconner": "retconning",
    "structure_analyst": "scoring structure",
}


@dataclass(frozen=True)
class LiveRunState:
    status: str = "idle"  # idle | running | finished | failed
    run_id: str = ""
    agent_name: str = ""
    started_at: float = 0.0  # monotonic
    ended_at: float = 0.0
    tokens: int = 0
    text: str = ""
    prompt: str = ""
    model: str = ""
    call_index: int = 0
    error: str = ""
    stream_attached: bool = True


def apply_bus_item(state: LiveRunState, item, now: float) -> LiveRunState:
    if isinstance(item, TokenDelta):
        if state.status != "running" or item.run_id != state.run_id:
            return state
        text = (state.text + item.text)[-TEXT_CAP:]
        return replace(state, text=text, tokens=state.tokens + 1)
    if not isinstance(item, StoredEvent):
        return state
    p = item.payload
    et = item.event_type
    if et == TelemetryEventType.AGENT_RUN_STARTED:
        # A fresh run always starts attached: even if the previous state was a
        # seeded not-attached run, this event arriving live means we're live.
        return LiveRunState(status="running", run_id=p.get("run_id", ""),
                            agent_name=p.get("agent_name", ""), started_at=now)
    if p.get("run_id") != state.run_id:
        return state
    if et == TelemetryEventType.LLM_CALL_STARTED:
        return replace(state, prompt=p.get("prompt", ""), model=p.get("model", ""),
                       call_index=p.get("call_index", 0))
    if et == TelemetryEventType.LLM_CALL_FINISHED:
        return replace(state, tokens=p.get("output_tokens", state.tokens))
    if et == TelemetryEventType.AGENT_RUN_FINISHED:
        return replace(state, status="finished", ended_at=now)
    if et == TelemetryEventType.AGENT_RUN_FAILED:
        error = f"{p.get('error_type', '?')}: {p.get('error_message', '')}"
        return replace(state, status="failed", ended_at=now, error=error)
    return state


def seed_state(recent: list[StoredEvent], now: float) -> LiveRunState:
    state = LiveRunState()
    for ev in recent:
        state = apply_bus_item(state, ev, now)
    if state.status == "running":
        # We rebooted mid-run: the ephemeral token stream from before the
        # restart is gone — say so instead of pretending to stream.
        state = replace(state, stream_attached=False)
    return state


def _fmt_tokens(n: int) -> str:
    return f"{n / 1000:.1f}k tok" if n >= 1000 else f"{n} tok"


def _fmt_ago(seconds: float) -> str:
    s = int(seconds)
    return f"{s // 60}m" if s >= 60 else f"{s}s"


def strip_line(state: LiveRunState, now: float, next_hint: str = "") -> str:
    if state.status == "running":
        verb = _VERBS.get(state.agent_name, "working")
        elapsed = int(now - state.started_at)
        call = state.call_index or 1
        return (f"▶ {state.agent_name} · {verb} · {_fmt_tokens(state.tokens)} · "
                f"{elapsed}s · call {call}")
    if state.status == "failed":
        return (f"✗ {state.agent_name} crashed {_fmt_ago(now - state.ended_at)} ago "
                f"(see Engine Room)")
    return f"idle · {next_hint}" if next_hint else "idle"


def vitals_line(state: LiveRunState, now: float) -> str:
    if state.status == "running":
        verb = _VERBS.get(state.agent_name, "working")
        model = state.model or "?"
        return (f"{state.agent_name} · {verb} · {model} · call {state.call_index or 1} · "
                f"{_fmt_tokens(state.tokens)} · {int(now - state.started_at)}s")
    if state.status == "failed":
        return f"{state.agent_name} · crashed · {state.error}"
    if state.status == "finished":
        return f"{state.agent_name} · finished"
    return "idle — waiting for the scheduler"


def live_body(state: LiveRunState) -> str:
    if state.status == "running" and not state.stream_attached:
        return "run in progress (stream not attached — restarted mid-run)"
    if state.status == "idle":
        return "no run yet"
    if state.status == "failed" and state.text:
        return state.text + "\n\n✗ crashed"
    return state.text or "(waiting for first token…)"


def _t(ev: StoredEvent) -> str:
    return ev.created_at[11:19]


def trace_line(ev: StoredEvent) -> str:
    p = ev.payload
    et = ev.event_type
    if et == TelemetryEventType.AGENT_RUN_STARTED:
        return f"{_t(ev)} {p.get('agent_name', '?')} run started"
    if et == TelemetryEventType.AGENT_RUN_FINISHED:
        return f"{_t(ev)} {p.get('agent_name', '?')} run ✓ {p.get('duration_s', 0):.0f}s"
    if et == TelemetryEventType.AGENT_RUN_FAILED:
        return (f"{_t(ev)} {p.get('agent_name', '?')} run ✗ {p.get('error_type', '?')} "
                f"({p.get('phase', '?')})")
    if et == TelemetryEventType.LLM_CALL_STARTED:
        return (f"{_t(ev)} {p.get('agent_name', '?')} llm call {p.get('call_index', '?')} "
                f"started ({p.get('model', '?')})")
    if et == TelemetryEventType.LLM_CALL_FINISHED:
        return (f"{_t(ev)} {p.get('agent_name', '?')} llm call {p.get('call_index', '?')} "
                f"✓ {p.get('duration_s', 0):.0f}s · {p.get('output_tokens', 0)} tok")
    if et == TelemetryEventType.LLM_CALL_FAILED:
        return (f"{_t(ev)} {p.get('agent_name', '?')} llm call {p.get('call_index', '?')} "
                f"✗ {p.get('error_type', '?')}")
    if et == TelemetryEventType.SCHEDULER_PICKED:
        return f"{_t(ev)} scheduler picked {p.get('agent_name', '?')}"
    if et == TelemetryEventType.SCHEDULER_ELIGIBILITY_CHANGED:
        flag = "eligible" if p.get("eligible") else "ineligible"
        return f"{_t(ev)} {p.get('agent_name', '?')} {flag}: {p.get('reason', '?')}"
    return f"{_t(ev)} {et}"


def trace_detail(ev: StoredEvent, produced: list[StoredEvent]) -> str:
    lines = [trace_line(ev), ""]
    p = dict(ev.payload)
    prompt = p.pop("prompt", None)
    for k, v in p.items():
        lines.append(f"{k}: {v}")
    for d in produced:
        lines.append(f"produced: {d.event_type} {d.aggregate_id}")
    if prompt is not None:
        lines += ["", "─ prompt ─", prompt]
    return "\n".join(lines)
```

- [ ] **Step 4: Run tests to verify green**

Run: `uv run pytest tests/tui/test_engine_room_model.py -v`
Expected: PASS (all, including the Hypothesis 1:1 property)

- [ ] **Step 5: Commit**

```bash
git add novelizer/tui/widgets/engine_room_model.py tests/tui/test_engine_room_model.py
git commit -m "feat(tui): engine room model — pure live-state machine, strip/vitals/trace formatters"
```

---

### Task 13: ActivityStrip widget + app bus/refresh wiring

**Files:**
- Create: `novelizer/tui/widgets/activity_strip.py`
- Modify: `novelizer/tui/app.py`, `novelizer/tui/app.tcss`
- Test: `tests/tui/test_engine_room.py` (first tests)

**Interfaces:**
- Consumes: model functions (Task 12), `runtime.telemetry_bus`, `runtime.telemetry_store` (Task 10), `scheduler.status()["next_ready_in"]` (Task 7).
- Produces: `ActivityStrip(Static)` with `render_state(state, now, next_hint)`; app state `self._live_state: LiveRunState` + `self._trace_events: deque[StoredEvent]` (maxlen 200); workers `_telemetry_bus_loop` (consumes the bus queue: folds items into `_live_state`, appends `StoredEvent`s to `_trace_events`, refreshes strip; seeds both from `telemetry_store` first) and `_telemetry_refresh_loop` (0.5 s: re-renders strip so elapsed/countdown tick). Helper `_next_hint()` derives `next: <agent> in <n>s` from `scheduler.status()`. Strip docked between `#statusbar` and `#command`.

- [ ] **Step 1: Write the failing tests**

```python
# tests/tui/test_engine_room.py
import os
import tempfile
import pytest
from novelizer.settings import EffectiveSettings as Settings
from novelizer.runtime import Runtime
from novelizer.tui.app import NovelizerApp
from novelizer.tui.widgets.activity_strip import ActivityStrip
from novelizer.telemetry.events import (
    TelemetryEventType, AgentRunStarted, AgentRunFailed, TokenDelta,
)
from novelizer.agents.schemas import (
    WorldEntriesDraft, KeeperOutput, EditorVerdict,
    ContinuityOutput, RetconAmendments, StructureAnalystOutput,
)
from novelizer.agents.base import ChapterDraft


class _R:
    def __init__(self, out):
        self._out = out

    async def ainvoke(self, inputs):
        return {"structured_response": self._out}


def _runners():
    return {
        "world_architect": _R(WorldEntriesDraft(entries=[])),
        "author": _R(ChapterDraft(title="T", prose="P")),
        "character_keeper": _R(KeeperOutput()),
        "editor": _R(EditorVerdict(verdict="approve", notes="ok")),
        "continuity_checker": _R(ContinuityOutput()),
        "continuity_checker_mining": _R(None),
        "retconner": _R(RetconAmendments()),
        "structure_analyst": _R(StructureAnalystOutput()),
    }


@pytest.fixture
async def rt(tmp_path):
    settings = Settings(db_path=str(tmp_path / "world.db"),
                        author_interval=3600, default_agent_interval=3600,
                        continuity_interval=3600, projector_interval=0.1)
    runtime = Runtime(settings, runners=_runners())
    await runtime.start()
    # Long intervals: agents were never marked ran, so first tick would run one.
    # Pause them all so tests drive telemetry by hand.
    for a in runtime.agents:
        a.pause()
    yield runtime
    await runtime.close()


async def test_strip_shows_live_run_from_bus_traffic(rt):
    app = NovelizerApp(rt)
    async with app.run_test(size=(120, 40)) as pilot:
        await rt.telemetry.emit(TelemetryEventType.AGENT_RUN_STARTED, "r1",
                                AgentRunStarted(run_id="r1", agent_name="author"))
        rt.telemetry.publish_token(TokenDelta(run_id="r1", agent_name="author", text="The sea"))
        await pilot.pause(0.8)
        strip = app.query_one("#activity_strip", ActivityStrip)
        text = str(strip.renderable)
        assert "▶" in text and "author" in text and "drafting" in text


async def test_strip_shows_crash_until_next_run(rt):
    app = NovelizerApp(rt)
    async with app.run_test(size=(120, 40)) as pilot:
        await rt.telemetry.emit(TelemetryEventType.AGENT_RUN_STARTED, "r1",
                                AgentRunStarted(run_id="r1", agent_name="author"))
        await rt.telemetry.emit(TelemetryEventType.AGENT_RUN_FAILED, "r1",
                                AgentRunFailed(run_id="r1", agent_name="author",
                                               error_type="TimeoutError", error_message="proxy",
                                               phase="llm_call", duration_s=4.0))
        await pilot.pause(0.8)
        text = str(app.query_one("#activity_strip", ActivityStrip).renderable)
        assert "✗" in text and "author" in text and "Engine Room" in text


async def test_strip_idle_shows_next_agent_hint(rt):
    app = NovelizerApp(rt)
    async with app.run_test(size=(120, 40)) as pilot:
        await pilot.pause(0.8)
        text = str(app.query_one("#activity_strip", ActivityStrip).renderable)
        assert text.startswith("idle")
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/tui/test_engine_room.py -v`
Expected: FAIL with `ModuleNotFoundError` (no `activity_strip`)

- [ ] **Step 3: Implement**

```python
# novelizer/tui/widgets/activity_strip.py
from __future__ import annotations
from textual.widgets import Static
from novelizer.tui.widgets.engine_room_model import LiveRunState, strip_line


class ActivityStrip(Static):
    """One-line ambient machinery status docked in Mission Control."""

    def render_state(self, state: LiveRunState, now: float, next_hint: str = "") -> None:
        self.update(strip_line(state, now, next_hint))
```

In `novelizer/tui/app.py`:

Add imports:

```python
import time
from collections import deque
from novelizer.tui.widgets.activity_strip import ActivityStrip
from novelizer.tui.widgets.engine_room_model import (
    LiveRunState, apply_bus_item, seed_state,
)
```

In `__init__`, after `self.messages = []`:

```python
        self._live_state = LiveRunState()
        self._trace_events: deque = deque(maxlen=200)
```

In `compose()`, between the `#statusbar` Static and the `#command` Input:

```python
        yield ActivityStrip("idle", id="activity_strip")
```

In `on_mount()`, add two workers:

```python
        self.run_worker(self._telemetry_bus_loop(), exclusive=False)
        self.run_worker(self._telemetry_refresh_loop(), exclusive=False)
```

Add the workers and helpers:

```python
    def _next_hint(self) -> str:
        try:
            rows = [r for r in self.runtime.scheduler.status() if not r["paused"]]
            if not rows:
                return ""
            soonest = min(rows, key=lambda r: r["next_ready_in"])
            return f"next: {soonest['name']} in {int(soonest['next_ready_in'])}s"
        except Exception:
            return ""

    def _refresh_strip(self) -> None:
        strip = self.query_one("#activity_strip", ActivityStrip)
        strip.render_state(self._live_state, time.monotonic(), self._next_hint())

    async def _telemetry_bus_loop(self) -> None:
        # Seed from the durable log first so a restart never shows a blank view.
        try:
            recent = await self.runtime.telemetry_store.events_since(0)
            self._trace_events.extend(recent[-200:])
            self._live_state = seed_state(recent[-50:], time.monotonic())
            self._refresh_strip()
        except Exception as e:
            self._report_worker_error("telemetry-seed", e)
        q = self.runtime.telemetry_bus.subscribe()
        while True:
            try:
                item = await q.get()
                self._live_state = apply_bus_item(self._live_state, item, time.monotonic())
                if isinstance(item, StoredEvent):
                    self._trace_events.append(item)
                self._refresh_strip()
            except Exception as e:
                self._report_worker_error("telemetry", e)

    async def _telemetry_refresh_loop(self) -> None:
        while True:
            try:
                self._refresh_strip()
            except Exception as e:
                self._report_worker_error("telemetry-refresh", e)
            await asyncio.sleep(0.5)
```

In `novelizer/tui/app.tcss` add:

```css
#activity_strip { height: 1; background: $boost; color: $text; padding: 0 1; }
```

- [ ] **Step 4: Run tests to verify green**

Run: `uv run pytest tests/tui/test_engine_room.py tests/tui/test_app_smoke.py tests/tui/test_app_layout.py -v`
Expected: PASS (strip tests green; existing layout/smoke tests still green)

- [ ] **Step 5: Commit**

```bash
git add novelizer/tui tests/tui/test_engine_room.py
git commit -m "feat(tui): Mission Control activity strip fed by the telemetry bus"
```

---

### Task 14: EngineRoom view — live stream + vitals + prompt toggle

**Files:**
- Create: `novelizer/tui/widgets/engine_room.py`
- Modify: `novelizer/tui/app.py`, `novelizer/tui/app.tcss`
- Test: `tests/tui/test_engine_room.py` (additions)

**Interfaces:**
- Consumes: model (Task 12), app state from Task 13.
- Produces: `EngineRoom(Vertical)` composing `#er_vitals` (Static, 1 line), `#er_stream` (Static inside a `VerticalScroll` `#er_stream_scroll` — a RichLog would render one line per token `write()`, so the stream body is a Static updated with the model's full tail text and scrolled to end), `#er_prompt` (Static, hidden by default), `#er_trace` (DataTable, Task 15 fills it), `#er_detail` (Static, hidden until a row is selected). Methods: `render_live(state, now=None)` (vitals + body; body re-write only on content change), `stream_text() -> str` (current rendered body, for tests), `toggle_prompt() -> bool` (returns new visibility), `set_trace_rows(rows)`, `show_detail(text)`. App: `e` binding `action_toggle_engine` (toggles `engine` class on `#body`); `p` binding `action_toggle_prompt` (only acts when engine view visible); the bus loop calls `render_live` after folding each item.

- [ ] **Step 1: Write the failing tests** (append to `tests/tui/test_engine_room.py`)

```python
async def test_engine_room_hidden_by_default_and_toggles_with_e(rt):
    app = NovelizerApp(rt)
    async with app.run_test(size=(120, 40)) as pilot:
        app.set_focus(None)  # keys must reach app bindings, not a focused widget
        body = app.query_one("#body")
        assert not body.has_class("engine")
        await pilot.press("e")
        assert body.has_class("engine")
        await pilot.press("e")
        assert not body.has_class("engine")


async def test_engine_room_streams_tokens_into_live_pane(rt):
    from novelizer.tui.widgets.engine_room import EngineRoom
    app = NovelizerApp(rt)
    async with app.run_test(size=(120, 40)) as pilot:
        app.set_focus(None)
        await pilot.press("e")
        await rt.telemetry.emit(TelemetryEventType.AGENT_RUN_STARTED, "r1",
                                AgentRunStarted(run_id="r1", agent_name="author"))
        rt.telemetry.publish_token(TokenDelta(run_id="r1", agent_name="author", text="The sea rose."))
        await pilot.pause(0.8)
        room = app.query_one("#engine_room", EngineRoom)
        vitals = str(app.query_one("#er_vitals").renderable)
        assert "author" in vitals and "drafting" in vitals
        assert "The sea rose." in room.stream_text()


async def test_prompt_pane_off_by_default_and_p_toggles_it(rt):
    from novelizer.telemetry.events import LlmCallStarted
    app = NovelizerApp(rt)
    async with app.run_test(size=(120, 40)) as pilot:
        app.set_focus(None)
        await pilot.press("e")
        await rt.telemetry.emit(TelemetryEventType.AGENT_RUN_STARTED, "r1",
                                AgentRunStarted(run_id="r1", agent_name="author"))
        await rt.telemetry.emit(TelemetryEventType.LLM_CALL_STARTED, "r1",
                                LlmCallStarted(run_id="r1", agent_name="author", call_index=1,
                                               model="qwen", prompt="[system]\nWrite the chapter."))
        await pilot.pause(0.8)
        prompt_pane = app.query_one("#er_prompt")
        assert prompt_pane.display is False  # off by default (spec)
        await pilot.press("p")
        assert prompt_pane.display is True
        assert "Write the chapter." in str(prompt_pane.renderable)
        await pilot.press("p")
        assert prompt_pane.display is False


async def test_p_outside_engine_view_does_nothing(rt):
    app = NovelizerApp(rt)
    async with app.run_test(size=(120, 40)) as pilot:
        app.set_focus(None)
        await pilot.press("p")  # engine view not open: must not crash or toggle
        assert app.query_one("#er_prompt").display is False
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/tui/test_engine_room.py -v`
Expected: new tests FAIL (`ModuleNotFoundError: engine_room`, no `e` binding).

- [ ] **Step 3: Implement**

```python
# novelizer/tui/widgets/engine_room.py
from __future__ import annotations
import time
from textual.app import ComposeResult
from textual.containers import Vertical, VerticalScroll
from textual.widgets import DataTable, Static
from novelizer.tui.widgets.engine_room_model import LiveRunState, live_body, vitals_line


class EngineRoom(Vertical):
    """The thick machinery view: live vitals + token stream on top, the
    durable trace below (rows filled by the app), prompt pane toggleable
    with `p` (off by default).

    The stream body is a Static inside a VerticalScroll, not a RichLog: a
    RichLog renders one line per write() call, which would put every
    streamed token on its own line."""

    _rendered_body: str = ""

    def compose(self) -> ComposeResult:
        yield Static("idle — waiting for the scheduler", id="er_vitals")
        with VerticalScroll(id="er_stream_scroll"):
            yield Static("", id="er_stream")
        yield Static("", id="er_prompt")
        yield DataTable(id="er_trace", cursor_type="row")
        yield Static("", id="er_detail")

    def on_mount(self) -> None:
        self.query_one("#er_prompt", Static).display = False
        self.query_one("#er_detail", Static).display = False
        table = self.query_one("#er_trace", DataTable)
        table.add_column("machinery", key="line", width=110)

    # -- live pane -----------------------------------------------------------

    def render_live(self, state: LiveRunState, now: float | None = None) -> None:
        now = time.monotonic() if now is None else now
        self.query_one("#er_vitals", Static).update(vitals_line(state, now))
        body = live_body(state)
        if body != self._rendered_body:
            self.query_one("#er_stream", Static).update(body)
            self._rendered_body = body
            self.query_one("#er_stream_scroll", VerticalScroll).scroll_end(animate=False)
        self.query_one("#er_prompt", Static).update(state.prompt or "(no call in flight)")

    def stream_text(self) -> str:
        return self._rendered_body

    def toggle_prompt(self) -> bool:
        pane = self.query_one("#er_prompt", Static)
        pane.display = not pane.display
        return pane.display

    # -- trace pane (rows managed by the app; see Task 15) --------------------

    def set_trace_rows(self, rows: list[tuple[str, str]]) -> None:
        """rows: (row_key, rendered_line), newest first."""
        table = self.query_one("#er_trace", DataTable)
        table.clear()
        for key, line in rows:
            table.add_row(line, key=key)

    def show_detail(self, text: str) -> None:
        detail = self.query_one("#er_detail", Static)
        detail.update(text)
        detail.display = True
```

In `novelizer/tui/app.py`:

- Import: `from novelizer.tui.widgets.engine_room import EngineRoom`.
- `BINDINGS` — add `("e", "toggle_engine", "Engine Room")` and `("p", "toggle_prompt", "Prompt")`.
- In `compose()`, inside the left `Vertical`, after `yield Causeway(...)`:

```python
                yield EngineRoom(id="engine_room")
```

- Actions:

```python
    def action_toggle_engine(self) -> None:
        self.query_one("#body").toggle_class("engine")

    def action_toggle_prompt(self) -> None:
        if self.query_one("#body").has_class("engine"):
            self.query_one("#engine_room", EngineRoom).toggle_prompt()
```

- In `_telemetry_bus_loop`, after `self._refresh_strip()` add one engine-room line inside the loop:

```python
                self.query_one("#engine_room", EngineRoom).render_live(self._live_state)
```

  Also render once after the seed block: `self.query_one("#engine_room", EngineRoom).render_live(self._live_state)`. (`render_live` diffs against its last body, so token deltas cost one Static update each and non-content items cost nothing.)

In `novelizer/tui/app.tcss` add:

```css
#engine_room { display: none; }
#body.engine #engine_room { display: block; height: 1fr; border: round $primary; }
#body.engine #feed, #body.engine #roster, #body.engine #proposals,
#body.engine #thread_board, #body.engine #story_shape,
#body.engine #who_knows_what, #body.engine #causeway { display: none; }
#er_vitals { height: 1; background: $boost; padding: 0 1; }
#er_stream_scroll { height: 2fr; }
#er_stream { height: auto; }
#er_prompt { height: auto; max-height: 12; border: round $secondary; padding: 0 1; }
#er_trace { height: 1fr; }
#er_detail { height: auto; max-height: 14; border: round $secondary; padding: 0 1; }
```

- [ ] **Step 4: Run tests to verify green**

Run: `uv run pytest tests/tui -v`
Expected: PASS (all TUI tests, old and new)

- [ ] **Step 5: Commit**

```bash
git add novelizer/tui tests/tui/test_engine_room.py
git commit -m "feat(tui): Engine Room view — live stream, vitals, off-by-default prompt toggle"
```

---

### Task 15: Trace pane rows + drill-in detail

**Files:**
- Modify: `novelizer/tui/app.py` (trace row refresh + row-selected handler)
- Test: `tests/tui/test_engine_room.py` (additions)

**Interfaces:**
- Consumes: `self._trace_events` deque (Task 13), `EngineRoom.set_trace_rows`/`show_detail` (Task 14), `trace_line`/`trace_detail` (Task 12), `EventStore.events_for_run` (Task 3).
- Produces: bus loop refreshes trace rows (newest first, key = str(sequence)) whenever a `StoredEvent` arrives (and once after seeding); `on_data_table_row_selected` handler looks up the event by sequence, fetches `runtime.events.events_for_run(run_id)` for its produced-domain-events list, and calls `show_detail(trace_detail(ev, produced))`.

- [ ] **Step 1: Write the failing tests** (append to `tests/tui/test_engine_room.py`)

```python
async def test_trace_rows_appear_newest_first(rt):
    from textual.widgets import DataTable
    from novelizer.telemetry.events import AgentRunFinished
    app = NovelizerApp(rt)
    async with app.run_test(size=(120, 40)) as pilot:
        app.set_focus(None)
        await pilot.press("e")
        await rt.telemetry.emit(TelemetryEventType.AGENT_RUN_STARTED, "r1",
                                AgentRunStarted(run_id="r1", agent_name="author"))
        await rt.telemetry.emit(TelemetryEventType.AGENT_RUN_FINISHED, "r1",
                                AgentRunFinished(run_id="r1", agent_name="author", duration_s=52.0))
        await pilot.pause(0.8)
        table = app.query_one("#er_trace", DataTable)
        assert table.row_count == 2
        first_row = table.get_row_at(0)
        assert "✓" in first_row[0]  # newest (run finished) first


async def test_selecting_a_trace_row_shows_detail_with_prompt_and_produced(rt):
    from textual.widgets import DataTable
    from novelizer.telemetry.events import LlmCallStarted
    from novelizer.canon.events import EventType, AgentRemark
    from novelizer.run_context import current_run_id
    app = NovelizerApp(rt)
    async with app.run_test(size=(120, 40)) as pilot:
        # A domain event stamped with the run, so detail can show "produced:"
        token = current_run_id.set("r1")
        try:
            await rt.committer.commit("author", EventType.AGENT_REMARKED, "author",
                                      AgentRemark(agent_name="author", note="done"))
        finally:
            current_run_id.reset(token)
        await rt.telemetry.emit(TelemetryEventType.LLM_CALL_STARTED, "r1",
                                LlmCallStarted(run_id="r1", agent_name="author", call_index=1,
                                               model="qwen", prompt="[system]\nWrite it."))
        await pilot.pause(0.8)
        app.set_focus(None)
        await pilot.press("e")
        table = app.query_one("#er_trace", DataTable)
        table.focus()
        await pilot.press("enter")
        await pilot.pause(0.3)
        detail = app.query_one("#er_detail")
        assert detail.display is True
        text = str(detail.renderable)
        assert "Write it." in text                       # stored prompt round-trips (C-in-D)
        assert "produced: agent.remarked author" in text  # run_id join to domain log


async def test_seeded_trace_survives_restart(rt):
    from textual.widgets import DataTable
    await rt.telemetry.emit(TelemetryEventType.AGENT_RUN_STARTED, "r1",
                            AgentRunStarted(run_id="r1", agent_name="author"))
    # A fresh app instance (a "restart") must show the persisted trace row.
    app = NovelizerApp(rt)
    async with app.run_test(size=(120, 40)) as pilot:
        app.set_focus(None)
        await pilot.press("e")
        await pilot.pause(0.8)
        assert app.query_one("#er_trace", DataTable).row_count == 1
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/tui/test_engine_room.py -v`
Expected: new tests FAIL (`row_count == 0` — nothing fills the table).

- [ ] **Step 3: Implement in `novelizer/tui/app.py`**

Add a refresh helper and call it from the bus loop:

```python
    def _refresh_trace(self) -> None:
        rows = [(str(ev.sequence), trace_line(ev)) for ev in reversed(self._trace_events)]
        self.query_one("#engine_room", EngineRoom).set_trace_rows(rows)
```

Import `trace_line`, `trace_detail` from `engine_room_model`. In `_telemetry_bus_loop`: call `self._refresh_trace()` right after the seed block, and inside the loop after `self._trace_events.append(item)`.

Add the row-selected handler (DataTable events bubble to the app; the existing `on_tree_node_selected` shows the pattern):

```python
    async def on_data_table_row_selected(self, event) -> None:
        if event.data_table.id != "er_trace":
            return
        seq = int(event.row_key.value)
        ev = next((e for e in self._trace_events if e.sequence == seq), None)
        if ev is None:
            return
        run_id = ev.payload.get("run_id")
        produced = await self.runtime.events.events_for_run(run_id) if run_id else []
        self.query_one("#engine_room", EngineRoom).show_detail(trace_detail(ev, produced))
```

- [ ] **Step 4: Run tests to verify green**

Run: `uv run pytest tests/tui -v` then `uv run pytest`
Expected: PASS (everything)

- [ ] **Step 5: Commit**

```bash
git add novelizer/tui/app.py tests/tui/test_engine_room.py
git commit -m "feat(tui): durable trace pane with drill-in detail (prompt + produced events)"
```

---

### Task 16: Live smoke — telemetry round-trip against the real endpoint

**Files:**
- Create: `tests/agents/test_telemetry_live_llm.py`

**Interfaces:**
- Consumes: full wiring (Tasks 1-10). Follows the existing live-smoke convention: marked `live_llm`, run explicitly with `uv run pytest -m live_llm tests/agents/test_telemetry_live_llm.py -v`, requires the configured OpenAI-compatible endpoint (`load_effective_settings().llm_base_url`).

- [ ] **Step 1: Write the test**

```python
# tests/agents/test_telemetry_live_llm.py
"""Live telemetry smoke (spec: Testing / Live smoke): run one real Author
turn and assert the telemetry log contains a completed run with nonzero
token vitals and a prompt payload that round-trips.

Requires the configured OpenAI-compatible LLM endpoint
(`load_effective_settings().llm_base_url`) to be reachable. Run explicitly:
uv run pytest -m live_llm tests/agents/test_telemetry_live_llm.py -v
"""
import os
import tempfile
import pytest
from novelizer.settings import load_effective_settings
from novelizer.canon.event_store import EventStore
from novelizer.canon.projector import Projector
from novelizer.canon.read_store import ReadStore
from novelizer.canon.committer import Committer
from novelizer.agents.author import Author, build_author_runner
from novelizer.telemetry.bus import TelemetryBus
from novelizer.telemetry.recorder import TelemetryRecorder
from novelizer.telemetry.callbacks import TelemetryCallbackHandler
from novelizer.telemetry.events import TelemetryEventType, TokenDelta

pytestmark = pytest.mark.live_llm


async def test_one_real_author_run_lands_full_telemetry_with_round_tripping_prompt():
    live = load_effective_settings()
    fd, dpath = tempfile.mkstemp(suffix=".db"); os.close(fd)
    fd, tpath = tempfile.mkstemp(suffix=".db"); os.close(fd)
    domain = EventStore(dpath); await domain.init()
    proj = Projector(domain, dpath); await proj.init()
    read = ReadStore(dpath); await read.init()
    tel_store = EventStore(tpath); await tel_store.init()
    bus = TelemetryBus()
    recorder = TelemetryRecorder(tel_store, bus)
    tokens_seen = bus.subscribe()
    handler = TelemetryCallbackHandler(recorder)
    author = Author(
        build_author_runner(live, callbacks=[handler]), read, Committer(domain),
        interval=0,
    )
    author.telemetry = recorder
    try:
        await author.run_once()

        tel = await tel_store.events_since(0)
        by_type = {}
        for e in tel:
            by_type.setdefault(e.event_type, []).append(e)

        assert TelemetryEventType.AGENT_RUN_STARTED in by_type
        assert TelemetryEventType.AGENT_RUN_FINISHED in by_type
        run_id = by_type[TelemetryEventType.AGENT_RUN_STARTED][0].payload["run_id"]

        call_started = by_type[TelemetryEventType.LLM_CALL_STARTED][0]
        assert call_started.payload["run_id"] == run_id
        # Prompt payload round-trips: persisted, non-empty, and contains the
        # Author's actual context framing (not a placeholder).
        prompt = call_started.payload["prompt"]
        assert len(prompt) > 100 and "chapter" in prompt.lower()

        finished = by_type[TelemetryEventType.LLM_CALL_FINISHED][0]
        assert finished.payload["output_tokens"] > 0
        assert finished.payload["duration_s"] > 0.0

        # Streaming really streamed: at least one TokenDelta hit the bus.
        deltas = []
        while not tokens_seen.empty():
            item = tokens_seen.get_nowait()
            if isinstance(item, TokenDelta):
                deltas.append(item)
        assert deltas, "expected live token deltas on the bus (streaming enabled)"

        # Correlation: the chapter the run produced carries the run's id.
        dom = await domain.events_since(0)
        assert any(e.run_id == run_id for e in dom)
    finally:
        await read.close(); await proj.close(); await domain.close(); await tel_store.close()
        os.unlink(dpath); os.unlink(tpath)
```

- [ ] **Step 2: Run the default suite to confirm the smoke is excluded**

Run: `uv run pytest tests/agents/test_telemetry_live_llm.py`
Expected: `deselected` under the default marker filter (matches other `live_llm` tests' behavior).

- [ ] **Step 3: Run live if the endpoint is up (optional but do attempt it)**

Run: `uv run pytest -m live_llm tests/agents/test_telemetry_live_llm.py -v`
Expected: PASS when the endpoint is reachable. If the endpoint is down, record that in the commit message body (the M4 precedent: CI-proven, live smoke deferred honestly).

- [ ] **Step 4: Commit**

```bash
git add tests/agents/test_telemetry_live_llm.py
git commit -m "test(telemetry): live smoke — real run round-trips prompt, vitals, correlation"
```

---

### Task 17: Final verification + docs touch

**Files:**
- Modify: `docs/MILESTONES.md` (only if the team files this under a milestone — otherwise skip), `README.md` (keybindings section if one exists)

- [ ] **Step 1: Full suite**

Run: `uv run pytest`
Expected: PASS, zero failures, live tests deselected.

- [ ] **Step 2: Manual sanity (if an endpoint is configured)**

Run: `uv run novelizer` against a scratch story; verify: strip shows `idle · next: …`, then a live `▶ author · drafting…` line with ticking tokens; `e` opens the Engine Room with the stream; `p` toggles the prompt; the trace fills; restart the app mid-run and confirm the "stream not attached" notice; delete `telemetry.db` and confirm the story still opens (disposability contract).

- [ ] **Step 3: Commit any doc updates**

```bash
git add -A
git commit -m "docs: engine room & telemetry — keybindings and milestone notes"
```

---

## Self-Review Notes (already applied)

- **Spec coverage:** telemetry store + event table (T1/T5/T10), bus + never-persisted tokens (T2/T5/T8), correlation (T3/T4/T6/T11), scheduler reasons throttled to changes (T7), prompt persisted and inspectable live + in trace (T1/T8/T14/T15), strip forms incl. crash-held-until-next-run (T12/T13 — the crash line persists because only the next `agent.run_started` changes status), Engine Room layout + `p` off-by-default (T14), trace newest-first with drill-in + `produced:` join (T15), restart reconstruction (T12 `seed_state` + T15 test), warn-and-drop with degraded-but-alive bus (T5), empty states (T12 `live_body`/`vitals_line` idle forms; empty DataTable is the empty trace state), disposability (no domain code reads `telemetry.db`; T17 manual check), all six test families from the spec (bus T2, instrumentation T6/T7, correlation property T11, trace 1:1 property T12, TUI pilot T13-T15, live smoke T16).
- **Out of scope honored:** no retention pruning, no multi-stream view, no cost accounting.
- **Known judgment calls:** `AgentRunFailed.phase` is derived from the recorder's open-call set (accurate for the llm-vs-agent distinction the spec asks for); `attempt` in the spec's strip mock is rendered as `call N` (call_index) since no retry loop exists; eligibility "readiness 0" is only observable on ticks where scoring ran, which matches what the scheduler actually knows.
```
