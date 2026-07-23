# Substrate Package Boundary Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Turn `substrate/` into a real internal package boundary — an explicit, enforced public API — without publishing it or changing any domain logic.

**Architecture:** Define `substrate/__init__.py` as the only sanctioned import surface, rewrite `novelizer/` and `research_domain/` to import from it, add an automated import-linter contract that fails the test suite if anyone reaches into a submodule again, clarify `ProjectionSpec.recompute`'s dual sync/async contract in its type and docstring, and document all of it in `substrate/README.md`.

**Tech Stack:** Python 3.13, `uv`, `pytest` (`uv run pytest`, `asyncio_mode = "auto"`), `import-linter` (new dev dependency).

## Global Constraints

- No PyPI packaging, no separate `pyproject.toml` or version number for `substrate/` — one repo, one `pyproject.toml`.
- No changes to `novelizer/` or `research_domain/` business logic beyond import statements.
- No changes to `agent_registry.py` role/spec content.
- No structural change to how `ProjectionSpec.recompute` is invoked — the `inspect.isawaitable` duck-typing in `ProjectionCatalog.recompute_dirty` stays exactly as-is. Only its documented contract (type annotation + docstring) changes.
- Files under `tests/` may keep importing `substrate` submodules directly (e.g. `from substrate.postgres.events import PostgresEventStore`) — the import-linter contract only constrains `novelizer.*` and `research_domain.*`.
- Run tests targeted to the files touched, never the full suite (`uv run pytest tests/substrate/ tests/research_domain/ -v` style, not bare `uv run pytest tests/`) — finite compute resources, standing instruction from the user this session.
- Use `uv run pytest`, not bare `pytest` (bare `pytest` fails in this repo due to a missing `deepagents` import in some environments).

---

### Task 1: Define the substrate public API

**Files:**
- Modify: `substrate/__init__.py` (currently empty)
- Test: `tests/substrate/test_public_api.py` (new)

**Interfaces:**
- Produces: `substrate.__all__` — a list of exactly 13 names, importable from the top-level `substrate` package: `AgentContext`, `AgentSpec`, `EventTypeRegistry`, `EventTypeSpec`, `GatingTier`, `PostgresDepsStore`, `PostgresEmbeddingStore`, `PostgresEventStore`, `ProjectionCatalog`, `ProjectionSpec`, `SubagentGrant`, `ToolGrant`, `is_gated`. Tasks 2 and 3 both depend on this exact set existing.

- [ ] **Step 1: Write the failing test**

Create `tests/substrate/test_public_api.py`:

```python
# tests/substrate/test_public_api.py
import substrate

EXPECTED_PUBLIC_API = [
    "AgentContext",
    "AgentSpec",
    "EventTypeRegistry",
    "EventTypeSpec",
    "GatingTier",
    "PostgresDepsStore",
    "PostgresEmbeddingStore",
    "PostgresEventStore",
    "ProjectionCatalog",
    "ProjectionSpec",
    "SubagentGrant",
    "ToolGrant",
    "is_gated",
]


def test_all_matches_expected_public_api():
    assert substrate.__all__ == EXPECTED_PUBLIC_API


def test_every_name_in_all_is_importable_from_top_level():
    for name in substrate.__all__:
        assert hasattr(substrate, name), f"{name} listed in __all__ but not importable from substrate"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/substrate/test_public_api.py -v`
Expected: FAIL — `AttributeError: module 'substrate' has no attribute '__all__'` (or similar; `substrate/__init__.py` is currently empty).

- [ ] **Step 3: Write the public API in `substrate/__init__.py`**

Replace the entire (empty) contents of `substrate/__init__.py` with:

```python
from __future__ import annotations

from substrate.agent_registry import AgentContext, AgentSpec, SubagentGrant, ToolGrant
from substrate.event_registry import EventTypeRegistry, EventTypeSpec, GatingTier
from substrate.policy import is_gated
from substrate.postgres.deps import PostgresDepsStore
from substrate.postgres.embeddings import PostgresEmbeddingStore
from substrate.postgres.events import PostgresEventStore
from substrate.projection import ProjectionCatalog, ProjectionSpec

__all__ = [
    "AgentContext",
    "AgentSpec",
    "EventTypeRegistry",
    "EventTypeSpec",
    "GatingTier",
    "PostgresDepsStore",
    "PostgresEmbeddingStore",
    "PostgresEventStore",
    "ProjectionCatalog",
    "ProjectionSpec",
    "SubagentGrant",
    "ToolGrant",
    "is_gated",
]
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/substrate/test_public_api.py -v`
Expected: PASS (2 passed)

