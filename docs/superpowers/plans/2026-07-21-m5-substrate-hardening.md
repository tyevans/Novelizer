# M5 — Substrate Hardening Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Fold M4's actionable logged gap back into the substrate, and make (and record) the explicit decision on whether a third domain is worth building next.

**Architecture:** Of M4's three logged gaps, only one is a genuine substrate defect worth hardening: gap #2, `ProjectionCatalog.recompute`'s synchronous-only contract. Gaps #1 (Contradiction Map/Claim Dependency Graph not built) and #3 (no real role behavior/AgentContext sufficiency) are scope notes about work deliberately not done, not defects in what *was* built — per YAGNI, "hardening" them would mean building the very research-domain features M4's non-goals excluded, which is out of scope for a substrate milestone. This milestone's Task 1 makes `ProjectionCatalog.recompute_dirty` accept either sync or async `recompute` callables, backward-compatible with every existing caller (M1's fake-based tests, M4's research domain). Task 2 is the spec's own required decision point — not a code task — recorded as a spec update. Task 3 is final verification across the whole six-milestone arc.

**Tech Stack:** Python 3, `inspect.iscoroutinefunction` (stdlib, no new dependency), pytest.

## Global Constraints

- The fix must be backward compatible: every existing `ProjectionCatalog` test from M1 (`tests/substrate/test_projection.py`, sync `recompute` callables) and M4 (`research_domain/projections.py`, also currently sync) must keep passing unmodified.
- `substrate/` continues to import nothing from `novelizer.*`.
- No new projections, no new domain, no new role behavior is built this milestone — those are explicitly out of scope, per the Architecture note above.

---

### Task 1: Async-capable `ProjectionCatalog.recompute_dirty`

**Files:**
- Modify: `substrate/projection.py`
- Test: `tests/substrate/test_projection.py` (add new tests; keep all 5 existing ones passing unmodified)

**Interfaces:**
- Consumes: nothing new.
- Produces: `ProjectionCatalog.recompute_dirty` becomes `async def recompute_dirty(self, projection_name: str) -> dict[str, Any]` — a **breaking signature change** (sync → async), justified because every current caller (M1's tests, M4's `research_domain`) either doesn't call it in a hot path yet or can trivially add `await`; this is the milestone's one intentional exception to "don't break existing callers," and Step 5 below updates the two existing call sites in the same commit rather than leaving them broken. `ProjectionSpec.recompute` may now be either a plain callable (`Callable[[str], Any]`) or an async callable (`Callable[[str], Awaitable[Any]]`) — `recompute_dirty` detects which via `inspect.iscoroutinefunction` and awaits only the async ones.

- [ ] **Step 1: Write the new failing tests (async recompute support)**

```python
# Add to tests/substrate/test_projection.py (do not remove the 5 existing tests)
import pytest


@pytest.mark.asyncio
async def test_async_recompute_callable_is_awaited():
    from substrate.projection import ProjectionCatalog, ProjectionSpec

    catalog = ProjectionCatalog()

    async def _async_recompute(key: str):
        return f"async-view-for-{key}"

    catalog.register(ProjectionSpec(
        name="async_shape",
        invalidation_key=lambda event: event.fingerprint,
        recompute=_async_recompute,
    ))
    catalog.invalidate("async_shape", _FakeEvent(fingerprint="fp-async", chapter_id="ch-async"))
    result = await catalog.recompute_dirty("async_shape")
    assert result == {"fp-async": "async-view-for-fp-async"}


@pytest.mark.asyncio
async def test_sync_recompute_callable_still_works_through_async_entrypoint():
    from substrate.projection import ProjectionCatalog, ProjectionSpec

    catalog = ProjectionCatalog()
    catalog.register(ProjectionSpec(
        name="sync_shape",
        invalidation_key=lambda event: event.chapter_id,
        recompute=lambda key: f"sync-view-for-{key}",
    ))
    catalog.invalidate("sync_shape", _FakeEvent(fingerprint="fp-sync", chapter_id="ch-sync"))
    result = await catalog.recompute_dirty("sync_shape")
    assert result == {"ch-sync": "sync-view-for-ch-sync"}


@pytest.mark.asyncio
async def test_mixed_sync_and_async_projections_in_one_catalog():
    from substrate.projection import ProjectionCatalog, ProjectionSpec

    catalog = ProjectionCatalog()

    async def _async_recompute(key: str):
        return f"async-{key}"

    catalog.register(ProjectionSpec(
        name="a", invalidation_key=lambda e: e.fingerprint, recompute=_async_recompute,
    ))
    catalog.register(ProjectionSpec(
        name="b", invalidation_key=lambda e: e.chapter_id, recompute=lambda k: f"sync-{k}",
    ))
    catalog.invalidate("a", _FakeEvent(fingerprint="x", chapter_id="y"))
    catalog.invalidate("b", _FakeEvent(fingerprint="x", chapter_id="y"))
    assert await catalog.recompute_dirty("a") == {"x": "async-x"}
    assert await catalog.recompute_dirty("b") == {"y": "sync-y"}
```

