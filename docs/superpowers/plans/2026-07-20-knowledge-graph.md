# Knowledge Graph Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Give agents recall of world detail (minor places, factions, objects,
relations) that prose introduces but no canon aggregate formalizes, surfaced through
the existing `search_canon` tool.

**Architecture:** A new `KGStore` (its own connection to `world.db`, own
`kg_entities`/`kg_relations`/`kg_entity_mentions` tables) holds the graph. A
`KGProjector`, event-cursor driven like `store/indexer.py`'s `CanonIndexer`, derives
entities/relations from two sources — deterministic upserts from structured canon
events (character, world_entry), and one LLM extraction call per chapter over its
full prose — and reflows (clears + re-extracts) on `chapter.revised`. Every upserted
entity is also written into a new `EmbeddingStore` "entities" collection, so it's
searchable through the exact same `search_canon` path chapters/characters/etc. already
use. `search_canon` gains an `entity` hit kind whose line inlines name/type/
description/relations, since entities have no `canon_fs` file to point at.

**Tech Stack:** Python 3.13, aiosqlite, chromadb (existing `EmbeddingStore`),
langchain/deepagents `ProviderStrategy` structured output (existing pattern from
`agents/continuity_checker.py`'s mining runner), pytest + pytest-asyncio.

## Global Constraints

- No new agent-facing tool — everything surfaces through the existing `search_canon`
  tool (spec: Agent-Facing Surface).
- KG tables live in `world.db`, not a separate file (spec: Storage Location).
- No bitemporal `valid_from`/`valid_to` property tracking (spec: Data Model).
- No visualization, no relation to the Causeway causal graph (spec: Non-Goals).
- Entity types are freeform strings, not a fixed enum (user decision during
  brainstorming).
- Follow the project's TDD convention: failing test first, then minimal
  implementation, for every step below.

---

### Task 1: KGStore — schema and entity/relation CRUD

**Files:**
- Create: `novelizer/store/kg_store.py`
- Test: `tests/store/test_kg_store.py`

**Interfaces:**
- Consumes: `novelizer.canon.db.connect(path) -> aiosqlite.Connection` (existing).
- Produces:
  - `KGStore(path: str)` — constructor, does not connect.
  - `async def init(self) -> None` — connects and creates tables if absent.
  - `async def close(self) -> None`
  - `async def upsert_entity(self, name: str, entity_type: str, description: str = "", canon_id: str | None = None, seq: int = 0) -> int` — returns entity id. Upsert keyed on `(name, entity_type)`, case-insensitive on `name`.
  - `async def upsert_relation(self, source_id: int, target_id: int, relation_type: str, seq: int = 0) -> int` — returns relation id. Upsert keyed on `(source_id, target_id, relation_type)`.
  - `async def link_mention(self, entity_id: int, event_fingerprint: str) -> None`
  - `async def clear_mentions_for_fingerprint(self, event_fingerprint: str) -> list[int]` — deletes `kg_entity_mentions` rows for that fingerprint, returns the affected `entity_id`s (Task 5 uses this to know which entities to consider for pruning after reflow, though pruning itself isn't in scope — see Task 5's docstring).
  - `async def find_entity_by_name(self, name: str, entity_type: str) -> dict | None`
  - `async def get_entity(self, entity_id: int) -> dict | None`
  - `async def entity_relations(self, entity_id: int) -> list[dict]` — rows with `relation_type`, `other_name`, `direction` ("out"/"in").

- [ ] **Step 1: Write the failing test**

```python
# tests/store/test_kg_store.py
import pytest
from novelizer.store.kg_store import KGStore


@pytest.fixture
async def store(tmp_path):
    s = KGStore(str(tmp_path / "world.db"))
    await s.init()
    yield s
    await s.close()


@pytest.mark.asyncio
async def test_upsert_entity_is_idempotent_by_name_and_type(store):
    first_id = await store.upsert_entity("Eldara", "character", "a potion master")
    second_id = await store.upsert_entity("eldara", "character", "updated description")

    assert first_id == second_id
    entity = await store.get_entity(first_id)
    assert entity["name"] == "Eldara"
    assert entity["description"] == "updated description"


@pytest.mark.asyncio
async def test_upsert_relation_is_idempotent(store):
    a = await store.upsert_entity("Eldara", "character")
    b = await store.upsert_entity("Grimm", "character")

    first_id = await store.upsert_relation(a, b, "friend_of")
    second_id = await store.upsert_relation(a, b, "friend_of")

    assert first_id == second_id
    relations = await store.entity_relations(a)
    assert len(relations) == 1
    assert relations[0]["relation_type"] == "friend_of"
    assert relations[0]["other_name"] == "Grimm"
    assert relations[0]["direction"] == "out"


@pytest.mark.asyncio
async def test_clear_mentions_for_fingerprint_removes_only_that_fingerprint(store):
    a = await store.upsert_entity("The Salted Gull", "location")
    await store.link_mention(a, "chapter-1-v1")
    await store.link_mention(a, "chapter-2-v1")

    cleared = await store.clear_mentions_for_fingerprint("chapter-1-v1")

    assert cleared == [a]
    # chapter-2-v1's mention survives
    cleared_again = await store.clear_mentions_for_fingerprint("chapter-1-v1")
    assert cleared_again == []
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/store/test_kg_store.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'novelizer.store.kg_store'`

- [ ] **Step 3: Write minimal implementation**

```python
# novelizer/store/kg_store.py
from __future__ import annotations
from typing import Optional
import aiosqlite
from novelizer.canon import db

_SCHEMA = """
CREATE TABLE IF NOT EXISTS kg_entities (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL,
    entity_type TEXT NOT NULL,
    description TEXT NOT NULL DEFAULT '',
    canon_id TEXT,
    first_seen INTEGER NOT NULL DEFAULT 0,
    last_seen INTEGER NOT NULL DEFAULT 0,
    UNIQUE(name, entity_type)
);

CREATE TABLE IF NOT EXISTS kg_relations (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    source_id INTEGER NOT NULL REFERENCES kg_entities(id),
    target_id INTEGER NOT NULL REFERENCES kg_entities(id),
    relation_type TEXT NOT NULL,
    first_seen INTEGER NOT NULL DEFAULT 0,
    last_seen INTEGER NOT NULL DEFAULT 0,
    UNIQUE(source_id, target_id, relation_type)
);

CREATE TABLE IF NOT EXISTS kg_entity_mentions (
    entity_id INTEGER NOT NULL REFERENCES kg_entities(id),
    event_fingerprint TEXT NOT NULL,
    PRIMARY KEY(entity_id, event_fingerprint)
);
"""


class KGStore:
    """Owns the knowledge-graph tables in world.db. A separate connection to
    the same file as ReadStore/EventStore/Projector -- multiple connections
    to one SQLite file, serialized by WAL + busy_timeout, is already the
    norm in this codebase (see novelizer/canon/db.py)."""

    def __init__(self, path: str) -> None:
        self._path = path
        self._conn: Optional[aiosqlite.Connection] = None

    async def init(self) -> None:
        self._conn = await db.connect(self._path)
        await self._conn.executescript(_SCHEMA)
        await self._conn.commit()

    async def close(self) -> None:
        if self._conn:
            await self._conn.close()

    async def upsert_entity(
        self, name: str, entity_type: str, description: str = "",
        canon_id: str | None = None, seq: int = 0,
    ) -> int:
        existing = await self.find_entity_by_name(name, entity_type)
        if existing:
            await self._conn.execute(
                "UPDATE kg_entities SET description=?, canon_id=COALESCE(?, canon_id), "
                "last_seen=? WHERE id=?",
                (description, canon_id, seq, existing["id"]),
            )
            await self._conn.commit()
            return existing["id"]
        cur = await self._conn.execute(
            "INSERT INTO kg_entities (name, entity_type, description, canon_id, "
            "first_seen, last_seen) VALUES (?, ?, ?, ?, ?, ?)",
            (name, entity_type, description, canon_id, seq, seq),
        )
        await self._conn.commit()
        return cur.lastrowid

    async def upsert_relation(
        self, source_id: int, target_id: int, relation_type: str, seq: int = 0,
    ) -> int:
        cur = await self._conn.execute(
            "SELECT id FROM kg_relations WHERE source_id=? AND target_id=? AND relation_type=?",
            (source_id, target_id, relation_type),
        )
        row = await cur.fetchone()
        if row:
            await self._conn.execute(
                "UPDATE kg_relations SET last_seen=? WHERE id=?", (seq, row[0])
            )
            await self._conn.commit()
            return row[0]
        cur = await self._conn.execute(
            "INSERT INTO kg_relations (source_id, target_id, relation_type, "
            "first_seen, last_seen) VALUES (?, ?, ?, ?, ?)",
            (source_id, target_id, relation_type, seq, seq),
        )
        await self._conn.commit()
        return cur.lastrowid

    async def link_mention(self, entity_id: int, event_fingerprint: str) -> None:
        await self._conn.execute(
            "INSERT OR IGNORE INTO kg_entity_mentions (entity_id, event_fingerprint) "
            "VALUES (?, ?)",
            (entity_id, event_fingerprint),
        )
        await self._conn.commit()

    async def clear_mentions_for_fingerprint(self, event_fingerprint: str) -> list[int]:
        cur = await self._conn.execute(
            "SELECT entity_id FROM kg_entity_mentions WHERE event_fingerprint=?",
            (event_fingerprint,),
        )
        ids = [r[0] for r in await cur.fetchall()]
        await self._conn.execute(
            "DELETE FROM kg_entity_mentions WHERE event_fingerprint=?", (event_fingerprint,)
        )
        await self._conn.commit()
        return ids

    async def find_entity_by_name(self, name: str, entity_type: str) -> Optional[dict]:
        cur = await self._conn.execute(
            "SELECT * FROM kg_entities WHERE LOWER(name)=LOWER(?) AND entity_type=?",
            (name, entity_type),
        )
        row = await cur.fetchone()
        return dict(zip([c[0] for c in cur.description], row)) if row else None

    async def get_entity(self, entity_id: int) -> Optional[dict]:
        cur = await self._conn.execute("SELECT * FROM kg_entities WHERE id=?", (entity_id,))
        row = await cur.fetchone()
        return dict(zip([c[0] for c in cur.description], row)) if row else None

    async def entity_relations(self, entity_id: int) -> list[dict]:
        cur = await self._conn.execute(
            "SELECT r.relation_type, e.name as other_name, 'out' as direction "
            "FROM kg_relations r JOIN kg_entities e ON e.id = r.target_id "
            "WHERE r.source_id=? "
            "UNION ALL "
            "SELECT r.relation_type, e.name as other_name, 'in' as direction "
            "FROM kg_relations r JOIN kg_entities e ON e.id = r.source_id "
            "WHERE r.target_id=?",
            (entity_id, entity_id),
        )
        rows = await cur.fetchall()
        cols = [c[0] for c in cur.description]
        return [dict(zip(cols, r)) for r in rows]
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/store/test_kg_store.py -v`
Expected: PASS (3 tests)

- [ ] **Step 5: Commit**

```bash
git add novelizer/store/kg_store.py tests/store/test_kg_store.py
git commit -m "feat(kg): add KGStore with entity/relation schema and CRUD"
```

---

### Task 2: EmbeddingStore — entities collection

**Files:**
- Modify: `novelizer/store/embeddings.py`
- Test: `tests/store/test_embeddings.py` (existing file — add cases; if it doesn't
  exist, create it following the fixture pattern already used for other
  `EmbeddingStore` tests in the suite, with a deterministic fake `embedding_function`
  passed to the constructor per the seam documented at `embeddings.py:73-78`)

**Interfaces:**
- Consumes: `EmbeddingStore.__init__` (existing), `_cap` (existing module-level
  helper).
- Produces:
  - `async def upsert_entity(self, entity_id: str, name: str, detail: str) -> None`
    — `entity_id` is `str(kg_entity_id)`; `detail` is the full inline hit text
    (Task 7 builds this string; here it's just the document body).
  - `async def delete_entity(self, entity_id: str) -> None` — thin wrapper calling
    the existing `delete(entity_id, "entities")`.
  - `_collections_by_kind()` gains `"entity": self._entities`.

- [ ] **Step 1: Write the failing test**

```python
# tests/store/test_embeddings.py (add to existing file, or create per note above)
import pytest
from novelizer.store.embeddings import EmbeddingStore
from tests.fakes.fake_embedding_function import FakeEmbeddingFunction  # existing fake used by other EmbeddingStore tests


@pytest.mark.asyncio
async def test_entity_upsert_is_searchable_as_entity_kind(tmp_path):
    store = EmbeddingStore(str(tmp_path / "chroma"), embedding_function=FakeEmbeddingFunction())

    await store.upsert_entity("42", "The Salted Gull", "The Salted Gull [location] a dockside tavern")

    hits = await store.search("Salted Gull", kinds=["entity"])
    assert len(hits) == 1
    assert hits[0].kind == "entity"
    assert hits[0].id == "42"


@pytest.mark.asyncio
async def test_delete_entity_removes_it_from_search(tmp_path):
    store = EmbeddingStore(str(tmp_path / "chroma"), embedding_function=FakeEmbeddingFunction())
    await store.upsert_entity("42", "The Salted Gull", "a dockside tavern")

    await store.delete_entity("42")

    hits = await store.search("Salted Gull", kinds=["entity"])
    assert hits == []
```

If `tests/fakes/fake_embedding_function.py` does not exist under that exact name,
grep the existing embeddings test file for whatever deterministic fake it already
imports and use that same import instead — do not create a second fake.

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/store/test_embeddings.py -k entity -v`
Expected: FAIL with `AttributeError: 'EmbeddingStore' object has no attribute 'upsert_entity'`

- [ ] **Step 3: Write minimal implementation**

Add to `novelizer/store/embeddings.py`, inside `EmbeddingStore.__init__` (after the
`self._arcs = ...` line):

```python
        self._entities = self._client.get_or_create_collection("entities", embedding_function=ef)
```

Add a new method (near the other `upsert_*` methods, after `upsert_arc`):

```python
    async def upsert_entity(self, entity_id: str, name: str, detail: str) -> None:
        text = _cap(f"{name}\n{detail}")
        async with self._write_lock:
            await asyncio.to_thread(
                self._entities.upsert, ids=[entity_id], documents=[text],
                metadatas=[{"title": name}],
            )

    async def delete_entity(self, entity_id: str) -> None:
        await self.delete(entity_id, "entities")
```

Update `_collections_by_kind`:

```python
    def _collections_by_kind(self) -> dict:
        return {
            "chapter": self._chapters,
            "character": self._chars,
            "world": self._world,
            "thread": self._threads,
            "secret": self._secrets,
            "theme": self._themes,
            "promise": self._promises,
            "brief": self._briefs,
            "arc": self._arcs,
            "entity": self._entities,
        }
```

Update `delete`'s `collection`-name dict (in the existing `delete` method) to include
`"entities": self._entities`.

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/store/test_embeddings.py -k entity -v`
Expected: PASS (2 tests)

- [ ] **Step 5: Commit**

```bash
git add novelizer/store/embeddings.py tests/store/test_embeddings.py
git commit -m "feat(kg): add entities collection to EmbeddingStore"
```

---

### Task 3: Structured-event extraction (character, world_entry — no LLM)

**Files:**
- Create: `novelizer/store/kg_structured.py`
- Test: `tests/store/test_kg_structured.py`

**Interfaces:**
- Consumes: `novelizer.store.models.Character`, `novelizer.store.models.WorldEntry`
  (existing).
- Produces:
  - `@dataclass EntityFact: name: str; entity_type: str; description: str = ""; canon_id: str | None = None`
  - `@dataclass RelationFact: source_name: str; source_type: str; target_name: str; target_type: str; relation_type: str`
  - `def facts_from_character(char: Character) -> tuple[list[EntityFact], list[RelationFact]]`
  - `def facts_from_world_entry(entry: WorldEntry) -> tuple[list[EntityFact], list[RelationFact]]`

Scope note: only `Character` and `WorldEntry` produce structured facts. Threads,
secrets, themes, promises, and arcs are plot-mechanics bookkeeping, not world
entities — they have no `relationships`-shaped field and extracting a KG entity
named e.g. "Thread: the debt Mateo owes" would duplicate what `search_canon`'s
existing `thread` kind already serves. This is a deliberate scope boundary, not an
oversight; per the design's "both" extraction-source decision, threads/secrets/etc.
are still covered — through chapter prose mining (Task 4), same as any other detail
that happens to appear in the text.

- [ ] **Step 1: Write the failing test**

```python
# tests/store/test_kg_structured.py
from novelizer.store.kg_structured import facts_from_character, facts_from_world_entry
from novelizer.store.models import Character, CharacterRelationship, WorldEntry


def test_facts_from_character_with_no_relationships():
    char = Character(id="c1", name="Eldara", traits="sharp-tongued", backstory="a potion master")

    entities, relations = facts_from_character(char)

    assert len(entities) == 1
    assert entities[0].name == "Eldara"
    assert entities[0].entity_type == "character"
    assert entities[0].canon_id == "c1"
    assert entities[0].description == "sharp-tongued"
    assert relations == []


def test_facts_from_character_with_relationships_needs_target_lookup():
    char = Character(
        id="c1", name="Eldara",
        relationships=[CharacterRelationship(target_character_id="c2", description="old friend")],
    )

    entities, relations = facts_from_character(char, character_names={"c2": "Grimm"})

    assert len(relations) == 1
    assert relations[0].source_name == "Eldara"
    assert relations[0].target_name == "Grimm"
    assert relations[0].relation_type == "old friend"


def test_facts_from_character_skips_relationship_with_unknown_target():
    char = Character(
        id="c1", name="Eldara",
        relationships=[CharacterRelationship(target_character_id="c2", description="old friend")],
    )

    entities, relations = facts_from_character(char, character_names={})

    assert relations == []


def test_facts_from_world_entry():
    entry = WorldEntry(id="w1", title="The Salted Gull", body="a dockside tavern")

    entities, relations = facts_from_world_entry(entry)

    assert len(entities) == 1
    assert entities[0].name == "The Salted Gull"
    assert entities[0].entity_type == "world_entry"
    assert entities[0].canon_id == "w1"
    assert entities[0].description == "a dockside tavern"
    assert relations == []
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/store/test_kg_structured.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'novelizer.store.kg_structured'`

- [ ] **Step 3: Write minimal implementation**

```python
# novelizer/store/kg_structured.py
from __future__ import annotations
from dataclasses import dataclass
from novelizer.store.models import Character, WorldEntry


@dataclass
class EntityFact:
    name: str
    entity_type: str
    description: str = ""
    canon_id: str | None = None


@dataclass
class RelationFact:
    source_name: str
    source_type: str
    target_name: str
    target_type: str
    relation_type: str


def facts_from_character(
    char: Character, character_names: dict[str, str] | None = None,
) -> tuple[list[EntityFact], list[RelationFact]]:
    """character_names maps character id -> name, needed to resolve
    relationship targets since Character.relationships only carries the
    target's id. Caller (KGProjector, Task 5) supplies this from
    ReadStore.list_characters(). A relationship whose target id isn't in
    the map (e.g. the target character was retconned away) is skipped
    rather than raising -- see the third test case above."""
    names = character_names or {}
    entities = [EntityFact(
        name=char.name, entity_type="character",
        description=char.traits, canon_id=char.id,
    )]
    relations = []
    for rel in char.relationships:
        target_name = names.get(rel.target_character_id)
        if target_name is None:
            continue
        relations.append(RelationFact(
            source_name=char.name, source_type="character",
            target_name=target_name, target_type="character",
            relation_type=rel.description,
        ))
    return entities, relations


def facts_from_world_entry(entry: WorldEntry) -> tuple[list[EntityFact], list[RelationFact]]:
    entities = [EntityFact(
        name=entry.title, entity_type="world_entry",
        description=entry.body, canon_id=entry.id,
    )]
    return entities, []
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/store/test_kg_structured.py -v`
Expected: PASS (4 tests)

- [ ] **Step 5: Commit**

```bash
git add novelizer/store/kg_structured.py tests/store/test_kg_structured.py
git commit -m "feat(kg): structured entity/relation extraction from character and world_entry records"
```

---

### Task 4: Chapter-prose LLM extraction

**Files:**
- Modify: `novelizer/agents/schemas.py` (add extraction output schema)
- Create: `novelizer/agents/kg_extraction.py`
- Test: `tests/agents/test_kg_extraction.py`

**Interfaces:**
- Consumes: `novelizer.agents.llm.build_chat_model` (existing),
  `langchain.agents.structured_output.ProviderStrategy` (existing dependency,
  already used by `build_continuity_mining_runner`).
- Produces (in `novelizer/agents/schemas.py`):
  - `class KGExtractedEntity(BaseModel): name: str; entity_type: str; description: str = ""`
  - `class KGExtractedRelation(BaseModel): source: str; target: str; relation_type: str`
  - `class KGExtractionOutput(BaseModel): entities: list[KGExtractedEntity] = Field(default_factory=list); relations: list[KGExtractedRelation] = Field(default_factory=list)`
- Produces (in `novelizer/agents/kg_extraction.py`):
  - `def build_kg_extraction_runner(settings, callbacks=None)` — returns a
    `create_deep_agent(...)` graph, mirroring
    `build_continuity_mining_runner`'s structure exactly (temperature 0.2, same
    reasoning about token free-running at higher temperature).
  - `def kg_extraction_prompt(chapter_title: str, chapter_prose: str) -> str`
  - `KG_EXTRACTION_SYSTEM_PROMPT: str` (module constant)

- [ ] **Step 1: Write the failing test**

```python
# tests/agents/test_kg_extraction.py
import pytest
from novelizer.agents.kg_extraction import kg_extraction_prompt
from novelizer.agents.schemas import KGExtractedEntity, KGExtractedRelation, KGExtractionOutput


def test_kg_extraction_prompt_includes_title_and_prose():
    prompt = kg_extraction_prompt("The Salted Gull", "Eldara walked into the tavern.")

    assert "The Salted Gull" in prompt
    assert "Eldara walked into the tavern." in prompt


def test_extraction_output_schema_round_trips():
    out = KGExtractionOutput(
        entities=[KGExtractedEntity(name="Eldara", entity_type="character")],
        relations=[KGExtractedRelation(source="Eldara", target="Grimm", relation_type="friend_of")],
    )

    dumped = out.model_dump()
    restored = KGExtractionOutput.model_validate(dumped)

    assert restored.entities[0].name == "Eldara"
    assert restored.relations[0].relation_type == "friend_of"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/agents/test_kg_extraction.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'novelizer.agents.kg_extraction'`

- [ ] **Step 3: Write minimal implementation**

Add to `novelizer/agents/schemas.py` (near the other `Mined*` schemas):

```python
class KGExtractedEntity(BaseModel):
    name: str
    entity_type: str
    description: str = ""


class KGExtractedRelation(BaseModel):
    source: str
    target: str
    relation_type: str


class KGExtractionOutput(BaseModel):
    entities: list[KGExtractedEntity] = Field(default_factory=list)
    relations: list[KGExtractedRelation] = Field(default_factory=list)
```

```python
# novelizer/agents/kg_extraction.py
from __future__ import annotations
from novelizer.agents.schemas import KGExtractionOutput

KG_EXTRACTION_SYSTEM_PROMPT = """You extract entities and relationships from a
novel chapter's prose. Extract every named person, place, faction, organization,
item, or creature mentioned -- not just major characters, which are already
tracked elsewhere; you exist specifically to catch what would otherwise be lost.
Use short, freeform entity_type labels (e.g. "character", "location", "faction",
"item", "creature") -- there is no fixed list, pick what fits.

For relations, use short lowercase relation_type labels (e.g. "located_in",
"owns", "member_of", "friend_of"). Only extract what the prose actually states;
do not infer facts it doesn't support. If nothing is extractable, return empty
lists for both entities and relations."""


def kg_extraction_prompt(chapter_title: str, chapter_prose: str) -> str:
    return f"Chapter: {chapter_title}\n\n{chapter_prose}"


def build_kg_extraction_runner(settings, callbacks=None):
    from deepagents import create_deep_agent
    from langchain.agents.structured_output import ProviderStrategy
    from novelizer.agents.llm import build_chat_model
    # Extraction is fact-finding, not composition -- cold temperature, same
    # reasoning as build_continuity_mining_runner: a creative temperature
    # here free-runs inside JSON string fields until the token cap.
    model = build_chat_model(
        settings.agent_model, settings.llm_base_url, settings.llm_api_key,
        temperature=0.2, max_tokens=settings.llm_max_tokens, callbacks=callbacks,
    )
    graph = create_deep_agent(
        model=model, system_prompt=KG_EXTRACTION_SYSTEM_PROMPT,
        response_format=ProviderStrategy(KGExtractionOutput),
    )
    if callbacks:
        return graph.with_config({"callbacks": callbacks})
    return graph
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/agents/test_kg_extraction.py -v`
Expected: PASS (2 tests)

- [ ] **Step 5: Commit**

```bash
git add novelizer/agents/schemas.py novelizer/agents/kg_extraction.py tests/agents/test_kg_extraction.py
git commit -m "feat(kg): chapter-prose entity/relation extraction runner"
```

---

### Task 5: KGProjector — event-cursor orchestration and reflow

**Files:**
- Create: `novelizer/store/kg_projector.py`
- Test: `tests/store/test_kg_projector.py`

**Interfaces:**
- Consumes:
  - `novelizer.canon.event_store.EventStore.events_since(seq, event_types) -> list[StoredEvent]` (existing)
  - `novelizer.canon.read_store.ReadStore.get_character/get_world_entry-equivalent`
    — note `ReadStore.list_world_entries()` is the only world-entry accessor (there
    is no `get_world_entry`; mirror `CanonIndexer._index_one`'s "world" branch,
    which builds a dict from `list_world_entries()`), plus `list_characters()`.
  - `KGStore` (Task 1), `EmbeddingStore.upsert_entity`/`delete_entity` (Task 2),
    `facts_from_character`/`facts_from_world_entry` (Task 3),
    `build_kg_extraction_runner`/`kg_extraction_prompt` (Task 4).
- Produces:
  - `KGProjector(events, read_store, kg_store, embedding_store, extraction_runner, cursor_path: str)`
  - `async def catch_up(self) -> int` — same cursor-file contract as
    `CanonIndexer.catch_up`: never raises, logs and stops at the first failing
    event, returns count processed.

Event types indexed: `character.created`, `character.updated`, `world_entry.created`,
`world_entry.superseded`, `chapter.created`, `chapter.revised` — the union of Task 3's
structured sources and Task 4's prose source.

- [ ] **Step 1: Write the failing test**

```python
# tests/store/test_kg_projector.py
import pytest
from novelizer.canon.event_store import EventStore
from novelizer.canon.projector import Projector
from novelizer.canon.read_store import ReadStore
from novelizer.canon.events import EventType
from novelizer.store.kg_store import KGStore
from novelizer.store.embeddings import EmbeddingStore
from novelizer.store.kg_projector import KGProjector
from tests.fakes.fake_embedding_function import FakeEmbeddingFunction


class FakeExtractionRunner:
    """Stands in for build_kg_extraction_runner's graph: same .ainvoke shape
    ContinuityChecker's mining runner uses, returning a canned
    KGExtractionOutput via structured_response."""

    def __init__(self, output):
        self._output = output
        self.calls = 0

    async def ainvoke(self, _messages):
        self.calls += 1
        return {"structured_response": self._output}


@pytest.fixture
async def wiring(tmp_path):
    db_path = str(tmp_path / "world.db")
    events = EventStore(db_path)
    await events.init()
    projector = Projector(events, db_path)
    await projector.init()
    read = ReadStore(db_path)
    await read.init()
    kg = KGStore(db_path)
    await kg.init()
    emb = EmbeddingStore(str(tmp_path / "chroma"), embedding_function=FakeEmbeddingFunction())
    yield events, projector, read, kg, emb
    await events.close()
    await projector.close()
    await read.close()
    await kg.close()


@pytest.mark.asyncio
async def test_catch_up_extracts_structured_character_facts(wiring):
    events, projector, read, kg, emb = wiring
    await events.append_raw(EventType.CHARACTER_CREATED, "c1", {
        "id": "c1", "name": "Eldara", "traits": "sharp-tongued",
    })
    await projector.catch_up()

    from novelizer.agents.schemas import KGExtractionOutput
    runner = FakeExtractionRunner(KGExtractionOutput())
    kgp = KGProjector(events, read, kg, emb, runner, str((await _cursor_path(kg))))

    processed = await kgp.catch_up()

    assert processed == 1
    entity = await kg.find_entity_by_name("Eldara", "character")
    assert entity is not None
    assert entity["canon_id"] == "c1"
    hits = await emb.search("Eldara", kinds=["entity"])
    assert len(hits) == 1


@pytest.mark.asyncio
async def test_chapter_revised_reflows_prose_extracted_entities(wiring, tmp_path):
    events, projector, read, kg, emb = wiring
    from novelizer.agents.schemas import KGExtractionOutput, KGExtractedEntity

    await events.append_raw(EventType.CHAPTER_CREATED, "ch1", {
        "id": "ch1", "title": "Ch 1", "prose": "Eldara enters The Salted Gull.",
    })
    await projector.catch_up()

    runner = FakeExtractionRunner(KGExtractionOutput(
        entities=[KGExtractedEntity(name="The Salted Gull", entity_type="location")],
    ))
    cursor_path = str(tmp_path / "kg_cursor.json")
    kgp = KGProjector(events, read, kg, emb, runner, cursor_path)
    await kgp.catch_up()
    assert (await kg.find_entity_by_name("The Salted Gull", "location")) is not None

    # Revise the chapter with prose that no longer mentions the tavern
    await events.append_raw(EventType.CHAPTER_REVISED, "ch1", {
        "chapter_id": "ch1", "title": "Ch 1", "prose": "Eldara stays home.",
    })
    await projector.catch_up()
    runner._output = KGExtractionOutput(entities=[])  # nothing extracted this time
    await kgp.catch_up()

    # Reflow cleared the mention; the embedding was deleted, though the
    # kg_entities row itself can remain (harmless orphan row, out of scope
    # to garbage-collect per this task -- see docstring below).
    hits = await emb.search("Salted Gull", kinds=["entity"])
    assert hits == []


async def _cursor_path(kg: KGStore):
    # helper only used by the first test above to avoid a second tmp_path fixture
    import tempfile, os
    fd, path = tempfile.mkstemp(suffix=".json")
    os.close(fd)
    os.remove(path)
    return path
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/store/test_kg_projector.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'novelizer.store.kg_projector'`

- [ ] **Step 3: Write minimal implementation**

```python
# novelizer/store/kg_projector.py
from __future__ import annotations
import json
import logging
import os
from pathlib import Path
from novelizer.canon.events import EventType

logger = logging.getLogger(__name__)

INDEXED_EVENT_TYPES = [
    EventType.CHARACTER_CREATED, EventType.CHARACTER_UPDATED,
    EventType.WORLD_ENTRY_CREATED, EventType.WORLD_ENTRY_SUPERSEDED,
    EventType.CHAPTER_CREATED, EventType.CHAPTER_REVISED,
]


class KGProjector:
    """Event-cursor-driven knowledge-graph projector, structurally identical
    to store/indexer.py's CanonIndexer (same cursor-file contract, same
    failure-tolerant catch_up), except this one DOES write to world.db (via
    KGStore) as well as to the embeddings collection -- CanonIndexer's "never
    writes to world.db" rule doesn't apply here because the KG's tables live
    in world.db by design (see docs/superpowers/specs/2026-07-20-knowledge-
    graph-design.md, Storage Location).

    Orphaned kg_entities rows (an entity whose only mention was cleared by a
    reflow) are left in place rather than garbage-collected: they're
    harmless (unreferenced by any embedding, so unreachable via search_canon)
    and pruning them is out of scope for this task -- YAGNI until an actual
    need (e.g. a Director-facing entity browser) shows up.
    """

    def __init__(self, events, read_store, kg_store, embedding_store, extraction_runner, cursor_path: str) -> None:
        self._events = events
        self._read = read_store
        self._kg = kg_store
        self._emb = embedding_store
        self._runner = extraction_runner
        self._cursor_path = Path(cursor_path)

    def _load_cursor(self) -> int:
        try:
            return json.loads(self._cursor_path.read_text())["last_sequence"]
        except (OSError, ValueError, KeyError):
            return 0

    def _save_cursor(self, seq: int) -> None:
        tmp_path = self._cursor_path.with_suffix(self._cursor_path.suffix + ".tmp")
        tmp_path.write_text(json.dumps({"last_sequence": seq}))
        os.replace(tmp_path, self._cursor_path)

    async def catch_up(self) -> int:
        processed = 0
        try:
            last = self._load_cursor()
            stored = await self._events.events_since(last, event_types=list(INDEXED_EVENT_TYPES))
            for ev in stored:
                try:
                    await self._index_one(ev.event_type, ev.aggregate_id, ev.sequence)
                except Exception as e:
                    logger.warning("kg indexing stopped at seq %s (%s: %s); will retry",
                                    ev.sequence, type(e).__name__, e)
                    break
                self._save_cursor(ev.sequence)
                processed += 1
        except Exception as e:
            logger.warning("kg indexing catch_up failed (%s: %s); will retry next tick",
                            type(e).__name__, e)
        return processed

    async def _index_one(self, event_type: str, aggregate_id: str, seq: int) -> None:
        if event_type in (EventType.CHARACTER_CREATED, EventType.CHARACTER_UPDATED):
            await self._index_character(aggregate_id, seq)
        elif event_type in (EventType.WORLD_ENTRY_CREATED, EventType.WORLD_ENTRY_SUPERSEDED):
            await self._index_world_entry(aggregate_id, seq)
        elif event_type in (EventType.CHAPTER_CREATED, EventType.CHAPTER_REVISED):
            await self._index_chapter(aggregate_id, seq)

    async def _index_character(self, character_id: str, seq: int) -> None:
        from novelizer.store.kg_structured import facts_from_character
        char = await self._read.get_character(character_id)
        if char is None:
            return
        names = {c.id: c.name for c in await self._read.list_characters()}
        entities, relations = facts_from_character(char, character_names=names)
        await self._commit_facts(entities, relations, fingerprint=f"character:{character_id}")

    async def _index_world_entry(self, entry_id: str, seq: int) -> None:
        from novelizer.store.kg_structured import facts_from_world_entry
        entries = {e.id: e for e in await self._read.list_world_entries()}
        entry = entries.get(entry_id)
        if entry is None:
            return
        entities, relations = facts_from_world_entry(entry)
        await self._commit_facts(entities, relations, fingerprint=f"world_entry:{entry_id}")

    async def _index_chapter(self, chapter_id: str, seq: int) -> None:
        chapter = await self._read.get_chapter(chapter_id)
        if chapter is None:
            return
        fingerprint = f"chapter:{chapter_id}"
        # Reflow: clear this chapter's prior prose-extracted mentions before
        # re-extracting, so a revision that drops a detail also drops its
        # entity's searchability (see class docstring on orphan rows).
        cleared_entity_ids = await self._kg.clear_mentions_for_fingerprint(fingerprint)
        for entity_id in cleared_entity_ids:
            remaining = await self._kg.entity_relations(entity_id)
            still_mentioned = await self._entity_has_other_mentions(entity_id, fingerprint)
            if not remaining and not still_mentioned:
                await self._emb.delete_entity(str(entity_id))

        from novelizer.agents.kg_extraction import kg_extraction_prompt
        prompt = kg_extraction_prompt(chapter.title, chapter.prose)
        result = await self._runner.ainvoke({"messages": [{"role": "user", "content": prompt}]})
        out = result.get("structured_response")
        if out is None:
            return

        name_to_id: dict[str, int] = {}
        for entity in out.entities:
            entity_id = await self._kg.upsert_entity(
                entity.name, entity.entity_type, entity.description, seq=seq,
            )
            name_to_id[entity.name] = entity_id
            await self._kg.link_mention(entity_id, fingerprint)
            detail = self._format_entity_detail(entity.name, entity.entity_type, entity.description, [])
            await self._emb.upsert_entity(str(entity_id), entity.name, detail)

        for relation in out.relations:
            source_id = name_to_id.get(relation.source)
            target_id = name_to_id.get(relation.target)
            if source_id is None or target_id is None:
                continue
            await self._kg.upsert_relation(source_id, target_id, relation.relation_type, seq=seq)

    async def _entity_has_other_mentions(self, entity_id: int, excluding_fingerprint: str) -> bool:
        # Structured-source entities (character/world_entry) are re-linked
        # under a stable fingerprint ("character:<id>") every time they're
        # indexed, independent of any chapter fingerprint -- so a chapter
        # reflow never deletes an entity that also has a structured source.
        entity = await self._kg.get_entity(entity_id)
        if entity and entity.get("canon_id"):
            return True
        return False

    @staticmethod
    def _format_entity_detail(name: str, entity_type: str, description: str, relations: list[dict]) -> str:
        rel_text = ", ".join(f"{r['relation_type']} {r['other_name']}" for r in relations)
        base = f"{name} [{entity_type}] {description}".strip()
        return f"{base} Relations: {rel_text}" if rel_text else base

    async def _commit_facts(self, entities, relations, fingerprint: str) -> None:
        name_to_id: dict[str, int] = {}
        for fact in entities:
            entity_id = await self._kg.upsert_entity(
                fact.name, fact.entity_type, fact.description, canon_id=fact.canon_id,
            )
            name_to_id[fact.name] = entity_id
            await self._kg.link_mention(entity_id, fingerprint)
            existing_relations = await self._kg.entity_relations(entity_id)
            detail = self._format_entity_detail(fact.name, fact.entity_type, fact.description, existing_relations)
            await self._emb.upsert_entity(str(entity_id), fact.name, detail)
        for rel in relations:
            source_id = name_to_id.get(rel.source_name)
            if source_id is None:
                continue
            target = await self._kg.find_entity_by_name(rel.target_name, rel.target_type)
            if target is None:
                continue
            await self._kg.upsert_relation(source_id, target["id"], rel.relation_type)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/store/test_kg_projector.py -v`
Expected: PASS (2 tests)

- [ ] **Step 5: Commit**

```bash
git add novelizer/store/kg_projector.py tests/store/test_kg_projector.py
git commit -m "feat(kg): KGProjector orchestrates structured + prose extraction with reflow"
```

---

### Task 6: Wire KGProjector into runtime.py

**Files:**
- Modify: `novelizer/runtime.py`

**Interfaces:**
- Consumes: `KGStore` (Task 1), `KGProjector` (Task 5),
  `build_kg_extraction_runner` (Task 4), the existing `self.events`, `self.read`,
  `self.embeddings`, `self.settings` already set up in `start()`.
- Produces: `self.kg_store`, `self.kg_projector` attributes;
  `async def kg_catch_up(self) -> None` mirroring the existing `index_catch_up`.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_runtime.py -- add this case to the existing runtime test module
# (mirrors whatever existing test asserts self.indexer is wired after start();
# grep tests/test_runtime.py for "index_catch_up" or "self.indexer" to match
# that test's fixture setup exactly before adding this one)

@pytest.mark.asyncio
async def test_start_wires_kg_projector(runtime):  # `runtime` fixture: existing shared fixture in this test module
    await runtime.start()

    assert runtime.kg_store is not None
    assert runtime.kg_projector is not None
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_runtime.py -k kg_projector -v`
Expected: FAIL with `AttributeError: 'Runtime' object has no attribute 'kg_store'`

- [ ] **Step 3: Write minimal implementation**

In `novelizer/runtime.py`, add imports near the existing `CanonIndexer` import:

```python
from novelizer.store.kg_store import KGStore
from novelizer.store.kg_projector import KGProjector
```

In `start()`, immediately after the existing block:

```python
        self.indexer = CanonIndexer(
            self.events, self.read, self.embeddings,
            str(Path(self.settings.db_path).with_name("embed_cursor.json")),
        )
        await self.index_catch_up()  # backfill; failure-tolerant by contract
```

add:

```python
        self.kg_store = KGStore(self.settings.db_path)
        await self.kg_store.init()
        from novelizer.agents.kg_extraction import build_kg_extraction_runner
        kg_runner = build_kg_extraction_runner(self.settings, callbacks=self._llm_callbacks)
        self.kg_projector = KGProjector(
            self.events, self.read, self.kg_store, self.embeddings, kg_runner,
            str(Path(self.settings.db_path).with_name("kg_cursor.json")),
        )
        await self.kg_catch_up()  # backfill; failure-tolerant by contract
```

Add a new method next to `index_catch_up`:

```python
    async def kg_catch_up(self) -> None:
        """Periodic-caller-safe KG catch-up: no-op without a projector, and
        never raises (KGProjector.catch_up swallows batch failures)."""
        if self.kg_projector is None:
            return
        await self.kg_projector.catch_up()
```

Initialize the new attributes to `None` alongside the existing `self.indexer = None`
near the top of `Runtime.__init__` (or wherever that line lives):

```python
        self.kg_store = None
        self.kg_projector = None
```

Find wherever `index_catch_up` is called on the periodic tick (search
`runtime.py` and the TUI's polling loop for `index_catch_up`) and add a
`await self.kg_catch_up()` call alongside it, same cadence.

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/test_runtime.py -k kg_projector -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add novelizer/runtime.py tests/test_runtime.py
git commit -m "feat(kg): wire KGProjector into Runtime.start() and the catch-up cycle"
```

---

### Task 7: search_canon — entity hit kind

**Files:**
- Modify: `novelizer/canon_fs/search.py`
- Modify: wherever `build_search_canon_tool` is called to construct the tool for
  agent runners (grep `build_search_canon_tool(` across `novelizer/agents/` and
  `novelizer/chat/` to find every call site — each needs the new `kg_store`
  argument threaded through)
- Test: `tests/canon_fs/test_search.py` (existing file — add cases)

**Interfaces:**
- Consumes: `KGStore.get_entity`, `KGStore.entity_relations` (Task 1),
  `EmbeddingStore.search` (existing, now returns `kind="entity"` hits per Task 2).
- Produces: `build_search_canon_tool(embedding_store, read_store, kg_store)` — signature
  gains a required third parameter.

- [ ] **Step 1: Write the failing test**

```python
# tests/canon_fs/test_search.py -- add to existing file
import pytest
from novelizer.canon_fs.search import build_search_canon_tool


@pytest.mark.asyncio
async def test_entity_hit_inlines_name_type_description_and_relations(monkeypatch):
    class FakeHit:
        kind = "entity"
        id = "42"
        title = "The Salted Gull"
        distance = 0.1

    class FakeEmbeddingStore:
        async def search(self, query, kinds=None):
            return [FakeHit()]

    class FakeReadStore:
        async def list_chapters(self): return []
        async def list_characters(self): return []
        async def list_world_entries(self): return []
        async def list_threads(self): return []
        async def list_secrets(self): return []
        async def list_themes(self): return []

    class FakeKGStore:
        async def get_entity(self, entity_id):
            assert entity_id == 42
            return {"id": 42, "name": "The Salted Gull", "entity_type": "location",
                    "description": "a dockside tavern"}
        async def entity_relations(self, entity_id):
            return [{"relation_type": "frequented_by", "other_name": "Mateo", "direction": "in"}]

    tool = build_search_canon_tool(FakeEmbeddingStore(), FakeReadStore(), FakeKGStore())

    result = await tool.ainvoke({"query": "tavern"})

    assert "(entity)" in result
    assert "The Salted Gull" in result
    assert "location" in result
    assert "a dockside tavern" in result
    assert "frequented_by Mateo" in result
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/canon_fs/test_search.py -k entity_hit -v`
Expected: FAIL with `TypeError: build_search_canon_tool() missing 1 required positional argument: 'kg_store'`

- [ ] **Step 3: Write minimal implementation**

Modify `novelizer/canon_fs/search.py`:

```python
def build_search_canon_tool(embedding_store, read_store, kg_store):
    """Factory so the tool closes over story-scoped stores (one tool
    instance per runner, mirroring how runners close over settings)."""

    @tool
    async def search_canon(query: str, kinds: list[str] | None = None) -> str:
        """Search the story canon by MEANING — chapters, characters, world
        entries, threads, secrets, themes, promises, chapter briefs, arcs,
        and knowledge-graph entities (minor places, factions, items, and
        relations the other kinds don't formalize). Use this when you don't
        know the exact words: "where was the locket last seen", "scenes
        about betrayal", "who frequents the Salted Gull". For an exact
        name, slug, or quoted phrase use grep instead; it is faster and
        exact.

        Returns one line per hit: (kind) <canon file path> — '<title>' [id: <id>].
        Read the file at that path for the full content, and cite the id
        exactly as shown. Entity hits have no file to read — the line
        already carries their description and relations inline. Results
        are ranked and capped; if what you need isn't here, narrow the
        query or filter by kind, e.g. kinds=["thread", "secret"].

        Path convention for the M7/M8-deferred kinds, which have no dedicated
        canon_fs file: promises point at the shared outline ledger
        (/outline/ledger.md); open briefs point at the briefs directory
        (/outline/briefs/) since briefs are not individually slugged into
        canon_fs; arcs have no backing file at all, so their hit line carries
        "(no file — cite id)" in the path slot instead.

        Example: search_canon("the debt Mateo owes", kinds=["thread", "secret"])
        """
        try:
            hits = await embedding_store.search(query, kinds=kinds)
        except ValueError as e:
            return str(e)
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
        FALLBACK_PATH_BY_KIND = {
            "promise": "/outline/ledger.md",
            "brief": "/outline/briefs/",
            "arc": "(no file — cite id)",
        }
        lines = []
        for h in hits[:SEARCH_RESULT_CAP]:
            if h.kind == "entity":
                lines.append(await _format_entity_hit(h, kg_store))
                continue
            lines.append(
                f"({h.kind}) "
                f"{path_by_id.get(h.id) or FALLBACK_PATH_BY_KIND.get(h.kind, '(no file)')} "
                f"— '{h.title}' [id: {h.id}]"
            )
        if len(hits) > SEARCH_RESULT_CAP:
            lines.append(
                f"... {len(hits) - SEARCH_RESULT_CAP} more results — narrow your query "
                f"or filter by kind."
            )
        return "\n".join(lines)

    return search_canon


async def _format_entity_hit(hit, kg_store) -> str:
    entity_id = int(hit.id)
    entity = await kg_store.get_entity(entity_id)
    if entity is None:
        return f"(entity) (no file — cite id) — '{hit.title}' [id: {hit.id}]"
    relations = await kg_store.entity_relations(entity_id)
    rel_text = ", ".join(f"{r['relation_type']} {r['other_name']}" for r in relations)
    detail = f"{entity['description']}" if entity["description"] else ""
    suffix = f" Relations: {rel_text}." if rel_text else ""
    return (
        f"(entity) [{entity['entity_type']}] {entity['name']} [id: {hit.id}] "
        f"— {detail}{suffix}"
    )
```

Then update every call site found by the grep above to pass the runtime's
`kg_store`, e.g. `build_search_canon_tool(embeddings, read, kg_store)`.

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/canon_fs/test_search.py -v`
Expected: PASS (all cases, including the new one)

- [ ] **Step 5: Commit**

```bash
git add novelizer/canon_fs/search.py tests/canon_fs/test_search.py
git commit -m "feat(kg): search_canon surfaces knowledge-graph entities inline"
```

Also commit whichever agent/runner files were updated to pass `kg_store` through:

```bash
git add -u
git commit -m "feat(kg): thread kg_store through search_canon tool construction"
```

---

## Post-plan verification

After Task 7, run the full suite once to confirm nothing upstream broke:

```bash
uv run pytest -x
```

Do this in the isolated worktree only — per project convention, never run the full
suite in the main checkout (a prior DB-lock incident is recorded in project memory).
