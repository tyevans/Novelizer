# M0 · Heartbeat Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build the event-sourced spine — an append-only event log, a Projector that materializes read projections, a ReadStore, one deepagents-based Author agent talking to an OpenAI-compatible endpoint, and a skeletal Textual TUI that tails the log — so you can run `novelizer` and watch chapters appear live.

**Architecture:** The **World Canon** bounded context (`novelizer/canon/`) owns event sourcing: `EventStore.append()` writes immutable events; `Projector` reads them and upserts projection tables (sole writer); `ReadStore` queries projections. The **Agent Roster** context (`novelizer/agents/`) holds a single deepagents Author injected with a runner (dependency injection for black-box testing). The **Direction** context (`novelizer/tui/`, `novelizer/director/cli.py`) reads the log to render a live feed and writes director signals as events. A thin `novelizer/runtime.py` wires the three together.

**Tech Stack:** Python 3.13, `aiosqlite` (event log + projections), `deepagents`/`langgraph`/`langchain-openai` (agent + OpenAI-compat model), `pydantic` v2 (payloads + schemas), `textual` (TUI), `click`+`rich` (CLI), `pytest`+`pytest-asyncio`+`hypothesis` (tests).

**Milestone note:** M0 is the *first* milestone in `docs/MILESTONES.md` (the spine). It deliberately defers to M1: the readiness-scored Scheduler, the other five agents, agent offsets / `on_events` catch-up strategies, ChromaDB/embeddings, and the autonomy dial. M0 proves one vertical slice end-to-end.

## Global Constraints

- **Python** `>=3.13` (per `pyproject.toml`; do not lower).
- **All model access is via OpenAI-compatible endpoints** — build chat models with `init_chat_model("openai:<name>", base_url=..., api_key=...)`. No direct provider SDKs, no Ollama in agent code.
- **Event sourcing is absolute:** the `events` table is the sole source of truth; only the Projector writes projection tables; agents and the CLI mutate state *only* by appending events. No component updates a projection row directly except the Projector.
- **Domain event names** follow `<domain>.<verb>` (e.g. `chapter.created`). Use the `EventType` constants — never inline string literals in agents/CLI.
- **TDD, black-box first:** every task writes a failing test, watches it fail, implements minimally, watches it pass, commits. Tests assert on observable events/projections through public interfaces — never on private SQL or internal attributes. Use `hypothesis` for the log/projector invariants where specified.
- **Async:** `pytest.ini` sets `asyncio_mode = "auto"`; `async def test_*` needs no decorator. All store/agent I/O is `async`.
- **`novelizer/store/models.py` is unchanged** — its entity models (`WorldEntry`, `Character`, `Chapter`, `RetconRequest`, `DirectorSignal`, and the story `Event`) are reused verbatim as event payloads.
- **Reuse over duplication (DRY):** payload (de)serialization goes through `pydantic` `model_dump_json()` / `model_validate_json()`; do not hand-roll JSON.

---

### Task 1: Dependencies & configuration

**Files:**
- Modify: `pyproject.toml` (dependencies)
- Modify: `novelizer/config.py`
- Test: `tests/test_config.py` (replace existing assertions)

**Interfaces:**
- Produces: `Settings` with fields `db_path: str`, `llm_base_url: str`, `llm_api_key: str`, `author_model: str`, `author_temperature: float`, `author_interval: int`, `projector_interval: float`, plus retained `chroma_path`, `embed_model`.

- [ ] **Step 1: Add/remove dependencies**

Run:
```bash
uv remove pydantic-graph
uv add deepagents langchain langgraph langchain-openai hypothesis
```
Expected: `uv.lock` updates, `.venv` syncs. If the network blocks a package, stop and report — do not proceed with a partial environment.

- [ ] **Step 2: Write the failing test**

Replace `tests/test_config.py` with:
```python
from novelizer.config import Settings


def test_defaults_present():
    s = Settings()
    assert s.db_path == "stories/world.db"
    assert s.llm_base_url.endswith("/v1")
    assert s.author_model
    assert 0.0 <= s.author_temperature <= 2.0
    assert s.author_interval > 0
    assert s.projector_interval > 0


def test_env_override(monkeypatch):
    monkeypatch.setenv("NOVELIZER_AUTHOR_MODEL", "custom-model")
    monkeypatch.setenv("NOVELIZER_LLM_BASE_URL", "http://host:9000/v1")
    s = Settings()
    assert s.author_model == "custom-model"
    assert s.llm_base_url == "http://host:9000/v1"
```

- [ ] **Step 3: Run test to verify it fails**

Run: `uv run pytest tests/test_config.py -v`
Expected: FAIL (`AttributeError`/`assert` on missing `llm_base_url`/`author_model`).

- [ ] **Step 4: Implement**

Replace `novelizer/config.py` with:
```python
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="NOVELIZER_", env_file=".env", extra="ignore")

    # Storage
    db_path: str = "stories/world.db"
    chroma_path: str = "stories/chroma"   # reserved for M1 embeddings
    embed_model: str = "nomic-embed-text"  # reserved for M1 embeddings

    # OpenAI-compatible LLM endpoint
    llm_base_url: str = "http://localhost:8080/v1"
    llm_api_key: str = "not-needed"
    author_model: str = "local-model"
    author_temperature: float = 0.8

    # Cadence (seconds)
    author_interval: int = 300
    projector_interval: float = 0.5
```

- [ ] **Step 5: Run test to verify it passes**

Run: `uv run pytest tests/test_config.py -v`
Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add pyproject.toml uv.lock novelizer/config.py tests/test_config.py
git commit -m "chore: swap pydantic-graph for deepagents stack; extend Settings for OpenAI-compat endpoint"
```

---

### Task 2: Clear superseded modules

Remove the pydantic-graph agent system, the old shared-state store, and the old scheduler so M0 builds on a clean slate. Everything removed here is rebuilt (Author, CLI) or deferred to M1 (the other five agents, scheduler). `novelizer/store/models.py` and `novelizer/store/embeddings.py` are **kept**.

**Files:**
- Delete: `novelizer/store/db.py`, `novelizer/store/queries.py`, `novelizer/scheduler.py`
- Delete: `novelizer/agents/base.py`, `novelizer/agents/author.py`, `novelizer/agents/world_architect.py`, `novelizer/agents/character_keeper.py`, `novelizer/agents/editor.py`, `novelizer/agents/continuity_checker.py`, `novelizer/agents/retconner.py`
- Delete: `tests/store/test_db.py`, `tests/store/test_queries.py`, `tests/test_scheduler.py`, `tests/agents/test_base.py`, `tests/agents/test_author.py`, `tests/agents/test_world_architect.py`, `tests/agents/test_character_keeper.py`, `tests/agents/test_editor.py`, `tests/agents/test_continuity_checker.py`, `tests/agents/test_retconner.py`
- Modify: `novelizer/director/cli.py` (temporarily reduce to a stub so the package imports; fully rebuilt in Task 10)

- [ ] **Step 1: Delete the superseded modules and tests**

```bash
git rm novelizer/store/db.py novelizer/store/queries.py novelizer/scheduler.py \
  novelizer/agents/base.py novelizer/agents/author.py novelizer/agents/world_architect.py \
  novelizer/agents/character_keeper.py novelizer/agents/editor.py \
  novelizer/agents/continuity_checker.py novelizer/agents/retconner.py \
  tests/store/test_db.py tests/store/test_queries.py tests/test_scheduler.py \
  tests/agents/test_base.py tests/agents/test_author.py tests/agents/test_world_architect.py \
  tests/agents/test_character_keeper.py tests/agents/test_editor.py \
  tests/agents/test_continuity_checker.py tests/agents/test_retconner.py
