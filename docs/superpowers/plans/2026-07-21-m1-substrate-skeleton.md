# M1 — Extract the Substrate Skeleton Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Pull the domain-neutral mechanisms the M0 seam map already identified as `generic-as-is` — the autonomy-gating decision, the agent-registry shape, and a projection-with-invalidation abstraction — into a new top-level `substrate/` package behind two declared extension points (an event-type registry and a projection catalog), with fiction's existing behavior unchanged and its own test suite passing without modification.

**Architecture:** Scoped deviation from the spec's literal "new repo" instruction, recorded here rather than silently: this repo has no configured git remote (confirmed via `git remote -v` returning nothing), so a genuinely separate repository cannot be created, pushed to, or later depended on as an installable package in this environment. `substrate/` is built as a new top-level Python package *within this repo*, import-path-independent of `novelizer/` (it imports nothing from `novelizer.*`), so the eventual mechanical move to a real separate repo (once one exists) is a directory copy, not a rewrite. Everything else in the spec's M1 section is honored as written: extraction, not new mechanism; fiction's test suite is the proof of success.

Per M0's seam map, only three things are extracted this milestone, each because M0 classified their *mechanism* (not their fiction-specific data) as `generic-as-is`:
1. The event-type→gating-tier decision (`AutonomyPolicy.is_gated`) — mechanism already generic, only the four hardcoded `EventType` sets in `canon/policy.py` are fiction-specific data.
2. The agent-registry shape (`AgentSpec`/`ToolGrant`/`AgentContext`) — already domain-neutral fields per M0's audit of `registry_types.py`.
3. A new `ProjectionCatalog` abstraction, generalized from the *shared shape* M0's Projections section found between `canon_fs` (render-keyed-to-snapshot) and `kg_store`/`kg_projector` (entity/relation-keyed-to-fingerprint) — proven via unit tests against fakes modeling both shapes, not by rewiring the real `canon_fs`/`kg_store` code onto it yet. Rewiring the two existing projections to actually use the new catalog is real work with real regression risk on production fiction code; per YAGNI it is logged as a fast-follow in Task 5, not done here. M1's job is to prove the abstraction fits both shapes, not to migrate call sites.

The event store (`canon/event_store.py`) itself is **not moved** this milestone — M0 classified its core functions generic-as-is, but extracting it requires the Postgres-vs-SQLite adapter boundary M2 is building; moving it now would mean moving it twice. Logged as M2 work, not dropped.

**Tech Stack:** Python 3, pytest (existing project test runner — confirmed via `pyproject.toml`), no new dependencies.

## Global Constraints

- `substrate/` must import nothing from `novelizer.*` — verified by grep in Task 5, not just asserted. This is what makes it independently extractable later.
- Zero behavior change for fiction: `novelizer/canon/policy.py`'s `AutonomyPolicy.is_gated(agent_name, event_type)` must return identical results for every existing test case after the refactor (spec's stated M1 proof of success, verbatim: "Novelizer's existing test suite passes unchanged").
- `novelizer/agents/registry_types.py`'s current public names (`AgentSpec`, `ToolGrant`, `AgentContext`) must remain importable from that exact module path — other novelizer modules import from there today (confirmed by M0's audit) and this plan does not touch those call sites.
- Every new abstraction gets a test written first (red), confirmed failing, then made to pass (green) — per the project's standing red/green TDD principle (see memory: engineering-principles).
- Do not touch `novelizer/canon_fs/` or `novelizer/store/kg_*.py` internals in this milestone — Task 4's tests exercise the new `ProjectionCatalog` against fakes, never the real fiction projection code.

---

### Task 1: `substrate.event_registry` — EventTypeSpec and EventTypeRegistry

**Files:**
- Create: `substrate/__init__.py` (empty)
- Create: `substrate/event_registry.py`
- Test: `tests/substrate/__init__.py` (empty)
- Test: `tests/substrate/test_event_registry.py`

