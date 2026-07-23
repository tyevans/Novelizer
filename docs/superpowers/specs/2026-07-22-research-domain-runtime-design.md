# Research domain runtime design

## Problem (unchanged)

`substrate/` (see `docs/superpowers/specs/2026-07-20-cross-domain-substrate-design.md`)
generalized event-sourcing primitives -- `EventTypeRegistry`/`is_gated`,
`ProjectionCatalog`, `AgentSpec` -- out of the fiction domain and proved them
domain-neutral with a synthetic second domain's tests
(`tests/substrate/test_second_domain_pluggability.py`). Those tests exercise
gating logic directly against ad hoc registries built inline in the test
module; they prove the *policy* primitive is domain-neutral, but nothing yet
proves a second domain can be stood up end to end as a real package: its own
event types, its own projections, its own runtime lifecycle (connect, append,
catch up, close), and its own CLI.

`substrate/runtime.py` defines `RuntimeBase`, a storage-agnostic lifecycle
(connect a `PostgresEventStore`, register `ProjectionCatalog`s against event
types, replay the stream on `catch_up()`, expose results via
`get_projection()`, `close()`). `RuntimeBase` itself has no domain-specific
subclass in `substrate/` -- it exists to be subclassed, but until now no
subclass existed anywhere in the codebase, so its design had never been
checked against a second, differently-shaped domain's actual wiring: does a
subclass need to override `catch_up()` to refresh its own lookup state
before delegating to the base replay, does `append()` need a sibling that
also triggers a re-catch-up, etc.

`research_domain/` was scaffolded (`event_types.py` with
`build_research_registry()`, `roles.py` with a `ROLE_REGISTRY` of stub
`AgentSpec`s, `projections.py` with three `ProjectionCatalog` builders) but,
before this work, had no `runtime.py` tying those event types and
projections to a live `PostgresEventStore`, and no `cli.py` giving a human
or script a way to append research events and inspect projection state
without writing Python. Concretely:

- `research_domain/projections.py` exports `build_source_coverage_catalog`,
  `build_contradiction_map_catalog`, and `build_claim_dependency_catalog`,
  each taking a lookup closure (`source_id -> count`,
  `target_claim_id -> [claim_id]`, `target_claim_id -> [claim_id]`) -- but
  nothing in the package populated those closures from real event data or
  registered the catalogs against a stream.
- `research_domain/event_types.py`'s `build_research_registry()` declares
  four event types (`claim.proposed`, `source.corroborated`,
  `claim.refuted`, `claim.corrected`) with a two-tier gating vocabulary
  (`RESEARCH_TIER_ORDER = ["auto", "reviewed"]`), but no runtime appended or
  replayed events of those types against a real stream.
- `research_domain/roles.py`'s six `ROLE_REGISTRY` entries are all
  `tool_grant=None` stubs (`_stub_construct`) -- out of scope for this work,
  carried over unchanged (see Non-goals).

Without a `research_domain/runtime.py` and `research_domain/cli.py`, the
second-domain proof stayed partial: policy and projection primitives were
shown to be reusable in isolation, but not as a working, appendable,
queryable domain runtime a caller could actually drive.

## Design (unchanged)

Two new modules close the gap, both built strictly on existing substrate
and `research_domain` primitives -- no changes to `substrate/runtime.py`,
`event_types.py`, `roles.py`, or `projections.py` were needed to make this
work:

- `research_domain/runtime.py` adds `ResearchRuntime(RuntimeBase)`, which
  constructs a `PostgresEventStore` from a DSN, registers the three
  `research_domain/projections.py` catalogs against the event types that
  should invalidate them, and overrides `catch_up()` to refresh its own
  lookup dicts before delegating to `RuntimeBase.catch_up()`.
- `research_domain/cli.py` adds a `click` group (`append`, `show`) that
  drives `ResearchRuntime` through `connect()` / `append_event()` /
  `catch_up()` / `get_projection()` / `close()` from the command line,
  resolving the DSN from `--dsn` or the `DATABASE_URL` environment
  variable.

The central design question this answers is exactly the one the Problem
section poses: does a subclass of `RuntimeBase` need to refresh its own
state before replaying the stream into projections, and does `append()`
need a sibling that also triggers a re-catch-up? `ResearchRuntime` answers
both yes, for a structural reason specific to how its three projection
catalogs are built.

