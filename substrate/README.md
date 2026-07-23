# substrate

Domain-neutral event-sourcing primitives, proven across two independent
domains: fiction (`novelizer/`) and a synthetic research domain
(`research_domain/`). See
`docs/superpowers/specs/2026-07-20-cross-domain-substrate-design.md` for the
generalization history.

Sibling kits: `tui_kit/` (domain-agnostic TUI) and `agent_kit/`
(domain-neutral agent execution — loop, scheduler, LLM runners; see
`agent_kit/README.md`).

`research_domain/` is the worked example for everything on this page -- it
exercises every primitive and the runtime-construction pattern end to end.
For the CLI built on top of it, see `docs/reference/research-domain-cli.md`;
for why the boundary between `substrate` and its domains is drawn the way it
is, see `docs/explanation/architecture-boundaries.md`.

## Primitives

- `EventTypeRegistry` (`substrate/event_registry.py`) -- the catalog of
  event types a domain is allowed to write; `register()` refuses
  duplicates, so a name maps to exactly one `EventTypeSpec`. Each spec
  carries `gating_tier` (`always` / `never` / `tiered`) and, for tiered
  specs, a `tier_level`. The autonomy check itself is the `is_gated()`
  function (`substrate/policy.py`), which reads a spec from the registry
  and evaluates it against a domain-supplied tier order.
- `PostgresEventStore` (`substrate/postgres/events.py`) -- durable event
  storage over one asyncpg connection: `connect()` opens it and installs
  the `substrate_events` schema (append-only, enforced by DB triggers that
  reject `UPDATE`/`DELETE`); `append()` writes one event to a named stream
  (optional `parent_ids` and `actor`) and returns its `seq`; `read_stream()`
  replays a stream in `seq` order; `close()` releases the connection.
- `ProjectionCatalog` / `ProjectionSpec` (`substrate/projection.py`) --
  named read models. `register()` adds a spec (`invalidation_key` +
  `recompute`, where `recompute` may be a plain function or `async def`);
  `invalidate()` marks a key dirty from a source event;
  `recompute_dirty()` awaits/recomputes every dirty key, clears them, and
  returns the `{key: result}` mapping.
- `RuntimeBase` (`substrate/runtime.py`) -- the storage-agnostic lifecycle
  that wires the two together for a domain: `connect()`/`close()` proxy the
  underlying `PostgresEventStore`; `register_projection(catalog,
  projection_name, event_types)` binds a `ProjectionCatalog` projection to
  the set of event types that should invalidate it; `append()` writes an
  event to the runtime's stream; `catch_up()` replays the stream, invalidates
  every registered projection whose event types match, and recomputes dirty
  keys; `get_projection(projection_name)` returns the last-computed read
  model for that projection. Domain runtimes subclass `RuntimeBase` -- see
  `research_domain/` for the worked example, exercised end to end.

## Building a new domain

`research_domain/` is the worked example -- read
`research_domain/event_types.py`, `research_domain/projections.py`, and
`research_domain/runtime.py` alongside these steps. For the CLI built on
that runtime, see `docs/reference/research-domain-cli.md`; for the design
rationale behind the `substrate`/domain boundary, see
`docs/explanation/architecture-boundaries.md`.

1. **Define your event types and a registry builder.** Build an
   `EventTypeRegistry` (`substrate/event_registry.py`) and register one
   `EventTypeSpec` per event type your domain writes, e.g.
   `research_domain/event_types.py::build_research_registry()`.

