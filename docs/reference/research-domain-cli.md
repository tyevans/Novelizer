# research-domain CLI

`research-domain` is the console script declared in `pyproject.toml`
(`[project.scripts]`, target `research_domain.cli:main`) and exposed by the
`research_domain` package — the synthetic proof-domain built on `substrate`
(see `substrate/README.md`). It is a thin `click` group over
`research_domain.runtime.ResearchRuntime` with two subcommands: `append`
writes one event to the research stream, `show` replays the stream and prints
a projection. The entry-point target is guarded by
`tests/research_domain/test_console_script.py`, which resolves the
`module:attr` string the same way packaging tools do.

## Global options and DSN resolution

Both subcommands declare the same two options. They are defined on each
subcommand, not on the `main` group, so they must appear *after* the
subcommand name (`research-domain append ... --dsn ...`);
`research-domain --dsn ... append` is a usage error.

| Option | Default | Help text |
|---|---|---|
| `--dsn TEXT` | `None` | Postgres DSN (defaults to `DATABASE_URL` env var) |
| `--stream TEXT` | `research-stream` | Event stream id |

DSN resolution (`research_domain/cli.py::_resolve_dsn`):

1. A truthy `--dsn` value wins. The check is `if dsn:`, so `--dsn ""` is
   treated as not given and falls through to step 2.
2. Otherwise `os.environ.get("DATABASE_URL")` is used; an empty
   `DATABASE_URL` counts as unset.
3. If neither yields a value, the command exits nonzero with
   `click.ClickException("No --dsn given and DATABASE_URL is not set.")`
   before any connection is attempted.

`--stream` is not validated; any string is passed straight through to
`ResearchRuntime(dsn, stream=stream)`. Its CLI default matches the
`stream="research-stream"` default on `ResearchRuntime.__init__`
(`research_domain/runtime.py`).

## `research-domain append EVENT_TYPE PAYLOAD_JSON`

Appends one event to the stream.

```
research-domain append claim.proposed '{"claim_id": "c1", "source_id": "s1"}' --dsn postgresql://...
```

| Argument | Meaning |
|---|---|
| `EVENT_TYPE` | Event type string, stored verbatim. Not validated (see below). |
| `PAYLOAD_JSON` | Event payload as a single JSON string; shell-quote it. Parsed with `json.loads`. |

Execution order (`research_domain/cli.py::append`):

1. `PAYLOAD_JSON` is parsed with `json.loads` *before* the DSN is resolved.
   Invalid JSON raises `click.ClickException(f"Invalid JSON payload: {exc}")` —
   nonzero exit, no connection opened, even if no DSN is available.
2. The DSN is resolved (see "Global options and DSN resolution").
3. The command constructs `ResearchRuntime(dsn, stream=stream)`, connects, and
   calls `ResearchRuntime.append_event(event_type, payload)`. `append_event`
   (`research_domain/runtime.py`) appends the event via
   `RuntimeBase.append` → `PostgresEventStore.append` and then runs a full
   `catch_up()` in the same process, so the runtime's lookup dicts and
   registered projections are recomputed immediately after the write. The
   connection is closed in a `finally` block.
4. On success it prints `Appended <event_type>` (Rich, green).

Validation notes:

- `EVENT_TYPE` is any string; the CLI performs no validation against the
  domain's event registry (see "Error behavior summary" below). The domain's
  canonical event types, per
  `research_domain/event_types.py::build_research_registry()`, are
  `claim.proposed`, `source.corroborated`, `claim.refuted`, and
  `claim.corrected`.
- Payload keys are likewise unvalidated at append time, but the runtime's
  `catch_up()` reads them positionally: `claim.proposed` events are indexed by
  `payload["source_id"]`, and `claim.refuted` / `claim.corrected` events by
  `payload["target_claim_id"]` and `payload["claim_id"]`
  (`ResearchRuntime._refresh_lookup_dicts`). A canonical event type appended
  with a payload missing those keys fails with a `KeyError` during the
  post-append `catch_up()` — after the event has been written.

## `research-domain show PROJECTION_NAME`

Replays the stream and prints one projection as a two-column Rich table
(`Key`, `Value`), titled with the projection name.

```
research-domain show source_coverage --dsn postgresql://...
```

| Argument | Meaning |
|---|---|
| `PROJECTION_NAME` | Name of a projection registered on `ResearchRuntime`. Any string is accepted; unknown names print an empty table (see below). |

Execution order (`research_domain/cli.py::show`):