**Interfaces:**
- Consumes: nothing (first task)
- Produces: `substrate.event_registry.GatingTier` (a `StrEnum` with members `always`, `never`, `tiered`), `substrate.event_registry.EventTypeSpec` (frozen dataclass: `name: str`, `gating_tier: GatingTier`, `tier_level: str | None = None` — `tier_level` is only meaningful when `gating_tier == GatingTier.tiered`, naming which ordinal tier e.g. `"retcons"`/`"canon"` the event belongs to; `None` for `always`/`never`), `substrate.event_registry.EventTypeRegistry` (class with `register(spec: EventTypeSpec) -> None`, `get(name: str) -> EventTypeSpec` raising `KeyError` if unregistered, `all() -> list[EventTypeSpec]`).

- [ ] **Step 1: Write the failing tests**

```python
# tests/substrate/test_event_registry.py
import pytest
from substrate.event_registry import EventTypeRegistry, EventTypeSpec, GatingTier


def test_register_and_get_roundtrips():
    registry = EventTypeRegistry()
    spec = EventTypeSpec(name="thing.created", gating_tier=GatingTier.never)
    registry.register(spec)
    assert registry.get("thing.created") is spec


def test_get_unregistered_raises_keyerror():
    registry = EventTypeRegistry()
    with pytest.raises(KeyError):
        registry.get("nope.nope")


def test_all_returns_every_registered_spec_in_registration_order():
    registry = EventTypeRegistry()
    a = EventTypeSpec(name="a.created", gating_tier=GatingTier.always)
    b = EventTypeSpec(name="b.created", gating_tier=GatingTier.never)
    registry.register(a)
    registry.register(b)
    assert registry.all() == [a, b]


def test_tiered_spec_carries_tier_level():
    spec = EventTypeSpec(name="c.created", gating_tier=GatingTier.tiered, tier_level="canon")
    assert spec.tier_level == "canon"


def test_register_duplicate_name_raises_valueerror():
    registry = EventTypeRegistry()
    registry.register(EventTypeSpec(name="a.created", gating_tier=GatingTier.never))
    with pytest.raises(ValueError):
        registry.register(EventTypeSpec(name="a.created", gating_tier=GatingTier.always))
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/substrate/test_event_registry.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'substrate'` (or `'substrate.event_registry'`)

- [ ] **Step 3: Write the implementation**

```python
# substrate/event_registry.py
from __future__ import annotations
from dataclasses import dataclass
from enum import StrEnum


class GatingTier(StrEnum):
    always = "always"
    never = "never"
    tiered = "tiered"


@dataclass(frozen=True)
class EventTypeSpec:
    name: str
    gating_tier: GatingTier
    tier_level: str | None = None


class EventTypeRegistry:
    def __init__(self) -> None:
        self._specs: dict[str, EventTypeSpec] = {}

    def register(self, spec: EventTypeSpec) -> None:
        if spec.name in self._specs:
            raise ValueError(f"event type already registered: {spec.name}")
        self._specs[spec.name] = spec

    def get(self, name: str) -> EventTypeSpec:
        return self._specs[name]

    def all(self) -> list[EventTypeSpec]:
        return list(self._specs.values())
```

```python
# substrate/__init__.py
```

```python
# tests/substrate/__init__.py
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/substrate/test_event_registry.py -v`
Expected: PASS (5 passed)

- [ ] **Step 5: Commit**

```bash
git add substrate/__init__.py substrate/event_registry.py tests/substrate/__init__.py tests/substrate/test_event_registry.py
git commit -m "feat(substrate): add EventTypeRegistry as the event-type extension point"
```

---

### Task 2: `substrate.policy` — generic gating mechanism, and rewire fiction's policy onto it