- [ ] **Step 5: Run existing substrate tests as a regression check**

Run: `uv run pytest tests/substrate/ -v`
Expected: all pass (this only adds an `__init__.py` re-export layer; nothing existing imports `substrate` as a bare package yet, so no existing test should be affected).

- [ ] **Step 6: Commit**

```bash
git add substrate/__init__.py tests/substrate/test_public_api.py
git commit -m "feat(substrate): define public API surface via __init__.py"
```

---

### Task 2: Rewrite consumer imports to use the top-level substrate package

**Files:**
- Modify: `novelizer/agents/registry_types.py`
- Modify: `novelizer/canon/policy.py` (lines 4-5)
- Modify: `research_domain/event_types.py` (line 2)
- Modify: `research_domain/roles.py` (line 2)
- Modify: `research_domain/projections.py` (line 4)

**Interfaces:**
- Consumes: `substrate.__all__` from Task 1 — all names used below (`AgentContext`, `AgentSpec`, `SubagentGrant`, `ToolGrant`, `EventTypeRegistry`, `EventTypeSpec`, `GatingTier`, `is_gated`, `ProjectionCatalog`, `ProjectionSpec`) are already exported at the top level.
- No new interfaces produced — this task only changes where five existing files import from. No function signatures, class names, or behavior change.

This task is a mechanical, behavior-preserving rewrite: every `from substrate.<submodule> import ...` becomes `from substrate import ...`. There is no new test to write — the existing test suites for these files are the regression check.

- [ ] **Step 1: Rewrite `novelizer/agents/registry_types.py`**

Current content:
```python
from __future__ import annotations
from substrate.agent_registry import AgentSpec, ToolGrant, SubagentGrant, AgentContext

__all__ = ["AgentSpec", "ToolGrant", "SubagentGrant", "AgentContext"]
```

Replace with:
```python
from __future__ import annotations
from substrate import AgentSpec, ToolGrant, SubagentGrant, AgentContext

__all__ = ["AgentSpec", "ToolGrant", "SubagentGrant", "AgentContext"]
```

- [ ] **Step 2: Rewrite the import lines in `novelizer/canon/policy.py`**

Current lines 4-5:
```python
from substrate.event_registry import EventTypeRegistry, EventTypeSpec, GatingTier
from substrate.policy import is_gated as _substrate_is_gated
```

Replace with:
```python
from substrate import EventTypeRegistry, EventTypeSpec, GatingTier, is_gated as _substrate_is_gated
```

Every other line in the file (imports of `novelizer.canon.autonomy`, `novelizer.canon.events`, and everything from line 6 onward) is unchanged.

- [ ] **Step 3: Rewrite the import line in `research_domain/event_types.py`**

Current line 2:
```python
from substrate.event_registry import EventTypeRegistry, EventTypeSpec, GatingTier
```

Replace with:
```python
from substrate import EventTypeRegistry, EventTypeSpec, GatingTier
```

- [ ] **Step 4: Rewrite the import line in `research_domain/roles.py`**

Current line 2:
```python
from substrate.agent_registry import AgentSpec
```

Replace with:
```python
from substrate import AgentSpec
```

- [ ] **Step 5: Rewrite the import line in `research_domain/projections.py`**

Current line 4:
```python
from substrate.projection import ProjectionCatalog, ProjectionSpec
```

Replace with:
```python
from substrate import ProjectionCatalog, ProjectionSpec
```

- [ ] **Step 6: Run targeted regression tests**

Run: `uv run pytest tests/canon/test_policy.py tests/agents/test_registry.py tests/agents/test_registry_types.py tests/research_domain/ tests/substrate/ -v`
Expected: all pass — these are the test files that most directly exercise the five modules just changed, plus the full `research_domain` and `substrate` suites since every file in `research_domain/` was touched.

- [ ] **Step 7: Commit**

```bash
git add novelizer/agents/registry_types.py novelizer/canon/policy.py research_domain/event_types.py research_domain/roles.py research_domain/projections.py
git commit -m "refactor: import substrate primitives from top-level package, not submodules"
```

---

### Task 3: Add import-linter enforcement

**Files:**
- Modify: `pyproject.toml` (add dev dependency + `[tool.importlinter]` config)
- Test: `tests/substrate/test_import_boundary.py` (new)

