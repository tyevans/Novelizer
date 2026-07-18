# M1.1 · The Room Runs — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Assemble the full six-agent writers' room on the M0 event-sourced spine — the five remaining agents ported to deepagents, coordinated by a readiness-scored scheduler, all writing canon through one injectable `Committer` seam — so the whole pipeline (world → chapter → edit → continuity → retcon) runs unattended.

**Architecture:** Agents never call `EventStore.append` directly; they write canon through a `Committer` collaborator (M1.1: append-only; M1.3 will swap in a gating implementation without touching agents — open/closed). All six agents share a `BaseAgent` (name, interval, pause, readiness, signal-consumption). A `Scheduler` scores agents by readiness each tick and runs the most-ready eligible one. The canon gains retcon-request events, a `retcon_requests` projection, and the reads agents need. Everything is wired by `Runtime`, which accepts per-agent runner overrides so the whole pipeline is black-box testable with fake LLMs.

**Tech Stack:** Python 3.13, `aiosqlite`, `deepagents`/`langchain-openai` (OpenAI-compat endpoints), `pydantic` v2, `textual`, `click`+`rich`, `pytest`+`pytest-asyncio`+`hypothesis`.

**Context — current state after M0 (all on `master`):**
- `novelizer/canon/events.py` — `EventType` (constants) + `StoredEvent`.
- `novelizer/canon/event_store.py` — `EventStore.append(event_type, aggregate_id, payload) -> StoredEvent`, `events_since(seq, event_types=None)`.
- `novelizer/canon/projector.py` — `Projector` with `_apply(ev)` dispatch, projection tables `chapters/world_entries/characters/director_signals` + `projector_state`. `_reset_state()` helper exists.
- `novelizer/canon/read_store.py` — `ReadStore.list_chapters(status)/get_chapter/list_world_entries(domain)/list_characters/list_unconsumed_signals(target_agent)`.
- `novelizer/agents/base.py` — `ChapterDraft`, `Runner` Protocol.
- `novelizer/agents/author.py` — `Author(runner, read_store, event_store, interval)` with `readiness/poll/work/commit/run_once`, `build_author_runner(settings)`.
- `novelizer/agents/llm.py` — `build_chat_model(model, base_url, api_key, temperature)`.
- `novelizer/runtime.py` — `Runtime(settings, runner=None)` with `.events/.projector/.read/.author`, `start()/close()`.
- `novelizer/tui/app.py` — `NovelizerApp(runtime)`, `format_event(ev)`, background `_projector_loop/_author_loop/_feed_loop` (each guarded), `messages` buffer.
- `novelizer/director/cli.py` — `cli` group (bare launches TUI), `seed/chapters/read`, `_with_runtime`.
- `novelizer/store/models.py` — entity models incl. `RetconRequest`, `DirectorSignal`, `SignalKind`, `WorldEntry`, `Character`, `Chapter`, `EditorialStatus`, `RetconStatus`, `Domain`, `CanonStatus` (UNCHANGED — reuse as payloads).
- Config: `Settings` with `db_path`, `llm_base_url`, `llm_api_key`, `author_model`, `author_temperature`, `author_interval`, `projector_interval`.

## Global Constraints

- **Python** `>=3.13`.
- **Event sourcing is absolute:** the `events` table is the sole source of truth; only the Projector writes projection tables; agents change state ONLY by having their `Committer` append events. No agent calls `EventStore.append` directly and no agent writes a projection.
- **The `Committer` seam:** every agent receives a `Committer` and writes canon exclusively via `committer.commit(agent_name, event_type, aggregate_id, payload)`. The M1.1 `Committer` appends the real event. Do not add gating logic here — that is M1.3.
- **Domain event names** use `EventType` constants (`<domain>.<verb>`), never inline string literals.
- **All LLM access** is via deepagents runners built on OpenAI-compatible endpoints (`build_chat_model`); agents take an injected `Runner` so they are testable with fakes.
- **TDD, black-box first:** failing test → watch fail → implement → watch pass → commit. Agent/store tests assert through public interfaces against a real store with only the LLM faked; use `hypothesis` where an invariant generalizes.
- **`asyncio_mode = "auto"`** — `async def test_*` needs no decorator.
- **`store/models.py` is unchanged** — reuse its models as event payloads.
- **DRY:** (de)serialize via pydantic `model_dump_json()`/`model_validate_json()`.

---

### Task 1: Canon — retcon-request events, projection, and reads

**Files:**
- Modify: `novelizer/canon/events.py` (add EventType constants)
- Modify: `novelizer/canon/projector.py` (add `retcon_requests` table + dispatch)
- Modify: `novelizer/canon/read_store.py` (add `list_retcon_requests`, `get_character`)
- Test: `tests/canon/test_retcons.py`

**Interfaces:**
- Produces:
  - `EventType.RETCON_REQUEST_CREATED = "retcon_request.created"`, `RETCON_REQUEST_RESOLVED = "retcon_request.resolved"`, `RETCON_REQUEST_REJECTED = "retcon_request.rejected"`.
  - Projection table `retcon_requests(id, data, status)`; Projector handles the three events (created upserts; resolved/rejected update `status` + re-store data).
  - `ReadStore.list_retcon_requests(status: str | None = None) -> list[RetconRequest]`, `ReadStore.get_character(character_id: str) -> Character | None`.

- [ ] **Step 1: Write the failing test**

`tests/canon/test_retcons.py`:
```python
import os
import tempfile
import pytest
from novelizer.canon.event_store import EventStore
from novelizer.canon.projector import Projector
from novelizer.canon.read_store import ReadStore
from novelizer.canon.events import EventType
from novelizer.store.models import RetconRequest, RetconStatus, Character


@pytest.fixture
async def stack():
    fd, path = tempfile.mkstemp(suffix=".db"); os.close(fd)
    events = EventStore(path); await events.init()
    proj = Projector(events, path); await proj.init()
    read = ReadStore(path); await read.init()
    yield events, proj, read
    await read.close(); await proj.close(); await events.close(); os.unlink(path)


async def test_retcon_created_then_resolved(stack):
    events, proj, read = stack
    req = RetconRequest(id="r1", description="scar hand mismatch",
                        conflicting_entry_ids=["a", "b"], proposed_resolution="left hand")
    await events.append(EventType.RETCON_REQUEST_CREATED, "r1", req)
    await proj.catch_up()
    assert [r.id for r in await read.list_retcon_requests(status=RetconStatus.open)] == ["r1"]
    resolved = req.model_copy(update={"status": RetconStatus.resolved, "resolved_by": "retconner"})
    await events.append(EventType.RETCON_REQUEST_RESOLVED, "r1", resolved)
    await proj.catch_up()
    assert await read.list_retcon_requests(status=RetconStatus.open) == []
    assert [r.id for r in await read.list_retcon_requests(status=RetconStatus.resolved)] == ["r1"]


async def test_get_character(stack):
    events, proj, read = stack
    await events.append(EventType.CHARACTER_CREATED, "c1", Character(id="c1", name="Mira", arc_status="wary"))
    await proj.catch_up()
    got = await read.get_character("c1")
    assert got is not None and got.name == "Mira" and got.arc_status == "wary"
    assert await read.get_character("nope") is None
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/canon/test_retcons.py -v`
Expected: FAIL (`AttributeError: RETCON_REQUEST_CREATED` / missing methods).

- [ ] **Step 3: Implement**

In `novelizer/canon/events.py`, add to `class EventType` (after the director-signal constants):
```python
    RETCON_REQUEST_CREATED = "retcon_request.created"
    RETCON_REQUEST_RESOLVED = "retcon_request.resolved"
    RETCON_REQUEST_REJECTED = "retcon_request.rejected"
```

In `novelizer/canon/projector.py`, add the table to the `_CREATE` script (inside the triple-quoted SQL, before `projector_state`):
```sql
CREATE TABLE IF NOT EXISTS retcon_requests (
    id TEXT PRIMARY KEY, data TEXT NOT NULL, status TEXT NOT NULL
);
```
Add `"retcon_requests"` to the tuple in `_reset_state`. In `_apply`, add these branches (after the director-signal branches):
```python
        elif t == EventType.RETCON_REQUEST_CREATED:
            await self._conn.execute(
                "INSERT OR REPLACE INTO retcon_requests (id, data, status) VALUES (?,?,?)",
                (p["id"], data, p.get("status", "open")),
            )
        elif t == EventType.RETCON_REQUEST_RESOLVED or t == EventType.RETCON_REQUEST_REJECTED:
            await self._conn.execute(
                "INSERT OR REPLACE INTO retcon_requests (id, data, status) VALUES (?,?,?)",
                (p["id"], data, p.get("status", "resolved" if t == EventType.RETCON_REQUEST_RESOLVED else "rejected")),
            )
```

