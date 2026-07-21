# Flag Severity + Escalation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add severity classification (`minor`/`major`/`critical`) to the `Flag` model, an escalation event pair so critical or repeatedly-failing flags become visible, and a dedicated TUI review screen so a human can inspect and clear escalations.

**Architecture:** Extend the existing event-sourced `Flag`/Triage system (see `docs/superpowers/specs/2026-07-20-generic-issue-flags-design.md`) with two new append-only events (`FLAG_ESCALATED`, `FLAG_ESCALATION_CLEARED`), three new `Flag` fields (`severity`, `escalated`, `failed_attempts`), a `severity` field on `TriageVerdict` so Triage assesses it during its existing pass, a `failed_attempts` increment wired into Retconner's existing `_decline` path (the only owning-agent decline path that exists today), and a new `escalations_screen.py` + `escalations_model.py` pair in the TUI following the `settings_screen.py` / `browser_model.py` conventions already in the codebase.

**Tech Stack:** Python, Pydantic (`BaseModel`), aiosqlite, Textual (TUI), pytest (implicit async via pytest-asyncio config).

## Global Constraints

- Escalation is a visibility signal, not a routing gate — an escalated flag still goes through the normal owning-agent poll/attempt cycle (spec: "Escalation semantics").
- All new state changes must be committed via events (`FLAG_ESCALATED` / `FLAG_ESCALATION_CLEARED`) — this is an event-sourced store, never mutate `flags` table state without a corresponding event.
- Clearing an escalation (`FLAG_ESCALATION_CLEARED`) does not resolve or reject the underlying flag — these are independent state machines (spec: "TUI: Escalations review screen").
- Only Retconner (`novelizer/agents/retconner.py`) has an existing decline/resolve code path today; `structure_analyst.py`, `world_architect.py`, `plotter.py` only ever file flags (`FLAG_CREATED`), they never resolve or reject one they own. `failed_attempts` tracking in this plan therefore only wires into Retconner's `_decline`. This is a known, pre-existing gap in the flag system, not something to silently "fix" as part of this feature — out of scope per YAGNI.
- Follow existing patterns: `*_model.py` files are pure functions with zero Textual imports; screens are `Screen` subclasses with `BINDINGS`, dependencies injected via `__init__`, UI built in `compose()`.

---

### Task 1: Extend `Flag` model + add new event types

**Files:**
- Modify: `novelizer/store/models.py:323-348`
- Modify: `novelizer/canon/events.py:19-21`
- Test: `tests/store/test_flag_model.py` (new file)

**Interfaces:**
- Produces: `Flag.severity: Literal["minor","major","critical"] | None`, `Flag.escalated: bool`, `Flag.failed_attempts: int`, `EventType.FLAG_ESCALATED = "flag.escalated"`, `EventType.FLAG_ESCALATION_CLEARED = "flag.escalation_cleared"`.

- [ ] **Step 1: Write the failing test**

```python
# tests/store/test_flag_model.py
from novelizer.store.models import Flag


def test_flag_defaults_severity_escalated_failed_attempts():
    flag = Flag(category="contradiction", description="x")
    assert flag.severity is None
    assert flag.escalated is False
    assert flag.failed_attempts == 0


def test_flag_severity_accepts_known_values():
    for sev in ("minor", "major", "critical"):
        flag = Flag(category="contradiction", description="x", severity=sev)
        assert flag.severity == sev
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/store/test_flag_model.py -v`
Expected: FAIL with `TypeError: Flag() got an unexpected keyword argument 'severity'` (or `AttributeError` on `flag.severity`).

- [ ] **Step 3: Add fields to `Flag` and new event type constants**

In `novelizer/store/models.py`, modify the `Flag` class (lines 330-348):

```python
class Flag(BaseModel):
    """A structured issue any agent can raise mid-work — a generalization of
    the old contradiction-only RetconRequest. `category` is free-form
    (e.g. "contradiction", "pacing", "thematic", "worldbuilding", "voice_drift")
    so agents aren't limited to a fixed taxonomy; the Triage agent routes by
    category via a small owner map, catch-alling anything unmapped.
    `triage_passes` counts unresolved catch-all Triage passes over an unowned
    flag; past a threshold it is marked `stale` rather than looping forever.
    `severity` is assessed by Triage alongside its real/dismiss verdict.
    `escalated` mirrors whether an unresolved FLAG_ESCALATED currently
    applies (cleared by FLAG_ESCALATION_CLEARED or flag resolution).
    `failed_attempts` counts owning-agent decline/fail cycles; past a
    threshold it triggers escalation regardless of original severity.
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
    severity: Optional[Literal["minor", "major", "critical"]] = None
    escalated: bool = False
    failed_attempts: int = 0
```