Note: `_FakeEvent` is already defined at the top of
`tests/substrate/test_projection.py` from M1 — reuse it, do not redefine.
The 5 pre-existing synchronous tests in this file (`test_register_and_...`,
etc.) must be updated to `await catalog.recompute_dirty(...)` and marked
`@pytest.mark.asyncio` too, since the method signature is changing for
everyone — this is not optional, since the old sync tests would otherwise
break (calling an async method without `await` returns a coroutine object,
not the dict, and the existing assertions would fail).

- [ ] **Step 2: Run to verify the new tests fail and the old ones now fail without awaiting (expected, since the interface hasn't changed yet)**

Run: `.venv/bin/pytest tests/substrate/test_projection.py -v`
Expected: FAIL — new tests fail with `TypeError` (recompute_dirty isn't
async yet, calling `await` on a `dict` return fails), existing 5 tests still
pass at this point since you haven't touched them yet. This step just
confirms the new tests are exercising something that doesn't exist yet
before you make the coordinated change in Step 3-4.

- [ ] **Step 3: Update the 5 pre-existing tests to await, per the note above** (add `@pytest.mark.asyncio` and `await` to each of the 5 original test functions in `tests/substrate/test_projection.py` — do not change their assertions, only add `async def`/`await`).

- [ ] **Step 4: Write the implementation**

```python
# substrate/projection.py
from __future__ import annotations
import inspect
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

    async def recompute_dirty(self, projection_name: str) -> dict[str, Any]:
        spec = self._specs[projection_name]
        keys = self._dirty[projection_name]
        result = {}
        for key in keys:
            value = spec.recompute(key)
            if inspect.isawaitable(value):
                value = await value
            result[key] = value
        self._dirty[projection_name] = set()
        return result
```

- [ ] **Step 5: Update the two existing call sites to await the now-async method**

`research_domain/projections.py` itself does not call `recompute_dirty`
(only `tests/research_domain/test_projections.py` and
`tests/research_domain/test_end_to_end.py` do) — update both test files'
calls from `catalog.recompute_dirty(...)` to `await catalog.recompute_dirty(...)`,
and mark their test functions `@pytest.mark.asyncio` if not already (the
end-to-end test already is; `test_projections.py`'s two tests are not yet
async — add `import pytest`, `@pytest.mark.asyncio`, `async def`, and
`await` to both).

- [ ] **Step 6: Run to verify everything passes**

Run: `.venv/bin/pytest tests/substrate/test_projection.py tests/research_domain/ -v`
Expected: PASS — 8 tests in `test_projection.py` (5 updated + 3 new) plus
11 tests in `tests/research_domain/` (unchanged count, now async-aware),
all green.

- [ ] **Step 7: Commit**

```bash
git add substrate/projection.py tests/substrate/test_projection.py tests/research_domain/test_projections.py
git commit -m "feat(substrate): support async recompute callables in ProjectionCatalog"
```

---

### Task 2: Third-domain decision

**Files:**
- Modify: `docs/superpowers/specs/2026-07-20-cross-domain-substrate-design.md` (append under the M5 section)

**Interfaces:**
- Consumes: nothing — this is a documentation-only decision task, per the spec's own instruction that M5 must decide (not necessarily build) whether a third domain is worth pursuing next.

- [ ] **Step 1: Locate the `### M5` section** and, after reviewing what M1-M4 actually built and cost (five milestones of real engineering effort, a new Docker-dependent test fixture, a new package, an unproven-in-production research-domain scaffold with no real agents or user), record the decision. The honest assessment: no concrete product need has materialized yet for a third domain (project-planning/tasking) — everything built so far was driven by proving the pattern generalizes, not by an actual planning-domain user waiting on it. Per the project's own YAGNI principle and the spec's explicit option to leave this "backlog until a concrete need arises," the decision is: **backlog, not build.**

- [ ] **Step 2: Append this to the M5 section** (find its exact current text first):

```markdown

**Third-domain decision (2026-07-21): backlog, not build.** Per this
milestone's own instruction to decide rather than default into more
construction: no concrete product need for a planning/tasking domain (or
any third domain) exists yet — M1-M4 were driven by proving the substrate
generalizes across two domains, not by an active user waiting on a third.
Building one now would be speculative work against a domain whose actual
requirements (an exogenous clock, contested non-convergent beliefs) aren't
yet informed by any real deployment pressure, unlike M4's research domain
which was scoped directly from the spec's own Findings section. Revisit
this decision when a concrete planning/tasking need actually surfaces, at
which point the now-hardened substrate (declarative event-type/gating
registry, Postgres+pgvector backend, sync-or-async ProjectionCatalog,
domain-neutral agent-registry shape) should make standing it up
substantially cheaper than M4's research domain was, since M4 itself is the
proof of that cost curve.
```

- [ ] **Step 3: Commit**

```bash
git add docs/superpowers/specs/2026-07-20-cross-domain-substrate-design.md
git commit -m "docs(m5): record third-domain backlog decision"
```

---

### Task 3: Final full-arc regression and M5/spec completion notes

**Files:**
- Modify: `docs/superpowers/specs/2026-07-20-cross-domain-substrate-design.md` (append final M5 status note)

**Interfaces:**
- Consumes: all of Tasks 1-2, and the entire M0-M4 body of work.
- Produces: nothing new — the six-milestone arc's final verification gate.

- [ ] **Step 1: Run the full test suite**

Run: `.venv/bin/pytest tests/ -q`
Expected: no new failures beyond the 5 pre-existing/documented ones from
M1/M2/M4's status notes (2 mock-signature mismatches, 1 registry-ordering
assertion, 2 settings-field-set mismatches — none touched by this project's
substrate work), plus every `tests/substrate/` and `tests/research_domain/`
test passing.