In `novelizer/canon/read_store.py`, add imports for `RetconRequest` (extend the existing `from novelizer.store.models import ...` line to include `RetconRequest`) and add methods:
```python
    async def get_character(self, character_id: str) -> Optional[Character]:
        cur = await self._conn.execute("SELECT data FROM characters WHERE id=?", (character_id,))
        row = await cur.fetchone()
        return Character.model_validate_json(row[0]) if row else None

    async def list_retcon_requests(self, status: Optional[str] = None) -> list[RetconRequest]:
        if status:
            cur = await self._conn.execute(
                "SELECT data FROM retcon_requests WHERE status=? ORDER BY rowid", (status,)
            )
        else:
            cur = await self._conn.execute("SELECT data FROM retcon_requests ORDER BY rowid")
        return [RetconRequest.model_validate_json(r[0]) for r in await cur.fetchall()]
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/canon/test_retcons.py -v`
Expected: PASS (2 tests). Also run `uv run pytest tests/canon/ -q` — all green.

- [ ] **Step 5: Commit**

```bash
git add novelizer/canon/events.py novelizer/canon/projector.py novelizer/canon/read_store.py tests/canon/test_retcons.py
git commit -m "feat: add retcon-request events, projection, and reads to canon"
```

---

### Task 2: Committer write-seam

**Files:**
- Create: `novelizer/canon/committer.py`
- Test: `tests/canon/test_committer.py`

