# Research Domain — Contradiction Map & Claim Dependency Graph Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Give claims a real identity (`claim_id`) and a real edge shape (`target_claim_id`) so the two projections M4 logged as gaps — Contradiction Map and Claim Dependency Graph — can be built and tested end-to-end against Postgres.

**Architecture:** Add pydantic payload schemas in a new `research_domain/events.py` (mirrors `novelizer/canon/events.py`'s convention). Add two new `ProjectionSpec`-based catalogs in `research_domain/projections.py`, following the exact "precompute outside, dict-lookup inside" pattern the existing `build_source_coverage_catalog` already uses. No changes to `substrate/` — the primitives (`ProjectionCatalog`, `EventTypeRegistry`) already suffice.

**Tech Stack:** Python, pydantic 2.12, pytest + pytest-asyncio (`asyncio_mode = "auto"`), Postgres via Docker fixture (`tests/substrate/postgres_fixture.py`).

## Global Constraints

- No changes to `substrate/` (spec non-goal — substrate primitives already generalize).
- No changes to `research_domain/roles.py` (spec non-goal — "projections first, roles later").
- No cycle-detection, topological sort, UI, or visualization (spec non-goals — raw edge lists only).
- Payload schemas use `pydantic.BaseModel`, matching `novelizer/canon/events.py`'s existing convention.
- `ClaimRefuted` and `ClaimCorrected` are identical in shape (`claim_id`, `target_claim_id`, `reason`) — the distinction is which event type fired, not the payload.
- Projections follow the existing `ProjectionSpec(name, invalidation_key, recompute)` shape from `substrate/projection.py` — do not modify that file.

---

### Task 1: Claim payload schemas

**Files:**
- Create: `research_domain/events.py`
- Test: `tests/research_domain/test_events.py`

**Interfaces:**
- Produces: `ClaimProposed(claim_id: str, source_id: str, text: str)`, `SourceCorroborated(source_id: str, claim_id: str)`, `ClaimRefuted(claim_id: str, target_claim_id: str, reason: str)`, `ClaimCorrected(claim_id: str, target_claim_id: str, reason: str)` — all `pydantic.BaseModel` subclasses, importable from `research_domain.events`.

- [ ] **Step 1: Write the failing tests**

Create `tests/research_domain/test_events.py`:

```python
import pytest
from pydantic import ValidationError

from research_domain.events import (
    ClaimProposed,
    SourceCorroborated,
    ClaimRefuted,
    ClaimCorrected,
)


def test_claim_proposed_requires_claim_id_source_id_text():
    event = ClaimProposed(claim_id="claim-1", source_id="source-a", text="x")
    assert event.claim_id == "claim-1"
    assert event.source_id == "source-a"
    assert event.text == "x"


def test_claim_proposed_missing_field_raises():
    with pytest.raises(ValidationError):
        ClaimProposed(claim_id="claim-1", source_id="source-a")


def test_source_corroborated_requires_source_id_claim_id():
    event = SourceCorroborated(source_id="source-a", claim_id="claim-1")
    assert event.source_id == "source-a"
    assert event.claim_id == "claim-1"


def test_claim_refuted_requires_claim_id_target_claim_id_reason():
    event = ClaimRefuted(claim_id="claim-2", target_claim_id="claim-1", reason="contradicted by source-b")
    assert event.claim_id == "claim-2"
    assert event.target_claim_id == "claim-1"
    assert event.reason == "contradicted by source-b"


def test_claim_corrected_requires_claim_id_target_claim_id_reason():
    event = ClaimCorrected(claim_id="claim-3", target_claim_id="claim-1", reason="superseded with better data")
    assert event.claim_id == "claim-3"
    assert event.target_claim_id == "claim-1"
    assert event.reason == "superseded with better data"


def test_claim_refuted_missing_target_claim_id_raises():
    with pytest.raises(ValidationError):
        ClaimRefuted(claim_id="claim-2", reason="x")
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/research_domain/test_events.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'research_domain.events'`

- [ ] **Step 3: Write minimal implementation**

Create `research_domain/events.py`:

```python
from __future__ import annotations
from pydantic import BaseModel


class ClaimProposed(BaseModel):
    """Payload for claim.proposed — mints a new claim's identity."""

    claim_id: str
    source_id: str
    text: str


class SourceCorroborated(BaseModel):
    """Payload for source.corroborated — additive evidence for an existing claim."""

    source_id: str
    claim_id: str


class ClaimRefuted(BaseModel):
    """Payload for claim.refuted — claim_id contradicts target_claim_id."""

    claim_id: str
    target_claim_id: str
    reason: str


class ClaimCorrected(BaseModel):
    """Payload for claim.corrected — claim_id supersedes target_claim_id.

    Same shape as ClaimRefuted; the distinction is which event type fired.
    """

    claim_id: str
    target_claim_id: str
    reason: str
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/research_domain/test_events.py -v`
Expected: PASS (6 tests)

- [ ] **Step 5: Commit**

```bash
git add research_domain/events.py tests/research_domain/test_events.py
git commit -m "feat(research-domain): add claim payload schemas with claim_id/target_claim_id"
```

---

### Task 2: Contradiction Map and Claim Dependency Graph projections (in-memory tests)

**Files:**
- Modify: `research_domain/projections.py`
- Test: `tests/research_domain/test_projections.py`

**Interfaces:**
- Consumes: nothing from Task 1 directly (the catalog builders take plain `Callable[[str], list[str]]` edge-lookup functions, same style as `build_source_coverage_catalog`'s `Callable[[str], int]` — callers supply the lookup, the payload schemas from Task 1 are only used by whoever builds that lookup, in Task 3).
- Produces: `build_contradiction_map_catalog(edges_for_claim: Callable[[str], list[str]]) -> ProjectionCatalog` registering a `"contradiction_map"` projection; `build_claim_dependency_catalog(edges_for_claim: Callable[[str], list[str]]) -> ProjectionCatalog` registering a `"claim_dependency_graph"` projection. Both use `invalidation_key=lambda event: event.payload["target_claim_id"]` (the projection is keyed by the claim being acted on, matching the existing `source_coverage` pattern of keying by the event's subject).

- [ ] **Step 1: Write the failing tests**

Append to `tests/research_domain/test_projections.py`:

```python
from research_domain.projections import (
    build_contradiction_map_catalog,
    build_claim_dependency_catalog,
)


class _FakeRefutationEvent:
    def __init__(self, target_claim_id: str) -> None:
        self.payload = {"target_claim_id": target_claim_id}


@pytest.mark.asyncio
async def test_contradiction_map_returns_refuting_claims_for_target():
    edges = {"claim-1": ["claim-2", "claim-3"]}
    catalog = build_contradiction_map_catalog(lambda claim_id: edges[claim_id])
    catalog.invalidate("contradiction_map", _FakeRefutationEvent(target_claim_id="claim-1"))
    result = await catalog.recompute_dirty("contradiction_map")
    assert result == {"claim-1": ["claim-2", "claim-3"]}


@pytest.mark.asyncio
async def test_contradiction_map_multiple_targets_invalidated_all_recompute():
    edges = {"claim-1": ["claim-2"], "claim-5": ["claim-6"]}
    catalog = build_contradiction_map_catalog(lambda claim_id: edges[claim_id])
    catalog.invalidate("contradiction_map", _FakeRefutationEvent(target_claim_id="claim-1"))
    catalog.invalidate("contradiction_map", _FakeRefutationEvent(target_claim_id="claim-5"))
    result = await catalog.recompute_dirty("contradiction_map")
    assert result == {"claim-1": ["claim-2"], "claim-5": ["claim-6"]}


@pytest.mark.asyncio
async def test_claim_dependency_graph_returns_superseding_claims_for_target():
    edges = {"claim-1": ["claim-4"]}
    catalog = build_claim_dependency_catalog(lambda claim_id: edges[claim_id])
    catalog.invalidate("claim_dependency_graph", _FakeRefutationEvent(target_claim_id="claim-1"))
    result = await catalog.recompute_dirty("claim_dependency_graph")
    assert result == {"claim-1": ["claim-4"]}


@pytest.mark.asyncio
async def test_claim_dependency_graph_and_contradiction_map_are_independent_catalogs():
    contradiction_edges = {"claim-1": ["claim-2"]}
    dependency_edges = {"claim-1": ["claim-9"]}
    contradiction_catalog = build_contradiction_map_catalog(lambda cid: contradiction_edges[cid])
    dependency_catalog = build_claim_dependency_catalog(lambda cid: dependency_edges[cid])
    contradiction_catalog.invalidate("contradiction_map", _FakeRefutationEvent(target_claim_id="claim-1"))
    dependency_catalog.invalidate("claim_dependency_graph", _FakeRefutationEvent(target_claim_id="claim-1"))
    contradiction_result = await contradiction_catalog.recompute_dirty("contradiction_map")
    dependency_result = await dependency_catalog.recompute_dirty("claim_dependency_graph")
    assert contradiction_result == {"claim-1": ["claim-2"]}
    assert dependency_result == {"claim-1": ["claim-9"]}
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/research_domain/test_projections.py -v`
Expected: FAIL with `ImportError: cannot import name 'build_contradiction_map_catalog'`

- [ ] **Step 3: Write minimal implementation**

Replace the full contents of `research_domain/projections.py` with:

```python
from __future__ import annotations
from typing import Callable

from substrate.projection import ProjectionCatalog, ProjectionSpec


def build_source_coverage_catalog(count_claims_for_source: Callable[[str], int]) -> ProjectionCatalog:
    catalog = ProjectionCatalog()
    catalog.register(
        ProjectionSpec(
            name="source_coverage",
            invalidation_key=lambda event: event.payload["source_id"],
            recompute=count_claims_for_source,
        )
    )
    return catalog


def build_contradiction_map_catalog(
    refuting_claims_for_target: Callable[[str], list[str]]
) -> ProjectionCatalog:
    catalog = ProjectionCatalog()
    catalog.register(
        ProjectionSpec(
            name="contradiction_map",
            invalidation_key=lambda event: event.payload["target_claim_id"],
            recompute=refuting_claims_for_target,
        )
    )
    return catalog


def build_claim_dependency_catalog(
    superseding_claims_for_target: Callable[[str], list[str]]
) -> ProjectionCatalog:
    catalog = ProjectionCatalog()
    catalog.register(
        ProjectionSpec(
            name="claim_dependency_graph",
            invalidation_key=lambda event: event.payload["target_claim_id"],
            recompute=superseding_claims_for_target,
        )
    )
    return catalog
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/research_domain/test_projections.py -v`
Expected: PASS (6 tests: 2 pre-existing `source_coverage` tests + 4 new)

- [ ] **Step 5: Commit**

```bash
git add research_domain/projections.py tests/research_domain/test_projections.py
git commit -m "feat(research-domain): add Contradiction Map and Claim Dependency Graph projections"
```

---

### Task 3: End-to-end Postgres proof

**Files:**
- Modify: `tests/research_domain/test_end_to_end.py`

**Interfaces:**
- Consumes: `ClaimProposed`, `ClaimRefuted`, `ClaimCorrected` from `research_domain.events` (Task 1); `build_contradiction_map_catalog`, `build_claim_dependency_catalog` from `research_domain.projections` (Task 2); `PostgresEventStore.append(stream, event_type, payload)` / `.read_stream(stream)` (existing, used unchanged).

- [ ] **Step 1: Write the failing test**

Append to `tests/research_domain/test_end_to_end.py` (keep the existing `test_research_domain_composes_events_gating_and_projection` test and `_ClaimEvent` helper untouched; add below them):

```python
from research_domain.events import ClaimProposed, ClaimRefuted, ClaimCorrected
from research_domain.projections import (
    build_contradiction_map_catalog,
    build_claim_dependency_catalog,
)


class _TargetClaimEvent:
    def __init__(self, target_claim_id: str) -> None:
        self.payload = {"target_claim_id": target_claim_id}


@pytest.mark.asyncio
async def test_contradiction_map_and_dependency_graph_recompute_from_real_postgres_events(postgres_dsn):
    store = PostgresEventStore(postgres_dsn)
    await store.connect()
    try:
        await store.append(
            "research-stream", "claim.proposed",
            ClaimProposed(claim_id="claim-1", source_id="source-a", text="the sky is green").model_dump(),
        )
        await store.append(
            "research-stream", "claim.proposed",
            ClaimProposed(claim_id="claim-2", source_id="source-b", text="the sky is blue").model_dump(),
        )
        await store.append(
            "research-stream", "claim.refuted",
            ClaimRefuted(claim_id="claim-2", target_claim_id="claim-1", reason="source-b directly observed").model_dump(),
        )
        await store.append(
            "research-stream", "claim.proposed",
            ClaimProposed(claim_id="claim-3", source_id="source-c", text="the sky is blue at noon").model_dump(),
        )
        await store.append(
            "research-stream", "claim.corrected",
            ClaimCorrected(claim_id="claim-3", target_claim_id="claim-2", reason="time-of-day qualifier added").model_dump(),
        )

        rows = await store.read_stream("research-stream")

        refuters_by_target: dict[str, list[str]] = {}
        superseders_by_target: dict[str, list[str]] = {}
        for r in rows:
            if r["event_type"] == "claim.refuted":
                tid = r["payload"]["target_claim_id"]
                refuters_by_target.setdefault(tid, []).append(r["payload"]["claim_id"])
            elif r["event_type"] == "claim.corrected":
                tid = r["payload"]["target_claim_id"]
                superseders_by_target.setdefault(tid, []).append(r["payload"]["claim_id"])

        contradiction_catalog = build_contradiction_map_catalog(lambda cid: refuters_by_target[cid])
        contradiction_catalog.invalidate("contradiction_map", _TargetClaimEvent(target_claim_id="claim-1"))
        contradiction_result = await contradiction_catalog.recompute_dirty("contradiction_map")
        assert contradiction_result == {"claim-1": ["claim-2"]}

        dependency_catalog = build_claim_dependency_catalog(lambda cid: superseders_by_target[cid])
        dependency_catalog.invalidate("claim_dependency_graph", _TargetClaimEvent(target_claim_id="claim-2"))
        dependency_result = await dependency_catalog.recompute_dirty("claim_dependency_graph")
        assert dependency_result == {"claim-2": ["claim-3"]}
    finally:
        await store.close()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/research_domain/test_end_to_end.py -v`
Expected: FAIL — `ImportError` if Task 1/2 aren't merged into this branch's working tree yet, or (if they are) FAIL on assertion until the append/read wiring above is confirmed correct. Since Tasks 1 and 2 are already complete by this point in the plan, the realistic first-run failure is a fixture/skip: if Docker isn't available, `pytest.skip("docker not available in this environment")` — treat that as an environment gap, not a task failure, and note it in the task's completion report; do not mark the step done without having actually seen PASS in an environment where Docker is available.

- [ ] **Step 3: Confirm implementation (no new production code — this task is test-only)**

No implementation changes are needed for this task; `research_domain/events.py` and `research_domain/projections.py` from Tasks 1–2 already provide everything this test uses. If the test fails for a reason other than missing Docker, re-check the payload field names against Task 1's schemas and the projection names/invalidation keys against Task 2's implementation before changing either file.

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/research_domain/test_end_to_end.py -v`
Expected: PASS (2 tests: the pre-existing `test_research_domain_composes_events_gating_and_projection` + the new one), in an environment with Docker available. If Docker is unavailable, both tests report SKIPPED — this is expected and matches the existing test's behavior, not a new gap.

- [ ] **Step 5: Commit**

```bash
git add tests/research_domain/test_end_to_end.py
git commit -m "test(research-domain): prove Contradiction Map and Claim Dependency Graph against real Postgres events"
```

---

### Task 4: Full suite regression check and spec status update

**Files:**
- Modify: `docs/superpowers/specs/2026-07-21-research-domain-contradiction-dependency-design.md`

**Interfaces:** None — this task only runs the suite and updates spec status.

- [ ] **Step 1: Run the full research_domain test suite**

Run: `pytest tests/research_domain/ -v`
Expected: PASS (all tests across `test_event_types.py`, `test_events.py`, `test_projections.py`, `test_roles.py`, `test_end_to_end.py`) or SKIPPED for the Postgres-backed test if Docker is unavailable — zero FAILED.

- [ ] **Step 2: Run the full project suite to confirm no regressions**

Run: `pytest tests/ -q`
Expected: same pass/fail/deselect counts as the pre-existing baseline noted in the spec's parent milestone (M5 status: 1985 passed, 6 failed, 7 deselected, all 6 failures pre-existing/unrelated) plus this plan's new tests passing — no *new* failures. If the failure count differs from that baseline by anything other than this plan's new tests, stop and investigate before proceeding (per the project's standing "no milestone is done without its stated proof passing" rule).

- [ ] **Step 3: Update spec status**

In `docs/superpowers/specs/2026-07-21-research-domain-contradiction-dependency-design.md`, change:

```markdown
Status: approved, not yet implemented.
```

to:

```markdown
Status: implemented (2026-07-21).
```

- [ ] **Step 4: Commit**

```bash
git add docs/superpowers/specs/2026-07-21-research-domain-contradiction-dependency-design.md
git commit -m "docs(research-domain): mark Contradiction Map / Claim Dependency Graph spec implemented"
```