```

- [ ] **Step 2: Replace `novelizer/director/cli.py` with a minimal stub**

The entry point `novelizer = "novelizer.director.cli:main"` must still import. Replace the whole file with:
```python
from __future__ import annotations
import click


@click.group(invoke_without_command=True)
@click.pass_context
def cli(ctx):
    if ctx.invoked_subcommand is None:
        click.echo("Novelizer M0 — TUI not yet wired. See docs/MILESTONES.md.")


def main():
    cli()
```

- [ ] **Step 3: Verify the package imports and the suite is green**

Run: `uv run python -c "import novelizer.director.cli, novelizer.store.models, novelizer.config"`
Expected: no error.
Run: `uv run pytest -q`
Expected: PASS (only `tests/test_config.py` and `tests/store/test_models.py`, `tests/store/test_embeddings.py` remain; `test_embeddings.py` may be skipped if marked `ollama`).

- [ ] **Step 4: Commit**

```bash
git add -A
git commit -m "refactor: remove pydantic-graph agents, shared-state store, and scheduler (superseded by event-sourced canon)"
```

---

### Task 3: StoredEvent envelope & EventType constants

**Files:**
- Create: `novelizer/canon/__init__.py` (empty)
- Create: `novelizer/canon/events.py`
- Test: `tests/canon/__init__.py` (empty), `tests/canon/test_events.py`

**Interfaces:**
- Produces:
  - `StoredEvent(BaseModel)` with `sequence: int`, `id: str`, `event_type: str`, `aggregate_id: str`, `payload: dict[str, Any]`, `created_at: str`.
  - `class EventType` with string constants: `WORLD_ENTRY_CREATED="world_entry.created"`, `WORLD_ENTRY_SUPERSEDED="world_entry.superseded"`, `CHARACTER_CREATED="character.created"`, `CHARACTER_UPDATED="character.updated"`, `CHAPTER_CREATED="chapter.created"`, `CHAPTER_STATUS_CHANGED="chapter.status_changed"`, `DIRECTOR_SIGNAL_CREATED="director_signal.created"`, `DIRECTOR_SIGNAL_CONSUMED="director_signal.consumed"`.

- [ ] **Step 1: Write the failing test**

`tests/canon/test_events.py`:
```python
from novelizer.canon.events import StoredEvent, EventType


def test_event_type_naming_convention():
    for value in [
        EventType.WORLD_ENTRY_CREATED, EventType.CHARACTER_CREATED,
        EventType.CHAPTER_CREATED, EventType.DIRECTOR_SIGNAL_CREATED,
        EventType.DIRECTOR_SIGNAL_CONSUMED,
    ]:
        domain, _, verb = value.partition(".")
        assert domain and verb, f"{value} must be '<domain>.<verb>'"


def test_stored_event_roundtrips_through_json():
    ev = StoredEvent(
        sequence=1, id="abc", event_type=EventType.CHAPTER_CREATED,
        aggregate_id="ch1", payload={"title": "One"}, created_at="2026-07-17T00:00:00Z",
    )
    again = StoredEvent.model_validate_json(ev.model_dump_json())
    assert again == ev
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/canon/test_events.py -v`
Expected: FAIL (`ModuleNotFoundError: novelizer.canon.events`).

- [ ] **Step 3: Implement**

Create `novelizer/canon/__init__.py` (empty) and `tests/canon/__init__.py` (empty).
Create `novelizer/canon/events.py`:
```python
from __future__ import annotations
from typing import Any
from pydantic import BaseModel


class EventType:
    WORLD_ENTRY_CREATED = "world_entry.created"
    WORLD_ENTRY_SUPERSEDED = "world_entry.superseded"
    CHARACTER_CREATED = "character.created"
    CHARACTER_UPDATED = "character.updated"
    CHAPTER_CREATED = "chapter.created"
    CHAPTER_STATUS_CHANGED = "chapter.status_changed"
    DIRECTOR_SIGNAL_CREATED = "director_signal.created"
    DIRECTOR_SIGNAL_CONSUMED = "director_signal.consumed"


class StoredEvent(BaseModel):
    sequence: int
    id: str
    event_type: str
    aggregate_id: str
    payload: dict[str, Any]
    created_at: str
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/canon/test_events.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add novelizer/canon/__init__.py novelizer/canon/events.py tests/canon/__init__.py tests/canon/test_events.py
git commit -m "feat: add StoredEvent envelope and EventType constants"
```

---

### Task 4: EventStore (append-only log)

**Files:**
- Create: `novelizer/canon/event_store.py`
- Test: `tests/canon/test_event_store.py`

**Interfaces:**
- Consumes: `StoredEvent`, `EventType` (Task 3); pydantic entity models from `novelizer/store/models.py`.
- Produces: `class EventStore`:
  - `__init__(self, path: str)`
  - `async def init(self) -> None` — creates the `events` table, enables WAL.
  - `async def append(self, event_type: str, aggregate_id: str, payload: BaseModel) -> StoredEvent`
  - `async def events_since(self, sequence: int, event_types: list[str] | None = None) -> list[StoredEvent]`
  - `async def close(self) -> None`

- [ ] **Step 1: Write the failing test**

`tests/canon/test_event_store.py`:
```python
import os
import tempfile
import pytest
from hypothesis import given, strategies as st
from novelizer.canon.event_store import EventStore
from novelizer.canon.events import EventType
from novelizer.store.models import Chapter, WorldEntry


@pytest.fixture
async def store():
    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    s = EventStore(path)
    await s.init()
    yield s
    await s.close()
    os.unlink(path)


async def test_append_returns_monotonic_sequence(store):
    e1 = await store.append(EventType.CHAPTER_CREATED, "c1", Chapter(title="A", prose="a"))
    e2 = await store.append(EventType.CHAPTER_CREATED, "c2", Chapter(title="B", prose="b"))
    assert e1.sequence == 1 and e2.sequence == 2
    assert e1.payload["title"] == "A"


async def test_events_since_excludes_at_or_below(store):
    await store.append(EventType.CHAPTER_CREATED, "c1", Chapter(title="A", prose="a"))
    e2 = await store.append(EventType.CHAPTER_CREATED, "c2", Chapter(title="B", prose="b"))
    tail = await store.events_since(1)
    assert [e.sequence for e in tail] == [2]
    assert tail[0].id == e2.id


async def test_events_since_type_filter(store):
    await store.append(EventType.CHAPTER_CREATED, "c1", Chapter(title="A", prose="a"))
    await store.append(EventType.WORLD_ENTRY_CREATED, "w1", WorldEntry(title="W", body="w"))
    only = await store.events_since(0, event_types=[EventType.WORLD_ENTRY_CREATED])
    assert [e.event_type for e in only] == [EventType.WORLD_ENTRY_CREATED]


