# M4 — Second Domain: Research/Fact-Gathering Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a research/fact-gathering domain deployment on the substrate — event types, roles, and one working projection wired end-to-end against the real Postgres backend — far enough to prove the substrate generalizes to a second domain, not as a polished standalone product.

**Architecture:** Per the spec's explicit non-goal ("M4 builds the research domain only as far as needed to prove the substrate generalizes, not as a polished standalone product"), this milestone does not build real LLM-backed agents — `AgentSpec.construct` callables are trivial synchronous stubs that prove the *shape* fits a different domain's roles, not working research agents (wiring real LLM calls is out of scope; Novelizer's own agents already prove `deepagents` composes with this shape, and duplicating that proof for stub agents that don't call an LLM would test nothing new). What *is* real: the event-type registry, the gating dial, and — the actual proof of generality — a projection wired against the live `substrate.postgres` backend from M2, exercised by an integration test that appends real events and reads back a real recomputed view.

Naming: this domain's package is `research_domain/` (top-level, alongside `substrate/`), distinct from `novelizer/research/` (the existing stateless chat Q&A feature) per M0's finding that the two must not be confused.

Six roles are declared (Scout, Extractor, Verifier, Retractor, Synthesizer, Coverage Analyst) per the spec, but only enough behavior to prove `AgentSpec`/`ToolGrant`/`AgentContext` fit a non-fiction role roster — not full role logic.

One projection is built for real: the **Source Coverage Board** (how many corroborated claims trace to each source) — chosen because it's the simplest of the three the spec names (Source Coverage Board, Contradiction Map, Claim Dependency Graph) and sufficient to prove the `ProjectionCatalog` abstraction (proven against fakes in M1) now works against real Postgres-backed events. The other two projections and full role behavior are explicitly logged as substrate gaps / future work in Task 5, not built here — per the spec's own scoping ("Anything the research domain needs that the substrate doesn't provide generically is logged as a substrate gap — not worked around with a domain-specific hack").

**Tech Stack:** Python 3, `asyncpg` (already installed), the M1-M3 `substrate/` package, the M2 `substrate/postgres/` backend, pytest.

## Global Constraints

- `research_domain/` may import from `substrate/` but never from `novelizer/` — same isolation principle as `substrate/` itself, since this is meant to be a second, independent deployment, not fiction-coupled code. Verified by grep in Task 5.
- Do not build real LLM-backed agent logic — `construct` callables are synchronous stubs. Do not build the Contradiction Map or Claim Dependency Graph projections — log them as scoped-out in Task 5's completion note, per YAGNI.
- Every event type this domain registers must specify a `GatingTier`, matching the low-blast-radius-vs-rippling distinction the spec's Findings section describes for research specifically (`source.corroborated` additive/never-gated; `claim.refuted`/`claim.corrected` rippling/always-gated).

---

### Task 1: `research_domain.event_types` — the research event-type registry

**Files:**
- Create: `research_domain/__init__.py` (empty)
- Create: `research_domain/event_types.py`
- Test: `tests/research_domain/__init__.py` (empty)
- Test: `tests/research_domain/test_event_types.py`