**Interfaces:**
- Consumes: the import rewrite from Task 2 — this task's contract will fail if Task 2 left any submodule import behind, which is the intended safety net.
- Produces: a `lint-imports` console script (from the `import-linter` package) on the venv's `PATH` after `uv sync`, and a `[tool.importlinter]` contract block in `pyproject.toml` that later tasks/files must not violate.

- [ ] **Step 1: Add the `import-linter` dev dependency**

Run: `uv add --dev import-linter`
Expected: `pyproject.toml`'s `[dependency-groups] dev` list gains an `import-linter` entry, and `uv.lock` is updated. Verify with:

Run: `grep -n "import-linter" pyproject.toml`
Expected: one line showing `"import-linter>=<version>"` in the dev group.

- [ ] **Step 2: Add the import-linter contract to `pyproject.toml`**

Append this block to the end of `pyproject.toml` (after the existing `[tool.pytest.ini_options]` section):

```toml
[tool.importlinter]
root_packages = ["substrate", "novelizer", "research_domain"]

[[tool.importlinter.contracts]]
name = "substrate package boundary"
type = "forbidden"
source_modules = ["novelizer", "research_domain"]
forbidden_modules = [
    "substrate.agent_registry",
    "substrate.event_registry",
    "substrate.policy",
    "substrate.postgres",
    "substrate.projection",
]
```