**Files:**
- Create: `substrate/policy.py`
- Test: `tests/substrate/test_policy.py`
- Modify: `novelizer/canon/policy.py` (replace the module's body; keep the module path and the `AutonomyPolicy` class name and constructor signature identical — `novelizer/canon/committer.py` and existing tests import `AutonomyPolicy(read_store)` from this exact path)
- Test: run (not modify) `tests/canon/test_policy.py` and any other existing test file that imports `novelizer.canon.policy` — locate them with `grep -rl "canon.policy\|canon import policy" tests/` before starting, so you know every test this refactor must keep green.

**Interfaces:**
- Consumes: `substrate.event_registry.EventTypeRegistry`, `EventTypeSpec`, `GatingTier` from Task 1.
- Produces: `substrate.policy.TierRank` (an ordinal `StrEnum`-like mapping: a tiered event's `tier_level` is gated when the caller's own autonomy level index is `<=` that tier's index in a caller-supplied ordering — see below), `substrate.policy.is_gated(event_name: str, registry: EventTypeRegistry, tier_order: list[str], current_tier_index: int) -> bool`. `novelizer/canon/policy.py` keeps its existing `AutonomyPolicy` class and `is_gated(self, agent_name, event_type) -> bool` async method signature unchanged — it becomes a thin adapter that builds one module-level `EventTypeRegistry` from the current fiction event-type sets, and delegates the gating math to `substrate.policy.is_gated`.

- [ ] **Step 1: Read the current implementation and its existing tests first**

Run: `grep -rl "canon\.policy\|canon import policy" tests/` and read every matching file, plus `novelizer/canon/policy.py` and `novelizer/canon/autonomy.py` in full, before writing anything. You must reproduce their exact current pass/fail behavior — do not guess at it.

- [ ] **Step 2: Write the failing test for the generic mechanism**

```python
# tests/substrate/test_policy.py
from substrate.event_registry import EventTypeRegistry, EventTypeSpec, GatingTier
from substrate.policy import is_gated


def _registry():
    registry = EventTypeRegistry()
    registry.register(EventTypeSpec(name="always.event", gating_tier=GatingTier.always))
    registry.register(EventTypeSpec(name="never.event", gating_tier=GatingTier.never))
    registry.register(EventTypeSpec(name="retcon.event", gating_tier=GatingTier.tiered, tier_level="retcons"))
    registry.register(EventTypeSpec(name="canon.event", gating_tier=GatingTier.tiered, tier_level="canon"))
    return registry


TIER_ORDER = ["full_auto", "retcons", "canon", "all"]


def test_always_gated_regardless_of_tier_index():
    registry = _registry()
    assert is_gated("always.event", registry, TIER_ORDER, current_tier_index=0) is True
    assert is_gated("always.event", registry, TIER_ORDER, current_tier_index=3) is True


def test_never_gated_regardless_of_tier_index():
    registry = _registry()
    assert is_gated("never.event", registry, TIER_ORDER, current_tier_index=3) is False


def test_tiered_event_gated_once_current_index_reaches_its_tier():
    registry = _registry()
    # current_tier_index=0 is "full_auto" -- nothing tiered is gated yet
    assert is_gated("retcon.event", registry, TIER_ORDER, current_tier_index=0) is False
    # current_tier_index=1 is "retcons" -- retcon.event's own tier is now active
    assert is_gated("retcon.event", registry, TIER_ORDER, current_tier_index=1) is True
    # canon.event's tier ("canon") is index 2, not yet active at index 1
    assert is_gated("canon.event", registry, TIER_ORDER, current_tier_index=1) is False
    assert is_gated("canon.event", registry, TIER_ORDER, current_tier_index=2) is True


def test_tiered_event_gated_at_max_tier_index():
    registry = _registry()
    assert is_gated("retcon.event", registry, TIER_ORDER, current_tier_index=3) is True
    assert is_gated("canon.event", registry, TIER_ORDER, current_tier_index=3) is True
```

- [ ] **Step 3: Run to verify it fails**

Run: `pytest tests/substrate/test_policy.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'substrate.policy'`

- [ ] **Step 4: Write the generic implementation**

```python
# substrate/policy.py
from __future__ import annotations
from substrate.event_registry import EventTypeRegistry, GatingTier


def is_gated(
    event_name: str,
    registry: EventTypeRegistry,
    tier_order: list[str],
    current_tier_index: int,
) -> bool:
    spec = registry.get(event_name)
    if spec.gating_tier == GatingTier.always:
        return True
    if spec.gating_tier == GatingTier.never:
        return False
    tier_index = tier_order.index(spec.tier_level)
    return current_tier_index >= tier_index
```

- [ ] **Step 5: Run to verify it passes**

Run: `pytest tests/substrate/test_policy.py -v`
Expected: PASS (4 passed)

- [ ] **Step 6: Rewire `novelizer/canon/policy.py` onto the generic mechanism**

Replace the file's contents. This must preserve every existing set's
membership exactly (copy the literal event names from the current file,
verbatim — do not add, drop, or rename any) and preserve the existing
`gated_all` special-case (resolved dynamically against `_NEVER_GATED`,
per the current file's comment) by expressing it as `tier_level="all"` with
`"all"` last in `FICTION_TIER_ORDER`:

```python
# novelizer/canon/policy.py
from __future__ import annotations
from novelizer.canon.autonomy import AutonomyLevel
from novelizer.canon.events import EventType
from substrate.event_registry import EventTypeRegistry, EventTypeSpec, GatingTier
from substrate.policy import is_gated as _substrate_is_gated

_RETCON_EVENTS = {EventType.WORLD_ENTRY_SUPERSEDED, EventType.FLAG_RESOLVED}
_CANON_EVENTS = _RETCON_EVENTS | {
    EventType.WORLD_ENTRY_CREATED,
    EventType.CHARACTER_CREATED,
    EventType.CHARACTER_UPDATED,
    EventType.CHAPTER_CREATED,
    EventType.CHAPTER_STATUS_CHANGED,
    EventType.CHAPTER_REVISED,
    EventType.SECRET_REVEALED,
}
_ALWAYS_GATED = {EventType.BLUEPRINT_ADOPTED}
_NEVER_GATED = {
    EventType.BLUEPRINT_RETARGETED,
    EventType.BEAT_FULFILLED,
    EventType.CHAPTER_BRIEF_DRAFTED,
    EventType.CHAPTER_BRIEF_SUPERSEDED,
    EventType.CHAPTER_BRIEF_FULFILLED,
    EventType.DIRECTOR_SIGNAL_CREATED,
    EventType.DIRECTOR_SIGNAL_CONSUMED,
    EventType.AGENT_REMARKED,
    EventType.CHAT_USER_MESSAGED,
    EventType.CHAT_AGENT_REPLIED,
    EventType.THREAD_PLANTED,
    EventType.THREAD_TOUCHED,
    EventType.THREAD_PAID_OFF,
    EventType.THREAD_ABANDONED,
    EventType.ANNOTATION_STRUCTURE_SCORED,
    EventType.SECRET_CREATED,
    EventType.SECRET_LEARNED,
    EventType.SECRET_REFERENCED,
    EventType.CAUSAL_EDGE_DECLARED,
    EventType.CHAPTER_MINED,
    EventType.THEME_INTRODUCED,
    EventType.THEME_DEVELOPED,
    EventType.INSPIRATION_DRAWN,
    EventType.INSPIRATION_HAND_CONSUMED,
    EventType.INSPIRATION_HAND_SUPERSEDED,
    EventType.INSPIRATION_UPTAKE_RECORDED,
    EventType.PROMISE_MADE,
    EventType.PROMISE_PROGRESSED,
    EventType.PROMISE_PAID,
    EventType.PROMISE_RELEASED,
    EventType.THREAD_RESOLUTION_PLANNED,
    EventType.SECRET_REVEAL_PLANNED,
    EventType.ARC_DECLARED,
    EventType.ARC_PIVOT_PLANNED,
    EventType.ARC_ADVANCED,
    EventType.ARC_RESOLVED,
    EventType.BOOK_COMPLETED,
}

FICTION_TIER_ORDER = ["full_auto", "retcons", "canon", "all"]


def _build_fiction_registry() -> EventTypeRegistry:
    registry = EventTypeRegistry()
    all_known: set[str] = set()
    for name in _ALWAYS_GATED:
        registry.register(EventTypeSpec(name=name, gating_tier=GatingTier.always))
        all_known.add(name)
    for name in _NEVER_GATED:
        registry.register(EventTypeSpec(name=name, gating_tier=GatingTier.never))
        all_known.add(name)
    for name in _CANON_EVENTS:
        if name in all_known:
            continue
        tier = "retcons" if name in _RETCON_EVENTS else "canon"
        registry.register(EventTypeSpec(name=name, gating_tier=GatingTier.tiered, tier_level=tier))
        all_known.add(name)
    # Every other EventType constant not explicitly bucketed above falls
    # under the dynamic gated_all catch-all the original module documented
    # ("gated_all is resolved dynamically in is_gated: everything not in
    # _NEVER_GATED"). Register the rest at tier_level="all" so they are
    # gated only once current_tier_index reaches gated_all.
    for name in vars(EventType).values():
        if not isinstance(name, str) or name in all_known:
            continue
        registry.register(EventTypeSpec(name=name, gating_tier=GatingTier.tiered, tier_level="all"))
        all_known.add(name)
    return registry


_FICTION_REGISTRY = _build_fiction_registry()

_LEVEL_TO_TIER_INDEX = {
    AutonomyLevel.full_auto: 0,
    AutonomyLevel.gated_retcons: 1,
    AutonomyLevel.gated_canon: 2,
    AutonomyLevel.gated_all: 3,
}


class AutonomyPolicy:
    """Reads the live AutonomyState from canon and decides what an agent may commit directly."""

    def __init__(self, read_store) -> None:
        self._read = read_store

    async def is_gated(self, agent_name: str, event_type: str) -> bool:
        state = await self._read.get_autonomy_state()
        level = state.level_for(agent_name)
        return _substrate_is_gated(
            event_type, _FICTION_REGISTRY, FICTION_TIER_ORDER, _LEVEL_TO_TIER_INDEX[level]
        )
```

- [ ] **Step 7: Run the fiction test files located in Step 1, plus the full canon test suite, to verify unchanged behavior**

Run: `pytest tests/canon/ -v`
Expected: PASS, same pass count as before this task started (record the
count from a `pytest tests/canon/ -v` run *before* Step 6's edit if you
have not already, so you have a before/after number to compare).

- [ ] **Step 8: Commit**

```bash
git add substrate/policy.py tests/substrate/test_policy.py novelizer/canon/policy.py
git commit -m "feat(substrate): generalize per-event-type gating; rewire fiction policy onto it"
```

---

### Task 3: Move `AgentSpec`/`ToolGrant`/`AgentContext` into `substrate.agent_registry`

**Files:**
- Create: `substrate/agent_registry.py`
- Test: `tests/substrate/test_agent_registry.py`
- Modify: `novelizer/agents/registry_types.py` (replace body with a re-export)
- Test: run (not modify) any existing test file importing from `novelizer.agents.registry_types` — locate with `grep -rl "registry_types" tests/ novelizer/` first.

**Interfaces:**
- Consumes: nothing new from earlier tasks (independent of Tasks 1-2's registry/policy).
- Produces: `substrate.agent_registry.AgentSpec`, `ToolGrant`, `AgentContext` — read the *current* `novelizer/agents/registry_types.py` first (M0's Task 3 audit found the real current fields are `name: str`, `tool_grant: ToolGrant | None`, `construct: Callable[[AgentContext], Any]` — fewer/different than an older design doc assumed) and copy its actual current class bodies verbatim into the new module; do not invent fields not present in the real file.

- [ ] **Step 1: Read the current `novelizer/agents/registry_types.py` in full** before writing anything — copy its real current field list, do not rely on this plan's summary of it.

- [ ] **Step 2: Write the failing test**

```python
# tests/substrate/test_agent_registry.py
from substrate.agent_registry import AgentSpec, ToolGrant, AgentContext


def test_agent_spec_is_constructible_with_no_fiction_specific_fields():
    def _construct(ctx):
        return object()

    spec = AgentSpec(name="scout", tool_grant=None, construct=_construct)
    assert spec.name == "scout"
    assert spec.tool_grant is None
    assert spec.construct is _construct
```

(Extend this test with one assertion per additional field/method you find
on `ToolGrant` and `AgentContext` in Step 1 — e.g. if `ToolGrant.is_enabled`
exists, add a test constructing a `ToolGrant` and calling it against a fake
settings object, mirroring whatever the current `registry_types.py` test
coverage already exercises for it. Locate that existing coverage via
`grep -rl "ToolGrant\|AgentContext" tests/` before writing this step.)

- [ ] **Step 3: Run to verify it fails**

Run: `pytest tests/substrate/test_agent_registry.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'substrate.agent_registry'`

- [ ] **Step 4: Write `substrate/agent_registry.py`** by copying the *exact current* class bodies you read in Step 1 verbatim (dataclass fields, methods, docstrings) — this step's code block cannot be written until Step 1 is done, since the plan intentionally does not guess the current field list.

- [ ] **Step 5: Run to verify it passes**

Run: `pytest tests/substrate/test_agent_registry.py -v`
Expected: PASS

- [ ] **Step 6: Replace `novelizer/agents/registry_types.py` with a re-export**

```python
# novelizer/agents/registry_types.py
from __future__ import annotations
from substrate.agent_registry import AgentSpec, ToolGrant, AgentContext

__all__ = ["AgentSpec", "ToolGrant", "AgentContext"]
```

- [ ] **Step 7: Run every test file located in Step 1's grep (import-site tests) plus the full agents test suite**

Run: `pytest tests/agents/ -v`
Expected: PASS, same pass count as a baseline run taken before Step 6's edit.

- [ ] **Step 8: Commit**

```bash
git add substrate/agent_registry.py tests/substrate/test_agent_registry.py novelizer/agents/registry_types.py
git commit -m "feat(substrate): move AgentSpec/ToolGrant/AgentContext to substrate.agent_registry"
```

---

### Task 4: `substrate.projection` — ProjectionCatalog abstraction (proven against fakes, not wired to real code)

**Files:**
- Create: `substrate/projection.py`
- Test: `tests/substrate/test_projection.py`

**Interfaces:**
- Consumes: nothing from earlier tasks (independent).
- Produces: `substrate.projection.ProjectionSpec` (frozen dataclass: `name: str`, `invalidation_key: Callable[[Any], str]` — given a source event, returns the key an invalidation is filed under (e.g. an `event_fingerprint` string, matching `kg_store`'s actual mechanism per M0's audit), `recompute: Callable[[str], Any]` — given an invalidation key, returns the recomputed view for that key), `substrate.projection.ProjectionCatalog` (class with `register(spec: ProjectionSpec) -> None`, `invalidate(projection_name: str, source_event: Any) -> None` — computes the key via the named spec's `invalidation_key` and records it as dirty, `recompute_dirty(projection_name: str) -> dict[str, Any]` — calls `recompute` for every key marked dirty since the last call, clears dirtiness, returns `{key: recomputed_view}`).

- [ ] **Step 1: Write the failing tests, modeling both real projection shapes as fakes**

```python
# tests/substrate/test_projection.py
import pytest
from substrate.projection import ProjectionCatalog, ProjectionSpec


class _FakeEvent:
    def __init__(self, fingerprint: str, chapter_id: str) -> None:
        self.fingerprint = fingerprint
        self.chapter_id = chapter_id


def test_register_and_invalidate_marks_key_dirty_for_recompute():
    catalog = ProjectionCatalog()
    recomputed_calls = []

    def _recompute(key: str):
        recomputed_calls.append(key)
        return f"view-for-{key}"

    catalog.register(ProjectionSpec(
        name="kg_shape",
        invalidation_key=lambda event: event.fingerprint,
        recompute=_recompute,
    ))
    catalog.invalidate("kg_shape", _FakeEvent(fingerprint="fp-1", chapter_id="ch-1"))
    result = catalog.recompute_dirty("kg_shape")
    assert result == {"fp-1": "view-for-fp-1"}
    assert recomputed_calls == ["fp-1"]


def test_recompute_dirty_clears_dirtiness_so_second_call_is_empty():
    catalog = ProjectionCatalog()
    catalog.register(ProjectionSpec(
        name="canon_fs_shape",
        invalidation_key=lambda event: event.chapter_id,
        recompute=lambda key: f"render-of-{key}",
    ))
    catalog.invalidate("canon_fs_shape", _FakeEvent(fingerprint="fp-2", chapter_id="ch-2"))
    first = catalog.recompute_dirty("canon_fs_shape")
    second = catalog.recompute_dirty("canon_fs_shape")
    assert first == {"ch-2": "render-of-ch-2"}
    assert second == {}


def test_two_invalidations_of_same_key_only_recompute_once():
    catalog = ProjectionCatalog()
    calls = []
    catalog.register(ProjectionSpec(
        name="canon_fs_shape",
        invalidation_key=lambda event: event.chapter_id,
        recompute=lambda key: calls.append(key) or f"render-of-{key}",
    ))
    catalog.invalidate("canon_fs_shape", _FakeEvent(fingerprint="fp-3", chapter_id="ch-3"))
    catalog.invalidate("canon_fs_shape", _FakeEvent(fingerprint="fp-4", chapter_id="ch-3"))
    result = catalog.recompute_dirty("canon_fs_shape")
    assert result == {"ch-3": "render-of-ch-3"}
    assert calls == ["ch-3"]


def test_invalidate_unregistered_projection_raises_keyerror():
    catalog = ProjectionCatalog()
    with pytest.raises(KeyError):
        catalog.invalidate("nope", _FakeEvent(fingerprint="fp-5", chapter_id="ch-5"))


def test_two_projections_track_dirtiness_independently():
    catalog = ProjectionCatalog()
    catalog.register(ProjectionSpec(
        name="kg_shape", invalidation_key=lambda e: e.fingerprint, recompute=lambda k: k,
    ))
    catalog.register(ProjectionSpec(
        name="canon_fs_shape", invalidation_key=lambda e: e.chapter_id, recompute=lambda k: k,
    ))
    catalog.invalidate("kg_shape", _FakeEvent(fingerprint="fp-6", chapter_id="ch-6"))
    assert catalog.recompute_dirty("canon_fs_shape") == {}
    assert catalog.recompute_dirty("kg_shape") == {"fp-6": "fp-6"}
```

- [ ] **Step 2: Run to verify they fail**

Run: `pytest tests/substrate/test_projection.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'substrate.projection'`

- [ ] **Step 3: Write the implementation**

```python
# substrate/projection.py
from __future__ import annotations
from dataclasses import dataclass
from typing import Any, Callable


@dataclass(frozen=True)
class ProjectionSpec:
    name: str
    invalidation_key: Callable[[Any], str]
    recompute: Callable[[str], Any]


class ProjectionCatalog:
    def __init__(self) -> None:
        self._specs: dict[str, ProjectionSpec] = {}
        self._dirty: dict[str, set[str]] = {}

    def register(self, spec: ProjectionSpec) -> None:
        self._specs[spec.name] = spec
        self._dirty[spec.name] = set()

    def invalidate(self, projection_name: str, source_event: Any) -> None:
        spec = self._specs[projection_name]
        key = spec.invalidation_key(source_event)
        self._dirty[projection_name].add(key)

    def recompute_dirty(self, projection_name: str) -> dict[str, Any]:
        spec = self._specs[projection_name]
        keys = self._dirty[projection_name]
        result = {key: spec.recompute(key) for key in keys}
        self._dirty[projection_name] = set()
        return result
```

- [ ] **Step 4: Run to verify they pass**

Run: `pytest tests/substrate/test_projection.py -v`
Expected: PASS (5 passed)

- [ ] **Step 5: Commit**

```bash
git add substrate/projection.py tests/substrate/test_projection.py
git commit -m "feat(substrate): add ProjectionCatalog, proven against canon_fs- and kg_store-shaped fakes"
```

---

### Task 5: Full-suite regression run, import-boundary check, and M1 completion notes

**Files:**
- Modify: `docs/superpowers/specs/2026-07-20-cross-domain-substrate-design.md` (append a short "M1 status" note under the M1 milestone section only — do not rewrite other sections)

**Interfaces:**
- Consumes: all of Tasks 1-4's committed code.
- Produces: nothing new — this is the milestone's verification gate.

- [ ] **Step 1: Run the full existing test suite**

Run: `pytest -v`
Expected: PASS, with the same total pass count as a `pytest -v` run taken
at the start of this milestone (before Task 1's first commit) plus exactly
the new `tests/substrate/*` tests added in Tasks 1-4. Zero fiction test
regressions — if any existing test outside `tests/substrate/` now fails,
that is a blocking defect in this milestone's rewiring (Task 2 or Task 3),
not an acceptable side effect.

- [ ] **Step 2: Verify the import-boundary constraint mechanically, not by inspection**

Run: `grep -rn "^from novelizer\|^import novelizer" substrate/`
Expected: no output (empty). If anything matches, `substrate/` depends on
`novelizer/`, which violates this milestone's Global Constraints — fix by
removing the offending import before proceeding.

- [ ] **Step 3: Append the M1 status note to the spec**

Locate the `### M1 — Extract the substrate skeleton` section in
`docs/superpowers/specs/2026-07-20-cross-domain-substrate-design.md` and
append this paragraph immediately after its existing text (do not replace
the existing text):

```markdown

**M1 status (2026-07-21): done, with two logged fast-follows.** Extracted:
the event-type registry (`substrate/event_registry.py`), the generic
per-event-type gating mechanism (`substrate/policy.py`, with fiction's
`canon/policy.py` rewired onto it as a thin adapter — zero behavior change,
full fiction test suite green), the agent-registry shape
(`substrate/agent_registry.py`, with `novelizer/agents/registry_types.py`
reduced to a re-export), and a `ProjectionCatalog` abstraction
(`substrate/projection.py`) proven against fakes modeling both `canon_fs`'s
and `kg_store`'s real invalidation shapes. **Not done, logged as fast-follow
work for a future milestone:** (1) rewiring the real `canon_fs`/`kg_store`
projection code to actually use `ProjectionCatalog` instead of their current
bespoke recompute paths — M1 proved the abstraction fits, migrating the
call sites is separate, riskier work against production fiction code; (2)
moving the event store (`canon/event_store.py`) into `substrate/` — deferred
to M2, since its extraction is entangled with the Postgres-vs-SQLite adapter
boundary M2 is building, and moving it twice would be wasted work; (3) the
literal separate-repo split the original spec text calls for — this repo has
no configured git remote, so `substrate/` was built as an import-independent
top-level package within this repo instead (verified via Task 5's grep: it
imports nothing from `novelizer.*`), ready for a mechanical directory copy
whenever a real separate repo exists.
```

- [ ] **Step 4: Commit**

```bash
git add docs/superpowers/specs/2026-07-20-cross-domain-substrate-design.md
git commit -m "docs(m1): record M1 completion status and logged fast-follows"
```