1. The DSN is resolved (see "Global options and DSN resolution"). Unlike
   `append`, nothing is parsed before this step — a missing DSN is the only
   pre-connection failure mode.
2. The command constructs `ResearchRuntime(dsn, stream=stream)`, connects, and
   calls `ResearchRuntime.catch_up()`. The override in
   `research_domain/runtime.py` does two full passes over the stream: first
   `_refresh_lookup_dicts()` re-reads every event and rebuilds the in-place
   lookup dicts (`_counts_by_source`, `_refuters_by_target`,
   `_superseders_by_target`); then `RuntimeBase.catch_up()`
   (`substrate/runtime.py`) reads the stream again, invalidates each
   registered projection for every event whose type matches its registration,
   and recomputes the dirty entries into `_results`.
3. It fetches `get_projection(projection_name)` — a plain
   `self._results.get(projection_name, {})` lookup — and closes the connection
   in a `finally` block.
4. The resulting dict is rendered as a Rich `Table` titled
   `PROJECTION_NAME` with columns `Key` and `Value`; each entry is added as
   `str(key)` / `str(value)`.

`show` is read-only: it appends nothing and mutates no stored state; all
projection state is recomputed in-process from the event stream on every
invocation, so the output always reflects the full stream at the time of the
command.

Output shape per projection (see "Registered projections" for the full
table): keys are the invalidation keys extracted from event payloads, values
are whatever the catalog's recompute closure returns, stringified —

- `source_coverage`: key = `source_id`, value = claim count (`int`).
- `contradiction_map`: key = `target_claim_id`, value = Python-repr list of
  refuting `claim_id`s (e.g. `['c2', 'c3']`).
- `claim_dependency_graph`: key = `target_claim_id`, value = Python-repr list
  of superseding `claim_id`s.

A `PROJECTION_NAME` outside the registered set is not an error: the table is
printed with its title and headers but no rows, and the exit code is 0. The
append-then-show round trip is covered by
`tests/research_domain/test_cli.py::test_append_then_show_reflects_the_appended_event`.

## Registered projections

`ResearchRuntime.__init__` (`research_domain/runtime.py`) registers exactly
three projections via `RuntimeBase.register_projection(catalog, name,
event_types)`. Each is a single-spec `ProjectionCatalog` built by a
`build_*_catalog` function in `research_domain/projections.py` and bound to
the one event type that invalidates it:

| Projection name | Invalidated by | Key (from payload) | Value | Catalog builder |
|---|---|---|---|---|
| `source_coverage` | `claim.proposed` | `source_id` | count of `claim.proposed` events for that source (`int`) | `build_source_coverage_catalog` |
| `contradiction_map` | `claim.refuted` | `target_claim_id` | list of refuting `claim_id`s, in stream order | `build_contradiction_map_catalog` |
| `claim_dependency_graph` | `claim.corrected` | `target_claim_id` | list of superseding `claim_id`s, in stream order | `build_claim_dependency_catalog` |

Each catalog builder takes a lookup callable and wraps it in a
`ProjectionSpec` (`substrate/projection.py`) whose `invalidation_key` lambda
extracts the key column above from the event payload and whose `recompute` is
the callable itself. `ResearchRuntime` supplies closures over three lookup
dicts (`_counts_by_source`, `_refuters_by_target`, `_superseders_by_target`)
that `_refresh_lookup_dicts()` rebuilds in place from the full stream at the
start of every `catch_up()` — the dicts are mutated, never rebound, so the
closures captured at construction time always read current data.

Behavioral consequences of this table:

- **`source.corroborated` drives no projection.** It is a canonical event
  type in `build_research_registry()`, but no registration names it, so
  appending it never changes any `show` output.
- **Only invalidated keys appear.** `RuntimeBase.catch_up()` invalidates a
  projection once per matching event, then replaces
  `_results[projection_name]` with `ProjectionCatalog.recompute_dirty()`'s
  output — a dict containing only the keys dirtied in that pass. Because the
  CLI constructs a fresh runtime per invocation and replays the whole stream,
  every key ever touched by a matching event is dirtied, so `show` output is
  always complete for the stream.
- **Recompute closures index, not `.get`.** `source_coverage`'s closure is
  `self._counts_by_source[source_id]` (likewise for the other two). This
  cannot `KeyError` during a normal replay: a key is only invalidated by the
  same event type that populates its lookup dict, and
  `_refresh_lookup_dicts()` runs before `RuntimeBase.catch_up()` in
  `ResearchRuntime.catch_up()`.