Each of `build_source_coverage_catalog`, `build_contradiction_map_catalog`,
and `build_claim_dependency_catalog` (`research_domain/projections.py`)
takes a *lookup closure* -- `source_id -> count`,
`target_claim_id -> [claim_id]`, `target_claim_id -> [claim_id]` -- that
the catalog calls during `recompute_dirty()`. `RuntimeBase.catch_up()`
only does two things: mark projections dirty from matching events, and
call `recompute_dirty()`. It has no hook for populating the data a
closure reads from -- that data has to already be current by the time
`recompute_dirty()` runs. So `ResearchRuntime.__init__` allocates three
plain dicts (`_counts_by_source`, `_refuters_by_target`,
`_superseders_by_target`) as instance attributes, and builds each lookup
closure as a lambda that indexes into one of them. Because the closures
are captured once, at construction time, and the dicts are mutated in
place (`.clear()` then repopulated) rather than rebound to a new object,
the closures always see whatever the dicts currently hold -- there is no
need to re-register catalogs or re-create closures on every refresh.

`_refresh_lookup_dicts()` is the method that keeps those dicts current: it
reads the full stream with `self._event_store.read_stream(self._stream)`,
clears all three dicts, and replays every event into the matching dict
based on `event["event_type"]` (`claim.proposed` increments
`_counts_by_source`; `claim.refuted` appends into
`_refuters_by_target[target_claim_id]`; `claim.corrected` appends into
`_superseders_by_target[target_claim_id]`). `ResearchRuntime.catch_up()`
overrides the base method to call `await self._refresh_lookup_dicts()`
first, then `await super().catch_up()` -- so by the time
`RuntimeBase.catch_up()` invalidates and recomputes the three catalogs,
the closures they call already read fresh data.

The other override, `append_event()`, exists because `RuntimeBase.append()`
only writes to the event store -- it does not touch projections. A caller
that wants an append to be immediately reflected in
`get_projection()` needs a catch-up afterward, so `append_event()` is a
thin convenience: `await self.append(event_type, payload)` followed by
`await self.catch_up()`. `RuntimeBase.append()` itself is unchanged and
still available directly for callers (e.g. tests) that want to batch
several appends before paying for a single catch-up.

`research_domain/cli.py` is a thin `click` wrapper with no logic of its
own beyond DSN resolution and Rich-table rendering: `append` parses a
JSON payload argument and calls `runtime.append_event(event_type,
payload)`; `show` calls `runtime.catch_up()` then
`runtime.get_projection(projection_name)` and renders the resulting dict
as a two-column Rich `Table` (`Key`, `Value`). Both commands construct a
fresh `ResearchRuntime`, `connect()`, do the one operation, and `close()`
in a `finally` block, run through `asyncio.run()` since the runtime's
methods are all `async def`.

Net effect: the second-domain proof from the Problem section is now
complete end to end. `ResearchRuntime` demonstrates that a `RuntimeBase`
subclass which needs derived/indexed state ahead of projection recompute
should override `catch_up()` to refresh that state first and delegate to
`super().catch_up()` for the invalidate/recompute mechanics, and that a
convenience append-then-catch-up method is a reasonable addition when
callers want appends to be visible immediately -- both patterns available
for future domains to follow without further changes to `substrate/`.

## 1. substrate/runtime.py -- RuntimeBase (unchanged)

`RuntimeBase` (`substrate/runtime.py`) is the storage-agnostic lifecycle every domain runtime subclasses. This section documents it as-is -- **no changes were made to this file** for `research_domain/runtime.py` to build on it; it is included here as the reference contract `ResearchRuntime` (next section) is checked against.

Construction takes a `PostgresEventStore` and a stream name:

```python
def __init__(self, event_store: PostgresEventStore, stream: str) -> None:
    self._event_store = event_store
    self._stream = stream
    self._registrations: list[_ProjectionRegistration] = []
    self._results: dict[str, dict] = {}
```

`register_projection(catalog, projection_name, event_types)` appends a `_ProjectionRegistration(catalog, projection_name, frozenset(event_types))` to `self._registrations`. This is how a subclass tells `RuntimeBase` which `ProjectionCatalog` to invalidate/recompute when which event types appear in the stream -- registration is expected to happen once, typically in the subclass `__init__`, before any `catch_up()` call.

Five async/sync methods make up the rest of the lifecycle:

- `connect()` -- awaits `self._event_store.connect()`. No other setup.
- `append(event_type, payload, **kwargs)` -- awaits and returns `self._event_store.append(self._stream, event_type, payload, **kwargs)`. This writes to the event store only; it does not touch any registered projection or trigger a recompute.
- `catch_up()` -- reads the full stream once (`await self._event_store.read_stream(self._stream)`), then for each registration: invalidates the catalog for every event whose `event_type` is in that registration's `event_types` set (wrapping each event's payload in an `_EventView` so it exposes the `.payload` attribute a `ProjectionSpec.invalidation_key` lambda expects), then calls `await registration.catalog.recompute_dirty(registration.projection_name)` and stores the result in `self._results[registration.projection_name]`.
- `get_projection(projection_name)` -- returns `self._results.get(projection_name, {})`. Purely a read of whatever the last `catch_up()` computed; it does not read the event store itself.
- `close()` -- awaits `self._event_store.close()`.

`RuntimeBase.catch_up()` has no hook for a subclass to populate ancillary state before the invalidate/recompute pass runs -- it does exactly two things (mark dirty, recompute) and nothing else. It also has no append-and-refresh convenience: `append()` alone leaves `self._results` stale until `catch_up()` is called again. Both of these are load-bearing facts for the next section: a subclass whose projection closures depend on derived/indexed state (as `ResearchRuntime`'s do) must override `catch_up()` to refresh that state first and delegate to `super().catch_up()`, and must add its own append-then-catch-up convenience if it wants writes to be immediately visible via `get_projection()`.

## 2. research_domain/runtime.py — ResearchRuntime (unchanged)

`ResearchRuntime` (`research_domain/runtime.py`) is the concrete `RuntimeBase` subclass this whole design exists to justify. It is documented here as the reference implementation of the two patterns the previous section flagged as missing from `RuntimeBase` itself: refreshing derived state ahead of `catch_up()`'s invalidate/recompute pass, and an append-then-catch-up convenience.

Construction wires all three `research_domain/projections.py` catalogs to a single Postgres-backed stream in one pass:

```python
def __init__(self, dsn: str, stream: str = "research-stream") -> None:
    super().__init__(PostgresEventStore(dsn), stream)

    self._counts_by_source: dict[str, int] = {}
    self._refuters_by_target: dict[str, list[str]] = {}
    self._superseders_by_target: dict[str, list[str]] = {}

    source_coverage = build_source_coverage_catalog(
        lambda source_id: self._counts_by_source[source_id]
    )
    contradiction_map = build_contradiction_map_catalog(
        lambda target_claim_id: self._refuters_by_target[target_claim_id]
    )
    claim_dependency_graph = build_claim_dependency_catalog(
        lambda target_claim_id: self._superseders_by_target[target_claim_id]
    )

    self.register_projection(source_coverage, "source_coverage", {"claim.proposed"})
    self.register_projection(contradiction_map, "contradiction_map", {"claim.refuted"})
    self.register_projection(
        claim_dependency_graph, "claim_dependency_graph", {"claim.corrected"}
    )
```

The three dicts (`_counts_by_source`, `_refuters_by_target`, `_superseders_by_target`) are allocated once as instance attributes and never rebound; each catalog's lookup closure captures one dict by reference at construction time. This is why `_refresh_lookup_dicts()` (below) can update the dicts in place via `.clear()` and repopulation rather than needing to re-register catalogs or rebuild closures on every refresh -- the closures always read whatever the dict currently holds. `register_projection` (inherited from `RuntimeBase`, see section 1) ties each catalog to the single event type that should invalidate it: `claim.proposed` for `source_coverage`, `claim.refuted` for `contradiction_map`, `claim.corrected` for `claim_dependency_graph`. `source.corroborated` is not registered against any of the three catalogs -- it is a valid event type in `research_domain/event_types.py`'s registry (see the Appendix) but none of the current projections read it.

`_refresh_lookup_dicts()` is the async method that keeps the three dicts current with the full event stream:

```python
async def _refresh_lookup_dicts(self) -> None:
    events = await self._event_store.read_stream(self._stream)
    self._counts_by_source.clear()
    self._refuters_by_target.clear()
    self._superseders_by_target.clear()
    for event in events:
        if event["event_type"] == "claim.proposed":
            source_id = event["payload"]["source_id"]
            self._counts_by_source[source_id] = self._counts_by_source.get(source_id, 0) + 1
        elif event["event_type"] == "claim.refuted":
            target_id = event["payload"]["target_claim_id"]
            self._refuters_by_target.setdefault(target_id, []).append(event["payload"]["claim_id"])
        elif event["event_type"] == "claim.corrected":
            target_id = event["payload"]["target_claim_id"]
            self._superseders_by_target.setdefault(target_id, []).append(event["payload"]["claim_id"])
```

It reads the entire stream on every call (`await self._event_store.read_stream(self._stream)`) -- there is no incremental/delta tracking -- clears all three dicts unconditionally, then replays every event exactly once, branching on `event["event_type"]` to increment `_counts_by_source` for `claim.proposed`, append into `_refuters_by_target[target_claim_id]` for `claim.refuted`, or append into `_superseders_by_target[target_claim_id]` for `claim.corrected`. Any other event type in the stream (e.g. `source.corroborated`) is silently skipped by this method -- it plays no part in the three lookup dicts.

`catch_up()` overrides `RuntimeBase.catch_up()` to guarantee `_refresh_lookup_dicts()` has run before the base class's invalidate/recompute pass reads the closures:

```python
async def catch_up(self) -> None:
    await self._refresh_lookup_dicts()
    await super().catch_up()
```

This is the direct answer to the question section 1 left open: `RuntimeBase.catch_up()` has no hook for populating ancillary state ahead of `recompute_dirty()`, so `ResearchRuntime` supplies that hook itself, purely by ordering -- refresh first, then delegate. `super().catch_up()` still does exactly what it does for any `RuntimeBase` instance (mark projections dirty from matching events, call `recompute_dirty()` on each, store results in `self._results`); `ResearchRuntime` does not touch that mechanics.

`append_event()` is the second addition, a convenience `RuntimeBase` itself has no equivalent for:

```python
async def append_event(self, event_type: str, payload: dict) -> None:
    await self.append(event_type, payload)
    await self.catch_up()
```

`self.append(...)` is the inherited `RuntimeBase.append()` (write-only, no projection effect on its own); the trailing `self.catch_up()` is what makes the write immediately visible through `get_projection()`. `RuntimeBase.append()` remains available directly and unwrapped on `ResearchRuntime` instances for callers that want to batch several appends before paying for a single catch-up.

`connect()`, `get_projection()`, and `close()` are not overridden -- `ResearchRuntime` uses `RuntimeBase`'s implementations of all three unchanged.

## 3. research_domain/cli.py — minimal CLI (unchanged)

`research_domain/cli.py` is a thin `click` command group giving a human or script two operations on a `ResearchRuntime` (previous section): append an event, and inspect a projection's current value. It has no logic beyond DSN resolution, JSON parsing, and Rich-table rendering -- every stateful operation is delegated straight to `ResearchRuntime`.

DSN resolution is centralized in one helper used by both commands:

```python
def _resolve_dsn(dsn: str | None) -> str:
    if dsn:
        return dsn
    env_dsn = os.environ.get("DATABASE_URL")
    if not env_dsn:
        raise click.ClickException("No --dsn given and DATABASE_URL is not set.")
    return env_dsn
```

An explicit `--dsn` always wins; otherwise the `DATABASE_URL` environment variable is used; if neither is present, `click.ClickException` reports the problem as a normal CLI error rather than a raw traceback.

The group itself, `main`, carries no options of its own -- it exists only as the parent for the two subcommands:

```python
@click.group()
def main() -> None:
    """research_domain: append events to the research stream and inspect projections."""
```

`append EVENT_TYPE PAYLOAD_JSON` takes two positional arguments plus `--dsn` and `--stream` (default `"research-stream"`, matching `ResearchRuntime`'s own default). It parses `PAYLOAD_JSON` with `json.loads`, converting a `json.JSONDecodeError` into a `click.ClickException` rather than letting it propagate raw. The async body constructs a fresh `ResearchRuntime(resolved_dsn, stream=stream)`, `connect()`s, calls `await runtime.append_event(event_type, payload)` -- the `ResearchRuntime` convenience from section 2 that appends and then immediately catches up -- and `close()`s in a `finally` block so the connection is released even if the append raises. The whole coroutine runs through `asyncio.run(_run())`, since every `ResearchRuntime` method is `async def`; on success the command prints `"Appended {event_type}"` in green via `rich.console.Console`.

`show PROJECTION_NAME` takes one positional argument plus the same `--dsn` and `--stream` options. Its async body follows the identical connect/operate/close-in-finally shape: construct `ResearchRuntime(resolved_dsn, stream=stream)`, `connect()`, then `await runtime.catch_up()` followed by `runtime.get_projection(projection_name)` (both inherited from `RuntimeBase`, see section 1, with `catch_up()` overridden by `ResearchRuntime` as described in section 2) to get the projection's current `dict`, and `close()` in `finally`. The result dict is rendered as a two-column Rich `Table` (`Key`, `Value` columns, one row per dict entry, `str()`-coerced) titled with the projection name and printed to the console.

Both commands construct their own `ResearchRuntime` per invocation -- the CLI is stateless between calls; every invocation reconnects, does its one operation, and disconnects. Neither command validates `event_type` or `projection_name` against `research_domain/event_types.py`'s registry or `research_domain/runtime.py`'s three registered projection names (see sections 1 and 2, and the Appendix below) -- an unrecognized `event_type` is written to the stream as-is (Postgres event store has no schema enforcement at this layer), and an unrecognized `projection_name` passed to `show` returns `{}` from `get_projection()` (`RuntimeBase.get_projection()`'s documented fallback, section 1) and renders as an empty table rather than an error.

## Non-goals (unchanged)

This work adds `research_domain/runtime.py` and `research_domain/cli.py` only. The following stayed exactly as they were before this work, and nothing here changes them:

- **`substrate/runtime.py` / `RuntimeBase` is untouched.** `ResearchRuntime` is a plain subclass built entirely against the existing contract documented in section 1 (`connect()`, `append()`, `catch_up()`, `get_projection()`, `close()`, `register_projection()`). No method signature, no default behavior, and no hook was added to `RuntimeBase` itself -- the "refresh derived state before recompute" and "append-then-catch-up" patterns both live in `ResearchRuntime` (section 2), not pushed down into the base class.
- **`research_domain/event_types.py` is untouched.** `build_research_registry()`'s four `EventTypeSpec`s (`claim.proposed`, `source.corroborated`, `claim.refuted`, `claim.corrected`) and the `RESEARCH_TIER_ORDER = ["auto", "reviewed"]` vocabulary are exactly as scaffolded before this work; `ResearchRuntime` and `cli.py` read/write events of these types but neither adds, renames, nor changes the gating tier of any of them (see the Appendix below for the full reference).
- **`research_domain/projections.py` is untouched.** `build_source_coverage_catalog`, `build_contradiction_map_catalog`, and `build_claim_dependency_catalog` keep their existing signatures (each taking a single lookup closure); `ResearchRuntime.__init__` calls them as-is and supplies the closures via its own instance dicts (section 2) rather than the catalogs changing shape to accept a runtime.
- **`research_domain/roles.py` and `ROLE_REGISTRY` are untouched and out of scope.** All six `AgentSpec` entries (`scout`, `extractor`, `verifier`, `retractor`, `synthesizer`, `coverage_analyst`) remain `tool_grant=None` stubs built by `_stub_construct` -- this work does not give any role a real `tool_grant`, does not wire any role to `ResearchRuntime`, and does not change `_stub_construct`'s behavior. Turning these into non-stub agents is separate follow-on work, not addressed here (see the Appendix below for the full roster).
- **No schema validation was added anywhere in this stack.** Neither `ResearchRuntime.append_event()` nor `research_domain/cli.py`'s `append` command validates `event_type` against `build_research_registry()`, or validates a payload's shape against anything -- an unrecognized `event_type` or malformed payload is written to the Postgres stream as-is, exactly as `PostgresEventStore.append()` already permitted before this work. This work does not introduce a validation layer.
- **No new projections were added.** `ResearchRuntime` registers exactly the three catalogs that already existed in `research_domain/projections.py`; it does not add a fourth projection, and `cli.py show` has no special-cased list of valid projection names -- an unrecognized `projection_name` simply renders as `RuntimeBase.get_projection()`'s empty-dict fallback (section 1), unchanged from how `RuntimeBase` already behaved.
- **No auth, multi-tenancy, or migration tooling was added.** `cli.py`'s DSN resolution (`--dsn` or `DATABASE_URL`) is the only configuration surface; there is no user/session concept, no per-tenant stream naming beyond the `--stream` option's single default (`"research-stream"`), and no schema-migration command. These remain out of scope, as they were for `substrate/runtime.py` itself.

## Testing (unchanged)

Two test modules exercise the new code directly; the four pre-existing `research_domain` test modules (`test_events.py`, `test_event_types.py`, `test_projections.py`, `test_roles.py`, plus `test_end_to_end.py`) are unchanged by this work and are not described here beyond noting how `test_end_to_end.py` relates to `test_runtime.py` below.

`tests/research_domain/test_runtime.py` (37 lines, one test) exercises `ResearchRuntime` end to end against a real Postgres container via the shared `postgres_dsn` fixture (`tests/substrate/postgres_fixture.py`, which skips with `pytest.skip("docker not available in this environment")` when Docker is not present). `test_research_runtime_keeps_all_three_projections_current_across_appends` constructs one `ResearchRuntime`, `connect()`s, and drives all three projections through a single sequence of `append_event()` calls, asserting `get_projection(...)` after each append:

- Two `claim.proposed` appends (`source-a`, `source-b`) are each immediately followed by an assertion that `source_coverage == {"source-a": 1, "source-b": 1}` -- proving `append_event()`'s trailing `catch_up()` (section 2) makes the write visible without a separate call.
- A `claim.refuted` append (`claim-2` refuting `claim-1`) is followed by asserting `contradiction_map == {"claim-1": ["claim-2"]}`.
- A third `claim.proposed` (`source-c`) plus a `claim.corrected` append (`claim-3` correcting `claim-2`) are followed by asserting both `claim_dependency_graph == {"claim-2": ["claim-3"]}` and that `source_coverage` has grown to include `source-c` (`{"source-a": 1, "source-b": 1, "source-c": 1}`) -- checking that an append affecting one projection's lookup dict does not disturb the other two, since `_refresh_lookup_dicts()` (section 2) rebuilds all three dicts from the full stream on every `catch_up()`.

The test uses `runtime.close()` in a `finally` block and a dedicated stream name (`"research-runtime-test-stream"`) to avoid cross-test interference. It does not separately test `RuntimeBase.append()` without a following `catch_up()`, or call `catch_up()` directly -- both are exercised only implicitly through `append_event()`.

`tests/research_domain/test_cli.py` (41 lines, two tests) exercises `research_domain/cli.py` through `click.testing.CliRunner`, also against the real `postgres_dsn` fixture:

- `test_append_then_show_reflects_the_appended_event` invokes `append claim.proposed '{"claim_id": ..., "source_id": "source-a", ...}' --dsn <dsn> --stream cli-test-stream`, asserts `exit_code == 0` and `"Appended"` in the output, then invokes `show source_coverage --dsn <dsn> --stream cli-test-stream` and asserts `exit_code == 0` and `"source-a"` appears in the rendered Rich table output. This is the only test that exercises `cli.py`'s two commands together as a caller would from a shell, and the only one that checks the CLI's success-path console output text rather than calling `ResearchRuntime` directly.
- `test_append_rejects_invalid_json_payload` invokes `append claim.proposed not-json --dsn <dsn> --stream cli-test-stream-2` and asserts `exit_code != 0` and `"Invalid JSON"` appears in the output -- covering the `json.JSONDecodeError` -> `click.ClickException` path described in section 3, without needing a live append to have happened first.

Neither CLI test exercises `_resolve_dsn()`'s `DATABASE_URL` fallback or its `click.ClickException` when both `--dsn` and `DATABASE_URL` are absent -- both tests always pass `--dsn` explicitly. Neither test exercises `show` against an unregistered `projection_name` (the `RuntimeBase.get_projection()` empty-dict fallback noted in sections 1 and 3), and neither test exercises an `event_type` outside `build_research_registry()`'s four types.

`tests/research_domain/test_end_to_end.py` predates this work and is unchanged: it builds `PostgresEventStore`, `EventTypeRegistry`, and the three projection catalogs directly, without going through `ResearchRuntime` or `cli.py` at all -- it is the "policy primitive is domain-neutral" proof the Problem section describes as already existing before this work, and remains the point of contrast for what `test_runtime.py` newly proves (a real subclass lifecycle, not just inline catalog wiring).

All new and pre-existing `research_domain`/`substrate` Postgres-backed tests share the same Docker-skip behavior via `postgres_dsn`; per the "Milestone execution state" note in project memory, test suites are not run in the main checkout (DB-lock incident) -- this document was validated by reading `tests/research_domain/test_runtime.py`, `test_cli.py`, `test_end_to_end.py`, and `tests/substrate/postgres_fixture.py` directly, not by executing them.

## Non-goals recap for the whole-branch boundary (unchanged)

The Non-goals section above scopes this specific piece of work (`research_domain/runtime.py` and `research_domain/cli.py`) against the files it touches. This recap restates, for the whole branch this document covers, exactly which existing modules stayed frozen and why that boundary was chosen -- not new rationale, just the inventory, so a reader who jumps straight to this point in the outline does not have to reconstruct it from the Non-goals prose above.

Frozen for the whole branch, confirmed unchanged by direct read of current source:

- `substrate/runtime.py` -- `RuntimeBase` (section 1). No new method, no changed signature, no new hook for subclass state refresh.
- `research_domain/event_types.py` -- `build_research_registry()` and `RESEARCH_TIER_ORDER = ["auto", "reviewed"]` are exactly as scaffolded (see the Appendix immediately below for the full table). Neither `ResearchRuntime` nor `cli.py` adds, renames, or retiers any event type.
- `research_domain/projections.py` -- `build_source_coverage_catalog`, `build_contradiction_map_catalog`, `build_claim_dependency_catalog` keep their existing single-closure-argument signatures; `ResearchRuntime` supplies closures over its own instance dicts (section 2) rather than the catalogs changing shape.
- `research_domain/roles.py` -- `ROLE_REGISTRY`'s six `AgentSpec` entries are all still `tool_grant=None` stubs built by `_stub_construct` (see the Appendix immediately below for the full roster). No role was wired to `ResearchRuntime`, and `_stub_construct`'s behavior (return a dict tagging `role` and `context`, nothing else) did not change.

The boundary this recap draws is the same one the Problem and Non-goals sections already argue for: this work closes the runtime/CLI gap identified in the Problem section by composing existing primitives, not by extending or reshaping them. The two Appendix sections that follow give the reference tables for `event_types.py` and `roles.py` cited above, for lookup without paging back through the Design or Non-goals prose.

## Appendix: event_type -> gating_tier reference (new)

`research_domain/event_types.py`'s `build_research_registry()` is unchanged by this work (see Non-goals and the recap above); this appendix is a direct-read reference table for the four `EventTypeSpec`s it registers, cited by name throughout the Design, section 2, and Non-goals sections above.

| event_type | gating_tier | tier_level |
|---|---|---|
| `claim.proposed` | `tiered` | `auto` |
| `source.corroborated` | `never` | -- |
| `claim.refuted` | `always` | -- |
| `claim.corrected` | `tiered` | `reviewed` |

Only the two `tiered` event types (`claim.proposed`, `claim.corrected`) carry a `tier_level`; `never` and `always` gating tiers don't consult a tier level at all, so `source.corroborated` and `claim.refuted` have none.

`RESEARCH_TIER_ORDER = ["auto", "reviewed"]` is the ordered tier vocabulary that gives meaning to the `tier_level` values above -- `"auto"` is the first/lowest tier, `"reviewed"` the second/highest. This ordering is what a `tiered` gating check compares against to decide whether a given tier level satisfies a gate: `claim.proposed` at `"auto"` is the lower bar, `claim.corrected` at `"reviewed"` the higher one. Neither `ResearchRuntime` (section 2) nor `research_domain/cli.py` (section 3) reads `RESEARCH_TIER_ORDER` or performs any gating check itself -- both simply append and replay events of these four types without validating tier or gating at all (see Non-goals); the vocabulary exists in `research_domain/event_types.py` for whatever future gating-enforcement layer consults `EventTypeRegistry`/`is_gated` (the `substrate` primitive this registry is built from), not for anything added in this document's scope.


## Appendix: ROLE_REGISTRY reference (new)

`research_domain/roles.py` is unchanged by this work (see Non-goals and the recap above); this appendix is a direct-read reference for the six `AgentSpec` entries it registers in `ROLE_REGISTRY`, cited by name in the Non-goals sections above.

Ordered as they appear in `ROLE_REGISTRY`:

1. `scout`
2. `extractor`
3. `verifier`
4. `retractor`
5. `synthesizer`
6. `coverage_analyst`

All six are `tool_grant=None` stubs built by `_stub_construct(name)`, which returns a `construct` closure that ignores its context argument's shape and simply returns `{"role": name, "context": ctx}`. This is out of scope for this work, exactly as the Non-goals section above states — see that section for the rationale; it is not restated here.