Check the top of `novelizer/store/models.py` for an existing `Literal` import (it's a common typing import — if not present, add `Literal` to the existing `from typing import ...` line).

In `novelizer/canon/events.py`, modify `EventType` (after line 21):

```python
    FLAG_CREATED = "flag.created"
    FLAG_RESOLVED = "flag.resolved"
    FLAG_REJECTED = "flag.rejected"
    FLAG_ESCALATED = "flag.escalated"
    FLAG_ESCALATION_CLEARED = "flag.escalation_cleared"
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/store/test_flag_model.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add novelizer/store/models.py novelizer/canon/events.py tests/store/test_flag_model.py
git commit -m "feat: add severity/escalated/failed_attempts to Flag model"
```

---

### Task 2: Projector handling for escalation events

**Files:**
- Modify: `novelizer/canon/projector.py` (schema at lines 39-41, event handling near lines 251-279)
- Test: `tests/canon/test_flags_projection.py` (existing file, add tests)

**Interfaces:**
- Consumes: `Flag` (Task 1), `EventType.FLAG_ESCALATED`/`FLAG_ESCALATION_CLEARED` (Task 1).
- Produces: `flags` table gains an `escalated` column, queryable via SQL `WHERE escalated=1`. The `data` column (full JSON) continues to carry `severity`/`failed_attempts` — no dedicated columns needed for those since nothing filters on them at the SQL layer yet.

- [ ] **Step 1: Write the failing test**

Add to `tests/canon/test_flags_projection.py` (matching the existing fixture pattern already in that file — a temp-sqlite `EventStore` + `Projector`, events appended raw, `await proj.catch_up()`, assertions via `read.list_flags(...)`):

```python
async def test_flag_escalated_sets_escalated_column(stack):
    events, proj, read = stack
    flag = Flag(id="f1", category="contradiction", description="x", severity="critical")
    await events.append("triage", EventType.FLAG_CREATED, flag.id, flag.model_dump(mode="json"))
    escalated_flag = flag.model_copy(update={"escalated": True})
    await events.append("triage", EventType.FLAG_ESCALATED, flag.id, escalated_flag.model_dump(mode="json"))
    await proj.catch_up()

    flags = await read.list_flags(escalated=True)
    assert len(flags) == 1
    assert flags[0].id == "f1"
    assert flags[0].severity == "critical"


async def test_flag_escalation_cleared_unsets_escalated_column(stack):
    events, proj, read = stack
    flag = Flag(id="f2", category="contradiction", description="x", severity="critical", escalated=True)
    await events.append("triage", EventType.FLAG_CREATED, flag.id, flag.model_dump(mode="json"))
    await events.append("triage", EventType.FLAG_ESCALATED, flag.id, flag.model_dump(mode="json"))
    cleared_flag = flag.model_copy(update={"escalated": False})
    await events.append("human", EventType.FLAG_ESCALATION_CLEARED, flag.id, cleared_flag.model_dump(mode="json"))
    await proj.catch_up()

    flags = await read.list_flags(escalated=True)
    assert flags == []
    all_flags = await read.list_flags()
    assert any(f.id == "f2" for f in all_flags)
```

Note: adapt `Flag(...)` / `EventType` / `events.append(...)` calls to exactly match the imports and fixture already present at the top of `tests/canon/test_flags_projection.py` — read that file's existing imports before writing this step for real, since the fixture name/shape (`stack`) must match exactly what's already there.

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/canon/test_flags_projection.py -v`
Expected: FAIL — `list_flags(escalated=True)` raises `TypeError: list_flags() got an unexpected keyword argument 'escalated'` (ReadStore change is Task 3, but write this test now so Task 3's test run proves projector + read store together).

- [ ] **Step 3: Add `escalated` column and event handling to projector**

In `novelizer/canon/projector.py`, modify the `flags` table schema (lines 39-41):

```python
CREATE TABLE IF NOT EXISTS flags (
    id TEXT PRIMARY KEY, data TEXT NOT NULL, status TEXT NOT NULL, category TEXT NOT NULL,
    escalated INTEGER NOT NULL DEFAULT 0
);
```

Add a migration guard: check how this file currently handles schema evolution for existing databases (grep `projector.py` for `ALTER TABLE` — if that pattern exists elsewhere in the file, follow it to add `escalated` to already-created `flags` tables; if no such pattern exists, `CREATE TABLE IF NOT EXISTS` combined with a startup `ALTER TABLE flags ADD COLUMN IF NOT EXISTS escalated INTEGER NOT NULL DEFAULT 0` wrapped in a try/except for sqlite versions without `ADD COLUMN IF NOT EXISTS` support — SQLite added `IF NOT EXISTS` support for `ADD COLUMN` in 3.35+; if the codebase targets older SQLite, use a `PRAGMA table_info(flags)` check instead. Match whatever pattern the file already uses for other tables' evolving columns before inventing a new one.

Add event handling after the existing `FLAG_RESOLVED`/`FLAG_REJECTED` branch (after line 262, before the `RETCON_REQUEST_*` legacy branch at line 263):

```python
        elif t == EventType.FLAG_ESCALATED:
            await self._conn.execute(
                "INSERT OR REPLACE INTO flags (id, data, status, category, escalated) VALUES (?,?,?,?,1)",
                (p["id"], data, p.get("status", "open"), p.get("category", "")),
            )
        elif t == EventType.FLAG_ESCALATION_CLEARED:
            await self._conn.execute(
                "INSERT OR REPLACE INTO flags (id, data, status, category, escalated) VALUES (?,?,?,?,0)",
                (p["id"], data, p.get("status", "open"), p.get("category", "")),
            )
```

Also update the `FLAG_CREATED` and `FLAG_RESOLVED`/`FLAG_REJECTED` branches (lines 251-262) to preserve the `escalated` column value already on that row rather than resetting it to the SQL default on every re-insert (since `INSERT OR REPLACE` replaces the whole row). Change both `INSERT OR REPLACE` statements in that block to include `escalated` sourced from the payload's `escalated` field:

```python
        elif t == EventType.FLAG_CREATED:
            await self._conn.execute(
                "INSERT OR REPLACE INTO flags (id, data, status, category, escalated) VALUES (?,?,?,?,?)",
                (p["id"], data, p.get("status", "open"), p.get("category", ""), int(p.get("escalated", False))),
            )
        elif t == EventType.FLAG_RESOLVED or t == EventType.FLAG_REJECTED:
            await self._conn.execute(
                "INSERT OR REPLACE INTO flags (id, data, status, category, escalated) VALUES (?,?,?,?,?)",
                (p["id"], data,
                 p.get("status", "resolved" if t == EventType.FLAG_RESOLVED else "rejected"),
                 p.get("category", ""), int(p.get("escalated", False))),
            )
```

This means callers committing `FLAG_RESOLVED` must pass `escalated=False` in the payload when auto-clearing (Task 5 handles this).

- [ ] **Step 4: Add `escalated` filter to `ReadStore.list_flags`**

In `novelizer/canon/read_store.py`, modify `list_flags` (lines 72-81):

```python
    async def list_flags(
        self, category: Optional[str] = None, status: Optional[str] = None,
        escalated: Optional[bool] = None,
    ) -> list[Flag]:
        clauses, params = [], []
        if category:
            clauses.append("category=?")
            params.append(category)
        if status:
            clauses.append("status=?")
            params.append(status)
        if escalated is not None:
            clauses.append("escalated=?")
            params.append(int(escalated))
        where = f" WHERE {' AND '.join(clauses)}" if clauses else ""
        cur = await self._conn.execute(f"SELECT data FROM flags{where} ORDER BY rowid", params)
        return [Flag.model_validate_json(r[0]) for r in await cur.fetchall()]
```

- [ ] **Step 5: Run test to verify it passes**

Run: `pytest tests/canon/test_flags_projection.py -v`
Expected: PASS

- [ ] **Step 6: Commit**

```bash
git add novelizer/canon/projector.py novelizer/canon/read_store.py tests/canon/test_flags_projection.py
git commit -m "feat: project FLAG_ESCALATED/FLAG_ESCALATION_CLEARED, add escalated filter to list_flags"
```

---

### Task 3: Triage assesses severity and escalates critical flags

**Files:**
- Modify: `novelizer/agents/schemas.py` (`TriageVerdict`, lines 25-36)
- Modify: `novelizer/agents/triage.py` (commit logic, lines 88-118, plus system prompt)
- Test: `tests/agents/test_triage.py` (existing file, add tests)

**Interfaces:**
- Consumes: `Flag.severity`/`escalated` (Task 1), `EventType.FLAG_ESCALATED` (Task 1).
- Produces: `TriageVerdict.severity: Literal["minor","major","critical"]`.

- [ ] **Step 1: Write the failing test**

Add to `tests/agents/test_triage.py`, following the existing `FakeRunner` + fixture pattern already in that file (read the file's current imports/fixtures first and match them exactly — the sketch below assumes `FakeRunner`, `stack`/equivalent fixture names as already established there):

```python
async def test_triage_critical_verdict_escalates_flag(stack):
    events, proj, read, committer, runner = stack
    flag = Flag(id="f1", category="contradiction", description="x")
    await events.append("continuity_checker", EventType.FLAG_CREATED, flag.id, flag.model_dump(mode="json"))
    await proj.catch_up()

    runner.set_response({"verdict": "real", "reason": "", "reclassify_category": "", "severity": "critical", "feed_note": ""})
    triage = build_triage_runner(committer=committer, read=read, runner=runner)
    await triage._run()

    flags = await read.list_flags(escalated=True)
    assert len(flags) == 1
    assert flags[0].id == "f1"
    assert flags[0].severity == "critical"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/agents/test_triage.py -v`
Expected: FAIL — `TriageVerdict` rejects/ignores the `severity` key, or `flags[0].severity` is `None`.

- [ ] **Step 3: Add `severity` to `TriageVerdict`**

In `novelizer/agents/schemas.py`, modify `TriageVerdict` (lines 25-36):

```python
class TriageVerdict(BaseModel):
    """Triage's per-flag decision: is it real, and (if unowned) does it get
    reclassified? `verdict="real"` with a known-owner category just leaves
    the flag open for its owner's own poll; `verdict="dismiss"` rejects it;
    `reclassify_category`, when set, overwrites an unowned flag's category
    before the owner-routing check runs again next pass. `severity` is
    assessed on every `real` verdict; `critical` triggers immediate
    escalation regardless of category ownership.
    """

    verdict: Literal["real", "dismiss"] = "real"
    reason: str = ""
    reclassify_category: str = ""
    severity: Literal["minor", "major", "critical"] = "minor"
    feed_note: str = ""
```

- [ ] **Step 4: Wire severity + escalation into Triage's commit logic**

In `novelizer/agents/triage.py`, modify `commit()` (lines 88-118). Insert severity assignment and escalation immediately after the `dismiss` early-return (after line 96), before the owner-routing check:

```python
    async def commit(self, out: TriageVerdict | None, ctx: dict) -> None:
        flag = ctx["target"]
        if flag is None or out is None:
            return
        if out.verdict == "dismiss":
            rejected = flag.model_copy(update={"status": FlagStatus.rejected, "resolved_by": self.name})
            await self._committer.commit(self.name, EventType.FLAG_REJECTED, flag.id, rejected)
            await self._remark(out.feed_note)
            return
        flag = flag.model_copy(update={"severity": out.severity})
        if out.severity == "critical" and not flag.escalated:
            flag = flag.model_copy(update={"escalated": True})
            await self._committer.commit(self.name, EventType.FLAG_ESCALATED, flag.id, flag)
        owner = _CATEGORY_OWNERS.get(flag.category)
        if owner is not None:
            await self._committer.commit(self.name, EventType.FLAG_CREATED, flag.id, flag)
            await self._remark(out.feed_note)
            return
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
```

Note the added `await self._committer.commit(self.name, EventType.FLAG_CREATED, flag.id, flag)` in the "owned + verified" branch — this is new; previously that branch left the flag untouched (no commit) since severity didn't exist to persist. Now severity must be persisted even for owned flags, so this branch needs a commit where it previously had none.

Update the `SYSTEM_PROMPT` in the same file (find the prompt string, likely a module-level constant near the top) to add a line instructing severity assessment, e.g. append to the existing instructions: `"Assess severity alongside your verdict: 'critical' if the issue contradicts a resolved arc, breaks a paid-off thread, or spans multiple already-written chapters; 'major' if it affects the current chapter's coherence; 'minor' otherwise."` — match the prompt's existing tone/format rather than pasting this verbatim; read the current prompt text before editing.

- [ ] **Step 5: Run test to verify it passes**

Run: `pytest tests/agents/test_triage.py -v`
Expected: PASS

- [ ] **Step 6: Commit**

```bash
git add novelizer/agents/schemas.py novelizer/agents/triage.py tests/agents/test_triage.py
git commit -m "feat: Triage assesses flag severity and escalates critical flags"
```

---

### Task 4: Retconner tracks failed_attempts and escalates on repeated failure; auto-clears on resolve

**Files:**
- Modify: `novelizer/agents/retconner.py` (`_decline` at lines 98-107, and the resolve path — locate the code that commits `FLAG_RESOLVED`, likely near where `_decline` is called from `commit()`)
- Test: `tests/agents/test_retconner.py` (find or create — check for an existing test file first via `ls tests/agents/`)

**Interfaces:**
- Consumes: `Flag.failed_attempts`/`escalated` (Task 1), `EventType.FLAG_ESCALATED`/`FLAG_ESCALATION_CLEARED` (Task 1).
- Produces: `_decline` increments `failed_attempts` and escalates at threshold 3; the resolve path clears escalation on success.

- [ ] **Step 1: Write the failing test**

```python
# in tests/agents/test_retconner.py — match existing fixture pattern in that file
async def test_decline_increments_failed_attempts_and_escalates_at_threshold(stack):
    events, proj, read, committer, retconner = stack
    flag = Flag(id="f1", category="contradiction", description="x", failed_attempts=2)
    await events.append("triage", EventType.FLAG_CREATED, flag.id, flag.model_dump(mode="json"))
    await proj.catch_up()

    await retconner._decline(flag, "cannot_reproduce", "no evidence")

    flags = await read.list_flags(escalated=True)
    assert len(flags) == 1
    assert flags[0].failed_attempts == 3


async def test_resolve_clears_prior_escalation(stack):
    events, proj, read, committer, retconner = stack
    flag = Flag(id="f2", category="contradiction", description="x", escalated=True, severity="critical")
    await events.append("triage", EventType.FLAG_CREATED, flag.id, flag.model_dump(mode="json"))
    await proj.catch_up()

    await retconner._resolve(flag, "amended the world entry")

    flags = await read.list_flags(escalated=True)
    assert flags == []
```

Adjust `_resolve`'s exact name/signature to whatever `retconner.py`'s actual resolve method is called (it wasn't captured verbatim in the earlier exploration — read `novelizer/agents/retconner.py` in full before writing this step to find the exact method name and signature for the success path that commits `FLAG_RESOLVED`).

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/agents/test_retconner.py -v`
Expected: FAIL — `failed_attempts` stays 0/2 (not incremented), no `FLAG_ESCALATED` committed, escalation not cleared on resolve.

- [ ] **Step 3: Modify `_decline` to track failed_attempts and escalate at threshold**

In `novelizer/agents/retconner.py`, modify `_decline` (lines 98-107):

```python
    _FAILURE_ESCALATION_THRESHOLD = 3

    async def _decline(self, req, resolution: str, reason: str) -> None:
        """Close a request without amending anything. Distinct from resolving
        it: nothing was repaired, and the filing agent's log should say so."""
        logger.info("retconner: declining request %s (%s): %s", req.id, resolution, reason)
        attempts = req.failed_attempts + 1
        rejected = req.model_copy(update={
            "status": FlagStatus.rejected,
            "resolved_by": self.name,
            "proposed_resolution": f"[{resolution}] {reason}" if reason else f"[{resolution}]",
            "failed_attempts": attempts,
        })
        await self._committer.commit(self.name, EventType.FLAG_REJECTED, req.id, rejected)
        if attempts >= self._FAILURE_ESCALATION_THRESHOLD and not rejected.escalated:
            escalated = rejected.model_copy(update={"escalated": True})
            await self._committer.commit(self.name, EventType.FLAG_ESCALATED, req.id, escalated)
```

Note: `_FAILURE_ESCALATION_THRESHOLD` as a class attribute — place it near the class's other constants (check whether `retconner.py`'s `Retconner` class already has similar class-level constants and match that placement/style).