There is no CLI-level allow-list separate from what is registered on the
runtime. `show` with a name outside this table does not error:
`RuntimeBase.get_projection` (`substrate/runtime.py`) returns
`self._results.get(projection_name, {})`, so an unregistered name yields an
empty dict and the command prints an empty table with exit code 0.

Coverage: the catalogs are unit-tested in
`tests/research_domain/test_projections.py`; the three-projection wiring and
recomputation across appends is covered by
`tests/research_domain/test_runtime.py::test_research_runtime_keeps_all_three_projections_current_across_appends`.

## Error behavior summary

The CLI raises exactly two errors of its own — both `click.ClickException`s,
which click prints as `Error: <message>` and turns into exit code 1. Every
other failure escapes `asyncio.run(_run())` unhandled and surfaces as a raw
Python traceback with a nonzero exit; there is no `try`/`except` around the
runtime calls in either subcommand (`research_domain/cli.py`).

| Condition | Applies to | Result |
|---|---|---|
| Missing or extra positional arguments | both | click `UsageError`: usage text on stderr, exit code 2, nothing executed |
| Malformed `PAYLOAD_JSON` | `append` | `click.ClickException("Invalid JSON payload: ...")`, exit code 1. Raised before DSN resolution, so no connection is attempted even when a DSN is available. Covered by `tests/research_domain/test_cli.py::test_append_rejects_invalid_json_payload`. |
| No `--dsn` and no `DATABASE_URL` (empty strings count as unset — see "Global options and DSN resolution") | both | `click.ClickException("No --dsn given and DATABASE_URL is not set.")`, exit code 1, no connection attempted |
| DSN present but unreachable / invalid | both | Unhandled `asyncpg` exception from `PostgresEventStore.connect()` (`asyncpg.connect(dsn)` in `substrate/postgres/events.py`): raw traceback, nonzero exit. Nothing is written; on `append` the event is not stored. |
| `EVENT_TYPE` not in the event registry | `append` | Not an error — `append` succeeds. Neither `cli.py` nor `ResearchRuntime` consults `build_research_registry()`; the registry exists to declare gating tiers, and `PostgresEventStore.append` inserts any `event_type` string verbatim. The event drives no projection unless a registration names its type. |
| Canonical `EVENT_TYPE` with payload missing its lookup keys (e.g. `claim.proposed` without `source_id`) | `append` | **Partial failure.** The insert commits, then the post-append `catch_up()` hits an unhandled `KeyError` in `ResearchRuntime._refresh_lookup_dicts`: raw traceback, nonzero exit, no `Appended` message — but the event *is* in the stream, and every later `append`/`show` on the same stream re-raises the same `KeyError` during replay. |
| `PROJECTION_NAME` not registered | `show` | Not an error — empty table (title and headers, no rows), exit code 0. `RuntimeBase.get_projection` returns `self._results.get(projection_name, {})`. |
| `--stream` naming a stream with no events | both | Not an error — `append` creates the stream implicitly (streams are just `stream_id` values on rows); `show` replays zero events and prints an empty table, exit code 0 |

Connections are closed in a `finally` block in both subcommands, so a failure
inside `append_event`/`catch_up` still releases the connection before the
traceback propagates.

## Related documents

- [`substrate/README.md`](../../substrate/README.md) — the primitives
  `ResearchRuntime` is built from (`RuntimeBase`, `ProjectionCatalog`,
  `EventTypeRegistry`, `PostgresEventStore`) and the ["Building a new
  domain"](../../substrate/README.md#building-a-new-domain) walkthrough;
  `research_domain` is the worked example of building a domain on substrate
  primitives, and its ["Testing your
  domain"](../../substrate/README.md#testing-your-domain) section shows how to
  consume the shared postgres fixtures from a new domain's tests.
- [`docs/TESTING-TUI.md` — "Why the suite is as fast as it
  is"](../TESTING-TUI.md#why-the-suite-is-as-fast-as-it-is-2026-07-22) — how
  `research_domain`'s postgres-backed tests (`tests/research_domain/`, via the
  `postgres_dsn` fixture) run against the session-scoped pgvector container
  (`pg_container`, surfaced in `tests/conftest.py`) with a throwaway
  `CREATE DATABASE` per test.
- [`docs/explanation/architecture-boundaries.md`](../explanation/architecture-boundaries.md)
  — why `research_domain` may only import `substrate`'s top-level package, not
  its submodules.