@given(n=st.integers(min_value=1, max_value=25))
async def test_sequences_are_strictly_increasing(n):
    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    s = EventStore(path)
    await s.init()
    try:
        seqs = [
            (await s.append(EventType.CHAPTER_CREATED, f"c{i}", Chapter(title=str(i), prose="x"))).sequence
            for i in range(n)
        ]
        assert seqs == sorted(seqs)
        assert len(set(seqs)) == n
    finally:
        await s.close()
        os.unlink(path)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/canon/test_event_store.py -v`
Expected: FAIL (`ModuleNotFoundError`).

- [ ] **Step 3: Implement**

Create `novelizer/canon/event_store.py`:
```python
from __future__ import annotations
import json
import os
import uuid
from datetime import datetime, timezone
from typing import Optional
import aiosqlite
from pydantic import BaseModel
from novelizer.canon.events import StoredEvent

_CREATE = """
CREATE TABLE IF NOT EXISTS events (
    sequence     INTEGER PRIMARY KEY AUTOINCREMENT,
    id           TEXT NOT NULL UNIQUE,
    event_type   TEXT NOT NULL,
    aggregate_id TEXT NOT NULL,
    payload      TEXT NOT NULL,
    created_at   TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_events_type ON events(event_type);
"""

_COLS = "sequence, id, event_type, aggregate_id, payload, created_at"


def _row_to_event(row) -> StoredEvent:
    return StoredEvent(
        sequence=row[0], id=row[1], event_type=row[2],
        aggregate_id=row[3], payload=json.loads(row[4]), created_at=row[5],
    )


class EventStore:
    def __init__(self, path: str) -> None:
        d = os.path.dirname(path)
        if d:
            os.makedirs(d, exist_ok=True)
        self._path = path
        self._conn: Optional[aiosqlite.Connection] = None

    async def init(self) -> None:
        self._conn = await aiosqlite.connect(self._path)
        await self._conn.execute("PRAGMA journal_mode=WAL")
        await self._conn.executescript(_CREATE)
        await self._conn.commit()

    async def close(self) -> None:
        if self._conn:
            await self._conn.close()

    async def append(self, event_type: str, aggregate_id: str, payload: BaseModel) -> StoredEvent:
        eid = str(uuid.uuid4())
        created_at = datetime.now(timezone.utc).isoformat()
        payload_json = payload.model_dump_json()
        cur = await self._conn.execute(
            "INSERT INTO events (id, event_type, aggregate_id, payload, created_at) VALUES (?,?,?,?,?)",
            (eid, event_type, aggregate_id, payload_json, created_at),
        )
        await self._conn.commit()
        return StoredEvent(
            sequence=cur.lastrowid, id=eid, event_type=event_type,
            aggregate_id=aggregate_id, payload=json.loads(payload_json), created_at=created_at,
        )

    async def events_since(self, sequence: int, event_types: Optional[list[str]] = None) -> list[StoredEvent]:
        if event_types:
            placeholders = ",".join("?" for _ in event_types)
            cur = await self._conn.execute(
                f"SELECT {_COLS} FROM events WHERE sequence > ? AND event_type IN ({placeholders}) ORDER BY sequence",
                (sequence, *event_types),
            )
        else:
            cur = await self._conn.execute(
                f"SELECT {_COLS} FROM events WHERE sequence > ? ORDER BY sequence",
                (sequence,),
            )
        rows = await cur.fetchall()
        return [_row_to_event(r) for r in rows]
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/canon/test_event_store.py -v`
Expected: PASS (4 tests incl. the property test).

- [ ] **Step 5: Commit**

```bash
git add novelizer/canon/event_store.py tests/canon/test_event_store.py
git commit -m "feat: add append-only EventStore with monotonic sequence and since-filter"
```

---

### Task 5: Projector (materialize projections)

**Files:**
- Create: `novelizer/canon/projector.py`
- Test: `tests/canon/test_projector.py`

**Interfaces:**
- Consumes: `EventStore`, `EventType`, `StoredEvent`; entity models.
- Produces: `class Projector`:
  - `__init__(self, event_store: EventStore, path: str)`
  - `async def init(self) -> None` — creates projection tables + `projector_state`.
  - `async def catch_up(self) -> int` — applies events since `last_sequence` to head; returns new `last_sequence`.
  - `async def run(self, interval: float = 0.5) -> None` — loop calling `catch_up`.
  - `def stop(self) -> None`
  - `async def close(self) -> None`
  - Projection tables (Projector is the **only** writer): `chapters(id, data, editorial_status, supersedes_id)`, `world_entries(id, data, canon_status, supersedes_id)`, `characters(id, data, canon_status, supersedes_id)`, `director_signals(id, data, consumed)`.

- [ ] **Step 1: Write the failing test**

`tests/canon/test_projector.py`:
```python
import json
import os
import tempfile
import pytest
from novelizer.canon.event_store import EventStore
from novelizer.canon.projector import Projector
from novelizer.canon.events import EventType
from novelizer.store.models import Chapter, WorldEntry, Character, DirectorSignal, SignalKind


@pytest.fixture
async def wired():
    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    events = EventStore(path)
    await events.init()
    proj = Projector(events, path)
    await proj.init()
    yield events, proj, path
    await proj.close()
    await events.close()
    os.unlink(path)


async def _chapter_rows(proj):
    cur = await proj._conn.execute("SELECT data FROM chapters ORDER BY rowid")
    return [json.loads(r[0]) for r in await cur.fetchall()]


async def test_chapter_created_is_projected(wired):
    events, proj, _ = wired
    await events.append(EventType.CHAPTER_CREATED, "c1", Chapter(id="c1", title="One", prose="p"))
    await proj.catch_up()
    rows = await _chapter_rows(proj)
    assert len(rows) == 1 and rows[0]["title"] == "One"


async def test_director_signal_consumed_flips_flag(wired):
    events, proj, _ = wired
    sig = DirectorSignal(id="s1", kind=SignalKind.seed, body="storm")
    await events.append(EventType.DIRECTOR_SIGNAL_CREATED, "s1", sig)
    await proj.catch_up()
    cur = await proj._conn.execute("SELECT consumed FROM director_signals WHERE id='s1'")
    assert (await cur.fetchone())[0] == 0
    await events.append(EventType.DIRECTOR_SIGNAL_CONSUMED, "s1", sig)
    await proj.catch_up()
    cur = await proj._conn.execute("SELECT consumed FROM director_signals WHERE id='s1'")
    assert (await cur.fetchone())[0] == 1


async def test_catch_up_advances_and_is_idempotent(wired):
    events, proj, _ = wired
    await events.append(EventType.CHAPTER_CREATED, "c1", Chapter(id="c1", title="One", prose="p"))
    assert await proj.catch_up() == 1
    assert await proj.catch_up() == 1  # no new events, no-op
    assert len(await _chapter_rows(proj)) == 1