- [ ] **Step 4: Modify the resolve path to auto-clear escalation**

Locate the method in `retconner.py` that commits `EventType.FLAG_RESOLVED` on success (referred to as `_resolve` above — confirm exact name by reading the file). Add escalation clearing there, following the same pattern:

```python
        if resolved.escalated:
            cleared = resolved.model_copy(update={"escalated": False})
            await self._committer.commit(self.name, EventType.FLAG_ESCALATION_CLEARED, req.id, cleared)
```

Insert this immediately after the existing `FLAG_RESOLVED` commit in that method, using `resolved`/`req` as whatever variable name that method already uses for the flag it just committed as resolved.

- [ ] **Step 5: Run test to verify it passes**

Run: `pytest tests/agents/test_retconner.py -v`
Expected: PASS

- [ ] **Step 6: Commit**

```bash
git add novelizer/agents/retconner.py tests/agents/test_retconner.py
git commit -m "feat: retconner tracks failed_attempts, escalates on repeated failure, auto-clears on resolve"
```

---

### Task 5: `escalations_model.py` — pure data functions for the review screen

**Files:**
- Create: `novelizer/tui/widgets/escalations_model.py`
- Test: `tests/tui/test_escalations_model.py`

**Interfaces:**
- Consumes: `ReadStore.list_flags(escalated=True)` (Task 2), `EventStore.events_for_aggregate(flag_id)` (existing, `novelizer/canon/event_store.py:123`), `StoredEvent` (existing, `novelizer/canon/events.py:65-72`).
- Produces: `async def escalated_flags(read: ReadStore) -> list[Flag]`, `async def escalation_timeline(events: EventStore, flag_id: str) -> list[TimelineEntry]` where `TimelineEntry` is a small dataclass `(event_type: str, created_at: str, summary: str)`.

