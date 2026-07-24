# World-Content Curation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Let a new dedicated Curator agent revise, reclassify, merge, and retire world entries in response to curation flags raised by existing agents.

**Architecture:** Event-sourced, append-only. One new event (`WORLD_ENTRY_RETIRED`); revise/reclassify/merge reuse the existing `WORLD_ENTRY_SUPERSEDED`. A new Curator agent mirrors the proven Retconner (`readiness → poll → lane-guard → work → commit/decline`), owning new free-form flag categories routed to it by Triage. World Architect and Continuity Checker gain prompt guidance to raise those categories (their commit paths already honor a FlagDraft's own `category`).

**Tech Stack:** Python 3.13, Pydantic v2, aiosqlite, ChromaDB (via `FakeEmbeddingFunction` in tests), pytest + Hypothesis, `uv` for running.

## Global Constraints

- The event log is append-only (Postgres enforces via triggers). Every mutation is a NEW appended event — never an UPDATE/DELETE of an existing event. A "retire" is a tombstone event, not a destructive write.
- Reuse existing events wherever possible: only `WORLD_ENTRY_RETIRED` is net-new. Revise/reclassify/merge all emit `WORLD_ENTRY_SUPERSEDED` (new entry, `supersedes_id` → old), exactly as the Retconner does.
- World-entry identity uses the supersede-with-new-id model (a revised entry gets a fresh uuid, `supersedes_id` names the retiring entry). Do NOT introduce chapter-style in-place stable ids.
- Follow the Retconner as the template for the Curator: same `_FAILURE_ESCALATION_THRESHOLD = 3`, same `_deferred` set, same decline/escalate shape.
- The Curator owns these flag categories: `world_craft`, `world_relevance`, `world_redundancy`, `worldbuilding`.
- Run only the TARGETED test(s) named in each task's steps. Do NOT run the full suite or a review pass until Task 9 — the whole-suite run and review are deferred to the very end of the workstream.
- Run tests with `uv run pytest <path> -v` from the repo root.

---

### Task 1: `WORLD_ENTRY_RETIRED` event, payload, and `CanonStatus.retired`

**Files:**
- Modify: `novelizer/canon/events.py:8` (add event constant), `novelizer/canon/events.py:288` (add payload class near `ChapterRevised`)
- Modify: `novelizer/store/models.py:25-29` (add enum member)
- Test: `tests/store/test_models.py` (append)

**Interfaces:**
- Produces: `EventType.WORLD_ENTRY_RETIRED == "world_entry.retired"`; `WorldEntryRetired(entry_id: str, reason: str = "", flag_id: str = "")`; `CanonStatus.retired == "retired"`.

- [ ] **Step 1: Write the failing test**

Append to `tests/store/test_models.py`:

```python
def test_world_entry_retired_payload_and_status():
    from novelizer.canon.events import EventType, WorldEntryRetired
    from novelizer.store.models import CanonStatus

    assert EventType.WORLD_ENTRY_RETIRED == "world_entry.retired"
    assert CanonStatus.retired == "retired"
    p = WorldEntryRetired(entry_id="w1", reason="no longer serves the story", flag_id="f1")
    assert p.entry_id == "w1"
    assert p.reason == "no longer serves the story"
    assert p.flag_id == "f1"
    # defaults
    assert WorldEntryRetired(entry_id="w2").reason == ""
    assert WorldEntryRetired(entry_id="w2").flag_id == ""
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/store/test_models.py::test_world_entry_retired_payload_and_status -v`
Expected: FAIL with `ImportError: cannot import name 'WorldEntryRetired'` (or `AttributeError` on `WORLD_ENTRY_RETIRED`).

- [ ] **Step 3: Write minimal implementation**

In `novelizer/canon/events.py`, add the constant directly after line 8 (`WORLD_ENTRY_SUPERSEDED = ...`):

```python
    WORLD_ENTRY_RETIRED = "world_entry.retired"
```

In `novelizer/canon/events.py`, add this payload class just before `class ChapterRevised(BaseModel):` (line 288):

```python
class WorldEntryRetired(BaseModel):
    """Payload for world_entry.retired — a tombstone. The entry named by
    entry_id leaves active canon with no successor (distinct from
    world_entry.superseded, which always names a replacement). The full body
    stays in the event log for provenance; the read model flips the row to
    canon_status='retired' and the indexer drops it from search. `flag_id`
    cites the curation flag that authorized the retirement.
    """

    entry_id: str
    reason: str = ""
    flag_id: str = ""
```

In `novelizer/store/models.py`, add to `class CanonStatus(StrEnum):` (after line 28, `contested = "contested"`):

```python
    retired = "retired"
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/store/test_models.py::test_world_entry_retired_payload_and_status -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add novelizer/canon/events.py novelizer/store/models.py tests/store/test_models.py
git commit -m "feat(canon): add WORLD_ENTRY_RETIRED event, payload, and retired canon status"
```

---

### Task 2: Projector folds `WORLD_ENTRY_RETIRED` into the read model

**Files:**
- Modify: `novelizer/canon/projector.py:238-246` (add an `elif` branch after the `WORLD_ENTRY_SUPERSEDED` handler)
- Test: `tests/canon/test_projector.py` (append)

**Interfaces:**
- Consumes: `EventType.WORLD_ENTRY_RETIRED`, `WorldEntryRetired` (Task 1).
- Produces: after a `WORLD_ENTRY_RETIRED` event for id X, the `world_entries` row X has `canon_status='retired'` and X is absent from `ReadStore.list_world_entries()`.

- [ ] **Step 1: Write the failing test**

Append to `tests/canon/test_projector.py`:

```python
async def test_world_entry_retired_drops_from_active(wired):
    events, proj, path = wired
    from novelizer.canon.events import WorldEntryRetired
    from novelizer.canon.read_store import ReadStore

    await events.append(EventType.WORLD_ENTRY_CREATED, "w1",
                        WorldEntry(id="w1", title="Old Tavern", body="A tavern."))
    await events.append(EventType.WORLD_ENTRY_RETIRED, "w1",
                        WorldEntryRetired(entry_id="w1", reason="redundant", flag_id="f1"))
    await proj.catch_up()

    read = ReadStore(path); await read.init()
    try:
        active = await read.list_world_entries()
        assert "w1" not in {e.id for e in active}
        cur = await proj._conn.execute("SELECT canon_status FROM world_entries WHERE id='w1'")
        assert (await cur.fetchone())[0] == "retired"
    finally:
        await read.close()


@hyp_settings(max_examples=25, deadline=None)
@given(retired=st.booleans())
async def test_retired_entry_never_active_regardless_of_history(retired):
    import os, tempfile
    from novelizer.canon.events import WorldEntryRetired
    fd, path = tempfile.mkstemp(suffix=".db"); os.close(fd)
    events = EventStore(path); await events.init()
    proj = Projector(events, path); await proj.init()
    read = ReadStore(path); await read.init()
    try:
        await events.append(EventType.WORLD_ENTRY_CREATED, "w1",
                            WorldEntry(id="w1", title="T", body="b"))
        if retired:
            await events.append(EventType.WORLD_ENTRY_RETIRED, "w1",
                                WorldEntryRetired(entry_id="w1"))
        await proj.catch_up()
        active_ids = {e.id for e in await read.list_world_entries()}
        assert ("w1" in active_ids) == (not retired)
    finally:
        await read.close(); await proj.close(); await events.close(); os.unlink(path)
```

(`ReadStore`, `EventStore`, `Projector`, `WorldEntry`, `EventType`, `st`, `hyp_settings`, `given` are already imported at the top of `test_projector.py`.)

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/canon/test_projector.py::test_world_entry_retired_drops_from_active tests/canon/test_projector.py::test_retired_entry_never_active_regardless_of_history -v`
Expected: FAIL — the retired row keeps `canon_status='active'` (no projector branch handles the event), so `w1` is still in the active list.

- [ ] **Step 3: Write minimal implementation**

In `novelizer/canon/projector.py`, add this `elif` immediately after the `WORLD_ENTRY_SUPERSEDED` block (after line 246, before the `CHARACTER_CREATED` branch):

```python
        elif t == EventType.WORLD_ENTRY_RETIRED:
            # Tombstone: the entry leaves active canon with no successor. The
            # UPDATE is a no-op on an unknown/already-gone id (0 rows), which
            # is exactly the resilience the Curator's stale-target case wants.
            await self._conn.execute(
                "UPDATE world_entries SET canon_status='retired' WHERE id=?", (p["entry_id"],)
            )
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/canon/test_projector.py::test_world_entry_retired_drops_from_active tests/canon/test_projector.py::test_retired_entry_never_active_regardless_of_history -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add novelizer/canon/projector.py tests/canon/test_projector.py
git commit -m "feat(projector): fold WORLD_ENTRY_RETIRED into read model as retired status"
```

---

### Task 3: Indexer removes retired entries from the vector store

**Files:**
- Modify: `novelizer/store/indexer.py:13-26` (add `WORLD_ENTRY_RETIRED` to `INDEXED_EVENT_TYPES`)
- Test: `tests/store/test_indexer.py` (append)

**Interfaces:**
- Consumes: `EventType.WORLD_ENTRY_RETIRED` (Task 1), the projector's retired-status handling (Task 2).
- Produces: after a retire is projected and indexed, the entry id is absent from the `world_entries` Chroma collection.

**Why this is a one-line change:** `_index_one`'s `world` branch (indexer.py:106-129) hydrates the *active* list and, when the aggregate id is absent from it, deletes that id from the index (the `else` branch at :124-129). A retired entry's aggregate id is exactly that case, so adding the event type to the indexed set is all that's needed.

- [ ] **Step 1: Write the failing test**

Append to `tests/store/test_indexer.py`:

```python
async def test_retired_world_entry_removed_from_index(stack):
    events, proj, read, store, indexer = stack
    from novelizer.canon.events import WorldEntryRetired

    await events.append(EventType.WORLD_ENTRY_CREATED, "w1",
                        WorldEntry(id="w1", title="Bell Cult", body="dusk bells"))
    await proj.catch_up()
    await indexer.catch_up()
    assert any(h.kind == "world" for h in await store.search("bell", n=20))

    await events.append(EventType.WORLD_ENTRY_RETIRED, "w1",
                        WorldEntryRetired(entry_id="w1", reason="redundant"))
    await proj.catch_up()
    await indexer.catch_up()
    hits = await store.search("bell", n=20)
    assert "w1" not in {h.id for h in hits}
```

(If `SearchHit` exposes the id under a different attribute than `.id`, mirror whatever `test_backfill_indexes_every_kind` uses for identity; `.kind` is confirmed present.)

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/store/test_indexer.py::test_retired_world_entry_removed_from_index -v`
Expected: FAIL — `WORLD_ENTRY_RETIRED` is not in `INDEXED_EVENT_TYPES`, so the indexer never processes it and `w1` remains searchable.

- [ ] **Step 3: Write minimal implementation**

In `novelizer/store/indexer.py`, add the event to `INDEXED_EVENT_TYPES` on the world-entry line (line 15):

```python
    EventType.WORLD_ENTRY_CREATED, EventType.WORLD_ENTRY_SUPERSEDED,
    EventType.WORLD_ENTRY_RETIRED,
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/store/test_indexer.py::test_retired_world_entry_removed_from_index -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add novelizer/store/indexer.py tests/store/test_indexer.py
git commit -m "feat(indexer): drop retired world entries from the vector store"
```

---

### Task 4: `CurationDecision` structured-output schema

**Files:**
- Modify: `novelizer/agents/schemas.py` (append, after `RetconAmendments` at line 349)
- Test: `tests/agents/test_schemas.py` (append; create if absent using the pattern below)

**Interfaces:**
- Consumes: `WorldEntryDraft` (schemas.py:10).
- Produces:
  ```python
  class CurationDecision(BaseModel):
      action: Literal["revise", "reclassify", "merge", "retire", "reject"] = "reject"
      reason: str = ""
      entry: Optional[WorldEntryDraft] = None   # the resulting/consolidated entry for revise/reclassify/merge; supersedes_id set to the target/primary
      retire_ids: list[str] = Field(default_factory=list)  # merge: absorbed sources; retire: the target(s)
      evidence: list[str] = Field(default_factory=list)
      feed_note: str = ""
  ```

- [ ] **Step 1: Write the failing test**

Append to `tests/agents/test_schemas.py` (create the file with `from novelizer.agents.schemas import CurationDecision, WorldEntryDraft` at top if it does not exist):

```python
def test_curation_decision_shapes():
    from novelizer.agents.schemas import CurationDecision, WorldEntryDraft

    # default is the safe no-op
    assert CurationDecision().action == "reject"

    revise = CurationDecision(
        action="revise",
        entry=WorldEntryDraft(title="Tavern", body="Tighter prose.", supersedes_id="w1"),
    )
    assert revise.entry.supersedes_id == "w1"

    merge = CurationDecision(
        action="merge",
        entry=WorldEntryDraft(title="The Tavern", body="Consolidated.", supersedes_id="w1"),
        retire_ids=["w2", "w3"],
    )
    assert merge.retire_ids == ["w2", "w3"]

    retire = CurationDecision(action="retire", retire_ids=["w9"], reason="no longer serves the story")
    assert retire.retire_ids == ["w9"]

    # unknown domain on the carried entry is coerced, never raised
    assert CurationDecision(
        action="revise",
        entry=WorldEntryDraft(title="X", body="y", domain="nonsense"),
    ).entry.domain == "other"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/agents/test_schemas.py::test_curation_decision_shapes -v`
Expected: FAIL with `ImportError: cannot import name 'CurationDecision'`.

- [ ] **Step 3: Write minimal implementation**

Append to `novelizer/agents/schemas.py` (after `RetconAmendments`, which ends at line 349):

```python
class CurationDecision(BaseModel):
    """The Curator's verdict on one curation flag against world entries.

    One action per resolve. `revise`/`reclassify`/`merge` carry the resulting
    entry in `entry` with `supersedes_id` set to the entry it replaces (the
    primary, for a merge); the Curator commits it as WORLD_ENTRY_SUPERSEDED.
    `merge` additionally lists the absorbed sources in `retire_ids` (committed
    as WORLD_ENTRY_RETIRED). `retire` lists the target(s) in `retire_ids` with
    no `entry`. `reject` carries only a `reason` and routes to the decline
    path. Defaults to `reject` so a malformed/empty response is a safe no-op,
    never an accidental mutation.
    """

    action: Literal["revise", "reclassify", "merge", "retire", "reject"] = "reject"
    reason: str = ""
    entry: Optional[WorldEntryDraft] = None
    retire_ids: list[str] = Field(default_factory=list)
    evidence: list[str] = Field(default_factory=list)
    feed_note: str = ""
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/agents/test_schemas.py::test_curation_decision_shapes -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add novelizer/agents/schemas.py tests/agents/test_schemas.py
git commit -m "feat(schemas): add CurationDecision structured-output schema"
```

---

### Task 5: The Curator agent

**Files:**
- Create: `novelizer/agents/curator.py`
- Test: `tests/agents/test_curator.py`

**Interfaces:**
- Consumes: `CurationDecision` (Task 4), `EventType.WORLD_ENTRY_RETIRED` + `WorldEntryRetired` (Task 1), `EventType.WORLD_ENTRY_SUPERSEDED`, `EventType.FLAG_RESOLVED/REJECTED/ESCALATED/ESCALATION_CLEARED`, `WorldEntry`, `Flag`, `FlagStatus`, `ReadStore.list_flags`, `ReadStore.list_world_entries`, `Committer.commit`.
- Produces: `class Curator(BaseAgent)` with `readiness/poll/work/commit/_decline/_run`; module-level `_CURATION_CATEGORIES`; `build_curator_runner(...)`; `SPEC` (wired in Task 6).

This mirrors `novelizer/agents/retconner.py` closely; read that file alongside this task.

- [ ] **Step 1: Write the failing test**

Create `tests/agents/test_curator.py`:

```python
import os
import tempfile
import pytest
from novelizer.canon.event_store import EventStore
from novelizer.canon.projector import Projector
from novelizer.canon.read_store import ReadStore
from novelizer.canon.committer import Committer
from novelizer.canon.events import EventType
from novelizer.agents.curator import Curator
from novelizer.agents.schemas import CurationDecision, WorldEntryDraft
from novelizer.store.models import WorldEntry, Flag, FlagStatus


class FakeRunner:
    def __init__(self, out):
        self._out = out
        self.calls = []

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


async def test_revise_supersedes_and_resolves(stack):
    events, proj, read, committer = stack
    await events.append(EventType.WORLD_ENTRY_CREATED, "w1",
                        WorldEntry(id="w1", title="Tavern", body="Bloated prose."))
    await events.append(EventType.FLAG_CREATED, "f1",
                        Flag(id="f1", category="world_craft", description="prose is bloated",
                             related_entry_ids=["w1"], proposed_resolution="tighten"))
    await proj.catch_up()
    out = CurationDecision(action="revise",
                           entry=WorldEntryDraft(title="Tavern", body="Tight prose.", supersedes_id="w1"))
    agent = Curator(FakeRunner(out), read, committer)
    await agent.run_once()
    await proj.catch_up()

    active = await read.list_world_entries()
    assert "w1" not in {e.id for e in active}
    assert [e for e in active if e.body == "Tight prose."]
    assert await read.list_flags(category="world_craft", status=FlagStatus.open) == []
    assert len(await read.list_flags(category="world_craft", status=FlagStatus.resolved)) == 1


async def test_merge_supersedes_primary_and_retires_others(stack):
    events, proj, read, committer = stack
    for wid, body in (("w1", "Tavern A."), ("w2", "Tavern B."), ("w3", "Tavern C.")):
        await events.append(EventType.WORLD_ENTRY_CREATED, wid, WorldEntry(id=wid, title="Tavern", body=body))
    await events.append(EventType.FLAG_CREATED, "f1",
                        Flag(id="f1", category="world_redundancy", description="three tavern entries",
                             related_entry_ids=["w1", "w2", "w3"], proposed_resolution="merge"))
    await proj.catch_up()
    out = CurationDecision(
        action="merge",
        entry=WorldEntryDraft(title="The Tavern", body="One consolidated tavern.", supersedes_id="w1"),
        retire_ids=["w2", "w3"],
    )
    await Curator(FakeRunner(out), read, committer).run_once()
    await proj.catch_up()

    active_ids = {e.id for e in await read.list_world_entries()}
    assert active_ids.isdisjoint({"w1", "w2", "w3"})
    assert [e for e in await read.list_world_entries() if e.body == "One consolidated tavern."]
    assert len(await read.list_flags(category="world_redundancy", status=FlagStatus.resolved)) == 1


async def test_retire_tombstones_and_resolves(stack):
    events, proj, read, committer = stack
    await events.append(EventType.WORLD_ENTRY_CREATED, "w1", WorldEntry(id="w1", title="Junk", body="Irrelevant."))
    await events.append(EventType.FLAG_CREATED, "f1",
                        Flag(id="f1", category="world_relevance", description="no longer serves the story",
                             related_entry_ids=["w1"], proposed_resolution="retire"))
    await proj.catch_up()
    out = CurationDecision(action="retire", retire_ids=["w1"], reason="no longer serves the story")
    await Curator(FakeRunner(out), read, committer).run_once()
    await proj.catch_up()

    assert "w1" not in {e.id for e in await read.list_world_entries()}
    assert len(await read.list_flags(category="world_relevance", status=FlagStatus.resolved)) == 1


async def test_reject_declines_and_counts_attempt(stack):
    events, proj, read, committer = stack
    await events.append(EventType.WORLD_ENTRY_CREATED, "w1", WorldEntry(id="w1", title="Keep", body="Load-bearing."))
    await events.append(EventType.FLAG_CREATED, "f1",
                        Flag(id="f1", category="world_relevance", description="maybe stale?",
                             related_entry_ids=["w1"], proposed_resolution="retire"))
    await proj.catch_up()
    out = CurationDecision(action="reject", reason="entry is load-bearing; keep it")
    await Curator(FakeRunner(out), read, committer).run_once()
    await proj.catch_up()

    assert "w1" in {e.id for e in await read.list_world_entries()}
    rejected = await read.list_flags(category="world_relevance", status=FlagStatus.rejected)
    assert len(rejected) == 1 and rejected[0].failed_attempts == 1


async def test_lane_guard_declines_non_world_target(stack):
    events, proj, read, committer = stack
    await events.append(EventType.FLAG_CREATED, "f1",
                        Flag(id="f1", category="world_relevance", description="about a character",
                             related_entry_ids=["char-mara"], proposed_resolution="retire"))
    await proj.catch_up()

    class BoomRunner:
        async def ainvoke(self, inputs):
            raise AssertionError("LLM must not be called when lane guard trips")

    await Curator(BoomRunner(), read, committer).run_once()
    await proj.catch_up()
    rejected = await read.list_flags(category="world_relevance", status=FlagStatus.rejected)
    assert len(rejected) == 1 and "out_of_lane" in rejected[0].proposed_resolution
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/agents/test_curator.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'novelizer.agents.curator'`.

- [ ] **Step 3: Write minimal implementation**

Create `novelizer/agents/curator.py`:

```python
from __future__ import annotations
import logging
from novelizer.agents.base import BaseAgent, Runner
from agent_kit import GRAPH_RECURSION_LIMIT
from novelizer.agents.prompts import OUTPUT_CONVENTIONS_NOTE
from novelizer.agents.schemas import CurationDecision
from novelizer.agents.author import RETRIEVAL_NOTE_BASE
from novelizer.canon.read_store import ReadStore
from novelizer.canon.committer import Committer
from novelizer.canon.events import EventType, WorldEntryRetired
from novelizer.store.models import WorldEntry, FlagStatus

logger = logging.getLogger(__name__)

# Categories the Curator owns. Triage routes these to "curator" (see triage.py).
_CURATION_CATEGORIES = ("world_craft", "world_relevance", "world_redundancy", "worldbuilding")

SYSTEM_PROMPT = """You are the Curator for a living fictional world — the editor who keeps the
WORLD ENTRIES (not the story prose) coherent, relevant, and free of clutter. A sibling agent has
filed a curation flag against one or more world entries. Verify the concern still holds in live
canon, then resolve it with the least destructive action that serves the story.

## Your lane
You improve WORLD ENTRIES by superseding or retiring them. That is the whole job. You do NOT touch
chapter prose, character sheets, threads, secrets, or themes. If the flag's ids name no world
entry, set action="reject" and say it's out of your lane.

## Your actions — prefer the least destructive
- revise: the entry stays canon but its prose needs work (bloated, muddled, weak). Return the
  improved entry in `entry`, keeping title/domain/tags unless the flag is about those. Set
  entry.supersedes_id to the exact id you are replacing.
- reclassify: the entry's facts are fine but its domain/tags are wrong, so it surfaces in the
  wrong places. Return the same body with corrected domain/tags in `entry`, supersedes_id set.
- merge: two or more entries overlap. Return ONE consolidated entry in `entry` (supersedes_id =
  the primary you keep) and list the OTHER entries' ids in `retire_ids`.
- retire: the entry no longer serves the story and has no better home. List its id in
  `retire_ids`. Retire is the LAST RESORT — when in doubt, revise, reclassify, or merge instead.
  Never retire an entry that current chapters clearly rely on.
- reject: the flag is stale, wrong, or out of lane. Give a one-line `reason`.

## How to work — VERIFY, then ACT
The flag and the inlined bodies were captured on an earlier pass and may be STALE. Use
read_file / grep / search_canon to read the CURRENT entries before you act. Put the spans you
actually read into `evidence`. If you cannot cite where you verified something, you have not
verified it: read first, then emit.

## Voice
Do the analysis under these neutral instructions. Put your personality only in the one-line
feed_note — never in entry bodies, which must read as plain canon."""


class Curator(BaseAgent):
    _FAILURE_ESCALATION_THRESHOLD = 3

    def __init__(
        self,
        runner: Runner,
        read_store: ReadStore,
        committer: Committer,
        interval: int = 120,
        personality: str = "",
        pull_mode: bool = False,
    ) -> None:
        super().__init__(runner, read_store, committer, interval, name="curator", personality=personality)
        self._deferred: set[str] = set()
        self.pull_mode = pull_mode

    async def _open_curation_flags(self) -> list:
        flags = await self._read.list_flags(status=FlagStatus.open)
        return [f for f in flags if f.category in _CURATION_CATEGORIES]

    async def readiness(self) -> float:
        return min(1.0, len(await self._open_curation_flags()) / 3)

    async def poll(self) -> dict:
        open_flags = await self._open_curation_flags()
        self._deferred &= {f.id for f in open_flags}
        candidates = [f for f in open_flags if f.id not in self._deferred]
        if not candidates and open_flags:
            self._deferred.clear()
            candidates = open_flags
        return {"target": candidates[0] if candidates else None,
                "world": await self._read.list_world_entries()}

    async def work(self, ctx: dict) -> CurationDecision | None:
        flag = ctx["target"]
        if flag is None:
            return None
        related = [e for e in ctx["world"] if e.id in flag.related_entry_ids]
        if self.pull_mode:
            text = "\n".join(f"[{e.id}] {e.title}" for e in related) or "(entries not found)"
        else:
            text = "\n".join(f"[{e.id}] {e.title}: {e.body}" for e in related) or "(entries not found)"
        cast = self._guarded_line("In character", self.personality)
        msg = (f"Curation flag [{flag.category}]: {flag.description}\n\n"
               f"Proposed resolution: {flag.proposed_resolution}\n\nRelated entries:\n{text}{cast}")
        result = await self._runner.ainvoke({"messages": [{"role": "user", "content": msg}]})
        return result.get("structured_response")

    async def _decline(self, flag, resolution: str, reason: str) -> None:
        logger.info("curator: declining flag %s (%s): %s", flag.id, resolution, reason)
        attempts = flag.failed_attempts + 1
        rejected = flag.model_copy(update={
            "status": FlagStatus.rejected,
            "resolved_by": self.name,
            "proposed_resolution": f"[{resolution}] {reason}" if reason else f"[{resolution}]",
            "failed_attempts": attempts,
        })
        await self._committer.commit(self.name, EventType.FLAG_REJECTED, flag.id, rejected)
        if attempts >= self._FAILURE_ESCALATION_THRESHOLD and not rejected.escalated:
            escalated = rejected.model_copy(update={"escalated": True})
            await self._committer.commit(self.name, EventType.FLAG_ESCALATED, flag.id, escalated)

    async def commit(self, out: CurationDecision | None, ctx: dict) -> None:
        flag = ctx["target"]
        if flag is None or out is None:
            return
        # Validate the decision's shape; a malformed action declines rather than
        # committing a half-formed mutation.
        if out.action == "reject":
            await self._decline(flag, "reject", out.reason)
            await self._remark(out.feed_note)
            return
        if out.action in ("revise", "reclassify", "merge") and out.entry is None:
            await self._decline(flag, "invalid", f"{out.action} requires an entry")
            await self._remark(out.feed_note)
            return
        if out.action in ("merge", "retire") and not out.retire_ids:
            await self._decline(flag, "invalid", f"{out.action} requires retire_ids")
            await self._remark(out.feed_note)
            return

        if out.action in ("revise", "reclassify", "merge"):
            e = out.entry
            entry = WorldEntry(title=e.title, body=e.body, domain=e.domain, tags=e.tags,
                               supersedes_id=e.supersedes_id)
            await self._committer.commit(self.name, EventType.WORLD_ENTRY_SUPERSEDED, entry.id, entry)
        if out.action in ("merge", "retire"):
            for rid in out.retire_ids:
                payload = WorldEntryRetired(entry_id=rid, reason=out.reason, flag_id=flag.id)
                await self._committer.commit(self.name, EventType.WORLD_ENTRY_RETIRED, rid, payload)

        resolved = flag.model_copy(update={"status": FlagStatus.resolved, "resolved_by": self.name})
        await self._committer.commit(self.name, EventType.FLAG_RESOLVED, flag.id, resolved)
        if resolved.escalated:
            cleared = resolved.model_copy(update={"escalated": False, "escalation_cleared_by": "agent"})
            await self._committer.commit(self.name, EventType.FLAG_ESCALATION_CLEARED, flag.id, cleared)
        await self._remark(out.feed_note)

    async def _run(self) -> None:
        ctx = await self.poll()
        flag = ctx["target"]
        if flag is None:
            return
        # Lane guard, before any LLM call: the Curator only mutates world
        # entries, so a flag naming no active world entry cannot be actioned.
        # An EMPTY id list is not out-of-lane — the filer may have described
        # the target in prose only.
        named = flag.related_entry_ids
        if named and not any(e.id in named for e in ctx["world"]):
            await self._decline(
                flag, "out_of_lane",
                "related_entry_ids name no world entry; the Curator only curates world entries",
            )
            self._deferred.discard(flag.id)
            return
        try:
            out = await self.work(ctx)
            if out is None:
                self._deferred.add(flag.id)
                return
            await self.commit(out, ctx)
        except Exception:
            self._deferred.add(flag.id)
            raise
        self._deferred.discard(flag.id)


def build_curator_runner(settings, callbacks=None, backend=None, tools=None, subagents=None):
    from deepagents import create_deep_agent
    from agent_kit import build_chat_model
    from agent_kit import ExcludeToolsMiddleware
    if backend is not None:
        model = build_chat_model(
            settings.agent_model, settings.llm_base_url, settings.llm_api_key,
            settings.agent_temperature, max_tokens=settings.llm_max_tokens,
            callbacks=None, streaming=callbacks is not None,
        )
        system_prompt = SYSTEM_PROMPT + RETRIEVAL_NOTE_BASE + OUTPUT_CONVENTIONS_NOTE
        graph = create_deep_agent(
            model=model, system_prompt=system_prompt, response_format=CurationDecision,
            backend=backend, tools=tools, subagents=subagents,
            middleware=[ExcludeToolsMiddleware(excluded=frozenset({"write_todos"}))],
        )
        config = {"recursion_limit": GRAPH_RECURSION_LIMIT}
        if callbacks:
            config["callbacks"] = callbacks
        return graph.with_config(config)
    model = build_chat_model(settings.agent_model, settings.llm_base_url, settings.llm_api_key, settings.agent_temperature, max_tokens=settings.llm_max_tokens, callbacks=callbacks)
    return create_deep_agent(model=model, system_prompt=SYSTEM_PROMPT, response_format=CurationDecision)


from novelizer.agents.registry_types import AgentContext, AgentSpec, ToolGrant, SubagentGrant


def _construct(ctx: AgentContext) -> Curator:
    enabled = ctx.settings.curator_tools_enabled
    subagent_enabled = ctx.settings.curator_subagent_enabled
    builder = ctx.tooled(build_curator_runner, enabled, subagent_enabled, "curator")
    runner = ctx.runner_for("curator", builder)
    return Curator(
        runner, ctx.read, ctx.committer,
        interval=ctx.settings.default_agent_interval,
        personality=ctx.personalities.get("curator", ""),
        pull_mode=enabled,
    )


SPEC = AgentSpec(
    name="curator",
    tool_grant=ToolGrant(enabled_setting="curator_tools_enabled"),
    subagent_grant=SubagentGrant(enabled_setting="curator_subagent_enabled"),
    construct=_construct,
)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/agents/test_curator.py -v`
Expected: PASS (all five tests). `SPEC`/`build_curator_runner` are exercised in Task 6; the agent-behavior tests construct `Curator` directly with a `FakeRunner`, so they pass without settings wiring.

- [ ] **Step 5: Commit**

```bash
git add novelizer/agents/curator.py tests/agents/test_curator.py
git commit -m "feat(agents): add Curator agent (revise/reclassify/merge/retire world entries)"
```

---

### Task 6: Settings knobs + registry registration

**Files:**
- Modify: `novelizer/settings/models.py:18-24` (override allowlist) and `:105,115` (field defaults)
- Modify: `novelizer/agents/registry.py:2-5,11-17`
- Test: `tests/agents/test_curator_registration.py` (create)

**Interfaces:**
- Consumes: `curator.SPEC` (Task 5).
- Produces: `Settings.curator_tools_enabled: bool = True`, `Settings.curator_subagent_enabled: bool = False`; `curator.SPEC` present in `AGENT_REGISTRY`.

- [ ] **Step 1: Write the failing test**

Create `tests/agents/test_curator_registration.py`:

```python
def test_curator_registered_and_settings_present():
    from novelizer.agents.registry import AGENT_REGISTRY
    from novelizer.settings.models import Settings

    names = [spec.name for spec in AGENT_REGISTRY]
    assert "curator" in names

    s = Settings()
    assert s.curator_tools_enabled is True
    assert s.curator_subagent_enabled is False
```

(If `Settings` lives under a different name/path, mirror the import used by `tests/test_apply_settings.py`.)

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/agents/test_curator_registration.py -v`
Expected: FAIL — `curator` not in registry / `curator_tools_enabled` attribute missing.

- [ ] **Step 3: Write minimal implementation**

In `novelizer/settings/models.py`, add the two field defaults. After line 105 (`retconner_tools_enabled: bool = True`):

```python
    curator_tools_enabled: bool = True
```

After line 115 (`retconner_subagent_enabled: bool = False`):

```python
    curator_subagent_enabled: bool = False
```

In the override-allowlist tuple at the top of the file (the block spanning lines 18-24), add the two names alongside the retconner entries — put `"curator_tools_enabled"` next to `"retconner_tools_enabled"` (line 20) and `"curator_subagent_enabled"` next to `"retconner_subagent_enabled"` (line 23):

```python
    "retconner_tools_enabled", "curator_tools_enabled", "structure_analyst_tools_enabled",
    ...
    "editor_subagent_enabled", "retconner_subagent_enabled", "curator_subagent_enabled", "structure_analyst_subagent_enabled",
```

In `novelizer/agents/registry.py`, add `curator` to the import tuple (line 2-5):

```python
from novelizer.agents import (
    author, world_architect, character_keeper, editor,
    continuity_checker, retconner, curator, structure_analyst, summarizer, plotter, muse, triage,
)
```

And add `curator.SPEC` to `AGENT_REGISTRY` right after `retconner.SPEC` (line 14):

```python
    editor.SPEC, continuity_checker.SPEC, retconner.SPEC, curator.SPEC, structure_analyst.SPEC,
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/agents/test_curator_registration.py -v`
Expected: PASS

Then verify the identity/TUI layer tolerates the new agent (it reads SPECs at `novelizer/tui/identity.py`):

Run: `uv run pytest tests/tui -k identity -v`
Expected: PASS. If a test asserts an exact agent-name set or a required per-agent identity entry, add a `curator` entry mirroring `retconner`'s in `novelizer/tui/identity.py` and re-run.

- [ ] **Step 5: Commit**

```bash
git add novelizer/settings/models.py novelizer/agents/registry.py tests/agents/test_curator_registration.py novelizer/tui/identity.py
git commit -m "feat(agents): register Curator in the agent registry and settings"
```

---

### Task 7: Triage routes curation categories to the Curator

**Files:**
- Modify: `novelizer/agents/triage.py:16-22` (`_CATEGORY_OWNERS`)
- Test: `tests/agents/test_triage.py` (append)

**Interfaces:**
- Consumes: the `curator` agent name (Task 5).
- Produces: `_CATEGORY_OWNERS` maps `world_craft`, `world_relevance`, `world_redundancy`, and `worldbuilding` → `"curator"`.

**Note:** this also fixes the pre-existing dead-letter: `worldbuilding` was mapped to `world_architect`, which never consumes its flags. Reassigning it to the Curator (which does consume) closes that leak.

- [ ] **Step 1: Write the failing test**

Append to `tests/agents/test_triage.py`:

```python
def test_curation_categories_route_to_curator():
    from novelizer.agents.triage import _CATEGORY_OWNERS
    for cat in ("world_craft", "world_relevance", "world_redundancy", "worldbuilding"):
        assert _CATEGORY_OWNERS[cat] == "curator"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/agents/test_triage.py::test_curation_categories_route_to_curator -v`
Expected: FAIL — the new categories are absent and `worldbuilding` maps to `world_architect`.

- [ ] **Step 3: Write minimal implementation**

In `novelizer/agents/triage.py`, replace the `_CATEGORY_OWNERS` dict (lines 16-22) with:

```python
_CATEGORY_OWNERS: dict[str, str] = {
    "contradiction": "retconner",
    "pacing": "structure_analyst",
    "worldbuilding": "curator",
    "world_craft": "curator",
    "world_relevance": "curator",
    "world_redundancy": "curator",
    "thematic": "plotter",
    "voice_drift": "retconner",
}
```

Also update the Triage system prompt's reclassify vocabulary (triage.py:35-36) so Triage can reclassify unowned flags into the curation categories. Change the sentence listing the fixed vocabulary to include them:

```python
`reclassify_category` if you can tell what it actually is from a fixed vocabulary the owning
agents understand: contradiction, pacing, worldbuilding, world_craft, world_relevance,
world_redundancy, thematic, voice_drift. If none fit,
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/agents/test_triage.py::test_curation_categories_route_to_curator -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add novelizer/agents/triage.py tests/agents/test_triage.py
git commit -m "feat(triage): route world-curation categories to the Curator"
```

---

### Task 8: Reactive raisers — World Architect and Continuity Checker

**Files:**
- Modify: `novelizer/agents/world_architect.py` (SYSTEM_PROMPT — add guidance to file `world_redundancy` / `world_relevance` flags)
- Modify: `novelizer/agents/continuity_checker.py` (SYSTEM_PROMPT — add guidance to file `world_relevance` flags for soft drift, distinct from hard contradictions)
- Test: `tests/agents/test_curation_raisers.py` (create)

**Interfaces:**
- Consumes: the existing flag-commit paths that already honor a `FlagDraft`/draft's own `category` (`world_architect.py:142` uses `r.category`; `continuity_checker.py:477` uses `r.category`).
- Produces: no new mechanism — verified behavior that a curation-category draft flag is committed with its category intact, plus prompt guidance that makes the agents actually raise them.

**Why prompt-only:** both agents already commit their structured-output flags using each draft's own `category` field, so raising a new category needs no plumbing change — only prompt guidance telling the agent when to use it. The test below pins the commit behavior so a future refactor can't silently regress it.

Scope note: `world_craft` has no dedicated Stage-1 raiser (the world-prose-quality signal is the weakest and most subjective). The category still exists and is Curator-owned; any agent may file it, and Stage 2's proactive sweep will. This is a deliberate YAGNI trim.

- [ ] **Step 1: Write the failing test**

Create `tests/agents/test_curation_raisers.py`. This drives the World Architect's real `commit` with a draft carrying a curation category and asserts it lands as an open `world_redundancy` flag:

```python
import os
import tempfile
import pytest
from novelizer.canon.event_store import EventStore
from novelizer.canon.projector import Projector
from novelizer.canon.read_store import ReadStore
from novelizer.canon.committer import Committer
from novelizer.canon.events import EventType
from novelizer.agents.world_architect import WorldArchitect
from novelizer.agents.schemas import WorldEntriesDraft, FlagDraft
from novelizer.store.models import FlagStatus


class FakeRunner:
    def __init__(self, out):
        self._out = out
    async def ainvoke(self, inputs):
        return {"structured_response": self._out}


@pytest.fixture
async def stack():
    fd, path = tempfile.mkstemp(suffix=".db"); os.close(fd)
    events = EventStore(path); await events.init()
    proj = Projector(events, path); await proj.init()
    read = ReadStore(path); await read.init()
    yield events, proj, read, Committer(events)
    await read.close(); await proj.close(); await events.close(); os.unlink(path)


async def test_world_architect_files_world_redundancy_with_category_intact(stack):
    events, proj, read, committer = stack
    draft = WorldEntriesDraft(
        entries=[],
        flags=[FlagDraft(category="world_redundancy",
                         description="two overlapping tavern entries",
                         related_entry_ids=["w1", "w2"], proposed_resolution="merge")],
    )
    agent = WorldArchitect(FakeRunner(draft), read, committer)
    await agent.run_once()
    await proj.catch_up()
    open_flags = await read.list_flags(category="world_redundancy", status=FlagStatus.open)
    assert len(open_flags) == 1
    assert open_flags[0].filed_by == "world_architect"
```

(Match `WorldArchitect`'s constructor signature to how `tests/agents/test_world_architect.py` builds it — if it takes extra args, copy that construction. The assertion, not the constructor, is the point of this test.)

- [ ] **Step 2: Run test to verify it fails or passes**

Run: `uv run pytest tests/agents/test_curation_raisers.py -v`
Expected: This may already PASS (the commit path honors `r.category`). If it FAILS because the commit path hardcodes a category, fix `world_architect.py`'s flag-commit loop to use the draft's own `category` (mirroring the `Flag(category=r.category, ...)` shape already at `world_architect.py:142`), then re-run to green. Either way, do not proceed until it is green — this test is the regression guard for the raiser mechanism.

- [ ] **Step 3: Add the prompt guidance**

In `novelizer/agents/world_architect.py` SYSTEM_PROMPT, add a short paragraph (the World Architect is currently "additive only" — this authorizes it to *flag*, not to mutate):

```
## Curation flags — flag, don't fix
You never edit or delete existing entries yourself. But when your survey of canon reveals a
world entry that should be curated, file a flag for the Curator to resolve:
- Two or more entries that clearly overlap or duplicate each other → category "world_redundancy",
  related_entry_ids naming them, proposed_resolution "merge".
- An entry filed under the wrong domain or carrying stale/wrong tags → category "world_relevance",
  naming the entry, proposed_resolution describing the correct classification.
File these in your `flags` output; do not act on them.
```

In `novelizer/agents/continuity_checker.py` SYSTEM_PROMPT, add:

```
## Soft drift vs hard contradiction
A factual, logical contradiction is a "contradiction" flag for the Retconner, as before. But when
a world entry has merely DRIFTED from where the story actually went — still internally consistent,
just no longer matching the narrative's direction — that is a curation concern, not a contradiction.
File it as category "world_relevance", naming the entry, proposed_resolution describing the drift.
The Curator, not the Retconner, resolves these.
```

Confirm the Continuity Checker's flag-commit loop honors each draft's `category` (it does at `continuity_checker.py:477`, `Flag(category=r.category, ...)`); if a `world_relevance` FlagDraft would be forced to `contradiction`, adjust that loop to use the draft's own category.

- [ ] **Step 4: Re-run the raiser test**

Run: `uv run pytest tests/agents/test_curation_raisers.py -v`
Expected: PASS (unchanged — prompt edits don't affect this mechanical test; it guards the commit path).

- [ ] **Step 5: Commit**

```bash
git add novelizer/agents/world_architect.py novelizer/agents/continuity_checker.py tests/agents/test_curation_raisers.py
git commit -m "feat(agents): World Architect and Continuity Checker raise world-curation flags"
```

---

### Task 9: Full-suite verification, docs sync, and branch finish

This is the deferred verification gate — the first time the whole suite runs and the work is reviewed.

- [ ] **Step 1: Run the full test suite**

Run: `uv run pytest -q`
Expected: PASS. Investigate and fix any failure before proceeding. (Per project memory, do NOT run suites in the main checkout — this worktree is the correct place.)

- [ ] **Step 2: Sync documentation**

Invoke the `syncing-diataxis-docs` skill to update how-to/reference/explanation docs for the new Curator agent, the `world_*` flag categories, and the `WORLD_ENTRY_RETIRED` event. At minimum, add the Curator to any agent-roster reference doc and document the new flag categories alongside the existing ones.

- [ ] **Step 3: Self-review the diff**

Run: `git diff main...HEAD --stat` and skim the full diff. Confirm: only `WORLD_ENTRY_RETIRED` is net-new in the event vocabulary; every mutation path appends events (no event-log UPDATE/DELETE); the Curator mirrors the Retconner's escalation/decline shape; no `world_craft` raiser was silently added beyond what the plan scopes.

- [ ] **Step 4: Request code review**

Invoke `superpowers:requesting-code-review` for the branch. Address findings.

- [ ] **Step 5: Final commit / open PR**

```bash
git add -A
git commit -m "docs: document Curator agent and world-curation flow" || true
git push -u origin worktree-world-curation-spec
gh pr create --draft --title "World-content curation: Curator agent + retire event" --body "Implements docs/superpowers/specs/2026-07-23-world-content-curation-design.md — reactive world-entry curation (Stage 1)."
```

---

## Self-Review

**Spec coverage:**
- One new event (`WORLD_ENTRY_RETIRED`) → Task 1. Retired canon status → Task 1. Projection → Task 2. Index removal → Task 3. ✓
- Revise/reclassify/merge via `SUPERSEDED`; retire via `RETIRED` → Curator `commit` (Task 5). ✓
- Three flag categories + Curator ownership + `worldbuilding` reassignment → Tasks 5 (constant), 7 (routing). ✓
- Reactive raisers (World Architect, Continuity Checker) → Task 8; `world_craft` no-raiser trim documented. ✓
- Curator modeled on Retconner (readiness/poll/lane-guard/commit/decline/escalate) → Task 5. ✓
- Relevance wiring (reclassify reindexes, retire de-indexes) → Task 3 + Curator `SUPERSEDED` path. ✓
- Testing: projector property test, per-verb Curator tests, indexer removal, lane guard, decline/attempt count → Tasks 2,3,5. ✓
- Registration/settings → Task 6. Full-suite + review deferred to end → Task 9. ✓
- Stage 2 (proactive sweep, last-referenced, split/relocate) → explicitly out of scope; not planned. ✓

**Placeholder scan:** No TBD/TODO; every code step shows complete code. The only conditional step (Task 8 Step 2) gives an explicit both-branches instruction, not a placeholder.

**Type consistency:** `CurationDecision` fields (`action`, `entry`, `retire_ids`, `reason`, `evidence`, `feed_note`) are identical across Task 4 (definition), Task 5 (consumption), and the tests. `WorldEntryRetired(entry_id, reason, flag_id)` matches across Tasks 1, 2, 5. `_CURATION_CATEGORIES` (Task 5) and `_CATEGORY_OWNERS` (Task 7) list the same four categories. Event constant `WORLD_ENTRY_RETIRED` is spelled identically everywhere.