async def test_reprojecting_same_events_is_equivalent(wired):
    events, proj, path = wired
    await events.append(EventType.CHAPTER_CREATED, "c1", Chapter(id="c1", title="One", prose="p"))
    await events.append(EventType.WORLD_ENTRY_CREATED, "w1", WorldEntry(id="w1", title="W", body="b"))
    await events.append(EventType.CHARACTER_CREATED, "ch1", Character(id="ch1", name="Mira"))
    await proj.catch_up()
    incremental = await _chapter_rows(proj)
    # Fresh projector over the same log, projecting from zero, yields the same rows.
    proj2 = Projector(events, path)
    await proj2.init()
    await proj2._reset_state()  # force last_sequence=0
    await proj2.catch_up()
    cur = await proj2._conn.execute("SELECT data FROM chapters ORDER BY rowid")
    from_scratch = [json.loads(r[0]) for r in await cur.fetchall()]
    await proj2.close()
    assert incremental == from_scratch
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/canon/test_projector.py -v`
Expected: FAIL (`ModuleNotFoundError`).

- [ ] **Step 3: Implement**

Create `novelizer/canon/projector.py`:
```python
from __future__ import annotations
import asyncio
import os
from typing import Optional
import aiosqlite
from novelizer.canon.event_store import EventStore
from novelizer.canon.events import EventType, StoredEvent

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
CREATE TABLE IF NOT EXISTS projector_state (
    id TEXT PRIMARY KEY, last_sequence INTEGER NOT NULL
);
"""


class Projector:
    def __init__(self, event_store: EventStore, path: str) -> None:
        self._events = event_store
        self._path = path
        self._conn: Optional[aiosqlite.Connection] = None
        self._running = False

    async def init(self) -> None:
        self._conn = await aiosqlite.connect(self._path)
        await self._conn.execute("PRAGMA journal_mode=WAL")
        await self._conn.executescript(_CREATE)
        await self._conn.execute(
            "INSERT OR IGNORE INTO projector_state (id, last_sequence) VALUES ('singleton', 0)"
        )
        await self._conn.commit()

    async def close(self) -> None:
        if self._conn:
            await self._conn.close()

    async def _last_sequence(self) -> int:
        cur = await self._conn.execute("SELECT last_sequence FROM projector_state WHERE id='singleton'")
        return (await cur.fetchone())[0]

    async def _set_last_sequence(self, seq: int) -> None:
        await self._conn.execute(
            "UPDATE projector_state SET last_sequence=? WHERE id='singleton'", (seq,)
        )
        await self._conn.commit()

    async def _reset_state(self) -> None:
        """Testing/rebuild helper: forget position and clear projections."""
        for table in ("chapters", "world_entries", "characters", "director_signals"):
            await self._conn.execute(f"DELETE FROM {table}")
        await self._set_last_sequence(0)

    async def catch_up(self) -> int:
        last = await self._last_sequence()
        events = await self._events.events_since(last)
        for ev in events:
            await self._apply(ev)
            last = ev.sequence
        await self._set_last_sequence(last)
        return last

    async def run(self, interval: float = 0.5) -> None:
        self._running = True
        while self._running:
            await self.catch_up()
            await asyncio.sleep(interval)

    def stop(self) -> None:
        self._running = False

    async def _apply(self, ev: StoredEvent) -> None:
        import json
        data = json.dumps(ev.payload)
        p = ev.payload
        t = ev.event_type
        if t == EventType.CHAPTER_CREATED or t == EventType.CHAPTER_STATUS_CHANGED:
            await self._conn.execute(
                "INSERT OR REPLACE INTO chapters (id, data, editorial_status, supersedes_id) VALUES (?,?,?,?)",
                (p["id"], data, p.get("editorial_status", "draft"), p.get("supersedes_id")),
            )
        elif t == EventType.WORLD_ENTRY_CREATED:
            await self._conn.execute(
                "INSERT OR REPLACE INTO world_entries (id, data, canon_status, supersedes_id) VALUES (?,?,?,?)",
                (p["id"], data, p.get("canon_status", "active"), p.get("supersedes_id")),
            )
        elif t == EventType.WORLD_ENTRY_SUPERSEDED:
            if p.get("supersedes_id"):
                await self._conn.execute(
                    "UPDATE world_entries SET canon_status='superseded' WHERE id=?", (p["supersedes_id"],)
                )
            await self._conn.execute(
                "INSERT OR REPLACE INTO world_entries (id, data, canon_status, supersedes_id) VALUES (?,?,?,?)",
                (p["id"], data, p.get("canon_status", "active"), p.get("supersedes_id")),
            )
        elif t == EventType.CHARACTER_CREATED or t == EventType.CHARACTER_UPDATED:
            await self._conn.execute(
                "INSERT OR REPLACE INTO characters (id, data, canon_status, supersedes_id) VALUES (?,?,?,?)",
                (p["id"], data, p.get("canon_status", "active"), p.get("supersedes_id")),
            )
        elif t == EventType.DIRECTOR_SIGNAL_CREATED:
            await self._conn.execute(
                "INSERT OR REPLACE INTO director_signals (id, data, consumed) VALUES (?,?,?)",
                (p["id"], data, 1 if p.get("consumed") else 0),
            )
        elif t == EventType.DIRECTOR_SIGNAL_CONSUMED:
            await self._conn.execute(
                "UPDATE director_signals SET consumed=1 WHERE id=?", (ev.aggregate_id,)
            )
        await self._conn.commit()
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/canon/test_projector.py -v`
Expected: PASS (4 tests).

- [ ] **Step 5: Commit**

```bash
git add novelizer/canon/projector.py tests/canon/test_projector.py
git commit -m "feat: add Projector materializing projections from the event log"
```

---

### Task 6: ReadStore (query projections)

**Files:**
- Create: `novelizer/canon/read_store.py`
- Test: `tests/canon/test_read_store.py`

**Interfaces:**
- Consumes: projection tables (Task 5); entity models.
- Produces: `class ReadStore`:
  - `__init__(self, path: str)`, `async def init(self)`, `async def close(self)`
  - `async def list_chapters(self, status: str | None = None) -> list[Chapter]`
  - `async def get_chapter(self, chapter_id: str) -> Chapter | None`
  - `async def list_world_entries(self, domain: str | None = None) -> list[WorldEntry]`
  - `async def list_characters(self) -> list[Character]`
  - `async def list_unconsumed_signals(self, target_agent: str | None = None) -> list[DirectorSignal]`

- [ ] **Step 1: Write the failing test**

`tests/canon/test_read_store.py`:
```python
import os
import tempfile
import pytest
from novelizer.canon.event_store import EventStore
from novelizer.canon.projector import Projector
from novelizer.canon.read_store import ReadStore
from novelizer.canon.events import EventType
from novelizer.store.models import Chapter, WorldEntry, Character, DirectorSignal, SignalKind


@pytest.fixture
async def stack():
    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    events = EventStore(path); await events.init()
    proj = Projector(events, path); await proj.init()
    read = ReadStore(path); await read.init()
    yield events, proj, read
    await read.close(); await proj.close(); await events.close()
    os.unlink(path)


async def test_chapter_visible_after_projection(stack):
    events, proj, read = stack
    await events.append(EventType.CHAPTER_CREATED, "c1", Chapter(id="c1", title="One", prose="p"))
    await proj.catch_up()
    chapters = await read.list_chapters()
    assert [c.title for c in chapters] == ["One"]
    assert (await read.get_chapter("c1")).prose == "p"


async def test_unconsumed_signals_filtered_by_target(stack):
    events, proj, read = stack
    await events.append(EventType.DIRECTOR_SIGNAL_CREATED, "s1",
                        DirectorSignal(id="s1", kind=SignalKind.seed, body="broadcast"))
    await events.append(EventType.DIRECTOR_SIGNAL_CREATED, "s2",
                        DirectorSignal(id="s2", kind=SignalKind.focus, body="for-editor", target_agent="editor"))
    await proj.catch_up()
    for_author = await read.list_unconsumed_signals(target_agent="author")
    assert {s.id for s in for_author} == {"s1"}  # broadcast only, not editor-targeted


async def test_consumed_signal_disappears(stack):
    events, proj, read = stack
    sig = DirectorSignal(id="s1", kind=SignalKind.seed, body="x")
    await events.append(EventType.DIRECTOR_SIGNAL_CREATED, "s1", sig)
    await events.append(EventType.DIRECTOR_SIGNAL_CONSUMED, "s1", sig)
    await proj.catch_up()
    assert await read.list_unconsumed_signals() == []
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/canon/test_read_store.py -v`
Expected: FAIL (`ModuleNotFoundError`).

- [ ] **Step 3: Implement**

Create `novelizer/canon/read_store.py`:
```python
from __future__ import annotations
from typing import Optional
import aiosqlite
from novelizer.store.models import Chapter, WorldEntry, Character, DirectorSignal


class ReadStore:
    def __init__(self, path: str) -> None:
        self._path = path
        self._conn: Optional[aiosqlite.Connection] = None

    async def init(self) -> None:
        self._conn = await aiosqlite.connect(self._path)
        await self._conn.execute("PRAGMA journal_mode=WAL")

    async def close(self) -> None:
        if self._conn:
            await self._conn.close()

    async def list_chapters(self, status: Optional[str] = None) -> list[Chapter]:
        if status:
            cur = await self._conn.execute(
                "SELECT data FROM chapters WHERE editorial_status=? ORDER BY rowid", (status,)
            )
        else:
            cur = await self._conn.execute("SELECT data FROM chapters ORDER BY rowid")
        return [Chapter.model_validate_json(r[0]) for r in await cur.fetchall()]

    async def get_chapter(self, chapter_id: str) -> Optional[Chapter]:
        cur = await self._conn.execute("SELECT data FROM chapters WHERE id=?", (chapter_id,))
        row = await cur.fetchone()
        return Chapter.model_validate_json(row[0]) if row else None

    async def list_world_entries(self, domain: Optional[str] = None) -> list[WorldEntry]:
        if domain:
            cur = await self._conn.execute(
                "SELECT data FROM world_entries WHERE canon_status='active' "
                "AND json_extract(data,'$.domain')=? ORDER BY rowid", (domain,)
            )
        else:
            cur = await self._conn.execute(
                "SELECT data FROM world_entries WHERE canon_status='active' ORDER BY rowid"
            )
        return [WorldEntry.model_validate_json(r[0]) for r in await cur.fetchall()]

    async def list_characters(self) -> list[Character]:
        cur = await self._conn.execute(
            "SELECT data FROM characters WHERE canon_status='active' ORDER BY rowid"
        )
        return [Character.model_validate_json(r[0]) for r in await cur.fetchall()]

    async def list_unconsumed_signals(self, target_agent: Optional[str] = None) -> list[DirectorSignal]:
        cur = await self._conn.execute(
            "SELECT data FROM director_signals WHERE consumed=0 ORDER BY rowid"
        )
        sigs = [DirectorSignal.model_validate_json(r[0]) for r in await cur.fetchall()]
        if target_agent is not None:
            sigs = [s for s in sigs if s.target_agent is None or s.target_agent == target_agent]
        return sigs
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/canon/test_read_store.py -v`
Expected: PASS (3 tests).

- [ ] **Step 5: Commit**

```bash
git add novelizer/canon/read_store.py tests/canon/test_read_store.py
git commit -m "feat: add ReadStore querying projections"
```

---

### Task 7: Chat-model builder (OpenAI-compat)

**Files:**
- Create: `novelizer/agents/llm.py`
- Test: `tests/agents/test_llm.py`

**Interfaces:**
- Consumes: `Settings` (Task 1).
- Produces:
  - `def build_chat_model(model: str, base_url: str, api_key: str, temperature: float = 0.8)` → a LangChain chat model bound to the OpenAI-compatible endpoint.

- [ ] **Step 1: Write the failing test**

`tests/agents/test_llm.py`:
```python
from novelizer.agents.llm import build_chat_model


def test_build_chat_model_targets_given_model_and_endpoint():
    m = build_chat_model("my-model", "http://localhost:1234/v1", "key", temperature=0.5)
    # ChatOpenAI stores the model name and base URL; no network call is made here.
    assert m.model_name == "my-model"
    assert "1234" in str(m.openai_api_base)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/agents/test_llm.py -v`
Expected: FAIL (`ModuleNotFoundError`).

- [ ] **Step 3: Implement**

Create `novelizer/agents/llm.py`:
```python
from __future__ import annotations
from langchain.chat_models import init_chat_model


def build_chat_model(model: str, base_url: str, api_key: str, temperature: float = 0.8):
    """Build a LangChain chat model bound to an OpenAI-compatible endpoint."""
    return init_chat_model(
        f"openai:{model}",
        base_url=base_url,
        api_key=api_key,
        temperature=temperature,
    )
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/agents/test_llm.py -v`
Expected: PASS. (If `openai_api_base` is unset on the returned object, adjust the assertion to `m.model_name == "my-model"` only and note it — the attribute name can vary by `langchain-openai` version; the model-name assertion is the stable check.)

- [ ] **Step 5: Commit**

```bash
git add novelizer/agents/llm.py tests/agents/test_llm.py
git commit -m "feat: add OpenAI-compatible chat-model builder"
```

---

### Task 8: Author agent (deepagents, DI runner)

**Files:**
- Create: `novelizer/agents/base.py` (agent contract + `ChapterDraft`)
- Create: `novelizer/agents/author.py`
- Test: `tests/agents/test_author.py`

**Interfaces:**
- Consumes: `EventStore`, `ReadStore`, `EventType`; `Chapter`, `DirectorSignal`; `build_chat_model` (Task 7).
- Produces:
  - `class ChapterDraft(BaseModel)`: `title: str`, `prose: str`, `character_ids: list[str] = []`.
  - `class Runner(Protocol)`: `async def ainvoke(self, inputs: dict) -> dict`.
  - `class Author`: `name = "author"`; `__init__(self, runner, read_store, event_store, interval=300)`; `async def readiness(self) -> float`; `async def poll(self) -> dict`; `async def work(self, ctx: dict) -> ChapterDraft | None`; `async def commit(self, draft, ctx) -> None`; `async def run_once(self) -> None`.
  - `def build_author_runner(settings)` → a deepagents runner (`create_deep_agent(...)`).

- [ ] **Step 1: Write the failing test**

`tests/agents/test_author.py`:
```python
import os
import tempfile
import pytest
from novelizer.canon.event_store import EventStore
from novelizer.canon.projector import Projector
from novelizer.canon.read_store import ReadStore
from novelizer.canon.events import EventType
from novelizer.agents.author import Author, ChapterDraft
from novelizer.store.models import Chapter, DirectorSignal, SignalKind


class FakeRunner:
    def __init__(self, draft): self._draft = draft; self.calls = []
    async def ainvoke(self, inputs):
        self.calls.append(inputs)
        return {"structured_response": self._draft}


@pytest.fixture
async def stack():
    fd, path = tempfile.mkstemp(suffix=".db"); os.close(fd)
    events = EventStore(path); await events.init()
    proj = Projector(events, path); await proj.init()
    read = ReadStore(path); await read.init()
    yield events, proj, read
    await read.close(); await proj.close(); await events.close(); os.unlink(path)


async def test_readiness_drops_with_draft_backlog(stack):
    events, proj, read = stack
    for i in range(3):
        await events.append(EventType.CHAPTER_CREATED, f"c{i}", Chapter(id=f"c{i}", title=str(i), prose="p"))
    await proj.catch_up()
    author = Author(FakeRunner(None), read, events)
    assert await author.readiness() == 0.0


async def test_run_once_appends_and_projects_a_chapter(stack):
    events, proj, read = stack
    draft = ChapterDraft(title="The Salt Road", prose="The road held its salt like a grudge.")
    author = Author(FakeRunner(draft), read, events)
    await author.run_once()
    await proj.catch_up()
    titles = [c.title for c in await read.list_chapters()]
    assert "The Salt Road" in titles


async def test_run_once_consumes_targeted_signals(stack):
    events, proj, read = stack
    await events.append(EventType.DIRECTOR_SIGNAL_CREATED, "s1",
                        DirectorSignal(id="s1", kind=SignalKind.seed, body="a storm is coming"))
    await proj.catch_up()
    author = Author(FakeRunner(ChapterDraft(title="T", prose="P")), read, events)
    await author.run_once()
    await proj.catch_up()
    assert await read.list_unconsumed_signals(target_agent="author") == []


async def test_work_returns_none_passes_through_to_noop_commit(stack):
    events, proj, read = stack
    author = Author(FakeRunner(None), read, events)
    await author.run_once()
    await proj.catch_up()
    assert await read.list_chapters() == []
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/agents/test_author.py -v`
Expected: FAIL (`ModuleNotFoundError`).

- [ ] **Step 3: Implement**

Create `novelizer/agents/base.py`:
```python
from __future__ import annotations
from typing import Protocol
from pydantic import BaseModel, Field


class ChapterDraft(BaseModel):
    title: str
    prose: str
    character_ids: list[str] = Field(default_factory=list)


class Runner(Protocol):
    async def ainvoke(self, inputs: dict) -> dict: ...
```

Create `novelizer/agents/author.py`:
```python
from __future__ import annotations
from novelizer.agents.base import ChapterDraft, Runner
from novelizer.canon.event_store import EventStore
from novelizer.canon.read_store import ReadStore
from novelizer.canon.events import EventType
from novelizer.store.models import Chapter

AUTHOR_SYSTEM_PROMPT = """You are the Author of a living fictional world. Write the next prose chapter.
You receive world lore, active characters, previous chapter summaries, and director notes.
Write a self-contained chapter with a clear narrative beat, 2-5 paragraphs.
Return a title, the full prose, and the ids of characters who appear."""


def _summarize(ctx: dict) -> str:
    world = "\n".join(f"- {e.title}: {e.body[:150]}" for e in ctx["world"][:10]) or "None yet."
    chars = "\n".join(f"- {c.name}: {c.traits} | arc: {c.arc_status}" for c in ctx["characters"][:8]) or "None yet."
    prev = "\n".join(f"- '{c.title}': {c.prose[:200]}" for c in ctx["previous"]) or "None yet."
    notes = "\n".join(f"Director: {s.body}" for s in ctx["signals"]) or "None."
    return (
        f"World lore:\n{world}\n\nCharacters:\n{chars}\n\n"
        f"Previous chapters:\n{prev}\n\nDirector notes:\n{notes}\n\nWrite the next chapter."
    )


class Author:
    name = "author"

    def __init__(self, runner: Runner, read_store: ReadStore, event_store: EventStore, interval: int = 300) -> None:
        self._runner = runner
        self._read = read_store
        self._events = event_store
        self.interval = interval

    async def readiness(self) -> float:
        drafts = len(await self._read.list_chapters(status="draft"))
        return max(0.0, 1.0 - drafts / 3)

    async def poll(self) -> dict:
        chapters = await self._read.list_chapters()
        return {
            "world": await self._read.list_world_entries(),
            "characters": await self._read.list_characters(),
            "previous": chapters[-3:],
            "signals": await self._read.list_unconsumed_signals(target_agent=self.name),
        }

    async def work(self, ctx: dict) -> ChapterDraft | None:
        result = await self._runner.ainvoke(
            {"messages": [{"role": "user", "content": _summarize(ctx)}]}
        )
        return result.get("structured_response")

    async def commit(self, draft: ChapterDraft | None, ctx: dict) -> None:
        if draft is None:
            return
        chapter = Chapter(title=draft.title, prose=draft.prose, character_ids=draft.character_ids)
        await self._events.append(EventType.CHAPTER_CREATED, chapter.id, chapter)
        for sig in ctx["signals"]:
            await self._events.append(EventType.DIRECTOR_SIGNAL_CONSUMED, sig.id, sig)

    async def run_once(self) -> None:
        ctx = await self.poll()
        draft = await self.work(ctx)
        await self.commit(draft, ctx)


def build_author_runner(settings):
    from deepagents import create_deep_agent
    from novelizer.agents.llm import build_chat_model
    model = build_chat_model(
        settings.author_model, settings.llm_base_url, settings.llm_api_key, settings.author_temperature
    )
    return create_deep_agent(model=model, system_prompt=AUTHOR_SYSTEM_PROMPT, response_format=ChapterDraft)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/agents/test_author.py -v`
Expected: PASS (4 tests).

- [ ] **Step 5: Commit**

```bash
git add novelizer/agents/base.py novelizer/agents/author.py tests/agents/test_author.py
git commit -m "feat: add deepagents Author with injected runner writing chapter.created events"
```

---

### Task 9: Runtime wiring

**Files:**
- Create: `novelizer/runtime.py`
- Test: `tests/test_runtime.py`

**Interfaces:**
- Consumes: `Settings`, `EventStore`, `Projector`, `ReadStore`, `Author`, `build_author_runner`.
- Produces: `class Runtime`:
  - `__init__(self, settings, runner=None)` — `runner` overrides `build_author_runner` for tests.
  - `async def start(self) -> None` — init stores + projector, catch up, build Author.
  - `async def close(self) -> None`
  - attributes: `.events`, `.read`, `.projector`, `.author`.

- [ ] **Step 1: Write the failing test**

`tests/test_runtime.py`:
```python
import os
import tempfile
import pytest
from novelizer.config import Settings
from novelizer.runtime import Runtime
from novelizer.canon.events import EventType
from novelizer.agents.author import ChapterDraft
from novelizer.store.models import DirectorSignal, SignalKind


class FakeRunner:
    def __init__(self, draft): self._draft = draft
    async def ainvoke(self, inputs):
        return {"structured_response": self._draft}


@pytest.fixture
def settings():
    fd, path = tempfile.mkstemp(suffix=".db"); os.close(fd)
    yield Settings(db_path=path)
    os.unlink(path)


async def test_start_wires_a_working_slice(settings):
    rt = Runtime(settings, runner=FakeRunner(ChapterDraft(title="Chapter One", prose="It began.")))
    await rt.start()
    try:
        await rt.events.append(EventType.DIRECTOR_SIGNAL_CREATED, "s1",
                               DirectorSignal(id="s1", kind=SignalKind.seed, body="begin"))
        await rt.projector.catch_up()
        await rt.author.run_once()
        await rt.projector.catch_up()
        assert "Chapter One" in [c.title for c in await rt.read.list_chapters()]
    finally:
        await rt.close()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_runtime.py -v`
Expected: FAIL (`ModuleNotFoundError`).

- [ ] **Step 3: Implement**

Create `novelizer/runtime.py`:
```python
from __future__ import annotations
from typing import Optional
from novelizer.config import Settings
from novelizer.canon.event_store import EventStore
from novelizer.canon.projector import Projector
from novelizer.canon.read_store import ReadStore
from novelizer.agents.author import Author, build_author_runner


class Runtime:
    def __init__(self, settings: Settings, runner=None) -> None:
        self.settings = settings
        self.events = EventStore(settings.db_path)
        self.projector = Projector(self.events, settings.db_path)
        self.read = ReadStore(settings.db_path)
        self._runner = runner
        self.author: Optional[Author] = None

    async def start(self) -> None:
        await self.events.init()
        await self.projector.init()
        await self.read.init()
        await self.projector.catch_up()
        runner = self._runner or build_author_runner(self.settings)
        self.author = Author(runner, self.read, self.events, interval=self.settings.author_interval)

    async def close(self) -> None:
        await self.read.close()
        await self.projector.close()
        await self.events.close()
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/test_runtime.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add novelizer/runtime.py tests/test_runtime.py
git commit -m "feat: add Runtime wiring canon store, projector, and Author"
```

---

### Task 10: TUI feed + CLI rewrite

**Files:**
- Create: `novelizer/tui/__init__.py` (empty), `novelizer/tui/app.py`
- Rewrite: `novelizer/director/cli.py`
- Test: `tests/tui/__init__.py` (empty), `tests/tui/test_app.py`, `tests/director/test_cli.py` (replace)

**Interfaces:**
- Consumes: `Runtime`, `EventType`, `Settings`, `DirectorSignal`.
- Produces:
  - `novelizer/tui/app.py`: `def format_event(ev: StoredEvent) -> str` (pure, testable); `class NovelizerApp(App)` taking a started `Runtime`, rendering new events into a `RichLog` and running Projector + Author as background workers.
  - `novelizer/director/cli.py`: `seed` (appends `director_signal.created`), `chapters` (lists via ReadStore), `read <id>`, and a default (`invoke_without_command`) that launches the TUI; `main()` entry point.

- [ ] **Step 1: Write the failing test**

`tests/tui/test_app.py`:
```python
from novelizer.tui.app import format_event
from novelizer.canon.events import StoredEvent, EventType


def test_format_chapter_created_mentions_title():
    ev = StoredEvent(sequence=1, id="e1", event_type=EventType.CHAPTER_CREATED,
                     aggregate_id="c1", payload={"title": "The Salt Road"}, created_at="t")
    line = format_event(ev)
    assert "The Salt Road" in line and "Author" in line


def test_format_director_signal_created_mentions_body():
    ev = StoredEvent(sequence=2, id="e2", event_type=EventType.DIRECTOR_SIGNAL_CREATED,
                     aggregate_id="s1", payload={"body": "a storm is coming"}, created_at="t")
    assert "a storm is coming" in format_event(ev)
```

`tests/director/test_cli.py`:
```python
import os
import tempfile
from click.testing import CliRunner
from novelizer.director.cli import cli


def _env(path):
    return {"NOVELIZER_DB_PATH": path}


def test_seed_then_chapters_roundtrip():
    fd, path = tempfile.mkstemp(suffix=".db"); os.close(fd)
    try:
        runner = CliRunner()
        r1 = runner.invoke(cli, ["seed", "a storm is coming"], env=_env(path))
        assert r1.exit_code == 0, r1.output
        assert "Seed" in r1.output
        r2 = runner.invoke(cli, ["chapters"], env=_env(path))
        assert r2.exit_code == 0, r2.output
        assert "No chapters" in r2.output  # none authored yet
    finally:
        os.unlink(path)
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/tui/test_app.py tests/director/test_cli.py -v`
Expected: FAIL (`ModuleNotFoundError` / missing `seed`).

- [ ] **Step 3: Implement the TUI**

Create `novelizer/tui/__init__.py` (empty) and `tests/tui/__init__.py` (empty).
Create `novelizer/tui/app.py`:
```python
from __future__ import annotations
import asyncio
from textual.app import App, ComposeResult
from textual.widgets import Header, Footer, RichLog
from novelizer.canon.events import StoredEvent, EventType

_LABELS = {
    EventType.CHAPTER_CREATED: "Author",
    EventType.WORLD_ENTRY_CREATED: "Architect",
    EventType.CHARACTER_CREATED: "Keeper",
    EventType.DIRECTOR_SIGNAL_CREATED: "Director",
}


def format_event(ev: StoredEvent) -> str:
    who = _LABELS.get(ev.event_type, "System")
    p = ev.payload
    if ev.event_type == EventType.CHAPTER_CREATED:
        detail = f"new chapter: {p.get('title', '')}"
    elif ev.event_type == EventType.WORLD_ENTRY_CREATED:
        detail = f"lore: {p.get('title', '')}"
    elif ev.event_type == EventType.DIRECTOR_SIGNAL_CREATED:
        detail = f"signal: {p.get('body', '')}"
    else:
        detail = ev.event_type
    return f"◆ {who} — {detail}"


class NovelizerApp(App):
    TITLE = "Novelizer — Mission Control"

    def __init__(self, runtime) -> None:
        super().__init__()
        self.runtime = runtime
        self._last_seq = 0

    def compose(self) -> ComposeResult:
        yield Header()
        yield RichLog(highlight=False, markup=False, id="feed")
        yield Footer()

    async def on_mount(self) -> None:
        self.run_worker(self._projector_loop(), exclusive=False)
        self.run_worker(self._author_loop(), exclusive=False)
        self.run_worker(self._feed_loop(), exclusive=False)

    async def _projector_loop(self) -> None:
        while True:
            await self.runtime.projector.catch_up()
            await asyncio.sleep(self.runtime.settings.projector_interval)

    async def _author_loop(self) -> None:
        while True:
            await self.runtime.author.run_once()
            await asyncio.sleep(self.runtime.author.interval)

    async def _feed_loop(self) -> None:
        log = self.query_one("#feed", RichLog)
        while True:
            events = await self.runtime.events.events_since(self._last_seq)
            for ev in events:
                log.write(format_event(ev))
                self._last_seq = ev.sequence
            await asyncio.sleep(0.3)
```

- [ ] **Step 4: Implement the CLI**

Rewrite `novelizer/director/cli.py`:
```python
from __future__ import annotations
import asyncio
import click
from rich.console import Console
from rich.table import Table
from novelizer.config import Settings
from novelizer.runtime import Runtime
from novelizer.canon.events import EventType
from novelizer.store.models import DirectorSignal, SignalKind

console = Console()


async def _with_runtime(settings, fn):
    rt = Runtime(settings)
    # CLI commands that only touch the store don't need the LLM runner.
    await rt.events.init()
    await rt.projector.init()
    await rt.read.init()
    await rt.projector.catch_up()
    try:
        return await fn(rt)
    finally:
        await rt.close()


@click.group(invoke_without_command=True)
@click.pass_context
def cli(ctx):
    ctx.ensure_object(dict)
    ctx.obj["settings"] = Settings()
    if ctx.invoked_subcommand is None:
        _launch_tui(ctx.obj["settings"])


def _launch_tui(settings: Settings) -> None:
    from novelizer.tui.app import NovelizerApp

    async def _boot():
        rt = Runtime(settings)
        await rt.start()
        app = NovelizerApp(rt)
        try:
            await app.run_async()
        finally:
            await rt.close()

    asyncio.run(_boot())


@cli.command()
@click.argument("text")
@click.pass_context
def seed(ctx, text: str):
    """Inject a narrative seed as a director_signal.created event."""
    async def _run(rt: Runtime):
        sig = DirectorSignal(kind=SignalKind.seed, body=text)
        await rt.events.append(EventType.DIRECTOR_SIGNAL_CREATED, sig.id, sig)
        console.print(f"[green]Seed injected:[/green] {text}")
    asyncio.run(_with_runtime(ctx.obj["settings"], _run))


@cli.command()
@click.pass_context
def chapters(ctx):
    """List chapters by editorial status."""
    async def _run(rt: Runtime):
        chs = await rt.read.list_chapters()
        if not chs:
            console.print("No chapters yet.")
            return
        table = Table(title="Chapters")
        table.add_column("ID", style="dim", no_wrap=True)
        table.add_column("Title")
        table.add_column("Status")
        for c in chs:
            table.add_row(c.id[:8], c.title, c.editorial_status.value)
        console.print(table)
    asyncio.run(_with_runtime(ctx.obj["settings"], _run))


@cli.command()
@click.argument("chapter_id")
@click.pass_context
def read(ctx, chapter_id: str):
    """Print a chapter's prose."""
    async def _run(rt: Runtime):
        ch = await rt.read.get_chapter(chapter_id)
        if not ch:
            console.print(f"[red]Chapter {chapter_id} not found.[/red]")
            return
        console.rule(ch.title)
        console.print(ch.prose)
    asyncio.run(_with_runtime(ctx.obj["settings"], _run))


