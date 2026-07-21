# Generic Issue-Flagging (Flag model + Triage agent) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the contradiction-only `RetconRequest` mechanism with a generic `Flag` model any agent can file (free-form `category`), add a Triage agent that verifies/dedups/routes flags to owning agents (or catch-alls unowned categories until stale), and update the TUI's Retcons view into a category-grouped Flags view.

**Architecture:** `Flag` is a new canon record (event-sourced, same shape family as `WorldEntry`/`Character`): filed via `FLAG_CREATED`, resolved via `FLAG_RESOLVED`/`FLAG_REJECTED`. Every filing agent gets one `flags: list[FlagDraft]` field on its structured output. A new `Triage` agent polls all open flags; a code-level `category -> owner agent name` map tells it whether to leave a verified flag open for its owner's own poll (same pattern Retconner already uses) or handle it itself as an unowned catch-all, with a `triage_passes` counter that flips unresolved catch-all flags to `stale` after a threshold. `RetconRequest`/`RetconStatus` are removed outright (not kept as a compat shim) — this is an internal-only type, no external API depends on the name.

**Tech Stack:** Python 3.13, Pydantic v2, aiosqlite (event store + read-model projections), pytest + pytest-asyncio (existing `stack` fixture pattern: real `EventStore`+`Projector`+`ReadStore`+`Committer` against a temp sqlite file, `FakeRunner`/`ScriptedRunner` standing in for the LLM).

## Global Constraints

- Never remove or rewrite historical events — the event log is append-only. Legacy `RETCON_REQUEST_CREATED/RESOLVED/REJECTED` events must keep projecting correctly forever (projector aliases them into `category="contradiction"` Flags), even though no code path emits them anymore after this plan.
- `RetconRequest`/`RetconStatus`/`list_retcon_requests`/`VoiceDriftFlag`/`VOICE_SOURCE_TAG` are deleted from the codebase once their call sites are migrated — no re-export shims, no `# removed` comments.
- Every task's tests run scoped (`pytest tests/agents/test_x.py -v` or similar) — do **not** run the full test suite until the final review task. Full-suite runs are known to produce dramatic runtimes / spurious load-flakes in this repo (see `docs/TESTING-TUI.md` and memory `testing-load-flakes`).
- Follow the existing agent template exactly (`readiness`/`poll`/`work`/`commit`/`_run`, `build_x_runner`, `_construct`, `SPEC = AgentSpec(...)`) for the new Triage agent — don't invent a different shape.
- `is_gated(agent_name, event_type)` in `novelizer/canon/policy.py` is keyed on event type only, not payload/category — accept that `gated_retcons` gating `FLAG_RESOLVED` gates *all* flag resolutions for an agent at that autonomy level, not just contradiction ones. This is a known, accepted scope broadening, not a bug to fix in this plan.

---

### Task 1: `Flag` model, `FlagStatus`, projector table + projection, `list_flags`

**Files:**
- Modify: `novelizer/store/models.py:37-40` (delete `RetconStatus`), `novelizer/store/models.py:329-336` (delete `RetconRequest`, add `FlagStatus` + `Flag` in its place)
- Modify: `novelizer/canon/events.py:16-18` (replace `RETCON_REQUEST_*` constants with `FLAG_*`, keep old string values available as a separate `_LEGACY_RETCON_REQUEST_CREATED` etc. constants used only by the projector for aliasing)
- Modify: `novelizer/canon/projector.py:39-41` (add `flags` table alongside — not replacing — nothing else needs `retcon_requests` table once Task 8 drops it, but this task adds `flags` and starts aliasing into it)
- Modify: `novelizer/canon/projector.py:145-151` (`_reset_state_locked` table list: add `"flags"`)
- Modify: `novelizer/canon/projector.py:251-260` (add `FLAG_CREATED`/`FLAG_RESOLVED`/`FLAG_REJECTED` handling; alias legacy `retcon_request.*` event types into the same `flags` table with `category="contradiction"` injected)
- Modify: `novelizer/canon/read_store.py:5-10` (import `Flag` instead of `RetconRequest`), `novelizer/canon/read_store.py:72-79` (replace `list_retcon_requests` with `list_flags`)
- Test: `tests/canon/test_flags_projection.py` (new)

**Interfaces:**
- Produces: `Flag(BaseModel)` with fields `id: str`, `created_at: datetime`, `category: str`, `description: str`, `related_entry_ids: list[str]`, `proposed_resolution: str`, `status: FlagStatus = FlagStatus.open`, `filed_by: str = ""`, `resolved_by: Optional[str] = None`, `triage_passes: int = 0`. `FlagStatus(StrEnum)` with `open`, `resolved`, `rejected`, `stale`.
- Produces: `EventType.FLAG_CREATED = "flag.created"`, `EventType.FLAG_RESOLVED = "flag.resolved"`, `EventType.FLAG_REJECTED = "flag.rejected"`, plus internal legacy aliases `EventType.RETCON_REQUEST_CREATED = "retcon_request.created"` etc. **retained as string constants** (so old events in existing databases still match something) but no longer used by any commit call site after this plan completes.
- Produces: `ReadStore.list_flags(self, category: Optional[str] = None, status: Optional[str] = None) -> list[Flag]`.
- Consumes: nothing new — this task is additive/foundational.

- [ ] **Step 1: Write the failing test for the model and projection round-trip**

```python
# tests/canon/test_flags_projection.py
import os
import tempfile
import pytest
from novelizer.canon.event_store import EventStore
from novelizer.canon.projector import Projector
from novelizer.canon.read_store import ReadStore
from novelizer.canon.events import EventType
from novelizer.store.models import Flag, FlagStatus


@pytest.fixture
async def stack():
    fd, path = tempfile.mkstemp(suffix=".db"); os.close(fd)
    events = EventStore(path); await events.init()
    proj = Projector(events, path); await proj.init()
    read = ReadStore(path); await read.init()
    yield events, proj, read
    await read.close(); await proj.close(); await events.close(); os.unlink(path)


async def test_flag_created_projects_and_lists_by_category(stack):
    events, proj, read = stack
    await events.append(EventType.FLAG_CREATED, "f1",
                        Flag(id="f1", category="pacing", description="Act 2 sags",
                             related_entry_ids=[], proposed_resolution="", filed_by="structure_analyst"))
    await proj.catch_up()
    flags = await read.list_flags(category="pacing", status=FlagStatus.open)
    assert len(flags) == 1
    assert flags[0].id == "f1"
    assert flags[0].filed_by == "structure_analyst"
    assert await read.list_flags(category="thematic") == []


async def test_flag_resolved_updates_status(stack):
    events, proj, read = stack
    f = Flag(id="f1", category="worldbuilding", description="no map of the north",
              related_entry_ids=[], proposed_resolution="")
    await events.append(EventType.FLAG_CREATED, "f1", f)
    await proj.catch_up()
    resolved = f.model_copy(update={"status": FlagStatus.resolved, "resolved_by": "world_architect"})
    await events.append(EventType.FLAG_RESOLVED, "f1", resolved)
    await proj.catch_up()
    assert await read.list_flags(status=FlagStatus.open) == []
    got = await read.list_flags(status=FlagStatus.resolved)
    assert len(got) == 1 and got[0].resolved_by == "world_architect"


async def test_legacy_retcon_request_created_event_aliases_into_flags(stack):
    """A pre-migration event log still has retcon_request.created events with
    no `category` key in the payload. The projector must alias these into the
    flags table as category="contradiction" so old databases keep working."""
    from novelizer.canon.events import EventType as ET
    events, proj, read = stack
    legacy_payload = {
        "id": "r1", "created_at": "2026-01-01T00:00:00+00:00",
        "description": "two vs one sun", "conflicting_entry_ids": ["w1"],
        "proposed_resolution": "one sun", "status": "open", "resolved_by": None,
    }
    await events._append_raw(ET.RETCON_REQUEST_CREATED, "r1", legacy_payload)  # see Step 3 note
    await proj.catch_up()
    flags = await read.list_flags(category="contradiction", status="open")
    assert len(flags) == 1
    assert flags[0].id == "r1"
    assert flags[0].related_entry_ids == ["w1"]
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/canon/test_flags_projection.py -v`
Expected: FAIL — `ImportError: cannot import name 'Flag' from 'novelizer.store.models'` (and, once that's fixed, `AttributeError: 'ReadStore' object has no attribute 'list_flags'`, and `events._append_raw` won't exist yet either — see Step 3).

- [ ] **Step 3: Check whether `EventStore` has a raw-payload append helper; add one if not**

Read `novelizer/canon/event_store.py`'s `append` signature. If it only accepts a `BaseModel` payload (not a plain dict), add a small internal helper so the legacy-event test can construct an event whose payload has no `category` key (a real `Flag(...)` always would, since it's a required-with-default field). Add to `novelizer/canon/event_store.py`, next to `append`:

```python
    async def _append_raw(self, event_type: str, aggregate_id: str, payload: dict) -> None:
        """Test-only helper: append an event with a raw dict payload, bypassing
        BaseModel serialization. Used to simulate historical events shaped
        before a field existed (e.g. pre-Flag retcon_request.created events
        with no `category` key) without needing a matching Pydantic model."""
        import json
        from novelizer.run_context import current_run_id
        await self._append_json(event_type, aggregate_id, json.dumps(payload), run_id=current_run_id.get())
```

