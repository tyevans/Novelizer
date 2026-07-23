# Research Domain Runtime — Design

**Status: approved, not yet implemented.**

## Problem

`research_domain/` proves `substrate/`'s event/gating/projection primitives generalize (per `docs/superpowers/specs/2026-07-20-cross-domain-substrate-design.md` and the Contradiction Map / Claim Dependency Graph work), but it isn't actually *usable* — there's no way to append an event and see a projection update short of writing a Python script that duplicates what `tests/research_domain/test_end_to_end.py` already does by hand. There's no runtime, no CLI, nothing analogous to `novelizer`'s `Runtime` + `novelizer` CLI entrypoint.

`novelizer`'s own `Runtime`/`Projector` are not directly reusable code: `novelizer/canon/projector.py` hand-writes SQLite tables and per-`EventType` dispatch logic specific to chapters/characters/threads/etc, and `novelizer/runtime.py` wires in voice packs, chat, knowledge-graph stores, and eight LLM agent runners — all fiction-specific. What *is* reusable is the **pattern**: connect an event store, replay events to keep projections current, expose read access, close cleanly. This design extracts that pattern as a new `substrate` primitive and uses it to build the first real `research_domain` runtime + CLI.

## Design

### 1. `substrate/runtime.py` — `RuntimeBase`

A minimal, storage-agnostic lifecycle class:

```python
class RuntimeBase:
    def __init__(self, event_store: PostgresEventStore, stream: str) -> None: ...

    def register_projection(
        self, catalog: ProjectionCatalog, projection_name: str, event_types: set[str]
    ) -> None: ...

    async def connect(self) -> None: ...
    async def catch_up(self) -> None: ...
    async def close(self) -> None: ...

    def get_projection(self, projection_name: str) -> dict: ...
```

- `connect()` calls `event_store.connect()`.
- `register_projection` records a `(catalog, projection_name, event_types)` triple. Multiple registrations may reference the same catalog with different projection names (a catalog can hold more than one `ProjectionSpec`).
- `catch_up()` reads every event in `stream` once via `event_store.read_stream(stream)`. For each event, for each registered triple where `event["event_type"] in event_types`, it calls `catalog.invalidate(projection_name, event)` (the event's `payload` dict is wrapped in a lightweight object exposing `.payload`, matching what `ProjectionSpec.invalidation_key` lambdas already expect — see `tests/research_domain/test_end_to_end.py`'s `_TargetClaimEvent` pattern). After the full pass, it calls `catalog.recompute_dirty(projection_name)` once per registered triple and caches the result.
- `get_projection(name)` returns the cached result dict from the most recent `catch_up()`.
- `close()` calls `event_store.close()`.

This is a new file in `substrate/`, added to `substrate/__init__.py`'s public API (`RuntimeBase`) per the import-boundary work from the prior session. `novelizer/` is not touched or migrated onto this — `RuntimeBase` is proven by `research_domain` as its second real consumer, the same way the projection primitives were proven by two domains before being trusted as generic.

### 2. `research_domain/runtime.py` — `ResearchRuntime`

Domain wiring on top of `RuntimeBase`:

```python
class ResearchRuntime(RuntimeBase):
    def __init__(self, dsn: str, stream: str = "research-stream") -> None:
        super().__init__(PostgresEventStore(dsn), stream)
        # builds and registers source_coverage, contradiction_map,
        # claim_dependency_graph catalogs against their triggering event types
        ...

    async def append_event(self, event_type: str, payload: dict) -> None:
        await self._event_store.append(self._stream, event_type, payload)
        await self.catch_up()
```

Registration map:
- `source_coverage` (from `build_source_coverage_catalog`) triggers on `claim.proposed`.
- `contradiction_map` (from `build_contradiction_map_catalog`) triggers on `claim.refuted`.
- `claim_dependency_graph` (from `build_claim_dependency_catalog`) triggers on `claim.corrected`.