- [ ] **Step 1: Write the failing test**

```python
# tests/tui/test_escalations_model.py
import pytest
from novelizer.store.models import Flag
from novelizer.canon.events import EventType
from novelizer.tui.widgets.escalations_model import escalated_flags, escalation_timeline


async def test_escalated_flags_returns_only_escalated(stack):
    events, proj, read = stack
    open_flag = Flag(id="f1", category="contradiction", description="normal")
    esc_flag = Flag(id="f2", category="contradiction", description="critical one", escalated=True, severity="critical")
    await events.append("triage", EventType.FLAG_CREATED, open_flag.id, open_flag.model_dump(mode="json"))
    await events.append("triage", EventType.FLAG_CREATED, esc_flag.id, esc_flag.model_dump(mode="json"))
    await events.append("triage", EventType.FLAG_ESCALATED, esc_flag.id, esc_flag.model_dump(mode="json"))
    await proj.catch_up()

    result = await escalated_flags(read)
    assert [f.id for f in result] == ["f2"]


async def test_escalation_timeline_orders_events(stack):
    events, proj, read = stack
    flag = Flag(id="f3", category="contradiction", description="x", escalated=True, severity="critical")
    await events.append("triage", EventType.FLAG_CREATED, flag.id, flag.model_dump(mode="json"))
    await events.append("triage", EventType.FLAG_ESCALATED, flag.id, flag.model_dump(mode="json"))
    await proj.catch_up()

    timeline = await escalation_timeline(events, flag.id)
    assert [e.event_type for e in timeline] == [EventType.FLAG_CREATED, EventType.FLAG_ESCALATED]
```