(A `forbidden` contract's `forbidden_modules` entries also cover their descendants, so listing `substrate.postgres` forbids `substrate.postgres.events`, `substrate.postgres.embeddings`, and `substrate.postgres.deps` too — no need to enumerate those three separately.)

- [ ] **Step 3: Verify the contract passes against the current (already-fixed) codebase**

Run: `uv run lint-imports`
Expected: `Contracts: 1 kept, 0 broken.` (Task 2 already removed every submodule import from `novelizer`/`research_domain`, so this should be clean on the first run. If it reports a broken contract, it names the exact offending import — fix that import to go through `substrate` before continuing.)

- [ ] **Step 4: Write the boundary test**

Create `tests/substrate/test_import_boundary.py`:

```python
# tests/substrate/test_import_boundary.py
import subprocess


def test_novelizer_and_research_domain_only_import_substrate_top_level():
    result = subprocess.run(
        ["lint-imports"],
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stdout + result.stderr
```

- [ ] **Step 5: Run test to verify it passes**

Run: `uv run pytest tests/substrate/test_import_boundary.py -v`
Expected: PASS (1 passed) — `lint-imports` is on `PATH` inside the `uv run` environment and the contract from Step 2 is already clean.

- [ ] **Step 6: Commit**

```bash
git add pyproject.toml uv.lock tests/substrate/test_import_boundary.py
git commit -m "chore(substrate): enforce package boundary with import-linter"
```

---

### Task 4: Clarify the `ProjectionSpec.recompute` contract

**Files:**
- Modify: `substrate/projection.py`

**Interfaces:**
- No signature or behavior change. `ProjectionCatalog.recompute_dirty`'s runtime handling (`inspect.isawaitable(value)` on the result of calling `spec.recompute(key)`) is untouched.

No new test — per the design's Testing section, this is a type-annotation and docstring change only; `tests/substrate/test_projection.py` and `tests/research_domain/test_projections.py` already cover both sync and async `recompute` callables and must continue to pass unmodified.

- [ ] **Step 1: Update `substrate/projection.py`**

Current content:
```python
from __future__ import annotations
import inspect
from dataclasses import dataclass
from typing import Any, Callable


@dataclass(frozen=True)
class ProjectionSpec:
    name: str
    invalidation_key: Callable[[Any], str]
    recompute: Callable[[str], Any]
```

Replace the top of the file (through the `ProjectionSpec` class) with:
```python
from __future__ import annotations
import inspect
from dataclasses import dataclass
from typing import Any, Awaitable, Callable


@dataclass(frozen=True)
class ProjectionSpec:
    """One named projection: how to invalidate it, and how to recompute a dirty key.

    `recompute` may return a value directly or an awaitable of one --
    `ProjectionCatalog.recompute_dirty` awaits it if needed, so both a plain
    function and an `async def` are valid.
    """
    name: str
    invalidation_key: Callable[[Any], str]
    recompute: Callable[[str], Any | Awaitable[Any]]
```

The rest of the file (`ProjectionCatalog` and its methods) is unchanged.

- [ ] **Step 2: Run regression tests**

Run: `uv run pytest tests/substrate/test_projection.py tests/research_domain/test_projections.py tests/research_domain/test_end_to_end.py -v`
Expected: all pass, unchanged from before this task (this confirms the annotation/docstring change didn't alter runtime behavior — `tests/research_domain/test_end_to_end.py` specifically exercises the async-recompute-from-real-Postgres path).

- [ ] **Step 3: Commit**

```bash
git add substrate/projection.py
git commit -m "docs(substrate): clarify ProjectionSpec.recompute's sync/async contract"
```

---

### Task 5: Write `substrate/README.md` and mark the design spec implemented

**Files:**
- Create: `substrate/README.md`
- Modify: `docs/superpowers/specs/2026-07-22-substrate-package-boundary-design.md` (status line)

**Interfaces:**
- Consumes: the final public API list from Task 1, the import rule from Task 3, and the `research_domain/` composition pattern (already-merged `research_domain/event_types.py`, `research_domain/projections.py`) as the quickstart's worked example.
- No code interfaces produced — this is a documentation-only task, plus a one-line status edit in the design spec.

- [ ] **Step 1: Create `substrate/README.md`**

```markdown
# substrate

Domain-neutral event-sourcing primitives, proven across two independent
domains: fiction (`novelizer/`) and a synthetic research domain
(`research_domain/`). See
`docs/superpowers/specs/2026-07-20-cross-domain-substrate-design.md` for the
generalization history.

## Primitives

- **Event type registry + gating** (`EventTypeRegistry`, `EventTypeSpec`,
  `GatingTier`, `is_gated`) — declare which event types exist, whether each
  is always/never/tiered-gated, and check gating against a tier order and
  current tier index.
- **Projections** (`ProjectionCatalog`, `ProjectionSpec`) — register named
  projections with an invalidation key and a recompute function (sync or
  async), mark keys dirty as events arrive, and recompute only the dirty
  ones.
- **Agent registry** (`AgentSpec`, `AgentContext`, `ToolGrant`,
  `SubagentGrant`) — declare an agent's name, construction function, and
  what gates its tool/subagent access.
- **Postgres stores** (`PostgresEventStore`, `PostgresEmbeddingStore`,
  `PostgresDepsStore`) — the append-only event log and supporting stores
  backing all of the above.

## Building a new domain

This is the pattern `research_domain/` follows:

1. Define your event types with a registry builder:
   ```python
   from substrate import EventTypeRegistry, EventTypeSpec, GatingTier

   def build_my_registry() -> EventTypeRegistry:
       registry = EventTypeRegistry()
       registry.register(EventTypeSpec(name="thing.happened", gating_tier=GatingTier.always))
       return registry
   ```
2. Define your tier order (a list of tier-level names) and check gating with
   `is_gated(event_name, registry, tier_order, current_tier_index)`.
3. Register projections on a `ProjectionCatalog`:
   ```python
   from substrate import ProjectionCatalog, ProjectionSpec

   def build_my_catalog(recompute_fn) -> ProjectionCatalog:
       catalog = ProjectionCatalog()
       catalog.register(ProjectionSpec(name="my_projection", invalidation_key=..., recompute=recompute_fn))
       return catalog
   ```
4. Wire a `PostgresEventStore` to append and read your domain's events.

## Import rule

Import from `substrate` directly:

```python
from substrate import ProjectionCatalog, EventTypeRegistry, is_gated
```

Never import a submodule directly (`substrate.projection`, `substrate.postgres.events`,
etc.) from `novelizer/` or `research_domain/` code. This is enforced by an
import-linter contract — see the `[tool.importlinter]` section in
`pyproject.toml`, and run `uv run lint-imports` to check locally.

(Files under `tests/` are exempt from this rule — they test submodule
internals directly by design.)
```

- [ ] **Step 2: Update the design spec's status line**

In `docs/superpowers/specs/2026-07-22-substrate-package-boundary-design.md`, change:
```markdown
**Status: approved, not yet implemented.**
```
to:
```markdown
**Status: implemented (2026-07-22).**
```

- [ ] **Step 3: Run the full targeted regression suite for this branch**

Run: `uv run pytest tests/substrate/ tests/research_domain/ tests/canon/test_policy.py tests/agents/test_registry.py tests/agents/test_registry_types.py -v`
Expected: all pass. (Still targeted, not the full `tests/` suite — per the standing instruction in Global Constraints.)

- [ ] **Step 4: Commit**

```bash
git add substrate/README.md docs/superpowers/specs/2026-07-22-substrate-package-boundary-design.md
git commit -m "docs(substrate): add README and mark package-boundary spec implemented"
```