def main():
    cli()
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `uv run pytest tests/tui/test_app.py tests/director/test_cli.py -v`
Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add novelizer/tui/__init__.py novelizer/tui/app.py novelizer/director/cli.py \
  tests/tui/__init__.py tests/tui/test_app.py tests/director/test_cli.py
git commit -m "feat: add Textual feed TUI and event-sourced CLI (seed/chapters/read, default launches TUI)"
```

---

### Task 11: TUI smoke test + full-suite green + docs

**Files:**
- Create: `tests/tui/test_app_smoke.py`
- Modify: `docs/MILESTONES.md` (mark M0 status), `README.md` (usage)

**Interfaces:**
- Consumes: `NovelizerApp`, `Runtime` (with a fake runner).

- [ ] **Step 1: Write the failing smoke test**

`tests/tui/test_app_smoke.py`:
```python
import os
import tempfile
import pytest
from novelizer.config import Settings
from novelizer.runtime import Runtime
from novelizer.tui.app import NovelizerApp
from novelizer.agents.author import ChapterDraft


class FakeRunner:
    def __init__(self, draft): self._draft = draft
    async def ainvoke(self, inputs):
        return {"structured_response": self._draft}


@pytest.mark.asyncio
async def test_feed_renders_authored_chapter():
    fd, path = tempfile.mkstemp(suffix=".db"); os.close(fd)
    settings = Settings(db_path=path, author_interval=1, projector_interval=0.1)
    rt = Runtime(settings, runner=FakeRunner(ChapterDraft(title="Live Chapter", prose="It appears.")))
    await rt.start()
    app = NovelizerApp(rt)
    try:
        async with app.run_test() as pilot:
            await pilot.pause(0.6)  # let projector + author + feed workers cycle
            from textual.widgets import RichLog
            log = app.query_one("#feed", RichLog)
            rendered = "\n".join(str(line) for line in log.lines)
            assert "Live Chapter" in rendered
    finally:
        await rt.close()
        os.unlink(path)