If `EventStore.append` internally already separates "serialize to JSON" from "write row" (check for a private method like `_append_json` or similar before adding a new one — reuse it rather than duplicating the write path). If no such split exists, factor `append` into `append` (keeps `BaseModel` signature, calls `model_dump(mode="json")`, then delegates to) + `_append_json(event_type, aggregate_id, json_str, run_id)` that does the actual row insert, then have `_append_raw` call `_append_json` too.

- [ ] **Step 4: Add `Flag`/`FlagStatus` to `store/models.py`**

Delete lines 37-40 (`RetconStatus`) and 329-336 (`RetconRequest`), replacing with:

```python
class FlagStatus(StrEnum):
    open = "open"
    resolved = "resolved"
    rejected = "rejected"
    stale = "stale"


class Flag(BaseModel):
    """A structured issue any agent can raise mid-work — a generalization of
    the old contradiction-only RetconRequest. `category` is free-form
    (e.g. "contradiction", "pacing", "thematic", "worldbuilding", "voice_drift")
    so agents aren't limited to a fixed taxonomy; the Triage agent routes by
    category via a small owner map, catch-alling anything unmapped.
    `triage_passes` counts unresolved catch-all Triage passes over an unowned
    flag; past a threshold it is marked `stale` rather than looping forever.
    """
    id: str = Field(default_factory=_uuid)
    created_at: datetime = Field(default_factory=_now)
    category: str
    description: str
    related_entry_ids: list[str] = Field(default_factory=list)
    proposed_resolution: str = ""
    status: FlagStatus = FlagStatus.open
    filed_by: str = ""
    resolved_by: Optional[str] = None
    triage_passes: int = 0
```

Keep this in the same position in the file (line ~329) so the diff stays local. Grep the file for any other `RetconStatus`/`RetconRequest` reference before moving on — there should be none left in this file.

- [ ] **Step 5: Update `events.py`**

Replace lines 16-18:

```python
    RETCON_REQUEST_CREATED = "retcon_request.created"  # legacy alias only, see projector.py
    RETCON_REQUEST_RESOLVED = "retcon_request.resolved"  # legacy alias only, see projector.py
    RETCON_REQUEST_REJECTED = "retcon_request.rejected"  # legacy alias only, see projector.py
    FLAG_CREATED = "flag.created"
    FLAG_RESOLVED = "flag.resolved"
    FLAG_REJECTED = "flag.rejected"
```

- [ ] **Step 6: Update the projector**

In `novelizer/canon/projector.py`, add to the `_CREATE` DDL block (near line 41, after the `retcon_requests` table which stays for now — Task 8 drops it):

```sql
CREATE TABLE IF NOT EXISTS flags (
    id TEXT PRIMARY KEY, data TEXT NOT NULL, status TEXT NOT NULL, category TEXT NOT NULL
);
```

Add `"flags"` to the `_reset_state_locked` table tuple (line ~147).

Replace the `RETCON_REQUEST_*` branch (lines 251-260) with:

```python
        elif t == EventType.FLAG_CREATED:
            await self._conn.execute(
                "INSERT OR REPLACE INTO flags (id, data, status, category) VALUES (?,?,?,?)",
                (p["id"], data, p.get("status", "open"), p.get("category", "")),
            )
        elif t == EventType.FLAG_RESOLVED or t == EventType.FLAG_REJECTED:
            await self._conn.execute(
                "INSERT OR REPLACE INTO flags (id, data, status, category) VALUES (?,?,?,?)",
                (p["id"], data,
                 p.get("status", "resolved" if t == EventType.FLAG_RESOLVED else "rejected"),
                 p.get("category", "")),
            )
        elif t in (EventType.RETCON_REQUEST_CREATED, EventType.RETCON_REQUEST_RESOLVED,
                   EventType.RETCON_REQUEST_REJECTED):
            # Legacy alias: pre-Flag databases only ever emitted these three
            # event types for contradictions. Project them into the same
            # `flags` table as category="contradiction" so old event logs
            # keep working without any code path emitting these anymore.
            legacy_status = p.get("status")
            if legacy_status is None:
                legacy_status = {
                    EventType.RETCON_REQUEST_CREATED: "open",
                    EventType.RETCON_REQUEST_RESOLVED: "resolved",
                    EventType.RETCON_REQUEST_REJECTED: "rejected",
                }[t]
            aliased = dict(p)
            aliased["category"] = "contradiction"
            aliased.setdefault("related_entry_ids", aliased.pop("conflicting_entry_ids", []))
            aliased.setdefault("filed_by", "")
            aliased.setdefault("triage_passes", 0)
            await self._conn.execute(
                "INSERT OR REPLACE INTO flags (id, data, status, category) VALUES (?,?,?,?)",
                (aliased["id"], json.dumps(aliased), legacy_status, "contradiction"),
            )
```

This must come immediately after the existing `elif t == EventType.PROPOSAL_CREATED:` predecessor block position (where the old retcon block was) — keep it in the same `if/elif` chain, don't create a second chain.

- [ ] **Step 7: Update `read_store.py`**

Replace the import (line ~6) to include `Flag` instead of `RetconRequest`, and replace `list_retcon_requests` (lines 72-79) with:

```python
    async def list_flags(self, category: Optional[str] = None, status: Optional[str] = None) -> list[Flag]:
        clauses, params = [], []
        if category:
            clauses.append("category=?")
            params.append(category)
        if status:
            clauses.append("status=?")
            params.append(status)
        where = f" WHERE {' AND '.join(clauses)}" if clauses else ""
        cur = await self._conn.execute(f"SELECT data FROM flags{where} ORDER BY rowid", params)
        return [Flag.model_validate_json(r[0]) for r in await cur.fetchall()]
```

- [ ] **Step 8: Run test to verify it passes**

Run: `pytest tests/canon/test_flags_projection.py -v`
Expected: PASS (all three tests).

- [ ] **Step 9: Commit**

```bash
git add novelizer/store/models.py novelizer/canon/events.py novelizer/canon/projector.py \
        novelizer/canon/read_store.py novelizer/canon/event_store.py tests/canon/test_flags_projection.py
git commit -m "feat: add generic Flag model, projection, and list_flags"
```

---

### Task 2: Add `FlagDraft` schema; migrate Retconner to `Flag(category="contradiction")`

**Files:**
- Modify: `novelizer/agents/schemas.py:250-253` (replace `RetconDraft` with `FlagDraft`)
- Modify: `novelizer/agents/retconner.py` (imports, `readiness`, `poll`, `_decline`, `commit`, `_run`)
- Modify: `tests/agents/test_retconner.py`, `tests/agents/test_retconner_resolution.py`

**Interfaces:**
- Consumes: `Flag`, `FlagStatus` from Task 1.
- Produces: `FlagDraft(BaseModel)` with `category: str`, `description: str`, `related_entry_ids: list[str] = []`, `proposed_resolution: str = ""` — every later filing-agent task depends on this exact shape.

- [ ] **Step 1: Write the failing test**

Edit `tests/agents/test_retconner.py`: replace every `RetconRequest`/`RetconStatus` import and construction with `Flag`/`FlagStatus`, `category="contradiction"`, and every `EventType.RETCON_REQUEST_CREATED` with `EventType.FLAG_CREATED`, every `read.list_retcon_requests(...)` with `read.list_flags(category="contradiction", ...)`. Concretely, replace the whole file's imports and each test body's setup lines. Example of the first test after edit:

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
from novelizer.store.models import WorldEntry, Flag, FlagStatus, Domain


class FakeRunner:
    def __init__(self, out):
        self._out = out
        self.calls = []

    async def ainvoke(self, inputs):
        self.calls.append(inputs)
        return {"structured_response": self._out}


class ScriptedRunner:
    def __init__(self, script):
        self._script = list(script)
        self.calls = []

    async def ainvoke(self, inputs):
        self.calls.append(inputs)
        step = self._script.pop(0)
        if isinstance(step, Exception):
            raise step
        return {"structured_response": step}


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
    await events.append(EventType.FLAG_CREATED, "r1",
                        Flag(id="r1", category="contradiction", description="two vs one",
                             related_entry_ids=["w1"], proposed_resolution="one sun"))
    await proj.catch_up()
    out = RetconAmendments(amended_entries=[WorldEntryDraft(title="Suns", body="One sun.", supersedes_id="w1")])
    agent = Retconner(FakeRunner(out), read, committer)
    await agent.run_once()
    await proj.catch_up()
    active_entries = await read.list_world_entries()
    assert "w1" not in {e.id for e in active_entries}
    matching = [e for e in active_entries if e.body == "One sun."]
    assert len(matching) == 1
    assert matching[0].supersedes_id == "w1"
    assert await read.list_flags(category="contradiction", status=FlagStatus.open) == []
    assert len(await read.list_flags(category="contradiction", status=FlagStatus.resolved)) == 1
