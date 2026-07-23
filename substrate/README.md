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