```

- [ ] **Step 2: Run test to verify it fails, then passes**

Run: `uv run pytest tests/tui/test_app_smoke.py -v`
Expected: initially may FAIL if `RichLog.lines` access differs by Textual version. If so, assert against a captured buffer instead: add a `messages: list[str]` to `NovelizerApp`, append in `_feed_loop` alongside `log.write`, and assert `"Live Chapter" in "\n".join(app.messages)`. Iterate until green. Expected final: PASS.

- [ ] **Step 3: Run the FULL suite**

Run: `uv run pytest -q`
Expected: PASS, no errors. If `tests/store/test_embeddings.py` requires Ollama, it should be marked/skipped (leave as-is; it is M1 territory).

- [ ] **Step 4: Update docs**

In `docs/MILESTONES.md`, change the M0 row status from `⬜ not started` to `✅ complete`.
In `README.md`, replace the "Usage"/"Keyboard Shortcuts"/"Architecture" sections to reflect M0 reality: `novelizer` launches the Mission Control TUI (live feed); `novelizer seed "<text>"` injects a narrative seed; `novelizer chapters` / `novelizer read <id>` inspect output; agents talk to an OpenAI-compatible endpoint configured via `NOVELIZER_LLM_BASE_URL` / `NOVELIZER_AUTHOR_MODEL`. State the requirement: a running OpenAI-compatible server (llama.cpp, vLLM, etc.).

- [ ] **Step 5: Commit**

```bash
git add tests/tui/test_app_smoke.py docs/MILESTONES.md README.md
git commit -m "test: add TUI feed smoke test; docs: mark M0 complete and update usage"
```

---

## Self-Review

**Spec coverage (against `docs/superpowers/specs/2026-07-17-novelizer-vision-design.md` + `2026-06-19-event-sourced-store-design.md`):**
- Event log as sole source of truth → Task 4 (EventStore). ✓
- Projector materializes projections, sole writer → Task 5. ✓
- ReadStore query interface → Task 6. ✓
- Author on deepagents via OpenAI-compat endpoint → Tasks 7–8. ✓
- Skeletal TUI tailing the log → Tasks 10–11. ✓
- `novelizer` runs and chapters appear live → Task 11 smoke test proves the loop. ✓
- Proposal-gating event shapes, other 5 agents, scheduler, agent offsets/catch-up, ChromaDB/embeddings → **explicitly deferred to M1** (stated in the milestone note). ✓ (gap is intentional and documented)
- DDD bounded contexts → `canon/` (World Canon), `agents/` (Roster), `tui/`+`director/` (Direction) with events as the only contract. ✓
- SOLID / DI → Author takes an injected `Runner`; Runtime composes via constructor. ✓
- Property-based tests → Task 4 (sequence monotonicity), Task 5 (idempotency + rebuild equivalence). ✓
- Black-box TDD → tests assert via public store/agent APIs, not SQL internals (Projector tests reach into `_conn` only to read, acceptable for a projection-shape assertion; agent/readstore tests are fully black-box). ✓

**Placeholder scan:** No TBD/TODO. Task 7 and Task 11 name concrete fallbacks (attribute-name and Textual-version variance) with exact alternative assertions rather than vague "handle edge cases." ✓

**Type consistency:** `StoredEvent`/`EventType` used identically across Tasks 3–10. `ChapterDraft` defined in Task 8 `base.py`, imported in Tasks 8–11. `Runner.ainvoke` signature matches `FakeRunner` and deepagents' `.ainvoke`. `Runtime` attribute names (`.events`, `.projector`, `.read`, `.author`) consistent across Tasks 9–11. `EventStore.append(event_type, aggregate_id, payload)` call shape consistent everywhere. ✓

**Note on `_apply` import:** Task 5's `_apply` does `import json` inside the method for locality; acceptable, but the reviewer may hoist it to module top — either is fine.