```

Apply the equivalent mechanical substitution (`RetconRequest`→`Flag` with `category="contradiction"` added, `RetconStatus.X`→`FlagStatus.X`, `EventType.RETCON_REQUEST_CREATED`→`EventType.FLAG_CREATED`, `read.list_retcon_requests(status=X)`→`read.list_flags(category="contradiction", status=X)`) to every remaining test function in the file: `test_run_once_survives_llm_inventing_a_domain`, `test_failing_head_request_does_not_block_the_queue`, `test_none_output_defers_head_request`, `test_deferral_resets_once_every_open_request_has_failed`, `test_noop_when_no_open_retcons`, `test_work_prompt_includes_personality_when_set`, `test_commit_emits_remark_when_feed_note_present`. The three `build_retconner_runner` tests at the bottom (`test_build_retconner_runner_without_backend_stays_constructible`, `..._with_backend_uses_retrieval_note_base`, `..._with_backend_bounds_recursion`) are unaffected and need no changes.

Also update `tests/agents/test_retconner_resolution.py` with the same substitutions (read it first to find its exact `RetconRequest`/`RetconStatus` usages, applying the identical rename pattern).

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/agents/test_retconner.py tests/agents/test_retconner_resolution.py -v`
Expected: FAIL — `ImportError: cannot import name 'RetconDraft'` is gone already from Task 1's model changes; failures now should be `Flag() missing 1 required positional argument: 'category'` is not applicable (category is required, tests already pass it) — actual expected failure is `retconner.py` still importing/using `RetconRequest`/`RetconStatus`/`RETCON_REQUEST_CREATED`, which no longer exist post-Task-1, so this currently fails with `ImportError` in `retconner.py` itself, surfacing as a collection error for the test file.

- [ ] **Step 3: Replace `RetconDraft` with `FlagDraft` in schemas.py**

```python
class FlagDraft(BaseModel):
    category: str
    description: str
    related_entry_ids: list[str] = Field(default_factory=list)
    proposed_resolution: str = ""
```

Delete `RetconDraft` (old lines 250-253) entirely. This is used by `RetconAmendments`? No — `RetconAmendments` (schemas.py line 297+) doesn't use `RetconDraft`; only `KeeperOutput.retcon_requests` and `ContinuityOutput.retcon_requests` reference it (Tasks 3-4 handle those). Leave `RetconAmendments` untouched in this task — it doesn't name `RetconRequest`/`RetconDraft` anywhere in its own fields.

- [ ] **Step 4: Migrate `retconner.py`**

Change the import (line 9):

```python
from novelizer.store.models import WorldEntry, FlagStatus
```

`readiness` (line 72-74):

```python
    async def readiness(self) -> float:
        open_flags = len(await self._read.list_flags(category="contradiction", status=FlagStatus.open))
        return min(1.0, open_flags / 3)
```

`poll` (lines 76-85):

```python
    async def poll(self) -> dict:
        open_reqs = await self._read.list_flags(category="contradiction", status=FlagStatus.open)
        self._deferred &= {r.id for r in open_reqs}
        candidates = [r for r in open_reqs if r.id not in self._deferred]
        if not candidates and open_reqs:
            self._deferred.clear()
            candidates = open_reqs
        return {"target": candidates[0] if candidates else None, "world": await self._read.list_world_entries()}
```

`_decline` (lines 98-107):

```python
    async def _decline(self, req, resolution: str, reason: str) -> None:
        logger.info("retconner: declining request %s (%s): %s", req.id, resolution, reason)
        rejected = req.model_copy(update={
            "status": FlagStatus.rejected,
            "resolved_by": self.name,
            "proposed_resolution": f"[{resolution}] {reason}" if reason else f"[{resolution}]",
        })
        await self._committer.commit(self.name, EventType.FLAG_REJECTED, req.id, rejected)
```

`commit` (lines 109-122): only the last two lines change, from `RetconStatus.resolved`/`RETCON_REQUEST_RESOLVED` to:

```python
        resolved = req.model_copy(update={"status": FlagStatus.resolved, "resolved_by": self.name})
        await self._committer.commit(self.name, EventType.FLAG_RESOLVED, req.id, resolved)
```

No other lines in `retconner.py` reference the old names (confirmed via the earlier grep sweep — `retconner.py:9,73,77,103,120` were the only hits, all covered above).

- [ ] **Step 5: Run test to verify it passes**

Run: `pytest tests/agents/test_retconner.py tests/agents/test_retconner_resolution.py -v`
Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add novelizer/agents/schemas.py novelizer/agents/retconner.py \
        tests/agents/test_retconner.py tests/agents/test_retconner_resolution.py
git commit -m "feat: migrate Retconner to generic Flag(category=contradiction)"
```

---

### Task 3: Migrate Continuity Checker to file Flags

**Files:**
- Modify: `novelizer/agents/continuity_checker.py` (import, `readiness`, `poll`, `_file_mined_retcon`, `commit`'s main-pass filing block, the deterministic leak/paradox escalation blocks)
- Modify: `novelizer/agents/schemas.py` (`ContinuityOutput.retcon_requests` → `flags`)
- Modify: `novelizer/brain/context.py:81-92` (`open_retcons_note` type hint + param name)
- Test: `tests/agents/test_continuity_checker.py`, `tests/agents/test_continuity_uptake.py`

**Interfaces:**
- Consumes: `Flag`, `FlagStatus`, `FlagDraft` from Tasks 1-2.
- Produces: nothing new consumed by later tasks (Continuity Checker is a leaf filer), but confirms the filing pattern Task 4-5 repeat.

- [ ] **Step 1: Write the failing test**

Open `tests/agents/test_continuity_checker.py` and `tests/agents/test_continuity_uptake.py`. Apply the same mechanical rename as Task 2's Step 1 to every test: `RetconRequest`→`Flag` (adding `category="contradiction"` to every construction), `RetconStatus`→`FlagStatus`, `EventType.RETCON_REQUEST_CREATED`→`EventType.FLAG_CREATED`, `read.list_retcon_requests(...)`→`read.list_flags(category="contradiction", ...)`, and any assertion checking `out.retcon_requests` on a `ContinuityOutput` becomes `out.flags` with each item now needing `category="contradiction"` set (e.g. `ContinuityOutput(retcon_requests=[RetconDraft(description=..., conflicting_entry_ids=...)])` becomes `ContinuityOutput(flags=[FlagDraft(category="contradiction", description=..., related_entry_ids=...)])`). Read both files in full first since I don't have their exact current bodies verbatim — apply the rename identically to every test function present, following the exact substitution table above.

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/agents/test_continuity_checker.py tests/agents/test_continuity_uptake.py -v`
Expected: FAIL — collection error, `continuity_checker.py` still imports `RetconRequest`/`RetconStatus` which no longer exist.

- [ ] **Step 3: Update `ContinuityOutput` in schemas.py**

```python
class ContinuityOutput(BaseModel):
    flags: list[FlagDraft] = Field(default_factory=list)
    feed_note: str = ""
    no_action: bool = False
```

- [ ] **Step 4: Migrate `continuity_checker.py`**

Change import (line 17): `from novelizer.store.models import Flag, FlagStatus`.

`readiness` (line 130): `open_retcons = len(await self._read.list_flags(category="contradiction", status=FlagStatus.open))`.

`poll` (line 151): `"open_retcons": await self._read.list_flags(category="contradiction", status=FlagStatus.open),`.

`_file_mined_retcon` (lines 394-412) — rename to `_file_mined_flag` and update its body:

```python
    async def _file_mined_flag(
        self, detail: str, conflicting_entry_ids: list[str], seen_descriptions: set[str],
    ) -> None:
        """File a mined-fact escalation flag, deduped by description against
        `seen_descriptions` — the open queue plus everything filed this cycle.
        The live miner emitted the same fact twice in one output and both were
        filed; a crash before the chapter.mined stamp re-files on the next pass.
        """
        description = f"{MINED_SOURCE_TAG} {detail}"
        if description in seen_descriptions:
            logger.info("%s: skipped duplicate mined flag %r", self.name, description)
            return
        seen_descriptions.add(description)
        flag = Flag(
            category="contradiction",
            filed_by=self.name,
            description=description,
            related_entry_ids=conflicting_entry_ids,
            proposed_resolution="Review the mined fact and add a covering event, or dismiss if not applicable.",
        )
        await self._committer.commit(self.name, EventType.FLAG_CREATED, flag.id, flag)
```

Update the three call sites at (old) lines 286, 316, 328 from `self._file_mined_retcon(...)` to `self._file_mined_flag(...)` (same arguments, no signature change).

`commit`'s main-pass filing block (lines 417-429):

```python
        open_reqs = await self._read.list_flags(category="contradiction", status=FlagStatus.open)
        seen_descriptions = {r.description for r in open_reqs}
        deterministic_filed = 0

        if out is not None and not out.no_action:
            for r in out.flags:
                if r.description in seen_descriptions:
                    continue
                seen_descriptions.add(r.description)
                flag = Flag(category=r.category, filed_by=self.name, description=r.description,
                            related_entry_ids=r.related_entry_ids, proposed_resolution=r.proposed_resolution)
                await self._committer.commit(self.name, EventType.FLAG_CREATED, flag.id, flag)
            await self._remark(out.feed_note)
```