Match the `stack` fixture to whatever's already established in `tests/canon/test_flags_projection.py` (extend it to also yield the raw `EventStore` if the existing fixture doesn't already expose it — check before assuming).

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/tui/test_escalations_model.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'novelizer.tui.widgets.escalations_model'`.

- [ ] **Step 3: Write the implementation**

```python
# novelizer/tui/widgets/escalations_model.py
"""Pure data functions for the Escalations review screen. No Textual
imports — screens call these and render the results."""
from __future__ import annotations
from dataclasses import dataclass

from novelizer.canon.event_store import EventStore
from novelizer.canon.read_store import ReadStore
from novelizer.store.models import Flag


@dataclass(frozen=True)
class TimelineEntry:
    event_type: str
    created_at: str
    summary: str


_SUMMARIES = {
    "flag.created": "Flag filed",
    "flag.resolved": "Resolved",
    "flag.rejected": "Rejected",
    "flag.escalated": "Escalated",
    "flag.escalation_cleared": "Escalation cleared",
}


async def escalated_flags(read: ReadStore) -> list[Flag]:
    return await read.list_flags(escalated=True)


async def escalation_timeline(events: EventStore, flag_id: str) -> list[TimelineEntry]:
    stored = await events.events_for_aggregate(flag_id)
    return [
        TimelineEntry(
            event_type=e.event_type,
            created_at=e.created_at,
            summary=_SUMMARIES.get(e.event_type, e.event_type),
        )
        for e in stored
    ]
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/tui/test_escalations_model.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add novelizer/tui/widgets/escalations_model.py tests/tui/test_escalations_model.py
git commit -m "feat: add escalations_model pure data functions"
```

---

### Task 6: `escalations_screen.py` — TUI review screen

**Files:**
- Create: `novelizer/tui/escalations_screen.py`
- Modify: `novelizer/tui/app.py` (`BINDINGS` near line 60-74, `APP_COMMANDS` near lines 540-574, add an `_app_open_escalations` handler near the existing `_app_open_research`/other `_app_open_*` handlers)
- Test: `tests/tui/test_escalations_screen.py`

**Interfaces:**
- Consumes: `escalated_flags`, `escalation_timeline`, `TimelineEntry` (Task 5), `ReadStore`/`EventStore`/`Committer` (existing), `AppCommand` (existing, `novelizer/tui/app.py:39-46`).
- Produces: `EscalationsScreen(Screen)`, `_app_open_escalations(app: NovelizerApp) -> None`.

- [ ] **Step 1: Write the failing test**

Read `tests/tui/` for an existing screen test (e.g. one for `settings_screen.py` or `export_screen.py`, if present — check `ls tests/tui/`) to match the Textual test-harness convention (likely `Pilot` via `app.run_test()`). Write a smoke test:

```python
# tests/tui/test_escalations_screen.py
import pytest
from novelizer.tui.escalations_screen import EscalationsScreen


