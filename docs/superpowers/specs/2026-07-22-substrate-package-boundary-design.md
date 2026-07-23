# Substrate Package Boundary — Design

**Status: implemented (2026-07-22).**

## Problem

`substrate/` has been proven to generalize across two domains (`novelizer/` for fiction, `research_domain/` for a synthetic research domain), per `docs/superpowers/specs/2026-07-20-cross-domain-substrate-design.md`. But it isn't actually a package boundary yet: nothing marks which names are the public surface, and consumers reach directly into submodules (`substrate.projection`, `substrate.postgres.events`, `substrate.event_registry`, `substrate.policy`, `substrate.agent_registry`). Nothing stops that from drifting further as more domains or contributors show up. This is the packaging step toward eventually extracting substrate as a standalone, publishable package — but that's explicitly out of scope for this pass (see Non-goals).

## Design

### 1. Public API via `substrate/__init__.py`

A grep of every cross-package `from substrate...` import (`novelizer/`, `research_domain/`) gives the exact public surface needed:

- `substrate.agent_registry`: `AgentSpec`, `ToolGrant`, `SubagentGrant`, `AgentContext`
- `substrate.event_registry`: `EventTypeRegistry`, `EventTypeSpec`, `GatingTier`
- `substrate.policy`: `is_gated`
- `substrate.projection`: `ProjectionCatalog`, `ProjectionSpec`
- `substrate.postgres.events`: `PostgresEventStore`
- `substrate.postgres.embeddings`: `PostgresEmbeddingStore` (imported today only by `tests/substrate/`, not by a domain package — included anyway since it's a first-class substrate primitive with the same shape as `PostgresEventStore`, and excluding it would leave the public API inconsistent for no reason)
- `substrate.postgres.deps`: `PostgresDepsStore` (same rationale as above)

`substrate/__init__.py` will import and re-export exactly these names with an explicit `__all__` list. No wildcard imports.

`novelizer/agents/registry_types.py`, `novelizer/canon/policy.py`, `research_domain/event_types.py`, `research_domain/roles.py`, and `research_domain/projections.py` are rewritten to import from `substrate` (top-level) instead of the submodule. This is a mechanical, behavior-preserving rewrite — no logic changes.

**Tests are out of scope for this rewrite.** Files under `tests/substrate/` test submodule internals directly (e.g. `test_postgres_events.py` imports `PostgresEventStore` specifically to test the Postgres store, not "substrate" abstractly) and may continue importing submodules directly. The import-linter contract (below) only constrains `novelizer.*` and `research_domain.*`.

### 2. Import-linter enforcement

Add `import-linter` to the `dev` dependency group. Add an `[[tool.importlinter.contracts]]` entry (or `.importlinter` config, whichever import-linter's current version prefers) with a `forbidden` contract: `novelizer` and `research_domain` may not import any `substrate.*` submodule — only `substrate` itself.

Wire `lint-imports` into the existing test workflow via a new test, `tests/substrate/test_import_boundary.py`, that shells out to `lint-imports` (via `subprocess`) and asserts a zero exit code. This keeps the check inside `pytest` (matching how tests are already run in this repo) rather than requiring a separate CI step to remember.

### 3. `ProjectionSpec.recompute` contract clarification

`substrate/projection.py`'s `ProjectionCatalog.recompute_dirty` already handles both sync and async `recompute` callables correctly via `inspect.isawaitable(value)` on the *result* of calling `recompute(key)`. This is idiomatic Python and is not being restructured — a structural flag (e.g. `is_async: bool`) would force every existing `ProjectionSpec` construction site in `research_domain/projections.py` (and any future novelizer projections) to be touched for zero behavioral gain, which fails YAGNI given the current dual-mode support already works.

Instead:
- Tighten `ProjectionSpec.recompute`'s type annotation from `Callable[[str], Any]` to `Callable[[str], Any | Awaitable[Any]]`.
- Add a one-line docstring on `ProjectionSpec` (or on the `recompute` field, via a class-level docstring since it's a frozen dataclass) stating the contract: "May return a value directly or an awaitable of one; `ProjectionCatalog.recompute_dirty` awaits it if needed."

No runtime behavior changes. This exists purely so the contract is visible from the newly-defined public API surface instead of only discoverable by reading `recompute_dirty`'s implementation.

### 4. `substrate/README.md`

New file. Contents:
- One paragraph: what substrate is (domain-neutral event-sourcing primitives proven across fiction and research domains).
- The four primitive groups (event type registry + gating, projections, agent registry, Postgres stores) with a one-line description each.
- A "build a new domain" quickstart mirroring how `research_domain/` composes them: define an event registry with `build_*_registry()`, define gating tiers, register `ProjectionSpec`s on a `ProjectionCatalog`, wire a `PostgresEventStore`.
- An explicit statement of the import rule: "Import from `substrate` directly (`from substrate import ProjectionCatalog`), never from a submodule. This is enforced by an import-linter contract — see `pyproject.toml`."

## Testing

- New: `tests/substrate/test_public_api.py` — asserts every name in `substrate.__all__` is importable from the top-level `substrate` package and that `substrate.__all__` matches the enumerated list above (prevents silent drift of the public surface).
- New: `tests/substrate/test_import_boundary.py` — runs `lint-imports` via `subprocess`, asserts exit code 0.
- Existing tests (`tests/substrate/`, `tests/research_domain/`, novelizer's own test suite) continue to pass with only their import statements changed where applicable. Run targeted, not full-suite (per standing instruction: finite compute resources).
- No new tests needed for the `recompute` docstring/annotation change — it's not a behavioral change, so nothing new to assert; existing `tests/substrate/test_projection.py` and `tests/research_domain/test_projections.py` already cover both sync and async `recompute` callables and continue to pass unchanged.

## Non-goals

- No PyPI packaging, no separate `pyproject.toml` or version number for `substrate/`.
- No changes to `novelizer/` or `research_domain/` business logic beyond import statements.
- No changes to `agent_registry.py` role/spec content.
- No structural change to how `ProjectionSpec.recompute` is invoked (sync/async duck-typing stays as-is; only its documented contract changes).
- No changes to `tests/` import style (tests may keep importing submodules directly).