2. **Pick a tier order and set `gating_tier`/`tier_level` per event type.**
   Each `EventTypeSpec` declares `gating_tier` (`always` / `never` /
   `tiered`) and, for `tiered` specs, a `tier_level` drawn from your
   domain's own ordered tier list (e.g. `RESEARCH_TIER_ORDER = ["auto",
   "reviewed"]`). At decision time, call `is_gated(event_name, registry,
   tier_order, current_tier_index)` (`substrate/policy.py`) -- this is what
   the autonomy dial checks before letting an event through unattended.

3. **Register projections on a `ProjectionCatalog`.** For each read model,
   write a `build_*_catalog()` function that constructs a `ProjectionCatalog`
   and calls `register()` with an `invalidation_key` and a `recompute`
   closure (see `research_domain/projections.py`). Keep the catalog builder
   free of storage concerns -- it only knows about payload shapes and lookup
   dicts.

4. **Construct a `PostgresEventStore` and pass it to a `RuntimeBase`
   subclass with your stream name.** `PostgresEventStore` takes a DSN;
   `RuntimeBase.__init__(self, event_store, stream)` takes the store and the
   name of the single stream your domain appends to and replays.

5. **Subclass `RuntimeBase` for your domain.** In `__init__`/setup, call
   `register_projection(catalog, projection_name, event_types)` once per
   catalog/event-types pairing so `catch_up()` knows which projections to
   invalidate from which events (`ResearchRuntime.__init__` registers three:
   `source_coverage` from `{"claim.proposed"}`, `contradiction_map` from
   `{"claim.refuted"}`, `claim_dependency_graph` from `{"claim.corrected"}`).
   At startup call `connect()` then `catch_up()` to replay the stream and
   populate projections. Call `append()` to write new events -- domains that
   need projections refreshed immediately after a write, rather than only at
   the next explicit `catch_up()`, can wrap it the way
   `ResearchRuntime.append_event()` does (`append()` then `catch_up()`).
   Call `get_projection(projection_name)` to read the last-computed result
   for a projection. Call `close()` at shutdown to release the underlying
   database connection.

## Testing your domain

For any test that hits real postgres, take the `postgres_dsn` fixture from
`tests/substrate/postgres_fixture.py` -- the way every test in
`tests/substrate/test_postgres_events.py` does:

```python
from tests.substrate.postgres_fixture import postgres_dsn

async def test_my_domain_appends(postgres_dsn):
    store = PostgresEventStore(postgres_dsn)
    ...
```

What the fixture gives you: a DSN pointing at a fresh, throwaway
`CREATE DATABASE` inside a single session-scoped pgvector container
(`pg_container`, one `pgvector/pgvector:pg16` per test session). Per-database
isolation is equivalent to the old container-per-test isolation -- extensions
like pgvector are per-database and are created by the code under test -- but
costs ~100ms per test instead of ~4s of `docker run` setup plus up to 10s of
`docker stop` teardown. The database is dropped after each test with
`DROP DATABASE ... WITH (FORCE)`, so even a pool your test failed to close
gets kicked.

You can do this from any test directory (a new domain's `tests/<domain>/`
included): test modules import `postgres_dsn` directly, and the root
`tests/conftest.py` re-exports `pg_container` so the session-scoped container
dependency resolves for `tests/substrate` and `tests/research_domain` alike.
No per-directory conftest wiring is needed -- just the one import above.

When docker is not available (no `docker` binary, or `docker info` fails),
the fixture skips your test rather than erroring, so postgres tests degrade
gracefully on machines without docker.

For the measured speed findings behind this design, the TCP-readiness pitfall
in the container probe (don't "simplify" it back to `pg_isready`), and why
you should never fix a slow property test by cutting `max_examples`, see
"Why the suite is as fast as it is" in `docs/TESTING-TUI.md`.

## Import rule

Import everything from the top-level `substrate` package, not from its
submodules -- `substrate/__init__.py` re-exports the full public surface,
`RuntimeBase` included:

```python
from substrate import (
    EventTypeRegistry, EventTypeSpec, GatingTier,
    PostgresEventStore, ProjectionCatalog, ProjectionSpec,
    RuntimeBase,
)
```

`research_domain/event_types.py` and `research_domain/runtime.py` both
follow this rule (`from substrate import EventTypeRegistry, EventTypeSpec,
GatingTier` and `from substrate import PostgresEventStore, RuntimeBase`
respectively) -- match that pattern rather than reaching into
`substrate.event_registry`, `substrate.postgres.events`, `substrate.runtime`,
or any other submodule path directly.