async def test_escalations_screen_lists_escalated_flags(read_store_with_escalated_flag):
    # read_store_with_escalated_flag: a fixture producing a ReadStore + EventStore
    # pre-populated with one escalated Flag — build this fixture following the
    # same setup used by tests/tui/test_escalations_model.py's `stack` fixture,
    # exposed as a pytest fixture if not already.
    read, events = read_store_with_escalated_flag
    screen = EscalationsScreen(read=read, events=events, committer=None)
    async with screen.app.run_test() as pilot:
        await pilot.pause()
        table = screen.query_one("#escalations-table")
        assert table.row_count == 1
```

Adjust to match whichever Textual test convention (`run_test()`, fixture composition, widget query syntax) is already established elsewhere in `tests/tui/` — read an existing screen test file in full before writing this step for real.

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/tui/test_escalations_screen.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'novelizer.tui.escalations_screen'`.

- [ ] **Step 3: Write the screen**

```python
# novelizer/tui/escalations_screen.py
from __future__ import annotations

from textual.app import ComposeResult
from textual.containers import Horizontal, Vertical
from textual.screen import Screen
from textual.widgets import DataTable, Static, Input, Button

from novelizer.canon.event_store import EventStore
from novelizer.canon.events import EventType
from novelizer.canon.read_store import ReadStore
from novelizer.store.models import Flag
from novelizer.tui.widgets.escalations_model import escalated_flags, escalation_timeline


class EscalationsScreen(Screen):
    """Review and clear escalated flags. Auto-clear on resolution happens
    upstream (owning agents); this screen is for human-initiated clears and
    for judging critical/repeatedly-failing issues with full context."""

    BINDINGS = [("escape", "dismiss_screen", "Back")]

    def __init__(self, read: ReadStore, events: EventStore, committer) -> None:
        super().__init__()
        self._read = read
        self._events = events
        self._committer = committer
        self._flags: list[Flag] = []
        self._selected: Flag | None = None

    def compose(self) -> ComposeResult:
        with Horizontal():
            with Vertical(id="escalations-list-pane"):
                yield DataTable(id="escalations-table")
            with Vertical(id="escalations-detail-pane"):
                yield Static(id="escalations-detail")
                yield Static(id="escalations-related")
                yield Input(placeholder="Clear note (optional)", id="escalations-clear-note")
                yield Button("Clear escalation", id="escalations-clear-button")

    async def on_mount(self) -> None:
        table = self.query_one("#escalations-table", DataTable)
        table.add_columns("Severity", "Category", "Description")
        await self.refresh_rows()

    async def refresh_rows(self) -> None:
        self._flags = await escalated_flags(self._read)
        table = self.query_one("#escalations-table", DataTable)
        table.clear()
        for flag in self._flags:
            table.add_row(flag.severity or "-", flag.category, flag.description[:60], key=flag.id)

    async def on_data_table_row_selected(self, event: DataTable.RowSelected) -> None:
        flag_id = event.row_key.value
        self._selected = next((f for f in self._flags if f.id == flag_id), None)
        if self._selected is None:
            return
        timeline = await escalation_timeline(self._events, flag_id)
        lines = "\n".join(f"{t.created_at}  {t.summary}" for t in timeline)
        self.query_one("#escalations-detail", Static).update(
            f"{self._selected.description}\n\nTimeline:\n{lines}"
        )
        related = ", ".join(self._selected.related_entry_ids) or "(none)"
        self.query_one("#escalations-related", Static).update(f"Related entries: {related}")

    async def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id != "escalations-clear-button" or self._selected is None:
            return
        note = self.query_one("#escalations-clear-note", Input).value or None
        cleared = self._selected.model_copy(update={"escalated": False})
        payload = cleared.model_dump(mode="json")
        payload["cleared_by"] = "human"
        payload["note"] = note
        await self._committer.commit("human", EventType.FLAG_ESCALATION_CLEARED, cleared.id, cleared)
        self._selected = None
        await self.refresh_rows()

    def action_dismiss_screen(self) -> None:
        self.app.pop_screen()
```