The three catalog-builder functions from `research_domain/projections.py` take a `recompute` callable that needs a lookup dict built from the event stream (per their existing signatures — e.g. `count_claims_for_source: Callable[[str], int]`). `ResearchRuntime` builds these lookup dicts from `event_store.read_stream()` results inside its constructor logic before registering, matching the pattern already established in `tests/research_domain/test_end_to_end.py`.

`append_event` writes the event, then immediately calls `catch_up()` so projections reflect it — simple and correct for this CLI-driven, low-volume use case; no incremental-invalidation-only optimization needed here (YAGNI).

### 3. `research_domain/cli.py` — minimal CLI

A `click` command group, styled after `novelizer`'s CLI (readable subcommands, `rich` for output) but not sharing code with it:

```
research-domain append <event-type> <json-payload> [--dsn ...]
research-domain show <projection-name> [--dsn ...]
```

- `append`: parses `<json-payload>` as JSON, calls `ResearchRuntime.append_event(event_type, payload)`.
- `show`: calls `catch_up()` then prints `get_projection(projection_name)` via a `rich.table.Table` (or a simple pretty-printed dict if the projection value isn't tabular — projections here are `dict[str, int]` or `dict[str, list[str]]`, both simple to render as two-column tables).
- `--dsn` defaults to reading `DATABASE_URL` from the environment (matching how `tests/substrate/postgres_fixture.py` already resolves a DSN for this repo), with an explicit `--dsn` override.
- Registered as a `[project.scripts]` entry in `pyproject.toml`: `research-domain = "research_domain.cli:main"`.

### Non-goals

- No LLM-backed agents. `research_domain/roles.py`'s six `AgentSpec` stubs are untouched — the CLI's `append` command is the only way events enter the stream, driven by a human, not an agent.
- No gating/autonomy enforcement in the CLI — `append` writes directly, unmediated by `is_gated`/tier checks. Gating enforcement is deferred along with real agents (it only matters once something autonomous is proposing events that need review).
- No new Postgres schema beyond what `PostgresEventStore` (from the prior substrate work) already creates.
- No changes to `novelizer/` code, `novelizer`'s `Runtime`, or `novelizer`'s CLI.
- No incremental/partial invalidation optimization in `catch_up()` — every call does a full replay-and-recompute over the whole stream. Fine at this scale (a CLI-driven proof-of-concept, not a production event volume); revisit only if it becomes a real bottleneck.

## Testing

- `tests/substrate/test_runtime.py` (new): unit tests for `RuntimeBase.catch_up()`'s dispatch logic against a real Postgres event store (extending the existing `postgres_dsn` fixture pattern) — register two fake catalogs with different triggering event types, append events of both types, assert only the matching catalog's projection updates.
- `tests/research_domain/test_runtime.py` (new): integration test constructing a real `ResearchRuntime`, appending `claim.proposed`/`claim.refuted`/`claim.corrected` events (reusing the same event shapes as `tests/research_domain/test_end_to_end.py`), and asserting all three projections (`source_coverage`, `contradiction_map`, `claim_dependency_graph`) show correct values after `catch_up()`.
- `tests/research_domain/test_cli.py` (new): CLI smoke test using `click.testing.CliRunner` — `append` a `claim.proposed` event, `show source_coverage`, assert the output reflects it.
- Existing `tests/substrate/`, `tests/research_domain/` suites continue to pass unmodified. Run targeted, not full-suite (standing project instruction: finite compute resources).

## Non-goals recap for the whole-branch boundary

This design deliberately does not attempt to unify `novelizer/canon/projector.py` and the new `substrate/runtime.py` — they solve overlapping but differently-shaped problems (novelizer's projector does fine-grained SQL-table-per-entity projection with retcon/supersession semantics; `RuntimeBase` does coarse-grained "replay stream, recompute named projections"). Forcing them onto one abstraction now would be premature generalization from a sample size of one real production system (novelizer) and one proof-of-concept (research_domain).