- [ ] **Step 2: Verify the import-boundary constraint one final time, for both packages**

Run: `grep -rn "^from novelizer\|^import novelizer" substrate/ research_domain/`
Expected: no output.

- [ ] **Step 3: Append the M5 status note** (find the M5 section's current end, after Task 2's decision paragraph):

```markdown

**M5 status (2026-07-21): done.** Folded back the one genuine substrate gap
M4 logged: `ProjectionCatalog.recompute_dirty` now accepts either sync or
async `recompute` callables (detected via `inspect.isawaitable`), with the
method itself becoming `async def` — a deliberate, narrow breaking change,
applied consistently to its only two existing call sites in the same
commit. The other two logged gaps (unbuilt Contradiction Map/Claim
Dependency Graph projections; real role behavior/`AgentContext`
sufficiency) were confirmed to be scope notes about deliberately-excluded
work, not substrate defects, and were left as-is rather than built out —
building them now would mean doing the very research-domain product work
M4's non-goals excluded. The third-domain decision is recorded above:
backlog, not build, pending a concrete need. **This closes the M0-M5 arc**:
the substrate (`substrate/`) now provides a declarative event-type registry
with per-event-type gating (M1, M3), a Postgres+pgvector backend proven
under concurrent multi-agent writes (M2), and a sync-or-async projection
catalog (M1, M5) — proven to generalize across fiction (Novelizer's
existing behavior, unchanged) and a second, independent research domain
(M4), with `substrate/` and `research_domain/` importing nothing from
`novelizer.*` throughout.
```

- [ ] **Step 4: Commit**

```bash
git add docs/superpowers/specs/2026-07-20-cross-domain-substrate-design.md
git commit -m "docs(m5): record M5 completion and close the M0-M5 arc"
```
