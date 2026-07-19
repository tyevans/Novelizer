# CPT-M3: search_canon Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Semantic search over all canon kinds — extend the dormant `EmbeddingStore` to cover threads and secrets, add a unified `search()`, build a `CanonIndexer` that incrementally embeds canon from the event log (backfill = same mechanism from sequence 0), expose the `search_canon` LangChain tool, and wire construction + catch-up into `Runtime` and the TUI tick.

**Architecture:** The indexer mirrors the Projector's cursor pattern but hydrates CURRENT records from `ReadStore` (not event payloads), so created/revised/updated events all take one code path and deletions fall out naturally. Embedding failures never crash anything: the indexer logs, leaves its cursor unadvanced, and retries next tick; the tool answers "Search unavailable… browse with ls/glob/grep instead." Search hits carry the record's virtual canon path (via `build_path_index`), so an agent can go straight from a hit to `read_file`.

**Tech Stack:** chromadb (installed), `FakeEmbeddingFunction` from `tests/conftest.py` for all tests (no live endpoint in CI — standing rule), `langchain_core.tools.tool`, JSON cursor file.

## Global Constraints

- Red/green TDD; tests ONLY in this worktree; `uv run pytest` prefix.
- No CI test may depend on a live embed endpoint — every test injects `FakeEmbeddingFunction` (import from `tests.conftest` via the existing fixture, or construct directly).
- The indexer NEVER writes to world.db and NEVER blocks event commits — it reads `events_since` and writes only to chroma + its own JSON cursor file.
- Kind names everywhere match CPT-M2: `{"chapter","character","world","thread","secret","theme"}`.
- `search_canon` output must include the exact record id for every hit (cite-ids-exactly discipline) and the virtual path when resolvable.
- Existing write-path behavior (intents' `upsert_theme` + near-duplicate suggestion) is untouched.

---

### Task 1: EmbeddingStore covers threads and secrets

**Files:**
- Modify: `novelizer/store/embeddings.py`
- Test: `tests/store/test_embeddings.py` (append)

**Interfaces:**
- Consumes: `ThreadRecord`, `SecretRecord` from `novelizer.store.models`.
- Produces: `upsert_thread(thread: ThreadRecord)`, `upsert_secret(secret: SecretRecord)`; collections `threads`, `secrets`; `delete(entity_id, collection)` accepts the two new collection names.

- [ ] **Step 1: Write the failing test**

```python
# append to tests/store/test_embeddings.py (reuse the file's existing
# EmbeddingStore-with-FakeEmbeddingFunction construction pattern/fixture)
from novelizer.store.models import SecretRecord, ThreadRecord


async def test_upsert_and_delete_thread_and_secret(store):
    await store.upsert_thread(ThreadRecord(id="t1", name="Bell's Curse", last_note="rang again"))
    await store.upsert_secret(SecretRecord(id="s1", title="The Scar"))
    assert store._threads.count() == 1
    assert store._secrets.count() == 1
    await store.delete("t1", "threads")
    await store.delete("s1", "secrets")
    assert store._threads.count() == 0
    assert store._secrets.count() == 0
```

(If the existing test file's store fixture has a different name, match it; if none exists, create one local to the new tests using `FakeEmbeddingFunction` and `tmp_path`.)

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/store/test_embeddings.py -v`
Expected: new test FAILS — `AttributeError: 'EmbeddingStore' object has no attribute 'upsert_thread'`

- [ ] **Step 3: Write minimal implementation**

In `EmbeddingStore.__init__`, after the existing four collections:

```python
        self._threads = self._client.get_or_create_collection("threads", embedding_function=ef)
        self._secrets = self._client.get_or_create_collection("secrets", embedding_function=ef)
```

Extend the import to include `SecretRecord, ThreadRecord`, add the two collections to `delete`'s map, and add:

```python
    async def upsert_thread(self, thread: ThreadRecord) -> None:
        text = f"{thread.name}\n{thread.last_note}" if thread.last_note else thread.name
        self._threads.upsert(ids=[thread.id], documents=[text], metadatas=[{"title": thread.name}])

    async def upsert_secret(self, secret: SecretRecord) -> None:
        self._secrets.upsert(ids=[secret.id], documents=[secret.title], metadatas=[{"title": secret.title}])
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/store/test_embeddings.py -v`
Expected: all PASS

- [ ] **Step 5: Commit**

```bash
git add novelizer/store/embeddings.py tests/store/test_embeddings.py
git commit -m "feat(embeddings): thread and secret collections"
```

---

### Task 2: Unified `EmbeddingStore.search`

**Files:**
- Modify: `novelizer/store/embeddings.py`
- Test: `tests/store/test_embeddings.py` (append)

**Interfaces:**
- Produces: `SearchHit` dataclass `(kind: str, id: str, title: str, distance: float)` exported from `novelizer.store.embeddings`, and `async def search(self, query: str, kinds: list[str] | None = None, n: int = 8) -> list[SearchHit]` — queries each requested kind's collection, tags hits with kind, merges sorted ascending by distance, truncates to `n`. Unknown kind in `kinds` → `ValueError`. Empty collections contribute nothing.

- [ ] **Step 1: Write the failing test**

```python
# append to tests/store/test_embeddings.py
from novelizer.store.embeddings import SearchHit
from novelizer.store.models import Chapter, Character


async def test_search_merges_kinds_sorted_by_distance(store):
    await store.upsert_chapter(Chapter(id="ch1", title="The Drowned Bell", prose="The bell rang over the water."))
    await store.upsert_character(Character(id="mara", name="Mara", traits="bell-ringer"))
    await store.upsert_secret(SecretRecord(id="s1", title="The bell is cracked"))
    hits = await store.search("bell", n=10)
    assert len(hits) == 3
    assert [type(h) for h in hits] == [SearchHit] * 3
    assert {(h.kind, h.id) for h in hits} == {("chapter", "ch1"), ("character", "mara"), ("secret", "s1")}
    assert hits == sorted(hits, key=lambda h: h.distance)


async def test_search_kind_filter_and_empty(store):
    await store.upsert_chapter(Chapter(id="ch1", title="One", prose="alpha beta"))
    only_secrets = await store.search("alpha", kinds=["secret"])
    assert only_secrets == []
    only_chapters = await store.search("alpha", kinds=["chapter"])
    assert [h.id for h in only_chapters] == ["ch1"]


async def test_search_unknown_kind_raises(store):
    import pytest
    with pytest.raises(ValueError):
        await store.search("x", kinds=["novel"])
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/store/test_embeddings.py -v`
Expected: FAIL — `ImportError: cannot import name 'SearchHit'`

- [ ] **Step 3: Write minimal implementation**

```python
# embeddings.py — top of file additions
from dataclasses import dataclass


@dataclass
class SearchHit:
    kind: str
    id: str
    title: str
    distance: float


# inside EmbeddingStore:
    def _collections_by_kind(self) -> dict:
        return {
            "chapter": self._chapters,
            "character": self._chars,
            "world": self._world,
            "thread": self._threads,
            "secret": self._secrets,
            "theme": self._themes,
        }

    async def search(self, query: str, kinds: list[str] | None = None, n: int = 8) -> list[SearchHit]:
        by_kind = self._collections_by_kind()
        wanted = list(by_kind) if kinds is None else kinds
        unknown = [k for k in wanted if k not in by_kind]
        if unknown:
            raise ValueError(f"Unknown kinds: {unknown}. Valid: {sorted(by_kind)}")
        hits: list[SearchHit] = []
        for kind in wanted:
            col = by_kind[kind]
            if col.count() == 0:
                continue
            results = col.query(query_texts=[query], n_results=min(n, col.count()))
            for i, doc_id in enumerate(results["ids"][0]):
                meta = results["metadatas"][0][i] or {}
                title = meta.get("title") or meta.get("name") or ""
                hits.append(SearchHit(kind=kind, id=doc_id, title=title,
                                      distance=results["distances"][0][i]))
        hits.sort(key=lambda h: h.distance)
        return hits[:n]
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/store/test_embeddings.py -v`
Expected: all PASS

- [ ] **Step 5: Commit**

```bash
git add novelizer/store/embeddings.py tests/store/test_embeddings.py
git commit -m "feat(embeddings): unified cross-kind search with distances"
```

---

### Task 3: `CanonIndexer`

**Files:**
- Create: `novelizer/store/indexer.py`
- Test: `tests/store/test_indexer.py`

**Interfaces:**
- Consumes: `EventStore.events_since(sequence, event_types)` (each `StoredEvent` has `.sequence`, `.event_type`, `.aggregate_id`); `ReadStore` getters (`get_chapter`, `get_character`, `get_thread`, `get_secret`, `get_theme`, `list_world_entries`); `EmbeddingStore` upserts/delete.
- Produces: `CanonIndexer(events, read_store, embedding_store, cursor_path)` with `async def catch_up(self) -> int` (events processed this call). Cursor is JSON `{"last_sequence": N}` at `cursor_path`; missing file = 0 (this IS the backfill). On any exception mid-batch: log a warning, persist the cursor at the last successfully indexed event, return the count so far — never raise.

- [ ] **Step 1: Write the failing test**

```python
# tests/store/test_indexer.py
import os
import tempfile

import pytest

from novelizer.canon.event_store import EventStore
from novelizer.canon.events import EventType, SecretCreated, ThemeIntroduced, ThreadPlanted
from novelizer.canon.projector import Projector
from novelizer.canon.read_store import ReadStore
from novelizer.store.embeddings import EmbeddingStore
from novelizer.store.indexer import CanonIndexer
from novelizer.store.models import Chapter, Character, WorldEntry
from tests.conftest import FakeEmbeddingFunction


@pytest.fixture
async def stack(tmp_path):
    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    events = EventStore(path); await events.init()
    proj = Projector(events, path); await proj.init()
    read = ReadStore(path); await read.init()
    store = EmbeddingStore(str(tmp_path / "emb"), embedding_function=FakeEmbeddingFunction())
    indexer = CanonIndexer(events, read, store, str(tmp_path / "cursor.json"))
    yield events, proj, read, store, indexer
    await read.close(); await proj.close(); await events.close()
    os.unlink(path)


async def seed(events, proj):
    await events.append(EventType.CHAPTER_CREATED, "ch1",
                        Chapter(id="ch1", title="One", prose="The bell rang."))
    await events.append(EventType.CHARACTER_CREATED, "mara",
                        Character(id="mara", name="Mara"))
    await events.append(EventType.WORLD_ENTRY_CREATED, "w1",
                        WorldEntry(id="w1", title="Bell Cult", body="dusk"))
    await events.append(EventType.THREAD_PLANTED, "t1", ThreadPlanted(id="t1", name="Curse"))
    await events.append(EventType.SECRET_CREATED, "s1", SecretCreated(id="s1", title="Scar"))
    await events.append(EventType.THEME_INTRODUCED, "th1", ThemeIntroduced(id="th1", title="Memory"))
    await proj.catch_up()


async def test_backfill_indexes_every_kind(stack):
    events, proj, read, store, indexer = stack
    await seed(events, proj)
    processed = await indexer.catch_up()
    assert processed == 6
    hits = await store.search("bell", n=20)
    assert {h.kind for h in hits} == {"chapter", "character", "world", "thread", "secret", "theme"}


async def test_catch_up_is_incremental_and_idempotent(stack):
    events, proj, read, store, indexer = stack
    await seed(events, proj)
    assert await indexer.catch_up() == 6
    assert await indexer.catch_up() == 0  # cursor persisted, nothing new
    await events.append(EventType.CHAPTER_CREATED, "ch2",
                        Chapter(id="ch2", title="Two", prose="More prose."))
    await proj.catch_up()
    assert await indexer.catch_up() == 1


async def test_cursor_survives_new_indexer_instance(stack, tmp_path):
    events, proj, read, store, indexer = stack
    await seed(events, proj)
    await indexer.catch_up()
    fresh = CanonIndexer(events, read, store, str(tmp_path / "cursor.json"))
    assert await fresh.catch_up() == 0


async def test_embed_failure_leaves_cursor_for_retry(stack, tmp_path):
    events, proj, read, store, indexer = stack
    await seed(events, proj)

    class Boom:
        def __getattr__(self, name):
            raise RuntimeError("endpoint down")

    broken = CanonIndexer(events, read, Boom(), str(tmp_path / "cursor2.json"))
    assert await broken.catch_up() == 0  # swallowed, not raised
    assert await indexer.catch_up() == 6  # untouched cursor path still backfills
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/store/test_indexer.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'novelizer.store.indexer'`

- [ ] **Step 3: Write minimal implementation**

```python
# novelizer/store/indexer.py
from __future__ import annotations

import json
import logging
from pathlib import Path

from novelizer.canon.events import EventType

logger = logging.getLogger(__name__)

# Every event type that changes what a canon record should embed as.
INDEXED_EVENT_TYPES = [
    EventType.CHAPTER_CREATED, EventType.CHAPTER_REVISED,
    EventType.WORLD_ENTRY_CREATED, EventType.WORLD_ENTRY_SUPERSEDED,
    EventType.CHARACTER_CREATED, EventType.CHARACTER_UPDATED,
    EventType.THREAD_PLANTED, EventType.THREAD_TOUCHED,
    EventType.THREAD_PAID_OFF, EventType.THREAD_ABANDONED,
    EventType.SECRET_CREATED, EventType.SECRET_REVEALED,
    EventType.THEME_INTRODUCED, EventType.THEME_DEVELOPED,
]

_PREFIX_TO_KIND = {
    "chapter": "chapter",
    "world_entry": "world",
    "character": "character",
    "thread": "thread",
    "secret": "secret",
    "theme": "theme",
}


class CanonIndexer:
    """Event-cursor-driven embedding indexer (Projector's cursor pattern,
    but hydrating CURRENT records from ReadStore so create/revise/update
    share one path). Failure-tolerant by contract: an embed-endpoint outage
    logs a warning and leaves the cursor at the last indexed event, so the
    next catch_up retries. Never writes to world.db.
    """

    def __init__(self, events, read_store, embedding_store, cursor_path: str) -> None:
        self._events = events
        self._read = read_store
        self._emb = embedding_store
        self._cursor_path = Path(cursor_path)

    def _load_cursor(self) -> int:
        try:
            return json.loads(self._cursor_path.read_text())["last_sequence"]
        except (OSError, ValueError, KeyError):
            return 0

    def _save_cursor(self, seq: int) -> None:
        self._cursor_path.write_text(json.dumps({"last_sequence": seq}))

    async def catch_up(self) -> int:
        last = self._load_cursor()
        stored = await self._events.events_since(
            last, event_types=[e.value for e in INDEXED_EVENT_TYPES]
        )
        processed = 0
        for ev in stored:
            try:
                await self._index_one(ev.event_type, ev.aggregate_id)
            except Exception as e:  # endpoint down, malformed record, ...
                logger.warning("canon indexing stopped at seq %s (%s: %s); will retry",
                               ev.sequence, type(e).__name__, e)
                break
            self._save_cursor(ev.sequence)
            processed += 1
        return processed

    async def _index_one(self, event_type: str, aggregate_id: str) -> None:
        kind = _PREFIX_TO_KIND[event_type.split(".")[0]]
        if kind == "chapter":
            record = await self._read.get_chapter(aggregate_id)
            if record is not None:
                await self._emb.upsert_chapter(record)
        elif kind == "world":
            entries = {e.id: e for e in await self._read.list_world_entries()}
            record = entries.get(aggregate_id)
            if record is not None:
                await self._emb.upsert_world_entry(record)
            else:  # superseded out of the active list
                await self._emb.delete(aggregate_id, "world_entries")
        elif kind == "character":
            record = await self._read.get_character(aggregate_id)
            if record is not None:
                await self._emb.upsert_character(record)
        elif kind == "thread":
            record = await self._read.get_thread(aggregate_id)
            if record is not None:
                await self._emb.upsert_thread(record)
        elif kind == "secret":
            record = await self._read.get_secret(aggregate_id)
            if record is not None:
                await self._emb.upsert_secret(record)
        else:
            record = await self._read.get_theme(aggregate_id)
            if record is not None:
                await self._emb.upsert_theme(record)
```

Note on the failure test: `Boom.__getattr__` raises on ANY attribute access,
so `_index_one` raises inside the first iteration and `catch_up` must catch,
log, break, and return 0 — with the cursor file never created.

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/store/test_indexer.py -v`
Expected: 4 PASS (`chromadb` deletion of a never-inserted world id is a no-op — if chromadb raises instead, guard the delete branch with a try/except and keep the test green)

- [ ] **Step 5: Commit**

```bash
git add novelizer/store/indexer.py tests/store/test_indexer.py
git commit -m "feat(store): CanonIndexer — cursor-driven canon embedding with backfill"
```

---

### Task 4: `search_canon` tool

**Files:**
- Create: `novelizer/canon_fs/search.py`
- Test: `tests/canon_fs/test_search.py`

**Interfaces:**
- Consumes: `EmbeddingStore.search`, `build_path_index`, the six `ReadStore` list methods.
- Produces: `build_search_canon_tool(embedding_store, read_store)` returning a LangChain tool named `search_canon` with async signature `(query: str, kinds: list[str] | None = None) -> str`. Output lines: `(kind) /path — 'title' [id: X]` sorted best-first; `"No results."` when empty; `"Search unavailable (<ExcName>); browse the canon filesystem with ls/glob/grep instead."` when the store raises. CPT-M4 passes this tool to `create_deep_agent(tools=[...])`.

- [ ] **Step 1: Write the failing test**

```python
# tests/canon_fs/test_search.py
import pytest

from novelizer.canon_fs.search import build_search_canon_tool
from novelizer.store.embeddings import EmbeddingStore
from novelizer.store.models import Chapter, Character, SecretRecord
from tests.conftest import FakeEmbeddingFunction


class FakeReadStore:
    """Just enough ReadStore for path resolution."""

    def __init__(self, chapters=(), characters=(), secrets=()):
        self._chapters, self._characters, self._secrets = (
            list(chapters), list(characters), list(secrets))

    async def list_chapters(self, status=None): return self._chapters
    async def list_characters(self): return self._characters
    async def list_world_entries(self, domain=None): return []
    async def list_threads(self): return []
    async def list_secrets(self): return self._secrets
    async def list_themes(self): return []


@pytest.fixture
def store(tmp_path):
    return EmbeddingStore(str(tmp_path / "emb"), embedding_function=FakeEmbeddingFunction())


async def test_search_canon_formats_hits_with_path_and_id(store):
    ch = Chapter(id="ch1", title="The Drowned Bell", prose="The bell rang.")
    await store.upsert_chapter(ch)
    read = FakeReadStore(chapters=[ch])
    tool = build_search_canon_tool(store, read)
    out = await tool.ainvoke({"query": "bell"})
    assert "(chapter) /chapters/001-the-drowned-bell.md" in out
    assert "[id: ch1]" in out


async def test_search_canon_kind_filter_and_no_results(store):
    ch = Chapter(id="ch1", title="One", prose="alpha")
    await store.upsert_chapter(ch)
    tool = build_search_canon_tool(store, FakeReadStore(chapters=[ch]))
    assert await tool.ainvoke({"query": "alpha", "kinds": ["secret"]}) == "No results."


async def test_search_canon_unavailable_on_store_error(store):
    class Boom:
        async def search(self, *a, **k): raise RuntimeError("down")

    tool = build_search_canon_tool(Boom(), FakeReadStore())
    out = await tool.ainvoke({"query": "x"})
    assert out.startswith("Search unavailable (RuntimeError)")
    assert "ls/glob/grep" in out


async def test_search_canon_tool_metadata():
    tool = build_search_canon_tool(None, None)
    assert tool.name == "search_canon"
    assert "canon" in tool.description.lower()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/canon_fs/test_search.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'novelizer.canon_fs.search'`

- [ ] **Step 3: Write minimal implementation**

```python
# novelizer/canon_fs/search.py
from __future__ import annotations

from langchain_core.tools import tool

from novelizer.canon_fs.paths import build_path_index


def build_search_canon_tool(embedding_store, read_store):
    """Factory so the tool closes over story-scoped stores (one tool
    instance per runner, mirroring how runners close over settings)."""

    @tool
    async def search_canon(query: str, kinds: list[str] | None = None) -> str:
        """Semantic search over the whole story canon (chapters, characters,
        world entries, threads, secrets, themes). Returns the best matches
        with their canon file path and exact record id — read the file at
        the returned path for full content. kinds filters to a subset, e.g.
        ["chapter", "secret"].
        """
        try:
            hits = await embedding_store.search(query, kinds=kinds)
        except Exception as e:
            return (f"Search unavailable ({type(e).__name__}); browse the canon "
                    f"filesystem with ls/glob/grep instead.")
        if not hits:
            return "No results."
        index = build_path_index(
            chapters=await read_store.list_chapters(),
            characters=await read_store.list_characters(),
            world_entries=await read_store.list_world_entries(),
            threads=await read_store.list_threads(),
            secrets=await read_store.list_secrets(),
            themes=await read_store.list_themes(),
        )
        path_by_id = {record_id: p for p, (_, record_id) in index.items()}
        lines = [
            f"({h.kind}) {path_by_id.get(h.id, '(no file)')} — '{h.title}' [id: {h.id}]"
            for h in hits
        ]
        return "\n".join(lines)

    return search_canon
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/canon_fs/test_search.py -v`
Expected: 4 PASS

- [ ] **Step 5: Commit**

```bash
git add novelizer/canon_fs/search.py tests/canon_fs/test_search.py
git commit -m "feat(canon_fs): search_canon tool — semantic hits with canon paths"
```

---

### Task 5: Runtime wiring + backfill

**Files:**
- Modify: `novelizer/runtime.py`
- Test: `tests/test_runtime.py` (append)

**Interfaces:**
- Consumes: `EmbeddingStore`, `CanonIndexer`; settings fields `embed_model`, `llm_base_url`, `llm_api_key`, `db_path` (all exist).
- Produces: `Runtime.__init__` gains keyword `embedding_store=None` (test seam, mirroring `runners=`); `Runtime.start()` constructs (when not injected) `EmbeddingStore(str(Path(settings.db_path).with_name("embeddings")), embed_model=s.embed_model, base_url=s.llm_base_url, api_key=s.llm_api_key)` and always constructs `self.indexer = CanonIndexer(self.events, self.read, self.embeddings, str(Path(settings.db_path).with_name("embed_cursor.json")))`, then runs one failure-tolerant backfill; `Runtime.index_catch_up()` — safe wrapper (no-op if indexer is None, never raises) for periodic callers. Task 6 hooks the TUI tick to it.

- [ ] **Step 1: Write the failing test**

```python
# append to tests/test_runtime.py (reuse its existing settings/fixture
# helpers for constructing a Runtime with fake runners; the embedding seam
# is new)
from novelizer.canon.events import EventType
from novelizer.store.embeddings import EmbeddingStore
from novelizer.store.models import Chapter
from tests.conftest import FakeEmbeddingFunction


async def test_runtime_backfills_and_ticks_indexer(tmp_path, make_runtime):
    """make_runtime: adapt to this file's existing runtime-construction
    helper (settings pointed at tmp DB, fake runners). Inject the embedding
    seam and verify start() backfills and index_catch_up() is incremental
    and safe."""
    store = EmbeddingStore(str(tmp_path / "emb"), embedding_function=FakeEmbeddingFunction())
    rt = make_runtime(embedding_store=store)
    await rt.start()
    await rt.events.append(EventType.CHAPTER_CREATED, "ch1",
                           Chapter(id="ch1", title="One", prose="The bell rang."))
    await rt.projector.catch_up()
    await rt.index_catch_up()
    hits = await store.search("bell", kinds=["chapter"])
    assert [h.id for h in hits] == ["ch1"]
    await rt.index_catch_up()  # idempotent, never raises
    await rt.stop()
```

(If `tests/test_runtime.py` has no reusable construction helper, build the
Runtime inline the same way that file's existing tests do — settings with
`db_path` under tmp_path, `runners={}` fakes — and keep the assertions
identical. The REQUIREMENT is: injected store, start() succeeds, event →
`index_catch_up()` → hit findable, second call processes nothing, no raise.)

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_runtime.py -v -k indexer`
Expected: FAIL — `TypeError: Runtime.__init__() got an unexpected keyword argument 'embedding_store'`

- [ ] **Step 3: Write minimal implementation**

In `Runtime.__init__`, add the keyword and state:

```python
    def __init__(self, settings, runner=None, runners=None, embedding_store=None) -> None:
        ...
        self.embeddings = embedding_store   # None => built in start()
        self.indexer = None
```

In `Runtime.start()`, after `await self.projector.catch_up()`:

```python
        if self.embeddings is None:
            from novelizer.store.embeddings import EmbeddingStore
            self.embeddings = EmbeddingStore(
                str(Path(s.db_path).with_name("embeddings")),
                embed_model=s.embed_model, base_url=s.llm_base_url, api_key=s.llm_api_key,
            )
        from novelizer.store.indexer import CanonIndexer
        self.indexer = CanonIndexer(
            self.events, self.read, self.embeddings,
            str(Path(s.db_path).with_name("embed_cursor.json")),
        )
        await self.index_catch_up()  # backfill; failure-tolerant by contract
```

(`s = self.settings` is already bound in start(); hoist the two imports to
the module's import block instead of inline if that matches file style.)

New method:

```python
    async def index_catch_up(self) -> None:
        """Periodic-caller-safe embedding catch-up: no-op without an indexer,
        and never raises (CanonIndexer.catch_up swallows batch failures)."""
        if self.indexer is None:
            return
        await self.indexer.catch_up()
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/test_runtime.py -v`
Expected: all PASS (existing runtime tests keep passing — the default path builds a real EmbeddingStore whose constructor makes NO network calls; embedding only happens on upsert/query, and start()'s backfill on an empty event log processes zero events)

- [ ] **Step 5: Commit**

```bash
git add novelizer/runtime.py tests/test_runtime.py
git commit -m "feat(runtime): embedding store + canon indexer wiring with start-time backfill"
```

---

### Task 6: TUI tick hook + milestone doc

**Files:**
- Modify: `novelizer/tui/app.py` (one line, in the periodic loop that calls `projector.catch_up()` around line 122)
- Modify: `docs/superpowers/plans/2026-07-19-canon-pull-tools-milestones.md`
- Test: `tests/tui/test_app_layout.py` or wherever the existing TUI tick behavior is pinned — follow `docs/TESTING-TUI.md`'s wedge recipe; if the tick loop has no existing direct test, assert the call is made via a spy on a Runtime double in the lightest existing TUI test instead of building new TUI harness machinery.

**Interfaces:**
- Consumes: `Runtime.index_catch_up()` (Task 5).
- Produces: the TUI's periodic refresh keeps embeddings current: immediately after `await self.runtime.projector.catch_up()` in the app's tick loop, add `await self.runtime.index_catch_up()`.

- [ ] **Step 1: Add the failing/spy test** (per this project's TUI testing recipe; if a spy on a fake Runtime is the pragmatic route, assert `index_catch_up` was awaited at least once after the tick fires)

- [ ] **Step 2: Run it, see it fail**

Run: `uv run pytest tests/tui -k "tick or index" -v` (adjust to the chosen test's name)

- [ ] **Step 3: Add the one-line call after `projector.catch_up()` in `novelizer/tui/app.py`'s tick loop**

- [ ] **Step 4: Run the chosen test + the TUI suite**

Run: `uv run pytest tests/tui -v`
Expected: all PASS (known flake: `test_story_brain_secrets_matrix_and_causeway_tabs_populate` can fail under full-suite load; rerun in isolation before treating as real)

- [ ] **Step 5: Update the milestone ladder** — mark CPT-M3 delivered in `docs/superpowers/plans/2026-07-19-canon-pull-tools-milestones.md` (add a Status column entry or a one-line note; keep the table intact), and commit:

```bash
git add novelizer/tui/app.py tests/tui docs/superpowers/plans/2026-07-19-canon-pull-tools-milestones.md
git commit -m "feat(tui): keep canon embeddings current on the app tick"
```