Deterministic leak/paradox escalation (lines 431-455): read this block in full and apply the identical `RetconRequest(...)` → `Flag(category="contradiction", filed_by=self.name, ...)` / `EventType.RETCON_REQUEST_CREATED` → `EventType.FLAG_CREATED` substitution — same shape as every other call site in this file, no structural change beyond the rename.

- [ ] **Step 5: Update `open_retcons_note` in `brain/context.py`**

```python
def open_retcons_note(requests: list[Flag]) -> str:
    """Build the checker-facing prompt block listing flags already sitting
    open in the queue, so an LLM pass that re-reviews the same material every
    cycle doesn't re-report a known issue under fresh wording (exact-
    description dedup at commit time can't catch a reworded repeat). Empty
    string when the queue is empty, so prompts stay byte-identical whenever
    there is nothing to say.
    """
    if not requests:
        return ""
    lines = "\n".join(f"- {r.description}" for r in requests[:20])
    return f"\n\nRetcon requests already filed (do not re-report these):\n{lines}"
```

Update the import at the top of `brain/context.py:15` from `RetconRequest` to `Flag`. Function body/prompt text unchanged (the wording "Retcon requests" stays — it's still true, contradictions are just one Flag category, and rewording the prompt text is out of scope for this plan).

- [ ] **Step 6: Run test to verify it passes**

Run: `pytest tests/agents/test_continuity_checker.py tests/agents/test_continuity_uptake.py -v`
Expected: PASS.

- [ ] **Step 7: Commit**

```bash
git add novelizer/agents/continuity_checker.py novelizer/agents/schemas.py novelizer/brain/context.py \
        tests/agents/test_continuity_checker.py tests/agents/test_continuity_uptake.py
git commit -m "feat: migrate Continuity Checker to generic Flag filing"
```

---

### Task 4: Migrate Character Keeper to file Flags

**Files:**
- Modify: `novelizer/agents/character_keeper.py` (import, `_fingerprint`, `poll`, `work`'s prompt-building, `commit`'s filing block)
- Modify: `novelizer/agents/schemas.py` (`KeeperOutput.retcon_requests` → `flags`)
- Test: `tests/agents/test_character_keeper.py`, `tests/agents/test_character_keeper_pull.py`, `tests/agents/test_character_keeper_uptake.py`, `tests/agents/test_character_keeper_property.py`

**Interfaces:**
- Consumes: `Flag`, `FlagStatus`, `FlagDraft` from Tasks 1-2.
- Produces: nothing new for later tasks.

- [ ] **Step 1: Write the failing test**

Read all four `test_character_keeper*.py` files in full. Apply the identical substitution table from Task 2/3 to every test: `RetconRequest`→`Flag` (`category="contradiction"` added), `RetconStatus`→`FlagStatus`, `EventType.RETCON_REQUEST_CREATED`→`EventType.FLAG_CREATED`, `read.list_retcon_requests(...)`→`read.list_flags(category="contradiction", ...)`, `KeeperOutput(retcon_requests=[RetconDraft(...)])`→`KeeperOutput(flags=[FlagDraft(category="contradiction", ...)])`.

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/agents/test_character_keeper.py tests/agents/test_character_keeper_pull.py tests/agents/test_character_keeper_uptake.py tests/agents/test_character_keeper_property.py -v`
Expected: FAIL — collection error, `character_keeper.py` imports the deleted names.

- [ ] **Step 3: Update `KeeperOutput` in schemas.py**

```python
class KeeperOutput(BaseModel):
    new_characters: list[NewCharacter] = Field(default_factory=list)
    updated_characters: list[CharacterUpdate] = Field(default_factory=list)
    flags: list[FlagDraft] = Field(default_factory=list)
    knowledge_intents: list[KnowledgeIntent] = Field(default_factory=list)
    arc_intents: list[ArcIntent] = Field(default_factory=list)
    feed_note: str = ""
    no_action: bool = False
```

- [ ] **Step 4: Migrate `character_keeper.py`**

Change import (line 12): `from novelizer.store.models import Character, Flag, FlagStatus`.

`_fingerprint` (~line 111-115): `open_retcons = await self._read.list_flags(category="contradiction", status=FlagStatus.open)` (variable name `open_retcons` can stay — it's a local, not part of any interface).

`poll` (~line 124): `"open_retcons": await self._read.list_flags(category="contradiction", status=FlagStatus.open),`.

`commit`'s filing block (~lines 229-241):

```python
        if out.flags:
            open_reqs = await self._read.list_flags(category="contradiction", status=FlagStatus.open)
            seen_descriptions = {r.description for r in open_reqs}
            for r in out.flags:
                if r.description in seen_descriptions:
                    continue
                seen_descriptions.add(r.description)
                flag = Flag(category=r.category, filed_by=self.name, description=r.description,
                            related_entry_ids=r.related_entry_ids, proposed_resolution=r.proposed_resolution)
                await self._committer.commit(self.name, EventType.FLAG_CREATED, flag.id, flag)
```

- [ ] **Step 5: Run test to verify it passes**

Run: `pytest tests/agents/test_character_keeper.py tests/agents/test_character_keeper_pull.py tests/agents/test_character_keeper_uptake.py tests/agents/test_character_keeper_property.py -v`
Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add novelizer/agents/character_keeper.py novelizer/agents/schemas.py \
        tests/agents/test_character_keeper.py tests/agents/test_character_keeper_pull.py \
        tests/agents/test_character_keeper_uptake.py tests/agents/test_character_keeper_property.py
git commit -m "feat: migrate Character Keeper to generic Flag filing"
```

---

### Task 5: Migrate Editor's voice-drift escalation to `Flag(category="voice_drift")`

**Files:**
- Modify: `novelizer/agents/editor.py` (import, `VOICE_SOURCE_TAG` removal, `poll`, the drift-filed prompt block, the voice_drift_flags commit block)
- Modify: `novelizer/agents/schemas.py` (`EditorVerdict.voice_drift_flags` field removed, replaced by folding into a shared `flags` field via `VoiceDriftFlag` staying as the model-facing structured type but translated to `FlagDraft` at commit time — keep `VoiceDriftFlag` as-is since its shape, `character_id`/`line`/`trait_violated`/`note`, is genuinely more structured than a bare description and the LLM benefits from that structure; only the *commit path* changes to produce `Flag` instead of `RetconRequest`)
- Test: find and update editor's voice-drift test file (search `tests/agents/test_editor*.py` for `voice_drift` — read it in full before editing)

**Interfaces:**
- Consumes: `Flag`, `FlagStatus` from Task 1.
- Produces: nothing new for later tasks.

- [ ] **Step 1: Write the failing test**

Find the editor test file(s) covering `voice_drift_flags` (glob `tests/agents/test_editor*.py`), read in full. Apply substitution: any assertion on `read.list_retcon_requests(...)` becomes `read.list_flags(category="voice_drift", ...)` (note the category — voice drift is its own category now, not lumped under "contradiction"), `RetconRequest`/`RetconStatus` imports become `Flag`/`FlagStatus`, and any `description.startswith(VOICE_SOURCE_TAG)` assertion is replaced with a direct `category == "voice_drift"` check since the tag-based dedup trick is being removed.

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/agents/test_editor.py -v` (adjust to whatever the actual discovered filename is)
Expected: FAIL — collection error from `editor.py` still importing deleted names.

- [ ] **Step 3: Migrate `editor.py`**

Change import (line 15): `from novelizer.store.models import DirectorSignal, SignalKind, EditorialStatus, Flag, FlagStatus`.

Delete `VOICE_SOURCE_TAG = "[source: voice_drift]"` (line 89) entirely — no longer needed once category replaces tag-prefixing.

`poll` (line 144): `"open_retcons": await self._read.list_flags(category="contradiction", status=FlagStatus.open),` — **note**: this feeds the checker-facing prompt note (`open_retcons_note`) which is specifically about contradictions being re-reported, so it correctly stays scoped to `category="contradiction"` even after this migration (voice-drift flags aren't contradictions and don't need the same "don't re-report" prompt treatment via this particular note — that's a separate concern from where they're filed).

The drift-filed prompt block (lines 196-204) — replace the `VOICE_SOURCE_TAG` string-matching with a direct category query:

```python
        drift_filed_flags = await self._read.list_flags(category="voice_drift", status=FlagStatus.open)
        drift = ""
        if drift_filed_flags:
            listing = "\n".join(f"- {d.description}" for d in drift_filed_flags[:20])
            drift = "\n\nVoice-drift flags already filed (do not re-flag these lines):\n" + listing
```

The voice_drift_flags commit block (lines 247-265):

```python
        if verdict.voice_drift_flags:
            open_flags = await self._read.list_flags(category="voice_drift", status=FlagStatus.open)
            open_descriptions = [r.description for r in open_flags]
            filed_keys: set[str] = set()
            for vflag in verdict.voice_drift_flags:
                key = f"violated by {vflag.character_id}: \"{vflag.line}\""
                if key in filed_keys or any(key in d for d in open_descriptions):
                    continue
                filed_keys.add(key)
                description = (
                    f"{vflag.trait_violated} {key}"
                    + (f" — {vflag.note}" if vflag.note else "")
                )
                flag = Flag(category="voice_drift", filed_by=self.name, description=description,
                            related_entry_ids=[vflag.character_id], proposed_resolution="")
                await self._committer.commit(self.name, EventType.FLAG_CREATED, flag.id, flag)
```

`EditorVerdict.voice_drift_flags` field itself (schemas.py line 287) is unchanged in shape/name — it's the Editor's *own* structured-output field for the LLM to fill in (a list of `VoiceDriftFlag`), separate from the generic filing wire format; only the commit-time translation into `Flag` changed. Update `VoiceDriftFlag`'s docstring (schemas.py lines 266-271) to drop the now-inaccurate `VOICE_SOURCE_TAG` reference:

```python
class VoiceDriftFlag(BaseModel):
    """One agent-declared instance of a character's prose voice violating its
    voice card, from Editor structured output. Committed at commit time as a
    Flag(category="voice_drift"), never a direct canon mutation.
    """

    character_id: str
    line: str
    trait_violated: str
    note: str = ""
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/agents/test_editor.py -v` (or the actual discovered filename)
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add novelizer/agents/editor.py novelizer/agents/schemas.py tests/agents/test_editor*.py
git commit -m "feat: migrate Editor voice-drift escalation to Flag(category=voice_drift)"
```

---

### Task 6: Migrate the shared theme-duplicate escalation in `intents.py`

**Files:**
- Modify: `novelizer/agents/intents.py` (import at line 22, escalation block at lines 210-220)
- Test: find the test covering `suggest_near_duplicate_theme` escalation (search `tests/` for `THEME_SIMILARITY_SOURCE_TAG` or `duplicate theme`, read in full)

**Interfaces:**
- Consumes: `Flag`, `FlagStatus` from Task 1.
- Produces: nothing new for later tasks.

- [ ] **Step 1: Write the failing test**

Locate and read the existing test for this escalation path in full. Apply substitution: `read_store.list_retcon_requests(status=RetconStatus.open)` → `read_store.list_flags(category="thematic", status=FlagStatus.open)`, `RetconRequest(...)` → `Flag(category="thematic", filed_by=agent_name, ...)`, `EventType.RETCON_REQUEST_CREATED` → `EventType.FLAG_CREATED`.

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/agents/test_intents.py -v` (adjust to the actual discovered filename/path)
Expected: FAIL — collection error, `intents.py` imports deleted names.

- [ ] **Step 3: Migrate `intents.py`**

Change import (line 22): `from novelizer.store.models import Flag, FlagStatus, ChapterBriefRecord`.

Replace lines 210-220:

```python
                    open_reqs = await read_store.list_flags(category="thematic", status=FlagStatus.open)
                    seen_descriptions = {r.description for r in open_reqs}
                    if description not in seen_descriptions:
                        flag = Flag(
                            category="thematic",
                            filed_by=agent_name,
                            description=description,
                            related_entry_ids=[theme_id, duplicate_id],
                            proposed_resolution="",
                        )
                        await committer.commit(
                            agent_name, EventType.FLAG_CREATED, flag.id, flag
                        )
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/agents/test_intents.py -v` (adjust to actual filename)
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add novelizer/agents/intents.py tests/agents/test_intents*.py
git commit -m "feat: migrate theme-duplicate escalation to Flag(category=thematic)"
```

---

### Task 7: Give Structure Analyst, Plotter, World Architect a generic `flags` filing field

**Files:**
- Modify: `novelizer/agents/schemas.py` (`StructureAnalystOutput`, `PlotterOutput`, and World Architect's draft output schema — find its exact class name via `grep -n "class.*Draft\|class.*Output" novelizer/agents/schemas.py` scoped to world_architect's usage before editing; likely `WorldEntriesDraft` per the earlier grep hit at schemas.py lines 25-28)
- Modify: `novelizer/agents/structure_analyst.py` (`commit`)
- Modify: `novelizer/agents/plotter.py` (`commit`)
- Modify: `novelizer/agents/world_architect.py` (`commit`)
- Test: `tests/agents/test_structure_analyst.py`, `tests/agents/test_plotter*.py`, `tests/agents/test_world_architect*.py` (glob and read each in full before editing)

**Interfaces:**
- Consumes: `Flag`, `FlagStatus`, `FlagDraft` from Tasks 1-2.
- Produces: nothing new for later tasks (these three become filers, like Tasks 3-5).

- [ ] **Step 1: Write the failing test for Structure Analyst**

Add to `tests/agents/test_structure_analyst.py` (after reading the existing file to match its exact `stack` fixture and `FakeRunner` pattern — it should be identical to `test_retconner.py`'s):

```python
async def test_flags_from_structured_output_are_filed(stack):
    events, proj, read, committer = stack
    from novelizer.agents.schemas import StructureAnalystOutput
    from novelizer.agents.schemas import FlagDraft
    out = StructureAnalystOutput(flags=[
        FlagDraft(category="pacing", description="Act 2 sags for six chapters",
                  related_entry_ids=[], proposed_resolution="cut or merge two middle chapters"),
    ])
    agent = StructureAnalyst(FakeRunner(out), read, committer)
    await agent.run_once()
    await proj.catch_up()
    flags = await read.list_flags(category="pacing", status="open")
    assert len(flags) == 1
    assert flags[0].description == "Act 2 sags for six chapters"
    assert flags[0].filed_by == "structure_analyst"
```

(Adjust the exact agent-construction call to whatever `StructureAnalyst`'s real constructor signature is, matching the file's existing tests — same positional args as every other agent's tests, `(runner, read, committer)` per the established pattern.)

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/agents/test_structure_analyst.py -v`
Expected: FAIL — `TypeError: StructureAnalystOutput() got an unexpected keyword argument 'flags'`.

- [ ] **Step 3: Add `flags` field and commit logic for Structure Analyst**

`schemas.py` (line ~323-325):

```python
class StructureAnalystOutput(BaseModel):
    scores: list[ChapterScore] = Field(default_factory=list)
    flags: list[FlagDraft] = Field(default_factory=list)
    feed_note: str = ""
```

`structure_analyst.py`'s `commit` (currently ~lines 75-89) — after the existing scores-commit loop, before `await self._remark(out.feed_note)`, insert:

```python
        if out.flags:
            open_flags = await self._read.list_flags(status="open")
            seen_descriptions = {f.description for f in open_flags}
            for r in out.flags:
                if r.description in seen_descriptions:
                    continue
                seen_descriptions.add(r.description)
                flag = Flag(category=r.category, filed_by=self.name, description=r.description,
                            related_entry_ids=r.related_entry_ids, proposed_resolution=r.proposed_resolution)
                await self._committer.commit(self.name, EventType.FLAG_CREATED, flag.id, flag)
```

Add the import at the top of `structure_analyst.py`: `from novelizer.store.models import Flag` (add `EventType` import too if not already present — check the existing import block first, it almost certainly already imports `EventType` since it commits `ANNOTATION_STRUCTURE_SCORED`).

Note the dedup here checks `status="open"` across *all* categories, not scoped to one category, since Structure Analyst might file more than just "pacing" in the future and dedup-by-description is a global sanity check, not a category-scoped one — same reasoning as Continuity Checker/Character Keeper's existing dedup, generalized.

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/agents/test_structure_analyst.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add novelizer/agents/schemas.py novelizer/agents/structure_analyst.py tests/agents/test_structure_analyst.py
git commit -m "feat: give Structure Analyst a generic flags filing capability"
```

- [ ] **Step 6: Write the failing test for Plotter**

Read `tests/agents/test_plotter*.py` in full first to match its exact harness. Add an analogous test to the one in Step 1, using `PlotterOutput(flags=[FlagDraft(category="thematic", ...)])` and asserting via `read.list_flags(category="thematic", status="open")`.

- [ ] **Step 7: Run test to verify it fails**

Run: `pytest tests/agents/test_plotter*.py -v`
Expected: FAIL — same `unexpected keyword argument 'flags'` shape as Step 2.

- [ ] **Step 8: Add `flags` field and commit logic for Plotter**

`schemas.py` (`PlotterOutput`, line ~328-339):

```python
class PlotterOutput(BaseModel):
    """The Plotter's structured response: at most one blueprint proposal
    (only meaningful while no blueprint is active) plus any number of
    brief/beat/resolution/promise intents for the current pass."""

    blueprint_plan: BlueprintPlan | None = None
    retarget_intent: RetargetIntent | None = None
    brief_intents: list[BriefIntent] = Field(default_factory=list)
    beat_intents: list[BeatIntent] = Field(default_factory=list)
    resolution_plan_intents: list[ResolutionPlanIntent] = Field(default_factory=list)
    promise_intents: list[PromiseIntent] = Field(default_factory=list)
    flags: list[FlagDraft] = Field(default_factory=list)
    feed_note: str = ""
```

`plotter.py`'s `commit` — insert the identical filing block (same shape as Step 3's) right before the existing `await self._remark(out.feed_note); await self._consume_signals(ctx["signals"])` tail. Add `Flag` to the import block at the top of `plotter.py`.

- [ ] **Step 9: Run test to verify it passes**

Run: `pytest tests/agents/test_plotter*.py -v`
Expected: PASS.

- [ ] **Step 10: Commit**

```bash
git add novelizer/agents/schemas.py novelizer/agents/plotter.py tests/agents/test_plotter*.py
git commit -m "feat: give Plotter a generic flags filing capability"
```

- [ ] **Step 11: Write the failing test for World Architect**

Read `tests/agents/test_world_architect*.py` in full first. Also read `novelizer/agents/world_architect.py`'s output-schema import to get the exact draft class name (grep confirmed `commit` reads `draft.entries` and `draft.no_action` — find the class this `draft` parameter is typed as; likely `WorldEntriesDraft` in `schemas.py`, confirm before editing). Add an analogous test using that class's constructor plus a new `flags=[FlagDraft(category="worldbuilding", ...)]`.

- [ ] **Step 12: Run test to verify it fails**

Run: `pytest tests/agents/test_world_architect*.py -v`
Expected: FAIL — same `unexpected keyword argument 'flags'` shape.

- [ ] **Step 13: Add `flags` field and commit logic for World Architect**

Add `flags: list[FlagDraft] = Field(default_factory=list)` to World Architect's draft output schema (whatever class was confirmed in Step 11), preserving every existing field. In `world_architect.py`'s `commit` (lines ~109-121), after the existing `for e in draft.entries: ...` loop and before the remark/signal-consume tail, insert the identical filing block pattern from Step 3/Step 8. Add `Flag` to the import block.

- [ ] **Step 14: Run test to verify it passes**

Run: `pytest tests/agents/test_world_architect*.py -v`
Expected: PASS.

- [ ] **Step 15: Commit**

```bash
git add novelizer/agents/schemas.py novelizer/agents/world_architect.py tests/agents/test_world_architect*.py
git commit -m "feat: give World Architect a generic flags filing capability"
```

---

### Task 8: Delete `retcon_requests` table/back-compat surface, migrate CLI

**Files:**
- Modify: `novelizer/canon/projector.py` (drop `retcon_requests` from `_CREATE` DDL and `_reset_state_locked`, since Task 1 already aliases legacy events into `flags` and no code commits to the old table anymore)
- Modify: `novelizer/director/cli.py:225-235` (the `retcons` CLI command)
- Test: `tests/canon/test_flags_projection.py` (extend), any CLI test covering the `retcons` command (search `tests/` for it)

**Interfaces:**
- Consumes: `list_flags` from Task 1.
- Produces: nothing new.

- [ ] **Step 1: Write the failing test**

Add to `tests/canon/test_flags_projection.py`:

```python
async def test_retcon_requests_table_no_longer_created(stack):
    events, proj, read = stack
    cur = await read._conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name='retcon_requests'"
    )
    assert await cur.fetchone() is None
```

Read the CLI test file covering `retcons` (search `tests/` for `def test.*retcons` or `cli.*retcons`) in full, and update its assertions from `list_retcon_requests`-shaped expectations to whatever `retcons` now does per Step 3 below.

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/canon/test_flags_projection.py -v`
Expected: FAIL — the `retcon_requests` table still exists (it's still in `_CREATE`).

- [ ] **Step 3: Drop the legacy table and update the CLI**

Remove the `CREATE TABLE IF NOT EXISTS retcon_requests (...)` block from `projector.py`'s `_CREATE` (the alias branch in Task 1's Step 6 only ever writes to `flags`, never to `retcon_requests`, so nothing depends on this table existing anymore — confirm via `grep -rn "retcon_requests" novelizer/` finding only the `_reset_state_locked` tuple entry and the DDL, both removed here). Remove `"retcon_requests"` from the `_reset_state_locked` tuple.

Update `director/cli.py`'s `retcons` command:

```python
@cli.command()
@click.pass_context
def retcons(ctx):
    """List open contradiction flags."""
    async def _run(rt: Runtime):
        reqs = await rt.read.list_flags(category="contradiction", status="open")
        if not reqs:
            console.print("No open retcon requests.")
            return
        table = Table(title="Open Retcon Requests")
        table.add_column("ID", style="dim", no_wrap=True)
        table.add_column("Description")
```

(keep whatever remaining rows of the table-building code follow — only the `list_retcon_requests` call and its replacement changes; the rest of the function is unaffected since `Flag` still has `.id`/`.description` etc. in the same shape `RetconRequest` did).

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/canon/test_flags_projection.py -v` and the CLI test file identified in Step 1.
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add novelizer/canon/projector.py novelizer/director/cli.py tests/canon/test_flags_projection.py tests/**/test_*cli*.py
git commit -m "chore: drop legacy retcon_requests table, alias fully into flags"
```

---

### Task 9: New Triage agent

**Files:**
- Create: `novelizer/agents/triage.py`
- Modify: `novelizer/agents/registry.py` (import + `AGENT_REGISTRY` append)
- Modify: `novelizer/settings/models.py` (add `triage_interval`, `triage_tools_enabled` near the other `*_interval`/`*_tools_enabled` fields, and to the field-name list at the top of the file used for settings validation/enumeration — lines 14-21 per the earlier grep, confirm exact list name before editing)
- Modify: `novelizer/agents/schemas.py` (add `TriageVerdict` output schema)
- Test: `tests/agents/test_triage.py` (new)

**Interfaces:**
- Consumes: `Flag`, `FlagStatus`, `list_flags` from Task 1; `AgentSpec`/`AgentContext`/`ToolGrant` from `registry_types.py`.
- Produces: `_CATEGORY_OWNERS: dict[str, str]` mapping (module-level constant in `triage.py`) — not consumed elsewhere in this plan but documented here since it's the single place category routing is decided, for future agents to extend.

- [ ] **Step 1: Write the failing test**

```python
# tests/agents/test_triage.py
import os
import tempfile
import pytest
from novelizer.canon.event_store import EventStore
from novelizer.canon.projector import Projector
from novelizer.canon.read_store import ReadStore
from novelizer.canon.committer import Committer
from novelizer.canon.events import EventType
from novelizer.agents.triage import Triage
from novelizer.agents.schemas import TriageVerdict
from novelizer.store.models import Flag, FlagStatus


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


async def test_verified_owned_flag_stays_open_for_owner(stack):
    events, proj, read, committer = stack
    await events.append(EventType.FLAG_CREATED, "f1",
                        Flag(id="f1", category="contradiction", description="two suns",
                             related_entry_ids=["w1"], proposed_resolution="", filed_by="continuity_checker"))
    await proj.catch_up()
    out = TriageVerdict(verdict="real")
    agent = Triage(FakeRunner(out), read, committer)
    await agent.run_once()
    await proj.catch_up()
    flags = await read.list_flags(category="contradiction", status=FlagStatus.open)
    assert len(flags) == 1 and flags[0].id == "f1"


async def test_dismissed_flag_is_rejected(stack):
    events, proj, read, committer = stack
    await events.append(EventType.FLAG_CREATED, "f1",
                        Flag(id="f1", category="pacing", description="false alarm",
                             related_entry_ids=[], proposed_resolution="", filed_by="structure_analyst"))
    await proj.catch_up()
    out = TriageVerdict(verdict="dismiss", reason="not actually a pacing problem")
    agent = Triage(FakeRunner(out), read, committer)
    await agent.run_once()
    await proj.catch_up()
    assert await read.list_flags(status=FlagStatus.open) == []
    rejected = await read.list_flags(status=FlagStatus.rejected)
    assert len(rejected) == 1 and rejected[0].resolved_by == "triage"


async def test_unowned_category_increments_triage_passes_then_goes_stale(stack):
    events, proj, read, committer = stack
    await events.append(EventType.FLAG_CREATED, "f1",
                        Flag(id="f1", category="mystery_gap", description="no owner for this",
                             related_entry_ids=[], proposed_resolution="", filed_by="author"))
    await proj.catch_up()
    out = TriageVerdict(verdict="real")
    agent = Triage(FakeRunner(out), read, committer, stale_after=2)
    await agent.run_once()
    await proj.catch_up()
    flags = await read.list_flags(category="mystery_gap")
    assert flags[0].triage_passes == 1 and flags[0].status == FlagStatus.open
    await agent.run_once()
    await proj.catch_up()
    flags = await read.list_flags(category="mystery_gap")
    assert flags[0].triage_passes == 2 and flags[0].status == FlagStatus.stale


async def test_owned_category_never_increments_triage_passes(stack):
    events, proj, read, committer = stack
    await events.append(EventType.FLAG_CREATED, "f1",
                        Flag(id="f1", category="worldbuilding", description="no map",
                             related_entry_ids=[], proposed_resolution="", filed_by="author"))
    await proj.catch_up()
    agent = Triage(FakeRunner(TriageVerdict(verdict="real")), read, committer, stale_after=1)
    await agent.run_once()
    await agent.run_once()
    await proj.catch_up()
    flags = await read.list_flags(category="worldbuilding")
    assert flags[0].triage_passes == 0
    assert flags[0].status == FlagStatus.open


async def test_noop_when_no_open_flags(stack):
    events, proj, read, committer = stack
    agent = Triage(FakeRunner(TriageVerdict(verdict="real")), read, committer)
    await agent.run_once()
    await proj.catch_up()
    assert await read.list_flags() == []
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/agents/test_triage.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'novelizer.agents.triage'`.

- [ ] **Step 3: Add `TriageVerdict` to schemas.py**

```python
class TriageVerdict(BaseModel):
    """Triage's per-flag decision: is it real, and (if unowned) does it get
    reclassified? `verdict="real"` with a known-owner category just leaves
    the flag open for its owner's own poll; `verdict="dismiss"` rejects it;
    `reclassify_category`, when set, overwrites an unowned flag's category
    before the owner-routing check runs again next pass.
    """

    verdict: Literal["real", "dismiss"] = "real"
    reason: str = ""
    reclassify_category: str = ""
    feed_note: str = ""
```

- [ ] **Step 4: Write `novelizer/agents/triage.py`**

```python
from __future__ import annotations
import logging
from novelizer.agents.base import BaseAgent, Runner, GRAPH_RECURSION_LIMIT
from novelizer.agents.schemas import TriageVerdict
from novelizer.canon.read_store import ReadStore
from novelizer.canon.committer import Committer
from novelizer.canon.events import EventType
from novelizer.store.models import FlagStatus

logger = logging.getLogger(__name__)

# category -> agent name that already polls list_flags(category=..., status=open)
# for its own resolution work, same pattern Retconner uses for "contradiction".
# Unmapped categories are Triage's own catch-all responsibility.
_CATEGORY_OWNERS: dict[str, str] = {
    "contradiction": "retconner",
    "pacing": "structure_analyst",
    "worldbuilding": "world_architect",
    "thematic": "plotter",
    "voice_drift": "retconner",
}

DEFAULT_STALE_AFTER = 5

SYSTEM_PROMPT = """You are Triage for a living fictional world — the one agent that reads
every flag any other agent raises, regardless of category, and decides whether it's a real
issue worth keeping open.

## Your lane
For the ONE flag you're shown: decide "real" or "dismiss". You never edit canon and you never
invent a fix — that's the owning agent's job once the flag is confirmed. Your only output is a
verdict, an optional reason, and (only for a flag whose category has no known owner) an optional
`reclassify_category` if you can tell what it actually is from a fixed vocabulary the owning
agents understand: contradiction, pacing, worldbuilding, thematic, voice_drift. If none fit,
leave `reclassify_category` blank — it stays a catch-all and ages toward stale.

## How to work
1. VERIFY: read the flag's description and cited entries. Is this still a real, current issue,
   or has canon already moved past it / was it never actually a problem?
2. DECIDE: "real" keeps it open. "dismiss" closes it — use this for stale, duplicate-in-substance,
   or simply wrong flags. Give a one-line `reason` either way; it goes in the log.
3. STOP once you can state the evidence for your verdict.

## Voice
Neutral. Put personality only in `feed_note`, never in `reason`."""


class Triage(BaseAgent):
    def __init__(
        self,
        runner: Runner,
        read_store: ReadStore,
        committer: Committer,
        interval: int = 120,
        personality: str = "",
        stale_after: int = DEFAULT_STALE_AFTER,
    ) -> None:
        super().__init__(runner, read_store, committer, interval, name="triage", personality=personality)
        self._deferred: set[str] = set()
        self._stale_after = stale_after

    async def readiness(self) -> float:
        open_flags = len(await self._read.list_flags(status=FlagStatus.open))
        return min(1.0, open_flags / 3)

    async def poll(self) -> dict:
        open_flags = await self._read.list_flags(status=FlagStatus.open)
        self._deferred &= {f.id for f in open_flags}
        candidates = [f for f in open_flags if f.id not in self._deferred]
        if not candidates and open_flags:
            self._deferred.clear()
            candidates = open_flags
        return {"target": candidates[0] if candidates else None}

    async def work(self, ctx: dict) -> TriageVerdict | None:
        flag = ctx["target"]
        if flag is None:
            return None
        cast = self._guarded_line("In character", self.personality)
        msg = (
            f"Flag category: {flag.category}\nDescription: {flag.description}\n"
            f"Related entry ids: {flag.related_entry_ids}\n"
            f"Proposed resolution: {flag.proposed_resolution}{cast}"
        )
        result = await self._runner.ainvoke({"messages": [{"role": "user", "content": msg}]})
        return result.get("structured_response")

    async def commit(self, out: TriageVerdict | None, ctx: dict) -> None:
        flag = ctx["target"]
        if flag is None or out is None:
            return
        if out.verdict == "dismiss":
            rejected = flag.model_copy(update={"status": FlagStatus.rejected, "resolved_by": self.name})
            await self._committer.commit(self.name, EventType.FLAG_REJECTED, flag.id, rejected)
            await self._remark(out.feed_note)
            return
        owner = _CATEGORY_OWNERS.get(flag.category)
        if owner is not None:
            # Owned and verified real: leave it open, untouched, for the
            # owner's own poll to pick up next cycle. Nothing to commit.
            await self._remark(out.feed_note)
            return
        # Unowned catch-all: reclassify if Triage recognized it, else age
        # the pass counter toward stale so it doesn't loop forever.
        new_category = out.reclassify_category or flag.category
        if out.reclassify_category and out.reclassify_category in _CATEGORY_OWNERS:
            reclassified = flag.model_copy(update={"category": new_category})
            await self._committer.commit(self.name, EventType.FLAG_CREATED, flag.id, reclassified)
            await self._remark(out.feed_note)
            return
        passes = flag.triage_passes + 1
        if passes >= self._stale_after:
            aged = flag.model_copy(update={"triage_passes": passes, "status": FlagStatus.stale})
            await self._committer.commit(self.name, EventType.FLAG_REJECTED, flag.id, aged)
        else:
            aged = flag.model_copy(update={"triage_passes": passes})
            await self._committer.commit(self.name, EventType.FLAG_CREATED, flag.id, aged)
        await self._remark(out.feed_note)

    async def _run(self) -> None:
        ctx = await self.poll()
        flag = ctx["target"]
        if flag is None:
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


def build_triage_runner(settings, callbacks=None, backend=None, tools=None):
    from deepagents import create_deep_agent
    from novelizer.agents.llm import build_chat_model
    from novelizer.agents.middleware import ExcludeToolsMiddleware
    if backend is not None:
        model = build_chat_model(
            settings.agent_model, settings.llm_base_url, settings.llm_api_key,
            settings.agent_temperature, max_tokens=settings.llm_max_tokens,
            callbacks=None, streaming=callbacks is not None,
        )
        graph = create_deep_agent(
            model=model, system_prompt=SYSTEM_PROMPT, response_format=TriageVerdict,
            backend=backend, tools=tools,
            middleware=[ExcludeToolsMiddleware(excluded=frozenset({"write_todos"}))],
        )
        config = {"recursion_limit": GRAPH_RECURSION_LIMIT}
        if callbacks:
            config["callbacks"] = callbacks
        return graph.with_config(config)
    model = build_chat_model(settings.agent_model, settings.llm_base_url, settings.llm_api_key, settings.agent_temperature, max_tokens=settings.llm_max_tokens, callbacks=callbacks)
    return create_deep_agent(model=model, system_prompt=SYSTEM_PROMPT, response_format=TriageVerdict)


from novelizer.agents.registry_types import AgentContext, AgentSpec, ToolGrant


def _construct(ctx: AgentContext) -> Triage:
    enabled = ctx.settings.triage_tools_enabled
    builder = ctx.tooled(build_triage_runner, enabled)
    runner = ctx.runner_for("triage", builder)
    return Triage(
        runner, ctx.read, ctx.committer,
        interval=ctx.settings.triage_interval,
        personality=ctx.personalities.get("triage", ""),
    )


SPEC = AgentSpec(
    name="triage",
    tool_grant=ToolGrant(enabled_setting="triage_tools_enabled"),
    construct=_construct,
)
```

Note: `commit`'s "reclassify" branch re-commits `FLAG_CREATED` for the *same* flag id with an updated `category` — this relies on the projector's `INSERT OR REPLACE INTO flags (id, ...)` (Task 1, Step 6) to overwrite the row rather than duplicate it, exactly like `RETCON_REQUEST_CREATED`'s original re-emit-to-update pattern never existed but `FLAG_CREATED`'s replace-by-id semantics support it cleanly — confirm this behavior is covered by `test_unowned_category_increments_triage_passes_then_goes_stale` (it re-emits `FLAG_CREATED` for `triage_passes` bumps and expects exactly one row back from `list_flags`, which the `INSERT OR REPLACE` guarantees).

- [ ] **Step 5: Add settings fields**

In `novelizer/settings/models.py`, add near the other `*_interval` fields (~line 74): `triage_interval: int = 120`, and near the other `*_tools_enabled` fields (~line 96): `triage_tools_enabled: bool = True`. Add both names to whatever field-name list groups them at the top of the file (lines 14-21 per the earlier grep — confirm the exact list name, e.g. `_INTERVAL_FIELDS`/`_TOOL_FIELDS` or similar, before editing, and add `"triage_interval"`/`"triage_tools_enabled"` in the same alphabetical/grouping position as their neighbors).

- [ ] **Step 6: Register the agent**

`novelizer/agents/registry.py`:

```python
from __future__ import annotations
from novelizer.agents import (
    author, world_architect, character_keeper, editor,
    continuity_checker, retconner, structure_analyst, plotter, muse, triage,
)
from novelizer.agents.registry_types import AgentSpec

AGENT_REGISTRY: list[AgentSpec] = [
    world_architect.SPEC, character_keeper.SPEC, muse.SPEC,
    plotter.SPEC, author.SPEC,
    editor.SPEC, continuity_checker.SPEC, retconner.SPEC, structure_analyst.SPEC,
    triage.SPEC,
]
```

Triage goes last: it consumes flags every other agent just filed this cycle, so it should tick after all filers, same reasoning that already puts `retconner.SPEC` near the end.

- [ ] **Step 7: Run test to verify it passes**

Run: `pytest tests/agents/test_triage.py -v`
Expected: PASS.

- [ ] **Step 8: Commit**

```bash
git add novelizer/agents/triage.py novelizer/agents/registry.py novelizer/agents/schemas.py \
        novelizer/settings/models.py tests/agents/test_triage.py
git commit -m "feat: add Triage agent for generic flag verification and routing"
```

---

### Task 10: Autonomy gating for `FLAG_RESOLVED`

**Files:**
- Modify: `novelizer/canon/policy.py:5`
- Test: find the existing autonomy/policy test file (search `tests/` for `gated_retcons` or `_RETCON_EVENTS`, read in full before editing)

**Interfaces:**
- Consumes: `EventType.FLAG_RESOLVED` from Task 1.
- Produces: nothing new.

- [ ] **Step 1: Write the failing test**

Read the existing policy test file in full. Add (or adapt an existing `RETCON_REQUEST_RESOLVED`-keyed test to use) an assertion like:

```python
async def test_gated_retcons_gates_flag_resolved(...):
    ...
    assert await policy.is_gated("retconner", EventType.FLAG_RESOLVED) is True
```

using whatever fixture/setup pattern the existing file already uses for constructing an `AutonomyPolicy` with `gated_retcons` as the effective level.

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/canon/test_policy.py -v` (adjust to the actual discovered filename)
Expected: FAIL — `FLAG_RESOLVED` not in `_RETCON_EVENTS`, so `is_gated` returns `False`.

- [ ] **Step 3: Update `policy.py`**

```python
_RETCON_EVENTS = {EventType.WORLD_ENTRY_SUPERSEDED, EventType.FLAG_RESOLVED}
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/canon/test_policy.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add novelizer/canon/policy.py tests/canon/test_policy.py
git commit -m "feat: gate FLAG_RESOLVED under gated_retcons autonomy level"
```

---

### Task 11: TUI — generalize the Retcons browser section into a Flags section

**Files:**
- Modify: `novelizer/tui/widgets/browser_model.py:60-94` (`browser_sections`), `:147-152` (detail view branch)
- Test: `tests/tui/test_browser_model.py`

**Interfaces:**
- Consumes: `list_flags` from Task 1.
- Produces: nothing new for later tasks (leaf/UI task).

- [ ] **Step 1: Write the failing test**

Read `tests/tui/test_browser_model.py` in full (already partially captured above). Replace every `RetconRequest`/`RetconStatus` import/construction with `Flag`/`FlagStatus` (`category="contradiction"` added to preserve existing test semantics), `EventType.RETCON_REQUEST_CREATED` → `EventType.FLAG_CREATED`, and update the specific assertions:

```python
async def test_sections_include_flags(stack):
    events, proj, read = stack
    ...
    secs = await browser_sections(read, staleness_threshold=10)
    assert [s["key"] for s in secs] == ["chapters", "characters", "world", "flags", "threads", "themes"]


async def test_flags_label_gains_alarm_mark_only_when_stale(stack):
    events, proj, read = stack
    secs = await browser_sections(read, staleness_threshold=10)
    assert [s for s in secs if s["key"] == "flags"][0]["label"] == "Flags (0)"
    await events.append(EventType.FLAG_CREATED, "r1",
                        Flag(id="r1", category="contradiction", description="open change",
                             related_entry_ids=[], proposed_resolution="fix"))
    r2 = Flag(id="r2", category="pacing", description="stale change", related_entry_ids=[], proposed_resolution="fix")
    await events.append(EventType.FLAG_CREATED, "r2", r2)
    await events.append(EventType.FLAG_REJECTED, "r2", r2.model_copy(update={"status": "stale"}))
    await proj.catch_up()
    secs = await browser_sections(read, staleness_threshold=10)
    flags = [s for s in secs if s["key"] == "flags"][0]
    assert flags["label"] == "Flags (1) ⚠"
    assert len(flags["items"]) == 1 and flags["items"][0]["id"] == "r1"


async def test_detail_view_flag(stack):
    events, proj, read = stack
    await events.append(EventType.FLAG_CREATED, "r1",
                        Flag(id="r1", category="contradiction", description="scar mismatch",
                             related_entry_ids=[], proposed_resolution="left hand"))
    await proj.catch_up()
    flag = await detail_view(read, "flags", "r1")
    assert flag.title == "scar mismatch" and "Proposed: left hand" in flag.body.plain
```

Apply the same rename mechanically to any other test in the file referencing `retcons`/`RetconRequest`, and update the `for section in (...)` smoke-test tuple (old line 193) to say `"flags"` instead of `"retcons"`.

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/tui/test_browser_model.py -v`
Expected: FAIL — `browser_model.py` still calls `read.list_retcon_requests` (deleted in Task 8), and section key is still `"retcons"`.

- [ ] **Step 3: Update `browser_model.py`**

Replace line 68 (`retcons = await read.list_retcon_requests(status="open")`) — the section now needs both the open list (for the item listing, per-category grouped or flat — flat is fine, categories show inline) and a stale check for the alarm:

```python
    open_flags = await read.list_flags(status="open")
    stale_flags = await read.list_flags(status="stale")
```

Replace line 80 (`retcons_label = f"Retcons ({len(retcons)}) ⚠" if retcons else "Retcons (0)"`):

```python
    flags_label = f"Flags ({len(open_flags)}) ⚠" if stale_flags else f"Flags ({len(open_flags)})"
```

Replace the section dict (lines 88-89):

```python
        {"key": "flags", "label": flags_label,
         "items": [{"id": f.id, "label": f"[{f.category}] {f.description[:32]}"} for f in open_flags]},
```

Replace the detail-view branch (lines 147-152):

```python
    if section_key == "flags":
        for f in await read.list_flags():
            if f.id == item_id:
                return _view(f.description, f"status: {_enum_val(f.status)}  category: {f.category}", "",
                             [("Proposed", f.proposed_resolution)])
        return None
```

Update the module docstring at the top of the file (line 7, `"Retcons label carries ⚠ when open items exist, ..."`) to describe the new semantics:

```python
Flags label carries ⚠ when any STALE flag exists (Triage's catch-all gave
up on an unowned-category flag and it needs a human), not merely when open
items exist -- an open flag is expected to be actively worked by its owner
agent or by Triage, and the count next to the label is a normal, non-
alarming queue depth. The Threads section ...
```

(keep whatever the docstring's Threads-section sentence originally said, unchanged, after this rewritten Flags sentence).

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/tui/test_browser_model.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add novelizer/tui/widgets/browser_model.py tests/tui/test_browser_model.py
git commit -m "feat: generalize TUI Retcons section into category-grouped Flags"
```

---

### Task 12: Full-repo sweep and final review

**Files:** none pre-determined — this task is a verification pass.

**Interfaces:** none new.

- [ ] **Step 1: Grep for any remaining legacy references**

Run: `grep -rn "RetconRequest\|RetconStatus\|list_retcon_requests\|RetconDraft\|VOICE_SOURCE_TAG" novelizer/ tests/`
Expected: no hits. If any remain, they were missed by an earlier task — fix them following that task's same rename pattern, then re-run the affected test file.

- [ ] **Step 2: Run every touched test file together (still not the full suite)**

Run:
```bash
pytest tests/canon/test_flags_projection.py tests/canon/test_policy.py \
       tests/agents/test_retconner.py tests/agents/test_retconner_resolution.py \
       tests/agents/test_continuity_checker.py tests/agents/test_continuity_uptake.py \
       tests/agents/test_character_keeper.py tests/agents/test_character_keeper_pull.py \
       tests/agents/test_character_keeper_uptake.py tests/agents/test_character_keeper_property.py \
       tests/agents/test_editor*.py tests/agents/test_intents*.py \
       tests/agents/test_structure_analyst.py tests/agents/test_plotter*.py \
       tests/agents/test_world_architect*.py tests/agents/test_triage.py \
       tests/tui/test_browser_model.py -v
```
Expected: PASS, all of them.

- [ ] **Step 3: Update the spec's status note**

Add a one-line pointer at the bottom of `docs/superpowers/specs/2026-07-20-generic-issue-flags-design.md`: `**Status:** implemented, see docs/superpowers/plans/2026-07-20-generic-issue-flags.md`.

- [ ] **Step 4: Commit**

```bash
git add docs/superpowers/specs/2026-07-20-generic-issue-flags-design.md
git commit -m "docs: mark generic issue-flagging spec as implemented"
```

- [ ] **Step 5: Hand off for full-suite verification**

This plan intentionally never runs the full test suite (per user instruction, to avoid dramatic runtimes / load-flakes — see `docs/TESTING-TUI.md`). Once all tasks are committed, tell the user the branch is ready for a full-suite run and final review before merge — do not run it automatically.