**Interfaces:**
- Consumes: `EventStore`.
- Produces: `class Committer`:
  - `__init__(self, event_store: EventStore)`
  - `async def commit(self, agent_name: str, event_type: str, aggregate_id: str, payload: BaseModel) -> None` — appends the event. (`agent_name` is unused in M1.1 but is part of the seam contract so M1.3's gating implementation can key on it without changing agents.)

- [ ] **Step 1: Write the failing test**

`tests/canon/test_committer.py`:
```python
import os
import tempfile
import pytest
from novelizer.canon.event_store import EventStore
from novelizer.canon.committer import Committer
from novelizer.canon.events import EventType
from novelizer.store.models import Chapter


@pytest.fixture
async def events():
    fd, path = tempfile.mkstemp(suffix=".db"); os.close(fd)
    es = EventStore(path); await es.init()
    yield es
    await es.close(); os.unlink(path)


async def test_commit_appends_the_real_event(events):
    c = Committer(events)
    await c.commit("author", EventType.CHAPTER_CREATED, "ch1", Chapter(id="ch1", title="One", prose="p"))
    log = await events.events_since(0)
    assert len(log) == 1
    assert log[0].event_type == EventType.CHAPTER_CREATED
    assert log[0].aggregate_id == "ch1"
    assert log[0].payload["title"] == "One"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/canon/test_committer.py -v`
Expected: FAIL (`ModuleNotFoundError`).

- [ ] **Step 3: Implement**

Create `novelizer/canon/committer.py`:
```python
from __future__ import annotations
from pydantic import BaseModel
from novelizer.canon.event_store import EventStore


class Committer:
    """The single seam through which agents write canon.

    M1.1 appends the event directly (full-auto). M1.3 will introduce a gating
    subclass/replacement that may append a proposal instead, keyed on
    ``agent_name`` and ``event_type`` — without any agent changing.
    """

    def __init__(self, event_store: EventStore) -> None:
        self._events = event_store

    async def commit(self, agent_name: str, event_type: str, aggregate_id: str, payload: BaseModel) -> None:
        await self._events.append(event_type, aggregate_id, payload)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/canon/test_committer.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add novelizer/canon/committer.py tests/canon/test_committer.py
git commit -m "feat: add Committer write-seam (append-only; gating deferred to M1.3)"
```

---

### Task 3: BaseAgent + refactor Author onto it

**Files:**
- Modify: `novelizer/agents/base.py` (add `BaseAgent`)
- Modify: `novelizer/agents/author.py` (extend `BaseAgent`, write via `Committer`)
- Modify: `novelizer/runtime.py` (construct `Committer`, pass to `Author`)
- Test: `tests/agents/test_author.py` (update to new constructor), `tests/agents/test_base.py` (new)

**Interfaces:**
- Consumes: `Runner`, `ReadStore`, `Committer`, `EventType`, `DirectorSignal`.
- Produces:
  - `class BaseAgent`: `name: str`; `__init__(self, runner, read_store, committer, interval, name=None)`; `pause()/resume()`; `ready_for_interval(now: float) -> bool`; `mark_ran(now: float)`; `async readiness() -> float` (default 0.0); `async run_once() -> None` (default no-op); `async _consume_signals(signals: list[DirectorSignal]) -> None` (commits `DIRECTOR_SIGNAL_CONSUMED` for each via the committer).
  - `Author(runner, read_store, committer, interval=300)` now extends `BaseAgent`, `name="author"`, and commits chapters + consumes signals via the committer.

- [ ] **Step 1: Write the failing tests**

Add `tests/agents/test_base.py`:
```python
import os
import tempfile
import pytest
from novelizer.canon.event_store import EventStore
from novelizer.canon.projector import Projector
from novelizer.canon.read_store import ReadStore
from novelizer.canon.committer import Committer
from novelizer.canon.events import EventType
from novelizer.agents.base import BaseAgent
from novelizer.store.models import DirectorSignal, SignalKind


@pytest.fixture
async def stack():
    fd, path = tempfile.mkstemp(suffix=".db"); os.close(fd)
    events = EventStore(path); await events.init()
    proj = Projector(events, path); await proj.init()
    read = ReadStore(path); await read.init()
    yield events, proj, read, Committer(events)
    await read.close(); await proj.close(); await events.close(); os.unlink(path)


def test_interval_and_pause():
    a = BaseAgent(runner=None, read_store=None, committer=None, interval=10, name="x")
    assert a.name == "x"
    assert a.ready_for_interval(now=100) is True
    a.mark_ran(now=100)
    assert a.ready_for_interval(now=105) is False
    assert a.ready_for_interval(now=110) is True
    a.pause(); assert a.paused is True
    a.resume(); assert a.paused is False


async def test_consume_signals_marks_consumed(stack):
    events, proj, read, committer = stack
    await events.append(EventType.DIRECTOR_SIGNAL_CREATED, "s1",
                        DirectorSignal(id="s1", kind=SignalKind.seed, body="x"))
    await proj.catch_up()
    agent = BaseAgent(runner=None, read_store=read, committer=committer, interval=0, name="a")
    sigs = await read.list_unconsumed_signals()
    await agent._consume_signals(sigs)
    await proj.catch_up()
    assert await read.list_unconsumed_signals() == []
```

Update `tests/agents/test_author.py` — replace the `FakeRunner`/fixtures/construction so `Author` is built with a `Committer` instead of the raw `EventStore`. The full replacement file:
```python
import os
import tempfile
import pytest
from novelizer.canon.event_store import EventStore
from novelizer.canon.projector import Projector
from novelizer.canon.read_store import ReadStore
from novelizer.canon.committer import Committer
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
    yield events, proj, read, Committer(events)
    await read.close(); await proj.close(); await events.close(); os.unlink(path)


async def test_readiness_drops_with_draft_backlog(stack):
    events, proj, read, committer = stack
    for i in range(3):
        await events.append(EventType.CHAPTER_CREATED, f"c{i}", Chapter(id=f"c{i}", title=str(i), prose="p"))
    await proj.catch_up()
    author = Author(FakeRunner(None), read, committer)
    assert await author.readiness() == 0.0


async def test_run_once_appends_and_projects_a_chapter(stack):
    events, proj, read, committer = stack
    draft = ChapterDraft(title="The Salt Road", prose="The road held its salt like a grudge.")
    author = Author(FakeRunner(draft), read, committer)
    await author.run_once()
    await proj.catch_up()
    assert "The Salt Road" in [c.title for c in await read.list_chapters()]


async def test_run_once_consumes_targeted_signals(stack):
    events, proj, read, committer = stack
    await events.append(EventType.DIRECTOR_SIGNAL_CREATED, "s1",
                        DirectorSignal(id="s1", kind=SignalKind.seed, body="a storm is coming"))
    await proj.catch_up()
    author = Author(FakeRunner(ChapterDraft(title="T", prose="P")), read, committer)
    await author.run_once()
    await proj.catch_up()
    assert await read.list_unconsumed_signals(target_agent="author") == []


async def test_work_returns_none_is_noop(stack):
    events, proj, read, committer = stack
    author = Author(FakeRunner(None), read, committer)
    await author.run_once()
    await proj.catch_up()
    assert await read.list_chapters() == []
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/agents/test_base.py tests/agents/test_author.py -v`
Expected: FAIL (`BaseAgent` missing / `Author` constructor mismatch).

- [ ] **Step 3: Implement**

In `novelizer/agents/base.py`, add (keep existing `ChapterDraft`/`Runner`):
```python
from novelizer.canon.events import EventType


class BaseAgent:
    name: str = "agent"

    def __init__(self, runner, read_store, committer, interval: int, name: str | None = None) -> None:
        self._runner = runner
        self._read = read_store
        self._committer = committer
        self.interval = interval
        if name is not None:
            self.name = name
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
```
Note: the import of `EventType` must not create a cycle — `events.py` imports nothing from `agents`, so this is safe.

Rewrite `novelizer/agents/author.py` to extend `BaseAgent` and use the committer:
```python
from __future__ import annotations
from novelizer.agents.base import BaseAgent, ChapterDraft, Runner
from novelizer.canon.read_store import ReadStore
from novelizer.canon.committer import Committer
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


class Author(BaseAgent):
    def __init__(self, runner: Runner, read_store: ReadStore, committer: Committer, interval: int = 300) -> None:
        super().__init__(runner, read_store, committer, interval, name="author")

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
        result = await self._runner.ainvoke({"messages": [{"role": "user", "content": _summarize(ctx)}]})
        return result.get("structured_response")

    async def commit(self, draft: ChapterDraft | None, ctx: dict) -> None:
        if draft is None:
            return
        chapter = Chapter(title=draft.title, prose=draft.prose, character_ids=draft.character_ids)
        await self._committer.commit(self.name, EventType.CHAPTER_CREATED, chapter.id, chapter)
        await self._consume_signals(ctx["signals"])

    async def run_once(self) -> None:
        ctx = await self.poll()
        draft = await self.work(ctx)
        await self.commit(draft, ctx)


def build_author_runner(settings):
    from deepagents import create_deep_agent
    from novelizer.agents.llm import build_chat_model
    model = build_chat_model(settings.author_model, settings.llm_base_url, settings.llm_api_key, settings.author_temperature)
    return create_deep_agent(model=model, system_prompt=AUTHOR_SYSTEM_PROMPT, response_format=ChapterDraft)
```

In `novelizer/runtime.py`, import and construct the committer, and pass it to Author. Change the imports and `start()`:
```python
from novelizer.canon.committer import Committer
```
In `__init__`, after `self.read = ReadStore(...)` add `self.committer = Committer(self.events)`. In `start()`, change the Author construction to:
```python
        self.author = Author(runner, self.read, self.committer, interval=self.settings.author_interval)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/agents/test_base.py tests/agents/test_author.py tests/test_runtime.py -q`
Expected: PASS. Then `uv run pytest -q` — full suite green.

- [ ] **Step 5: Commit**

```bash
git add novelizer/agents/base.py novelizer/agents/author.py novelizer/runtime.py tests/agents/test_base.py tests/agents/test_author.py
git commit -m "refactor: add BaseAgent; Author writes canon via Committer seam"
```

---

### Task 4: Agent response schemas

**Files:**
- Create: `novelizer/agents/schemas.py`
- Test: `tests/agents/test_schemas.py`

**Interfaces:**
- Produces (deepagents `response_format` targets — LLM fills these, ids/timestamps are generated at commit):
  - `WorldEntryDraft(title: str, body: str, domain: str = "physical", tags: list[str] = [], supersedes_id: str | None = None)`
  - `WorldEntriesDraft(entries: list[WorldEntryDraft] = [])`
  - `CharacterUpdate(id: str, arc_status: str = "", traits: str | None = None, motivations: str | None = None, backstory: str | None = None)`
  - `RetconDraft(description: str, conflicting_entry_ids: list[str] = [], proposed_resolution: str = "")`
  - `KeeperOutput(updated_characters: list[CharacterUpdate] = [], retcon_requests: list[RetconDraft] = [])`
  - `EditorVerdict(verdict: Literal["approve","revise"] = "approve", notes: str = "")`
  - `ContinuityOutput(retcon_requests: list[RetconDraft] = [])`
  - `RetconAmendments(amended_entries: list[WorldEntryDraft] = [])`

- [ ] **Step 1: Write the failing test**

`tests/agents/test_schemas.py`:
```python
from novelizer.agents.schemas import (
    WorldEntryDraft, WorldEntriesDraft, CharacterUpdate, RetconDraft,
    KeeperOutput, EditorVerdict, ContinuityOutput, RetconAmendments,
)


def test_world_entries_draft_roundtrip():
    d = WorldEntriesDraft(entries=[WorldEntryDraft(title="Brinemarsh", body="salt flats")])
    again = WorldEntriesDraft.model_validate_json(d.model_dump_json())
    assert again.entries[0].domain == "physical"


def test_keeper_output_defaults_empty():
    k = KeeperOutput()
    assert k.updated_characters == [] and k.retcon_requests == []


def test_editor_verdict_literal():
    assert EditorVerdict(verdict="revise", notes="tighten act two").verdict == "revise"


def test_retcon_amendments_carry_supersedes():
    a = RetconAmendments(amended_entries=[WorldEntryDraft(title="North", body="v2", supersedes_id="old1")])
    assert a.amended_entries[0].supersedes_id == "old1"


def test_continuity_and_character_shapes():
    ContinuityOutput(retcon_requests=[RetconDraft(description="x", proposed_resolution="y")])
    assert CharacterUpdate(id="c1", arc_status="wary").traits is None
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/agents/test_schemas.py -v`
Expected: FAIL (`ModuleNotFoundError`).

- [ ] **Step 3: Implement**

Create `novelizer/agents/schemas.py`:
```python
from __future__ import annotations
from typing import Literal, Optional
from pydantic import BaseModel, Field


class WorldEntryDraft(BaseModel):
    title: str
    body: str
    domain: str = "physical"
    tags: list[str] = Field(default_factory=list)
    supersedes_id: Optional[str] = None


class WorldEntriesDraft(BaseModel):
    entries: list[WorldEntryDraft] = Field(default_factory=list)


class CharacterUpdate(BaseModel):
    id: str
    arc_status: str = ""
    traits: Optional[str] = None
    motivations: Optional[str] = None
    backstory: Optional[str] = None


class RetconDraft(BaseModel):
    description: str
    conflicting_entry_ids: list[str] = Field(default_factory=list)
    proposed_resolution: str = ""


class KeeperOutput(BaseModel):
    updated_characters: list[CharacterUpdate] = Field(default_factory=list)
    retcon_requests: list[RetconDraft] = Field(default_factory=list)


class EditorVerdict(BaseModel):
    verdict: Literal["approve", "revise"] = "approve"
    notes: str = ""


class ContinuityOutput(BaseModel):
    retcon_requests: list[RetconDraft] = Field(default_factory=list)


class RetconAmendments(BaseModel):
    amended_entries: list[WorldEntryDraft] = Field(default_factory=list)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/agents/test_schemas.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add novelizer/agents/schemas.py tests/agents/test_schemas.py
git commit -m "feat: add agent response schemas for deepagents structured output"
```

---

### Task 5: WorldArchitect agent

**Files:**
- Create: `novelizer/agents/world_architect.py`
- Test: `tests/agents/test_world_architect.py`

**Interfaces:**
- Consumes: `BaseAgent`, `WorldEntriesDraft`, `ReadStore`, `Committer`, `EventType`, `WorldEntry`, `build_chat_model`.
- Produces: `class WorldArchitect(BaseAgent)` (`name="world_architect"`), `build_world_architect_runner(settings)`. `readiness()` = `max(0.2, 1.0 - count/50)`. `commit` appends one `world_entry.created` per drafted entry (via committer) and consumes signals.

- [ ] **Step 1: Write the failing test**

`tests/agents/test_world_architect.py`:
```python
import os
import tempfile
import pytest
from novelizer.canon.event_store import EventStore
from novelizer.canon.projector import Projector
from novelizer.canon.read_store import ReadStore
from novelizer.canon.committer import Committer
from novelizer.agents.world_architect import WorldArchitect
from novelizer.agents.schemas import WorldEntriesDraft, WorldEntryDraft


class FakeRunner:
    def __init__(self, out): self._out = out
    async def ainvoke(self, inputs): return {"structured_response": self._out}


@pytest.fixture
async def stack():
    fd, path = tempfile.mkstemp(suffix=".db"); os.close(fd)
    events = EventStore(path); await events.init()
    proj = Projector(events, path); await proj.init()
    read = ReadStore(path); await read.init()
    yield events, proj, read, Committer(events)
    await read.close(); await proj.close(); await events.close(); os.unlink(path)


async def test_readiness_high_when_world_empty(stack):
    events, proj, read, committer = stack
    agent = WorldArchitect(FakeRunner(WorldEntriesDraft()), read, committer)
    assert await agent.readiness() == 1.0


async def test_run_once_creates_world_entries(stack):
    events, proj, read, committer = stack
    out = WorldEntriesDraft(entries=[
        WorldEntryDraft(title="The Brinemarsh", body="A salt flat.", domain="physical", tags=["geo"]),
        WorldEntryDraft(title="Salt Guild", body="Controls the trade.", domain="social"),
    ])
    agent = WorldArchitect(FakeRunner(out), read, committer)
    await agent.run_once()
    await proj.catch_up()
    titles = {e.title for e in await read.list_world_entries()}
    assert {"The Brinemarsh", "Salt Guild"} <= titles
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/agents/test_world_architect.py -v`
Expected: FAIL (`ModuleNotFoundError`).

- [ ] **Step 3: Implement**

Create `novelizer/agents/world_architect.py`:
```python
from __future__ import annotations
from novelizer.agents.base import BaseAgent, Runner
from novelizer.agents.schemas import WorldEntriesDraft
from novelizer.canon.read_store import ReadStore
from novelizer.canon.committer import Committer
from novelizer.canon.events import EventType
from novelizer.store.models import WorldEntry

SYSTEM_PROMPT = """You are the World Architect for an ever-expanding fictional world.
Generate new lore, geography, factions, history, and cosmology. You receive a summary of
what already exists plus any director seeds; identify thin or unexplored areas and expand them.
Return 1-3 new world entries, each with a title, 2-4 paragraphs of rich body lore, a domain
(one of: physical, social, metaphysical, historical, other), and tags."""


class WorldArchitect(BaseAgent):
    def __init__(self, runner: Runner, read_store: ReadStore, committer: Committer, interval: int = 120) -> None:
        super().__init__(runner, read_store, committer, interval, name="world_architect")

    async def readiness(self) -> float:
        count = len(await self._read.list_world_entries())
        return max(0.2, 1.0 - count / 50)

    async def poll(self) -> dict:
        return {
            "entries": await self._read.list_world_entries(),
            "signals": await self._read.list_unconsumed_signals(target_agent=self.name),
        }

    async def work(self, ctx: dict) -> WorldEntriesDraft | None:
        existing = "\n".join(f"- [{e.domain}] {e.title}: {e.body[:100]}" for e in ctx["entries"][:20]) or "The world is empty."
        seeds = "\n".join(f"Director seed: {s.body}" for s in ctx["signals"]) or "None."
        msg = f"Existing world entries:\n{existing}\n\nDirector seeds:\n{seeds}\n\nGenerate new world entries."
        result = await self._runner.ainvoke({"messages": [{"role": "user", "content": msg}]})
        return result.get("structured_response")

    async def commit(self, draft: WorldEntriesDraft | None, ctx: dict) -> None:
        if draft is not None:
            for e in draft.entries:
                entry = WorldEntry(title=e.title, body=e.body, domain=e.domain, tags=e.tags)
                await self._committer.commit(self.name, EventType.WORLD_ENTRY_CREATED, entry.id, entry)
        await self._consume_signals(ctx["signals"])

    async def run_once(self) -> None:
        ctx = await self.poll()
        draft = await self.work(ctx)
        await self.commit(draft, ctx)


def build_world_architect_runner(settings):
    from deepagents import create_deep_agent
    from novelizer.agents.llm import build_chat_model
    model = build_chat_model(settings.agent_model, settings.llm_base_url, settings.llm_api_key, settings.agent_temperature)
    return create_deep_agent(model=model, system_prompt=SYSTEM_PROMPT, response_format=WorldEntriesDraft)
```
Note: `settings.agent_model`/`settings.agent_temperature` are added in Task 11. If Task 5 is implemented before Task 11, temporarily use `settings.author_model`/`settings.author_temperature` and switch in Task 11 — but the tests here never call `build_*_runner` (they inject `FakeRunner`), so this does not affect Task 5's tests.

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/agents/test_world_architect.py -v`
Expected: PASS (2 tests).

- [ ] **Step 5: Commit**

```bash
git add novelizer/agents/world_architect.py tests/agents/test_world_architect.py
git commit -m "feat: add WorldArchitect agent (deepagents, Committer seam)"
```

---

### Task 6: CharacterKeeper agent

**Files:**
- Create: `novelizer/agents/character_keeper.py`
- Test: `tests/agents/test_character_keeper.py`

**Interfaces:**
- Produces: `class CharacterKeeper(BaseAgent)` (`name="character_keeper"`), `build_character_keeper_runner(settings)`. `readiness()` = `0.5` when characters exist and there are chapters, else `0.2`. `commit`: for each `CharacterUpdate`, load the current `Character` via `read.get_character(id)`, apply non-None fields, append `character.updated`; for each `RetconDraft`, append `retcon_request.created` (build a `RetconRequest`).

- [ ] **Step 1: Write the failing test**

`tests/agents/test_character_keeper.py`:
```python
import os
import tempfile
import pytest
from novelizer.canon.event_store import EventStore
from novelizer.canon.projector import Projector
from novelizer.canon.read_store import ReadStore
from novelizer.canon.committer import Committer
from novelizer.canon.events import EventType
from novelizer.agents.character_keeper import CharacterKeeper
from novelizer.agents.schemas import KeeperOutput, CharacterUpdate, RetconDraft
from novelizer.store.models import Character, Chapter, RetconStatus


class FakeRunner:
    def __init__(self, out): self._out = out
    async def ainvoke(self, inputs): return {"structured_response": self._out}


@pytest.fixture
async def stack():
    fd, path = tempfile.mkstemp(suffix=".db"); os.close(fd)
    events = EventStore(path); await events.init()
    proj = Projector(events, path); await proj.init()
    read = ReadStore(path); await read.init()
    yield events, proj, read, Committer(events)
    await read.close(); await proj.close(); await events.close(); os.unlink(path)


async def test_updates_character_arc_and_files_retcon(stack):
    events, proj, read, committer = stack
    await events.append(EventType.CHARACTER_CREATED, "c1", Character(id="c1", name="Mira", traits="stoic", arc_status="wary"))
    await events.append(EventType.CHAPTER_CREATED, "ch1", Chapter(id="ch1", title="One", prose="Mira wept openly."))
    await proj.catch_up()
    out = KeeperOutput(
        updated_characters=[CharacterUpdate(id="c1", arc_status="cracking")],
        retcon_requests=[RetconDraft(description="stoic vs weeping", conflicting_entry_ids=["c1", "ch1"], proposed_resolution="show restraint")],
    )
    agent = CharacterKeeper(FakeRunner(out), read, committer)
    await agent.run_once()
    await proj.catch_up()
    mira = await read.get_character("c1")
    assert mira.arc_status == "cracking" and mira.name == "Mira" and mira.traits == "stoic"
    assert len(await read.list_retcon_requests(status=RetconStatus.open)) == 1


async def test_noop_when_no_characters(stack):
    events, proj, read, committer = stack
    agent = CharacterKeeper(FakeRunner(KeeperOutput()), read, committer)
    await agent.run_once()
    await proj.catch_up()
    assert await read.list_characters() == []
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/agents/test_character_keeper.py -v`
Expected: FAIL (`ModuleNotFoundError`).

- [ ] **Step 3: Implement**

Create `novelizer/agents/character_keeper.py`:
```python
from __future__ import annotations
from novelizer.agents.base import BaseAgent, Runner
from novelizer.agents.schemas import KeeperOutput
from novelizer.canon.read_store import ReadStore
from novelizer.canon.committer import Committer
from novelizer.canon.events import EventType
from novelizer.store.models import RetconRequest

SYSTEM_PROMPT = """You are the Character Keeper for a living fictional world.
You receive characters (with traits and arcs) and recent prose chapters. Your tasks:
1. Update each character's arc_status to reflect what recent chapters show.
2. Flag behavioral contradictions between a character's defined traits and their actions.
Return updated_characters (id + revised arc_status, and any corrected traits/motivations/backstory)
and retcon_requests (description, conflicting_entry_ids, proposed_resolution)."""


class CharacterKeeper(BaseAgent):
    def __init__(self, runner: Runner, read_store: ReadStore, committer: Committer, interval: int = 120) -> None:
        super().__init__(runner, read_store, committer, interval, name="character_keeper")

    async def readiness(self) -> float:
        chars = await self._read.list_characters()
        chapters = await self._read.list_chapters()
        return 0.5 if (chars and chapters) else 0.2

    async def poll(self) -> dict:
        chapters = await self._read.list_chapters()
        return {"characters": await self._read.list_characters(), "recent": chapters[-5:]}

    async def work(self, ctx: dict) -> KeeperOutput | None:
        if not ctx["characters"]:
            return None
        chars = "\n".join(f"- {c.name} (id:{c.id}): traits={c.traits}, arc={c.arc_status}" for c in ctx["characters"])
        chapters = "\n\n".join(f"Chapter '{c.title}': {c.prose[:300]}" for c in ctx["recent"]) or "None."
        msg = f"Characters:\n{chars}\n\nRecent chapters:\n{chapters}"
        result = await self._runner.ainvoke({"messages": [{"role": "user", "content": msg}]})
        return result.get("structured_response")

    async def commit(self, out: KeeperOutput | None, ctx: dict) -> None:
        if out is None:
            return
        for upd in out.updated_characters:
            current = await self._read.get_character(upd.id)
            if current is None:
                continue
            fields = {"arc_status": upd.arc_status}
            for f in ("traits", "motivations", "backstory"):
                v = getattr(upd, f)
                if v is not None:
                    fields[f] = v
            updated = current.model_copy(update=fields)
            await self._committer.commit(self.name, EventType.CHARACTER_UPDATED, updated.id, updated)
        for r in out.retcon_requests:
            req = RetconRequest(description=r.description, conflicting_entry_ids=r.conflicting_entry_ids,
                                proposed_resolution=r.proposed_resolution)
            await self._committer.commit(self.name, EventType.RETCON_REQUEST_CREATED, req.id, req)

    async def run_once(self) -> None:
        ctx = await self.poll()
        out = await self.work(ctx)
        await self.commit(out, ctx)


def build_character_keeper_runner(settings):
    from deepagents import create_deep_agent
    from novelizer.agents.llm import build_chat_model
    model = build_chat_model(settings.agent_model, settings.llm_base_url, settings.llm_api_key, settings.agent_temperature)
    return create_deep_agent(model=model, system_prompt=SYSTEM_PROMPT, response_format=KeeperOutput)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/agents/test_character_keeper.py -v`
Expected: PASS (2 tests).

- [ ] **Step 5: Commit**

```bash
git add novelizer/agents/character_keeper.py tests/agents/test_character_keeper.py
git commit -m "feat: add CharacterKeeper agent (arc updates + retcon flags via Committer)"
```

---

### Task 7: Editor agent

**Files:**
- Create: `novelizer/agents/editor.py`
- Test: `tests/agents/test_editor.py`

**Interfaces:**
- Produces: `class Editor(BaseAgent)` (`name="editor"`), `build_editor_runner(settings)`. `readiness()` = `min(1.0, drafts/3)`. Targets the oldest `draft` chapter. On `approve`: append `chapter.status_changed` with the chapter promoted to `reviewed` (+ editor_notes). On `revise`: append a `director_signal.created` (kind=note, target_agent="author", body=notes) — leaving the chapter a draft. Uses the committer for both.

- [ ] **Step 1: Write the failing test**

`tests/agents/test_editor.py`:
```python
import os
import tempfile
import pytest
from novelizer.canon.event_store import EventStore
from novelizer.canon.projector import Projector
from novelizer.canon.read_store import ReadStore
from novelizer.canon.committer import Committer
from novelizer.canon.events import EventType
from novelizer.agents.editor import Editor
from novelizer.agents.schemas import EditorVerdict
from novelizer.store.models import Chapter, EditorialStatus


class FakeRunner:
    def __init__(self, out): self._out = out
    async def ainvoke(self, inputs): return {"structured_response": self._out}


@pytest.fixture
async def stack():
    fd, path = tempfile.mkstemp(suffix=".db"); os.close(fd)
    events = EventStore(path); await events.init()
    proj = Projector(events, path); await proj.init()
    read = ReadStore(path); await read.init()
    yield events, proj, read, Committer(events)
    await read.close(); await proj.close(); await events.close(); os.unlink(path)


async def test_readiness_scales_with_drafts(stack):
    events, proj, read, committer = stack
    for i in range(3):
        await events.append(EventType.CHAPTER_CREATED, f"c{i}", Chapter(id=f"c{i}", title=str(i), prose="p"))
    await proj.catch_up()
    assert await Editor(FakeRunner(None), read, committer).readiness() == 1.0


async def test_approve_promotes_to_reviewed(stack):
    events, proj, read, committer = stack
    await events.append(EventType.CHAPTER_CREATED, "c1", Chapter(id="c1", title="One", prose="p"))
    await proj.catch_up()
    agent = Editor(FakeRunner(EditorVerdict(verdict="approve", notes="clean")), read, committer)
    await agent.run_once()
    await proj.catch_up()
    ch = await read.get_chapter("c1")
    assert ch.editorial_status == EditorialStatus.reviewed and ch.editor_notes == "clean"


async def test_revise_keeps_draft_and_notes_author(stack):
    events, proj, read, committer = stack
    await events.append(EventType.CHAPTER_CREATED, "c1", Chapter(id="c1", title="One", prose="p"))
    await proj.catch_up()
    agent = Editor(FakeRunner(EditorVerdict(verdict="revise", notes="middle sags")), read, committer)
    await agent.run_once()
    await proj.catch_up()
    assert (await read.get_chapter("c1")).editorial_status == EditorialStatus.draft
    notes = await read.list_unconsumed_signals(target_agent="author")
    assert any("middle sags" in s.body for s in notes)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/agents/test_editor.py -v`
Expected: FAIL (`ModuleNotFoundError`).

- [ ] **Step 3: Implement**

Create `novelizer/agents/editor.py`:
```python
from __future__ import annotations
from novelizer.agents.base import BaseAgent, Runner
from novelizer.agents.schemas import EditorVerdict
from novelizer.canon.read_store import ReadStore
from novelizer.canon.committer import Committer
from novelizer.canon.events import EventType
from novelizer.store.models import DirectorSignal, SignalKind, EditorialStatus

SYSTEM_PROMPT = """You are the Editor of a living fictional world's story. Review the given chapter
for prose quality, narrative coherence, and pacing. Return a verdict of "approve" or "revise" and
notes: if revising, specific actionable feedback; if approving, brief praise."""


class Editor(BaseAgent):
    def __init__(self, runner: Runner, read_store: ReadStore, committer: Committer, interval: int = 120) -> None:
        super().__init__(runner, read_store, committer, interval, name="editor")

    async def readiness(self) -> float:
        drafts = len(await self._read.list_chapters(status=EditorialStatus.draft))
        return min(1.0, drafts / 3)

    async def poll(self) -> dict:
        drafts = await self._read.list_chapters(status=EditorialStatus.draft)
        return {"target": drafts[0] if drafts else None}

    async def work(self, ctx: dict) -> EditorVerdict | None:
        ch = ctx["target"]
        if ch is None:
            return None
        msg = f"Chapter title: {ch.title}\n\nProse:\n{ch.prose}"
        result = await self._runner.ainvoke({"messages": [{"role": "user", "content": msg}]})
        return result.get("structured_response")

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

    async def run_once(self) -> None:
        ctx = await self.poll()
        verdict = await self.work(ctx)
        await self.commit(verdict, ctx)


def build_editor_runner(settings):
    from deepagents import create_deep_agent
    from novelizer.agents.llm import build_chat_model
    model = build_chat_model(settings.agent_model, settings.llm_base_url, settings.llm_api_key, settings.agent_temperature)
    return create_deep_agent(model=model, system_prompt=SYSTEM_PROMPT, response_format=EditorVerdict)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/agents/test_editor.py -v`
Expected: PASS (3 tests).

- [ ] **Step 5: Commit**

```bash
git add novelizer/agents/editor.py tests/agents/test_editor.py
git commit -m "feat: add Editor agent (approve->reviewed, revise->author note via Committer)"
```

---

### Task 8: ContinuityChecker agent

**Files:**
- Create: `novelizer/agents/continuity_checker.py`
- Test: `tests/agents/test_continuity_checker.py`

**Interfaces:**
- Produces: `class ContinuityChecker(BaseAgent)` (`name="continuity_checker"`), `build_continuity_checker_runner(settings)`. `readiness()` = `max(0.1, 1.0 - open_retcons/5)`. Polls world + characters + last 10 chapters; `commit` appends one `retcon_request.created` per `RetconDraft`.

- [ ] **Step 1: Write the failing test**

`tests/agents/test_continuity_checker.py`:
```python
import os
import tempfile
import pytest
from novelizer.canon.event_store import EventStore
from novelizer.canon.projector import Projector
from novelizer.canon.read_store import ReadStore
from novelizer.canon.committer import Committer
from novelizer.canon.events import EventType
from novelizer.agents.continuity_checker import ContinuityChecker
from novelizer.agents.schemas import ContinuityOutput, RetconDraft
from novelizer.store.models import WorldEntry, RetconStatus


class FakeRunner:
    def __init__(self, out): self._out = out
    async def ainvoke(self, inputs): return {"structured_response": self._out}


@pytest.fixture
async def stack():
    fd, path = tempfile.mkstemp(suffix=".db"); os.close(fd)
    events = EventStore(path); await events.init()
    proj = Projector(events, path); await proj.init()
    read = ReadStore(path); await read.init()
    yield events, proj, read, Committer(events)
    await read.close(); await proj.close(); await events.close(); os.unlink(path)


async def test_files_retcons_for_contradictions(stack):
    events, proj, read, committer = stack
    await events.append(EventType.WORLD_ENTRY_CREATED, "w1", WorldEntry(id="w1", title="Sun", body="There are two suns."))
    await events.append(EventType.WORLD_ENTRY_CREATED, "w2", WorldEntry(id="w2", title="Sky", body="The lone sun set."))
    await proj.catch_up()
    out = ContinuityOutput(retcon_requests=[RetconDraft(description="two suns vs one", conflicting_entry_ids=["w1", "w2"], proposed_resolution="pick one")])
    agent = ContinuityChecker(FakeRunner(out), read, committer)
    await agent.run_once()
    await proj.catch_up()
    assert len(await read.list_retcon_requests(status=RetconStatus.open)) == 1


async def test_no_contradictions_is_noop(stack):
    events, proj, read, committer = stack
    agent = ContinuityChecker(FakeRunner(ContinuityOutput()), read, committer)
    await agent.run_once()
    await proj.catch_up()
    assert await read.list_retcon_requests() == []
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/agents/test_continuity_checker.py -v`
Expected: FAIL (`ModuleNotFoundError`).

- [ ] **Step 3: Implement**

Create `novelizer/agents/continuity_checker.py`:
```python
from __future__ import annotations
from novelizer.agents.base import BaseAgent, Runner
from novelizer.agents.schemas import ContinuityOutput
from novelizer.canon.read_store import ReadStore
from novelizer.canon.committer import Committer
from novelizer.canon.events import EventType
from novelizer.store.models import RetconRequest, RetconStatus

SYSTEM_PROMPT = """You are the Continuity Checker for a living fictional world. Review the given world
entries, characters, and chapter excerpts for contradictions, anachronisms, or logical inconsistencies.
Return retcon_requests, each with a description (what contradicts what), conflicting_entry_ids (the ids
of the conflicting records), and a proposed_resolution. Return an empty list if you find nothing."""


class ContinuityChecker(BaseAgent):
    def __init__(self, runner: Runner, read_store: ReadStore, committer: Committer, interval: int = 900) -> None:
        super().__init__(runner, read_store, committer, interval, name="continuity_checker")

    async def readiness(self) -> float:
        open_retcons = len(await self._read.list_retcon_requests(status=RetconStatus.open))
        return max(0.1, 1.0 - open_retcons / 5)

    async def poll(self) -> dict:
        chapters = await self._read.list_chapters()
        return {
            "world": await self._read.list_world_entries(),
            "characters": await self._read.list_characters(),
            "chapters": chapters[-10:],
        }

    async def work(self, ctx: dict) -> ContinuityOutput | None:
        world = "\n".join(f"[{e.id[:8]}] {e.title}: {e.body[:200]}" for e in ctx["world"][:20]) or "None."
        chars = "\n".join(f"[{c.id[:8]}] {c.name}: {c.traits}" for c in ctx["characters"][:10]) or "None."
        chapters = "\n".join(f"[{c.id[:8]}] {c.title}: {c.prose[:300]}" for c in ctx["chapters"]) or "None."
        msg = f"World entries:\n{world}\n\nCharacters:\n{chars}\n\nRecent chapters:\n{chapters}"
        result = await self._runner.ainvoke({"messages": [{"role": "user", "content": msg}]})
        return result.get("structured_response")

    async def commit(self, out: ContinuityOutput | None, ctx: dict) -> None:
        if out is None:
            return
        for r in out.retcon_requests:
            req = RetconRequest(description=r.description, conflicting_entry_ids=r.conflicting_entry_ids,
                                proposed_resolution=r.proposed_resolution)
            await self._committer.commit(self.name, EventType.RETCON_REQUEST_CREATED, req.id, req)

    async def run_once(self) -> None:
        ctx = await self.poll()
        out = await self.work(ctx)
        await self.commit(out, ctx)


def build_continuity_checker_runner(settings):
    from deepagents import create_deep_agent
    from novelizer.agents.llm import build_chat_model
    model = build_chat_model(settings.agent_model, settings.llm_base_url, settings.llm_api_key, settings.agent_temperature)
    return create_deep_agent(model=model, system_prompt=SYSTEM_PROMPT, response_format=ContinuityOutput)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/agents/test_continuity_checker.py -v`
Expected: PASS (2 tests).

- [ ] **Step 5: Commit**

```bash
git add novelizer/agents/continuity_checker.py tests/agents/test_continuity_checker.py
git commit -m "feat: add ContinuityChecker agent (files retcon requests via Committer)"
```

---

### Task 9: Retconner agent

**Files:**
- Create: `novelizer/agents/retconner.py`
- Test: `tests/agents/test_retconner.py`

**Interfaces:**
- Produces: `class Retconner(BaseAgent)` (`name="retconner"`), `build_retconner_runner(settings)`. `readiness()` = `min(1.0, open_retcons/3)`. Targets the oldest open `RetconRequest`; polls world entries; `commit`: for each amended entry, append `world_entry.superseded` with a new `WorldEntry(supersedes_id=<the entry it replaces>)`; then append `retcon_request.resolved` for the target request (status=resolved, resolved_by="retconner").

- [ ] **Step 1: Write the failing test**

`tests/agents/test_retconner.py`:
```python
import os
import tempfile
import pytest
from novelizer.canon.event_store import EventStore
from novelizer.canon.projector import Projector
from novelizer.canon.read_store import ReadStore
from novelizer.canon.committer import Committer
from novelizer.canon.events import EventType
from novelizer.agents.retconner import Retconner
from novelizer.agents.schemas import RetconAmendments, WorldEntryDraft
from novelizer.store.models import WorldEntry, RetconRequest, RetconStatus


class FakeRunner:
    def __init__(self, out): self._out = out
    async def ainvoke(self, inputs): return {"structured_response": self._out}


@pytest.fixture
async def stack():
    fd, path = tempfile.mkstemp(suffix=".db"); os.close(fd)
    events = EventStore(path); await events.init()
    proj = Projector(events, path); await proj.init()
    read = ReadStore(path); await read.init()
    yield events, proj, read, Committer(events)
    await read.close(); await proj.close(); await events.close(); os.unlink(path)


async def test_resolves_retcon_and_supersedes_entry(stack):
    events, proj, read, committer = stack
    await events.append(EventType.WORLD_ENTRY_CREATED, "w1", WorldEntry(id="w1", title="Suns", body="Two suns."))
    await events.append(EventType.RETCON_REQUEST_CREATED, "r1",
                        RetconRequest(id="r1", description="two vs one", conflicting_entry_ids=["w1"], proposed_resolution="one sun"))
    await proj.catch_up()
    out = RetconAmendments(amended_entries=[WorldEntryDraft(title="Suns", body="One sun.", supersedes_id="w1")])
    agent = Retconner(FakeRunner(out), read, committer)
    await agent.run_once()
    await proj.catch_up()
    # old entry superseded (gone from active list), new entry present
    active = {e.title: e.body for e in await read.list_world_entries()}
    assert active.get("Suns") == "One sun."
    # retcon marked resolved
    assert await read.list_retcon_requests(status=RetconStatus.open) == []
    assert len(await read.list_retcon_requests(status=RetconStatus.resolved)) == 1


async def test_noop_when_no_open_retcons(stack):
    events, proj, read, committer = stack
    agent = Retconner(FakeRunner(RetconAmendments()), read, committer)
    await agent.run_once()
    await proj.catch_up()
    assert await read.list_retcon_requests() == []
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/agents/test_retconner.py -v`
Expected: FAIL (`ModuleNotFoundError`).

- [ ] **Step 3: Implement**

Create `novelizer/agents/retconner.py`:
```python
from __future__ import annotations
from novelizer.agents.base import BaseAgent, Runner
from novelizer.agents.schemas import RetconAmendments
from novelizer.canon.read_store import ReadStore
from novelizer.canon.committer import Committer
from novelizer.canon.events import EventType
from novelizer.store.models import WorldEntry, RetconStatus

SYSTEM_PROMPT = """You are the Retconner for a living fictional world. You receive a contradiction report
and the conflicting world entries. Propose amended versions of the conflicting entries that resolve the
contradiction. Return amended_entries, each with a title, revised body, domain, tags, and supersedes_id
set to the id of the entry it replaces. Only include entries that need to change."""


class Retconner(BaseAgent):
    def __init__(self, runner: Runner, read_store: ReadStore, committer: Committer, interval: int = 120) -> None:
        super().__init__(runner, read_store, committer, interval, name="retconner")

    async def readiness(self) -> float:
        open_retcons = len(await self._read.list_retcon_requests(status=RetconStatus.open))
        return min(1.0, open_retcons / 3)

    async def poll(self) -> dict:
        open_reqs = await self._read.list_retcon_requests(status=RetconStatus.open)
        return {"target": open_reqs[0] if open_reqs else None, "world": await self._read.list_world_entries()}

    async def work(self, ctx: dict) -> RetconAmendments | None:
        req = ctx["target"]
        if req is None:
            return None
        conflicting = [e for e in ctx["world"] if e.id in req.conflicting_entry_ids]
        text = "\n".join(f"[{e.id}] {e.title}: {e.body}" for e in conflicting) or "(entries not found)"
        msg = f"Contradiction: {req.description}\n\nProposed resolution: {req.proposed_resolution}\n\nConflicting entries:\n{text}"
        result = await self._runner.ainvoke({"messages": [{"role": "user", "content": msg}]})
        return result.get("structured_response")

    async def commit(self, out: RetconAmendments | None, ctx: dict) -> None:
        req = ctx["target"]
        if req is None or out is None:
            return
        for e in out.amended_entries:
            entry = WorldEntry(title=e.title, body=e.body, domain=e.domain, tags=e.tags, supersedes_id=e.supersedes_id)
            await self._committer.commit(self.name, EventType.WORLD_ENTRY_SUPERSEDED, entry.id, entry)
        resolved = req.model_copy(update={"status": RetconStatus.resolved, "resolved_by": self.name})
        await self._committer.commit(self.name, EventType.RETCON_REQUEST_RESOLVED, req.id, resolved)

    async def run_once(self) -> None:
        ctx = await self.poll()
        out = await self.work(ctx)
        await self.commit(out, ctx)


def build_retconner_runner(settings):
    from deepagents import create_deep_agent
    from novelizer.agents.llm import build_chat_model
    model = build_chat_model(settings.agent_model, settings.llm_base_url, settings.llm_api_key, settings.agent_temperature)
    return create_deep_agent(model=model, system_prompt=SYSTEM_PROMPT, response_format=RetconAmendments)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/agents/test_retconner.py -v`
Expected: PASS (2 tests).

- [ ] **Step 5: Commit**

```bash
git add novelizer/agents/retconner.py tests/agents/test_retconner.py
git commit -m "feat: add Retconner agent (supersede + resolve via Committer)"
```

---

### Task 10: Scheduler

**Files:**
- Create: `novelizer/scheduler.py`
- Test: `tests/test_scheduler.py`

**Interfaces:**
- Consumes: `BaseAgent` subclasses, `ReadStore`, `SignalKind`.
- Produces: `class Scheduler`:
  - `__init__(self, agents, read_store, tick_sleep=1.0, clock=time.monotonic)`
  - `pause_agent(name)/resume_agent(name)`
  - `async def tick(self) -> str | None` — returns the name of the agent it ran (or None). Honors a `director_signal(kind=override, target_agent=X)` by running X if eligible; otherwise runs the highest-readiness eligible agent whose score > 0. Calls `agent.mark_ran(now)` on the agent it runs.
  - `async def run(self)` / `def stop(self)`.

- [ ] **Step 1: Write the failing test**

`tests/test_scheduler.py`:
```python
import pytest
from unittest.mock import AsyncMock
from novelizer.scheduler import Scheduler


class StubAgent:
    def __init__(self, name, score, interval=0):
        self.name = name; self._score = score; self.interval = interval
        self.paused = False; self._last = -999; self.ran = 0
    async def readiness(self): return self._score
    def ready_for_interval(self, now): return (now - self._last) >= self.interval
    def mark_ran(self, now): self._last = now; self.ran += 1
    async def run_once(self): pass
    def pause(self): self.paused = True
    def resume(self): self.paused = False


class StubRead:
    def __init__(self, signals=None): self._signals = signals or []
    async def list_unconsumed_signals(self, target_agent=None): return self._signals


async def test_runs_highest_readiness():
    a = StubAgent("a", 0.2); b = StubAgent("b", 0.9)
    sched = Scheduler([a, b], StubRead(), clock=lambda: 1000.0)
    ran = await sched.tick()
    assert ran == "b" and b.ran == 1 and a.ran == 0


async def test_skips_paused_and_zero_score():
    a = StubAgent("a", 0.0); b = StubAgent("b", 0.5)
    b.pause()
    sched = Scheduler([a, b], StubRead(), clock=lambda: 1000.0)
    assert await sched.tick() is None


async def test_override_signal_forces_agent():
    from novelizer.store.models import DirectorSignal, SignalKind
    a = StubAgent("a", 0.9); b = StubAgent("b", 0.1)
    sig = DirectorSignal(kind=SignalKind.override, body="", target_agent="b")
    sched = Scheduler([a, b], StubRead([sig]), clock=lambda: 1000.0)
    assert await sched.tick() == "b"


async def test_respects_interval():
    a = StubAgent("a", 0.9, interval=10)
    sched = Scheduler([a], StubRead(), clock=lambda: 1000.0)
    assert await sched.tick() == "a"
    # same clock -> not interval-ready now
    sched2 = Scheduler([a], StubRead(), clock=lambda: 1005.0)
    assert await sched2.tick() is None
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_scheduler.py -v`
Expected: FAIL (`ModuleNotFoundError`).

- [ ] **Step 3: Implement**

Create `novelizer/scheduler.py`:
```python
from __future__ import annotations
import asyncio
import logging
import time
from typing import Optional, Sequence
from novelizer.store.models import SignalKind

logger = logging.getLogger(__name__)


class Scheduler:
    def __init__(self, agents: Sequence, read_store, tick_sleep: float = 1.0, clock=time.monotonic) -> None:
        self._agents = list(agents)
        self._read = read_store
        self._tick_sleep = tick_sleep
        self._clock = clock
        self._running = False

    def pause_agent(self, name: str) -> None:
        for a in self._agents:
            if a.name == name:
                a.pause()

    def resume_agent(self, name: str) -> None:
        for a in self._agents:
            if a.name == name:
                a.resume()

    async def tick(self) -> Optional[str]:
        now = self._clock()
        signals = await self._read.list_unconsumed_signals()
        override = next((s.target_agent for s in signals
                         if s.kind == SignalKind.override and s.target_agent), None)
        eligible = [a for a in self._agents if not a.paused and a.ready_for_interval(now)]
        if not eligible:
            return None
        if override:
            for a in eligible:
                if a.name == override:
                    await self._run(a, now)
                    return a.name
        scored = [(await a.readiness(), a) for a in eligible]
        scored.sort(key=lambda x: x[0], reverse=True)
        best_score, best = scored[0]
        if best_score > 0.0:
            await self._run(best, now)
            return best.name
        return None

    async def _run(self, agent, now: float) -> None:
        logger.info("scheduler: running %s", agent.name)
        await agent.run_once()
        agent.mark_ran(now)

    async def run(self) -> None:
        self._running = True
        while self._running:
            try:
                await self.tick()
            except Exception:
                logger.exception("scheduler: error in tick")
            await asyncio.sleep(self._tick_sleep)

    def stop(self) -> None:
        self._running = False
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/test_scheduler.py -v`
Expected: PASS (4 tests).

- [ ] **Step 5: Commit**

```bash
git add novelizer/scheduler.py tests/test_scheduler.py
git commit -m "feat: add readiness-scored Scheduler with override + interval gating"
```

---

### Task 11: Runtime wiring + config for all six agents

**Files:**
- Modify: `novelizer/config.py` (add `agent_model`, `agent_temperature`, per-agent intervals)
- Modify: `novelizer/runtime.py` (build all six agents + scheduler; accept `runners` override)
- Test: `tests/test_runtime.py` (extend with a full-pipeline integration test)

**Interfaces:**
- Produces:
  - `Settings` gains: `agent_model: str = "local-model"`, `agent_temperature: float = 0.7`, `default_agent_interval: int = 120`, `continuity_interval: int = 900`.
  - `Runtime(settings, runner=None, runners: dict[str, Runner] | None = None)`. After `start()`, exposes `.agents` (list of all six), `.scheduler`, and named attrs `.author/.world_architect/.character_keeper/.editor/.continuity_checker/.retconner`. When `runners` is provided, each agent uses `runners[name]`; otherwise each `build_*_runner(settings)` is called. `runner=` (singular) remains a back-compat override for the author only (used by existing tests).

- [ ] **Step 1: Write the failing test**

Add to `tests/test_runtime.py` (keep the existing test; append this one and the imports it needs):
```python
from novelizer.agents.schemas import (
    WorldEntriesDraft, WorldEntryDraft, KeeperOutput, CharacterUpdate,
    EditorVerdict, ContinuityOutput, RetconAmendments,
)
from novelizer.agents.base import ChapterDraft


class ScriptedRunner:
    """Returns a fixed structured_response every call."""
    def __init__(self, out): self._out = out
    async def ainvoke(self, inputs): return {"structured_response": self._out}


async def test_full_pipeline_runs_under_runtime(settings):
    runners = {
        "world_architect": ScriptedRunner(WorldEntriesDraft(entries=[WorldEntryDraft(title="Brinemarsh", body="salt")])),
        "author": ScriptedRunner(ChapterDraft(title="Chapter One", prose="It began on the salt flats.")),
        "character_keeper": ScriptedRunner(KeeperOutput()),
        "editor": ScriptedRunner(EditorVerdict(verdict="approve", notes="ok")),
        "continuity_checker": ScriptedRunner(ContinuityOutput()),
        "retconner": ScriptedRunner(RetconAmendments()),
    }
    rt = Runtime(settings, runners=runners)
    await rt.start()
    try:
        assert {a.name for a in rt.agents} == {
            "world_architect", "author", "character_keeper", "editor", "continuity_checker", "retconner"
        }
        # Drive each agent once directly (deterministic), projecting between.
        for name in ["world_architect", "author", "editor"]:
            agent = next(a for a in rt.agents if a.name == name)
            await agent.run_once()
            await rt.projector.catch_up()
        assert "Brinemarsh" in [e.title for e in await rt.read.list_world_entries()]
        chapters = await rt.read.list_chapters()
        assert chapters and chapters[0].title == "Chapter One"
        assert chapters[0].editorial_status.value == "reviewed"
    finally:
        await rt.close()
```
(The existing `settings` fixture and `test_start_wires_a_working_slice` remain; the old test constructs `Runtime(settings, runner=FakeRunner(...))` — keep that working via the back-compat `runner=` param.)

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_runtime.py -v`
Expected: FAIL (`Runtime` has no `runners`/`.agents`/`.scheduler`; missing settings fields).

- [ ] **Step 3: Implement**

In `novelizer/config.py`, add fields (after `author_temperature`):
```python
    agent_model: str = "local-model"
    agent_temperature: float = 0.7
```
and after `author_interval`:
```python
    default_agent_interval: int = 120
    continuity_interval: int = 900
```

Rewrite `novelizer/runtime.py`:
```python
from __future__ import annotations
from typing import Optional
from novelizer.config import Settings
from novelizer.canon.event_store import EventStore
from novelizer.canon.projector import Projector
from novelizer.canon.read_store import ReadStore
from novelizer.canon.committer import Committer
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
        self.committer = Committer(self.events)
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
Note: the back-compat `runner=` path only overrides the author; when `runner=` is given, the other five call their real `build_*_runner(settings)`. The existing `test_start_wires_a_working_slice` uses `runner=FakeRunner(...)` and only drives the author, so the other builders are constructed but never invoked (no network). That is fine — construction of a deepagents runner does not call the endpoint. If any builder errors at construction in that test's environment, switch that test to pass a full `runners=` dict instead.

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/test_runtime.py -v`
Expected: PASS (both tests). Then `uv run pytest -q` — full suite green.

- [ ] **Step 5: Commit**

```bash
git add novelizer/config.py novelizer/runtime.py tests/test_runtime.py
git commit -m "feat: wire all six agents + Scheduler into Runtime with per-agent runner overrides"
```

---

### Task 12: Drive the scheduler from the app + retcon feed labels + CLI retcons

**Files:**
- Modify: `novelizer/tui/app.py` (replace `_author_loop` with a scheduler loop; extend `format_event`)
- Modify: `novelizer/director/cli.py` (add `retcons` command)
- Modify: `docs/submilestones/M1-the-room-assembles.md` (mark M1.1 complete)
- Test: `tests/tui/test_app.py` (extend `format_event` cases), `tests/director/test_cli.py` (add retcons case), `tests/tui/test_app_smoke.py` (update to assert the room produces output under the scheduler)

**Interfaces:**
- Produces:
  - `format_event` labels `retcon_request.created` ("Continuity") and `chapter.status_changed` ("Editor") in addition to existing types.
  - `NovelizerApp` runs `self.runtime.scheduler.tick()` on a loop (replacing the single-author loop), so the whole room advances live.
  - CLI `retcons` lists open retcon requests via `read.list_retcon_requests(status="open")`.

- [ ] **Step 1: Write the failing tests**

Add to `tests/tui/test_app.py`:
```python
def test_format_retcon_created_labels_continuity():
    from novelizer.tui.app import format_event
    from novelizer.canon.events import StoredEvent, EventType
    ev = StoredEvent(sequence=1, id="e", event_type=EventType.RETCON_REQUEST_CREATED,
                     aggregate_id="r1", payload={"description": "scar mismatch"}, created_at="t")
    line = format_event(ev)
    assert "scar mismatch" in line


def test_format_chapter_status_changed_labels_editor():
    from novelizer.tui.app import format_event
    from novelizer.canon.events import StoredEvent, EventType
    ev = StoredEvent(sequence=2, id="e", event_type=EventType.CHAPTER_STATUS_CHANGED,
                     aggregate_id="c1", payload={"title": "One", "editorial_status": "reviewed"}, created_at="t")
    assert "One" in format_event(ev)
```

Add to `tests/director/test_cli.py`:
```python
def test_retcons_command_empty():
    import os, tempfile
    from click.testing import CliRunner
    from novelizer.director.cli import cli
    fd, path = tempfile.mkstemp(suffix=".db"); os.close(fd)
    try:
        r = CliRunner().invoke(cli, ["retcons"], env={"NOVELIZER_DB_PATH": path})
        assert r.exit_code == 0, r.output
        assert "No open retcon" in r.output
    finally:
        os.unlink(path)
```

Update `tests/tui/test_app_smoke.py` — change the smoke test so the app is started with a full `runners=` dict (all six scripted) and asserts a chapter still appears in the feed while the app drives the scheduler. Replace the `FakeRunner`/`Runtime(...)` construction with:
```python
from novelizer.agents.schemas import (
    WorldEntriesDraft, WorldEntryDraft, KeeperOutput, EditorVerdict,
    ContinuityOutput, RetconAmendments,
)
from novelizer.agents.base import ChapterDraft

def _room_runners():
    return {
        "world_architect": _R(WorldEntriesDraft(entries=[WorldEntryDraft(title="Brinemarsh", body="salt")])),
        "author": _R(ChapterDraft(title="Live Chapter", prose="It appears.")),
        "character_keeper": _R(KeeperOutput()),
        "editor": _R(EditorVerdict(verdict="approve", notes="ok")),
        "continuity_checker": _R(ContinuityOutput()),
        "retconner": _R(RetconAmendments()),
    }

class _R:
    def __init__(self, out): self._out = out
    async def ainvoke(self, inputs): return {"structured_response": self._out}
```
and build `Runtime(settings, runners=_room_runners())`, keeping the existing pilot-pause assertion that `"Live Chapter"` (or a world entry) appears in `app.messages`. Give the scheduler loop enough pauses (the app's scheduler interval should be short in tests). Do not weaken the assertion to nothing.

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/tui/ tests/director/test_cli.py -v`
Expected: FAIL (new format cases / `retcons` command / smoke wiring).

- [ ] **Step 3: Implement**

In `novelizer/tui/app.py`:
- Extend the `_LABELS` dict and `format_event` detail logic to handle `EventType.RETCON_REQUEST_CREATED` → label "Continuity", detail `f"retcon: {p.get('description','')}"`; and `EventType.CHAPTER_STATUS_CHANGED` → label "Editor", detail `f"chapter reviewed: {p.get('title','')}"`.
- Replace `_author_loop` with:
```python
    async def _scheduler_loop(self) -> None:
        while True:
            try:
                await self.runtime.scheduler.tick()
            except Exception as e:
                self._report_worker_error("scheduler", e)
            await asyncio.sleep(self.runtime.settings.projector_interval)
```
and in `on_mount`, start `self._scheduler_loop()` instead of `self._author_loop()`. (Match the existing guarded-loop style and `_report_worker_error` helper added in M0.)

In `novelizer/director/cli.py`, add:
```python
@cli.command()
@click.pass_context
def retcons(ctx):
    """List open retcon requests."""
    async def _run(rt: Runtime):
        reqs = await rt.read.list_retcon_requests(status="open")
        if not reqs:
            console.print("No open retcon requests.")
            return
        table = Table(title="Open Retcon Requests")
        table.add_column("ID", style="dim", no_wrap=True)
        table.add_column("Description")
        table.add_column("Proposed Resolution")
        for r in reqs:
            table.add_row(r.id[:8], r.description, r.proposed_resolution)
        console.print(table)
    asyncio.run(_with_runtime(ctx.obj["settings"], _run))
```
(Ensure `Table` is imported — it already is in cli.py from M0.)

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/tui/ tests/director/test_cli.py -v`
Expected: PASS. Then `uv run pytest -q` — full suite green.

- [ ] **Step 5: Mark sub-milestone complete + commit**

In `docs/submilestones/M1-the-room-assembles.md`, change the M1.1 row status to `✅ complete`.
```bash
git add novelizer/tui/app.py novelizer/director/cli.py docs/submilestones/M1-the-room-assembles.md tests/tui/test_app.py tests/tui/test_app_smoke.py tests/director/test_cli.py
git commit -m "feat: drive the full room from the scheduler; add retcon feed labels + CLI retcons; mark M1.1 complete"
```

---

## Self-Review

**Spec coverage (against the M1.1 row + vision spec):**
- Five agents ported to deepagents (WorldArchitect, CharacterKeeper, Editor, ContinuityChecker, Retconner) → Tasks 5–9. ✓
- Readiness-scored scheduler with override + interval → Task 10. ✓
- `Committer` write-seam (append-only; gating deferred) → Task 2, used by every agent. ✓
- Canon extended for retcons (events/projection/reads) → Task 1. ✓
- Runtime wires all six + scheduler; the room runs unattended → Task 11 (integration test) + Task 12 (app drives scheduler). ✓
- CLI `retcons` read → Task 12. ✓
- Deferred to M1.2/M1.3 (NOT gaps): Mission Control multi-pane TUI, autonomy dial, proposal/approval infra. Correctly absent.

**Placeholder scan:** none. Task 5's note about `settings.agent_model` (added in Task 11) is resolved with an explicit fallback and the fact that Task 5's tests inject fakes, so no build_* call runs in Task 5. ✓

**Type consistency:** `BaseAgent(runner, read_store, committer, interval, name=)` used identically by all six agents. `Committer.commit(agent_name, event_type, aggregate_id, payload)` call shape consistent everywhere. Agent `run_once()`/`readiness()` match what the `Scheduler` calls. Schemas in Task 4 are the exact `response_format`/return types used in Tasks 5–9 and the Task 11 integration test. `ReadStore.get_character`/`list_retcon_requests` (Task 1) are consumed by Tasks 6, 8, 9. `Runtime.runners`/`.agents`/`.scheduler` (Task 11) are consumed by Task 12. ✓

**DDD/SOLID:** agents depend only on `ReadStore`/`Committer` abstractions; the `Committer` seam is the single open/closed extension point for M1.3 gating; the scheduler depends on the structural agent interface, not concretes. ✓

**Note on the back-compat `runner=` path (Task 11):** the pre-existing `test_start_wires_a_working_slice` constructs the five non-author real runners without invoking them. If deepagents runner *construction* makes a network call in the CI environment (it should not), convert that test to pass a full `runners=` dict — called out in Task 11's note.