**Interfaces:**
- Consumes: `substrate.event_registry.EventTypeRegistry`, `EventTypeSpec`, `GatingTier` (M1); `substrate.policy.is_gated` (M1).
- Produces: `research_domain.event_types.RESEARCH_TIER_ORDER` (`list[str]`, two tiers: `["auto", "reviewed"]` — matches the tier vocabulary M3's proof test used, since this is the domain that vocabulary was modeled on), `research_domain.event_types.build_research_registry() -> EventTypeRegistry` (a function, not a module-level singleton, so tests can build fresh independent instances — matching M3's proof that registries don't share state).

Event types registered (per the spec's M4 text, verbatim event names chosen to match): `claim.proposed` (tiered, `"auto"` — additive, low-stakes, a Scout/Extractor proposing a claim to investigate is safe to auto-run), `source.corroborated` (never — purely additive evidence-gathering, matches the spec's Findings section example exactly), `claim.refuted` (always — ripples to every downstream dependent per the spec's Findings section, needs human sign-off unconditionally), `claim.corrected` (tiered, `"reviewed"` — a correction that isn't an outright refutation still needs review before being trusted, but isn't as severe as a refutation).

- [ ] **Step 1: Write the failing tests**

```python
# tests/research_domain/test_event_types.py
from substrate.policy import is_gated
from research_domain.event_types import RESEARCH_TIER_ORDER, build_research_registry


def test_claim_proposed_is_gated_once_auto_tier_active():
    registry = build_research_registry()
    assert is_gated("claim.proposed", registry, RESEARCH_TIER_ORDER, current_tier_index=0) is True


def test_source_corroborated_is_never_gated():
    registry = build_research_registry()
    assert is_gated("source.corroborated", registry, RESEARCH_TIER_ORDER, current_tier_index=1) is False


def test_claim_refuted_is_always_gated():
    registry = build_research_registry()
    assert is_gated("claim.refuted", registry, RESEARCH_TIER_ORDER, current_tier_index=0) is True


def test_claim_corrected_gates_only_at_reviewed_tier():
    registry = build_research_registry()
    assert is_gated("claim.corrected", registry, RESEARCH_TIER_ORDER, current_tier_index=0) is False
    assert is_gated("claim.corrected", registry, RESEARCH_TIER_ORDER, current_tier_index=1) is True


def test_build_research_registry_returns_a_fresh_instance_each_call():
    a = build_research_registry()
    b = build_research_registry()
    assert a is not b
```

- [ ] **Step 2: Run to verify they fail**

Run: `.venv/bin/pytest tests/research_domain/test_event_types.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'research_domain'`

- [ ] **Step 3: Write the implementation**

```python
# research_domain/__init__.py
```

```python
# tests/research_domain/__init__.py
```

```python
# research_domain/event_types.py
from __future__ import annotations
from substrate.event_registry import EventTypeRegistry, EventTypeSpec, GatingTier

RESEARCH_TIER_ORDER = ["auto", "reviewed"]


def build_research_registry() -> EventTypeRegistry:
    registry = EventTypeRegistry()
    registry.register(
        EventTypeSpec(name="claim.proposed", gating_tier=GatingTier.tiered, tier_level="auto")
    )
    registry.register(EventTypeSpec(name="source.corroborated", gating_tier=GatingTier.never))
    registry.register(EventTypeSpec(name="claim.refuted", gating_tier=GatingTier.always))
    registry.register(
        EventTypeSpec(name="claim.corrected", gating_tier=GatingTier.tiered, tier_level="reviewed")
    )
    return registry
```

- [ ] **Step 4: Run to verify they pass**

Run: `.venv/bin/pytest tests/research_domain/test_event_types.py -v`
Expected: PASS (5 passed)

- [ ] **Step 5: Commit**

```bash
git add research_domain/__init__.py research_domain/event_types.py tests/research_domain/__init__.py tests/research_domain/test_event_types.py
git commit -m "feat(research-domain): declare research event types and gating tiers on the substrate registry"
```

---

### Task 2: `research_domain.roles` — the six-role roster (stub construction only)

**Files:**
- Create: `research_domain/roles.py`
- Test: `tests/research_domain/test_roles.py`

**Interfaces:**
- Consumes: `substrate.agent_registry.AgentSpec`, `ToolGrant`, `AgentContext` (M1) — read `substrate/agent_registry.py` first to confirm the exact current field names/types before writing this task's code (M1's audit found real fields `name: str`, `tool_grant: ToolGrant | None`, `construct: Callable[[AgentContext], Any]`).
- Produces: `research_domain.roles.ROLE_REGISTRY: list[AgentSpec]` — six entries named `scout`, `extractor`, `verifier`, `retractor`, `synthesizer`, `coverage_analyst`, each with `tool_grant=None` (no tool wiring in this proof) and a `construct` callable that is a plain synchronous stub returning a small marker object (not an LLM agent) — proving the registry shape accepts a non-fiction role roster, not that these roles do anything yet.

- [ ] **Step 1: Read `substrate/agent_registry.py` in full first** to confirm exact current field names before writing this task's code — do not guess.

- [ ] **Step 2: Write the failing tests**

```python
# tests/research_domain/test_roles.py
from research_domain.roles import ROLE_REGISTRY


def test_role_registry_has_six_roles_in_declared_order():
    names = [spec.name for spec in ROLE_REGISTRY]
    assert names == ["scout", "extractor", "verifier", "retractor", "synthesizer", "coverage_analyst"]


def test_every_role_construct_is_callable_and_returns_something():
    for spec in ROLE_REGISTRY:
        result = spec.construct(None)
        assert result is not None


def test_no_role_has_a_tool_grant_in_this_proof():
    assert all(spec.tool_grant is None for spec in ROLE_REGISTRY)
```

(If Step 1 reveals `AgentSpec`/`AgentContext` have different or additional
fields than assumed above, adjust this test and the implementation in Step
3 to match the real fields — the interface block above names the fields
known as of M1's audit, but confirm before relying on them.)

- [ ] **Step 3: Run to verify they fail**

Run: `.venv/bin/pytest tests/research_domain/test_roles.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'research_domain.roles'`

- [ ] **Step 4: Write the implementation** (using the real current `AgentSpec` fields confirmed in Step 1)

```python
# research_domain/roles.py
from __future__ import annotations
from substrate.agent_registry import AgentSpec


def _stub_construct(name: str):
    def _construct(ctx):
        return {"role": name, "context": ctx}
    return _construct


ROLE_REGISTRY: list[AgentSpec] = [
    AgentSpec(name="scout", tool_grant=None, construct=_stub_construct("scout")),
    AgentSpec(name="extractor", tool_grant=None, construct=_stub_construct("extractor")),
    AgentSpec(name="verifier", tool_grant=None, construct=_stub_construct("verifier")),
    AgentSpec(name="retractor", tool_grant=None, construct=_stub_construct("retractor")),
    AgentSpec(name="synthesizer", tool_grant=None, construct=_stub_construct("synthesizer")),
    AgentSpec(name="coverage_analyst", tool_grant=None, construct=_stub_construct("coverage_analyst")),
]
```

- [ ] **Step 5: Run to verify they pass**

Run: `.venv/bin/pytest tests/research_domain/test_roles.py -v`
Expected: PASS (3 passed)

- [ ] **Step 6: Commit**

```bash
git add research_domain/roles.py tests/research_domain/test_roles.py
git commit -m "feat(research-domain): declare the six-role roster on the substrate agent registry"
```

---

### Task 3: `research_domain.projections` — Source Coverage Board

**Files:**
- Create: `research_domain/projections.py`
- Test: `tests/research_domain/test_projections.py`

**Interfaces:**
- Consumes: `substrate.projection.ProjectionSpec`, `ProjectionCatalog` (M1).
- Produces: `research_domain.projections.build_source_coverage_catalog() -> ProjectionCatalog` — registers one `ProjectionSpec` named `"source_coverage"` whose `invalidation_key` extracts a source identifier from a research event dict (`event["payload"]["source_id"]`), and whose `recompute` function takes a source id and — in this task, using an **injectable lookup function** rather than hardwiring a real event-store query (that wiring is Task 4's job, exercised against real Postgres) — returns a coverage count for that source. `build_source_coverage_catalog(count_claims_for_source: Callable[[str], int]) -> ProjectionCatalog` takes the count function as a parameter, so this task's unit tests use a fake counter and Task 4's integration test passes a real one backed by `PostgresEventStore`.

- [ ] **Step 1: Write the failing tests**

```python
# tests/research_domain/test_projections.py
from research_domain.projections import build_source_coverage_catalog


class _FakeClaimEvent:
    def __init__(self, source_id: str) -> None:
        self.payload = {"source_id": source_id}


def test_invalidating_a_source_and_recomputing_returns_its_count():
    counts = {"source-a": 3, "source-b": 7}
    catalog = build_source_coverage_catalog(lambda source_id: counts[source_id])
    catalog.invalidate("source_coverage", _FakeClaimEvent(source_id="source-a"))
    result = catalog.recompute_dirty("source_coverage")
    assert result == {"source-a": 3}


def test_multiple_sources_invalidated_all_recompute():
    counts = {"source-a": 3, "source-b": 7}
    catalog = build_source_coverage_catalog(lambda source_id: counts[source_id])
    catalog.invalidate("source_coverage", _FakeClaimEvent(source_id="source-a"))
    catalog.invalidate("source_coverage", _FakeClaimEvent(source_id="source-b"))
    result = catalog.recompute_dirty("source_coverage")
    assert result == {"source-a": 3, "source-b": 7}
```

- [ ] **Step 2: Run to verify they fail**

Run: `.venv/bin/pytest tests/research_domain/test_projections.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'research_domain.projections'`

- [ ] **Step 3: Write the implementation**

```python
# research_domain/projections.py
from __future__ import annotations
from typing import Any, Callable

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
```

- [ ] **Step 4: Run to verify they pass**

Run: `.venv/bin/pytest tests/research_domain/test_projections.py -v`
Expected: PASS (2 passed)

- [ ] **Step 5: Commit**

```bash
git add research_domain/projections.py tests/research_domain/test_projections.py
git commit -m "feat(research-domain): add Source Coverage Board projection over ProjectionCatalog"
```

---

### Task 4: End-to-end integration — real events, real Postgres, real recomputed projection

**Files:**
- Test: `tests/research_domain/test_end_to_end.py`

**Interfaces:**
- Consumes: `substrate.postgres.events.PostgresEventStore` (M2), `research_domain.event_types.build_research_registry`/`RESEARCH_TIER_ORDER` (Task 1), `research_domain.projections.build_source_coverage_catalog` (Task 3), `substrate.policy.is_gated` (M1), `tests/substrate/postgres_fixture.py`'s `postgres_dsn` fixture (M2 Task 1).
- Produces: nothing new — this is the milestone's proof-of-success test, showing the whole stack composes: real events appended to Postgres, a gating check against the research registry, and a projection recomputed from a real count query against those events.

- [ ] **Step 1: Write the test**

```python
# tests/research_domain/test_end_to_end.py
import pytest

from substrate.postgres.events import PostgresEventStore
from substrate.policy import is_gated
from research_domain.event_types import build_research_registry, RESEARCH_TIER_ORDER
from research_domain.projections import build_source_coverage_catalog
from tests.substrate.postgres_fixture import postgres_dsn


class _ClaimEvent:
    def __init__(self, source_id: str) -> None:
        self.payload = {"source_id": source_id}


@pytest.mark.asyncio
async def test_research_domain_composes_events_gating_and_projection(postgres_dsn):
    store = PostgresEventStore(postgres_dsn)
    await store.connect()
    try:
        await store.append("research-stream", "claim.proposed", {"source_id": "source-a", "text": "x"})
        await store.append("research-stream", "claim.proposed", {"source_id": "source-a", "text": "y"})
        await store.append("research-stream", "claim.proposed", {"source_id": "source-b", "text": "z"})
        await store.append("research-stream", "source.corroborated", {"source_id": "source-a"})

        registry = build_research_registry()
        # A Scout's claim.proposed event is auto-runnable once the "auto" tier is active.
        assert is_gated("claim.proposed", registry, RESEARCH_TIER_ORDER, current_tier_index=0) is True
        # A corroboration is never gated -- pure additive evidence.
        assert is_gated("source.corroborated", registry, RESEARCH_TIER_ORDER, current_tier_index=1) is False

        # ProjectionCatalog.recompute (per its M1 interface) is a synchronous
        # callable, so the async count query is run once up front and the
        # catalog's recompute function is a plain dict lookup over the result
        # -- not an async call back into Postgres from inside recompute.
        rows = await store.read_stream("research-stream")
        counts_by_source: dict[str, int] = {}
        for r in rows:
            if r["event_type"] == "claim.proposed":
                sid = r["payload"]["source_id"]
                counts_by_source[sid] = counts_by_source.get(sid, 0) + 1

        catalog = build_source_coverage_catalog(lambda source_id: counts_by_source[source_id])
        catalog.invalidate("source_coverage", _ClaimEvent(source_id="source-a"))
        catalog.invalidate("source_coverage", _ClaimEvent(source_id="source-b"))
        result = catalog.recompute_dirty("source_coverage")
        assert result == {"source-a": 2, "source-b": 1}
    finally:
        await store.close()
```

- [ ] **Step 2: Run to verify it passes**

Run: `.venv/bin/pytest tests/research_domain/test_end_to_end.py -v`
Expected: PASS (1 passed). If it fails, determine whether the failure is a
test-authoring mistake (fix it) or reveals a genuine composition gap
between `substrate.postgres`, `substrate.projection`, and this domain's own
code (if so, this is exactly the kind of finding Task 5 should log as a
substrate gap, not silently patch around).

- [ ] **Step 3: Commit**

```bash
git add tests/research_domain/test_end_to_end.py
git commit -m "test(research-domain): end-to-end proof — real Postgres events, gating, and projection compose"
```

---

### Task 5: Full regression, import-boundary check, and M4 completion notes with logged gaps

**Files:**
- Modify: `docs/superpowers/specs/2026-07-20-cross-domain-substrate-design.md` (append an "M4 status" note under the M4 section only)

**Interfaces:**
- Consumes: all of Tasks 1-4.
- Produces: nothing new — verification gate plus the milestone's required gap log.

- [ ] **Step 1: Run the full existing test suite**

Run: `.venv/bin/pytest tests/ -q`
Expected: no new failures beyond the known pre-existing/load-flake set
documented in M1/M2's status notes, plus all new `tests/research_domain/*`
tests passing (5 + 3 + 2 + 1 = 11 new tests).

- [ ] **Step 2: Verify the import-boundary constraint**

Run: `grep -rn "^from novelizer\|^import novelizer" research_domain/`
Expected: no output.

- [ ] **Step 3: Append the M4 status note**

Locate `### M4 — Second domain: research/fact-gathering` in the spec and
append after its existing paragraph (find current text first, don't assume
line numbers):

```markdown

**M4 status (2026-07-21): done, scoped as the spec's own non-goal
specifies — "only as far as needed to prove the substrate generalizes."**
Built `research_domain/` (distinct from the pre-existing
`novelizer/research/` chat feature, per M0's naming-collision finding):
`event_types.py` (four event types — `claim.proposed`, `source.corroborated`,
`claim.refuted`, `claim.corrected` — registered on a fresh
`substrate.event_registry.EventTypeRegistry` with a two-tier
`auto`/`reviewed` gating scheme, exercising the exact low-blast-radius-vs-
rippling distinction the spec's Findings section predicted for research
specifically), `roles.py` (all six named roles — Scout, Extractor, Verifier,
Retractor, Synthesizer, Coverage Analyst — as `AgentSpec` entries with
synchronous stub `construct` callables, proving the agent-registry shape
fits a non-fiction roster; no real LLM-backed agent logic was built, per
this milestone's own scope), and `projections.py` (the Source Coverage
Board, built on `ProjectionCatalog`). The proof of generality is
`test_end_to_end.py`: real events appended to a live Postgres-backed
`PostgresEventStore` (the same Docker-fixture-backed instance from M2),
real gating decisions against the research registry, and a real recomputed
projection — composing M1's registry/policy/projection abstractions with
M2's Postgres backend for a domain that has nothing to do with fiction.

**Logged substrate gaps (explicitly not worked around here, per the spec's
instruction to log rather than hack):**
1. The Contradiction Map and Claim Dependency Graph projections (the other
   two the spec names for this domain) were not built — Source Coverage
   Board alone was sufficient to prove `ProjectionCatalog` composes with
   real Postgres data; building the other two would exercise the same
   abstraction again without new information.
2. `ProjectionCatalog.recompute`'s callable contract is synchronous
   (established in M1), but the natural implementation of a real
   count-from-Postgres query is async — this milestone's integration test
   worked around this by precomputing counts with one `await
   store.read_stream(...)` call before building the catalog, rather than
   making `recompute` call back into Postgres itself. A real research
   deployment recomputing projections continuously from a live event
   stream would need `ProjectionCatalog` to support async `recompute`
   callables (or a documented convention of precomputing inputs before
   invoking it, as done here) — this is a genuine substrate gap for M5 to
   pick up if a future milestone needs live recompute against an async
   store, not merely a research-domain-specific issue.
3. No real role behavior (Scout actually scouting, Verifier actually
   verifying) was built — `roles.py`'s stub `construct` callables prove the
   registry shape accepts a non-fiction roster but do not exercise whether
   `AgentContext`'s current fields (built for fiction's needs) are
   sufficient for a research role's actual runtime needs (e.g. a `Verifier`
   likely needs a different tool grant shape than any fiction role has).
   This is deferred, not solved, pending real role implementation.
```

- [ ] **Step 4: Commit**

```bash
git add docs/superpowers/specs/2026-07-20-cross-domain-substrate-design.md
git commit -m "docs(m4): record M4 completion status and logged substrate gaps"
```