Note: `payload`/extra fields (`cleared_by`, `note`) built above but not used in the final `commit(...)` call — the `Committer.commit` signature (confirm in `novelizer/canon/committer.py` or wherever it's defined) takes a model instance, not a raw dict, matching every other commit call in this plan. If `Committer.commit` needs `cleared_by`/`note` persisted, those aren't fields on `Flag` per the spec (spec only requires the *event* to carry them, not the projected `Flag` row) — check how `Committer.commit` actually serializes the payload (does it call `.model_dump()` on the model, or accept a dict directly?) before finalizing this step; if it only accepts a `Flag` instance, `cleared_by`/`note` should be passed as a separate kwarg to `commit` if the method supports one, or the event payload assembled manually. Read `novelizer/canon/committer.py` in full before implementing this step to get the exact call signature right — this is the one place in the plan where the interface wasn't independently verified.

Wire the screen into `novelizer/tui/app.py`. Add an opener function near the other `_app_open_*` handlers (same pattern as `_app_open_research`):

```python
def _app_open_escalations(app: "NovelizerApp") -> None:
    app.push_screen(EscalationsScreen(read=app.read_store, events=app.event_store, committer=app.committer))
```

(Confirm the exact attribute names `app.read_store`/`app.event_store`/`app.committer` — read `NovelizerApp.__init__` in `app.py` to match whatever the app instance actually exposes; other `_app_open_*` handlers already do this, follow their exact attribute access pattern.)

Add the import at the top of `app.py`:

```python
from novelizer.tui.escalations_screen import EscalationsScreen, _app_open_escalations
```

(Adjust: only import `_app_open_escalations` if it's defined in `escalations_screen.py`; alternatively define the opener directly in `app.py` next to the other openers if that's the established convention — check whether `_app_open_research` lives in `app.py` itself or is imported, and match that.)

Add to `BINDINGS` (near line 72, alongside `talk_to_project`):

```python
    ("ctrl+e", "open_escalations", "Escalations"),
```

Add the action handler (near `action_talk_to_project` at line 380):

```python
    def action_open_escalations(self) -> None:
        _app_open_escalations(self)
```

Add to `APP_COMMANDS` (near line 548):

```python
    AppCommand("escalations", "Review escalated flags", _app_open_escalations),
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/tui/test_escalations_screen.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add novelizer/tui/escalations_screen.py novelizer/tui/app.py tests/tui/test_escalations_screen.py
git commit -m "feat: add Escalations review screen with ctrl+e binding and command palette entry"
```

---

### Task 7: Full-stack integration test

**Files:**
- Test: `tests/integration/test_flag_escalation_roundtrip.py` (new; check whether `tests/integration/` already exists — if not, follow whatever top-level integration test directory the repo already uses, e.g. `tests/e2e/`)

**Interfaces:**
- Consumes: everything from Tasks 1-4 (no TUI dependency — this test exercises the event/agent layer end to end).

- [ ] **Step 1: Write the test**

```python
# tests/integration/test_flag_escalation_roundtrip.py
async def test_critical_flag_escalates_then_resolves_and_clears(stack):
    events, proj, read, committer, runner, retconner = stack
    flag = Flag(id="f1", category="contradiction", description="x")
    await events.append("continuity_checker", EventType.FLAG_CREATED, flag.id, flag.model_dump(mode="json"))
    await proj.catch_up()

    runner.set_response({"verdict": "real", "reason": "", "reclassify_category": "", "severity": "critical", "feed_note": ""})
    triage = build_triage_runner(committer=committer, read=read, runner=runner)
    await triage._run()

    escalated = await read.list_flags(escalated=True)
    assert len(escalated) == 1 and escalated[0].severity == "critical"

    # owning agent (retconner) eventually resolves it
    current = (await read.list_flags())[0]
    await retconner._resolve(current, "amended the world entry")  # confirm exact method name per Task 4

    remaining = await read.list_flags(escalated=True)
    assert remaining == []
    resolved = (await read.list_flags())[0]
    assert resolved.status == "resolved"


async def test_repeated_failure_escalates_minor_flag(stack):
    events, proj, read, committer, runner, retconner = stack
    flag = Flag(id="f2", category="contradiction", description="x", severity="minor")
    await events.append("continuity_checker", EventType.FLAG_CREATED, flag.id, flag.model_dump(mode="json"))
    await proj.catch_up()

    for _ in range(3):
        current = (await read.list_flags())[0]
        await retconner._decline(current, "cannot_reproduce", "no evidence")
        current = current.model_copy(update={"status": "open"})  # re-open for next attempt in test setup
        await events.append("continuity_checker", EventType.FLAG_CREATED, current.id, current.model_dump(mode="json"))
        await proj.catch_up()

    escalated = await read.list_flags(escalated=True)
    assert len(escalated) == 1
```

This is a design sketch for the round trip — the exact re-open mechanics in the loop (a real system wouldn't re-file a rejected flag this way) are test scaffolding only, to exercise `_decline` three times against the same flag id without needing a live Triage/owning-agent poll loop. Adjust the fixture/loop once Task 4's actual `_decline`/resolve method names and the test `stack` fixture composition (which agents/components it yields) are confirmed by that task's real implementation.

- [ ] **Step 2: Run test to verify it passes**

Run: `pytest tests/integration/test_flag_escalation_roundtrip.py -v`
Expected: PASS

- [ ] **Step 3: Run full test suite**

Run: `pytest -x`
Expected: PASS (no regressions in `tests/agents/test_triage.py`, `tests/canon/test_flags_projection.py`, `tests/agents/test_retconner.py`, or anywhere else touching `Flag`/`EventType`).

- [ ] **Step 4: Commit**

```bash
git add tests/integration/test_flag_escalation_roundtrip.py
git commit -m "test: add flag escalation round-trip integration test"
```

---

## Self-Review Notes

- **Spec coverage:** Data model (Task 1) ✓, projector/events (Task 2) ✓, Triage severity assessment + critical escalation (Task 3) ✓, repeated-failure escalation via Retconner (Task 4, scoped to Retconner only per Global Constraints — the spec's "owning agent" language is honored for the one owning agent that has a decline path) ✓, auto-clear on resolve (Task 4) ✓, TUI review screen with timeline + related entries + clear action (Tasks 5-6) ✓, command palette + binding registration (Task 6) ✓. Out-of-scope items (Repair Planner, notifications) correctly excluded.
- **Known open item carried into Task 6, Step 3:** the exact `Committer.commit` signature for the human-clear path (whether `cleared_by`/`note` can be attached) needs verification against `novelizer/canon/committer.py` during implementation — flagged explicitly in that step rather than guessed at, since guessing wrong here would silently drop the audit note.
- **Known open item carried into Task 4:** exact method name for Retconner's resolve path (referred to as `_resolve` throughout) must be confirmed by reading `retconner.py` in full — not independently verified during plan research.
