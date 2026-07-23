# research-domain CLI

`research-domain` is the console script (`[project.scripts]` in
`pyproject.toml`, entry point `research_domain.cli:main`) exposed by the
`research_domain` package — the synthetic proof-domain built on `substrate`
(see `substrate/README.md`). It is a thin `click` group over
`research_domain.runtime.ResearchRuntime`: `append` writes one event to the
research stream, `show` replays the stream and prints a projection.

## Global options and DSN resolution

Both commands accept:

- `--dsn TEXT` — a Postgres DSN. Optional.
- `--stream TEXT` — the event stream id to operate on. Defaults to
  `research-stream`.

If `--dsn` is omitted, the CLI falls back to the `DATABASE_URL` environment
variable (`research_domain/cli.py::_resolve_dsn`). If neither is set, the
command exits with `click.ClickException("No --dsn given and DATABASE_URL is
not set.")` — a nonzero exit and no connection attempt.

There is no validation on `--stream` beyond being a string; any value is
passed straight through to `ResearchRuntime(dsn, stream=stream)`.

## `research-domain append EVENT_TYPE PAYLOAD_JSON`

Appends one event to the stream.

```
research-domain append claim.proposed '{"claim_id": "c1", "source_id": "s1"}' --dsn postgresql://...
```

- `EVENT_TYPE` — must be a name registered in the domain's event registry
  (`research_domain/event_types.py::build_research_registry()`). The
  registered types are `claim.proposed`, `source.corroborated`,
  `claim.refuted`, and `claim.corrected`.
- `PAYLOAD_JSON` — a JSON object, parsed with `json.loads`. Invalid JSON
  raises `click.ClickException(f"Invalid JSON payload: {exc}")` before any
  connection is opened.

On success the command connects, calls
`ResearchRuntime.append_event(event_type, payload)`, closes the connection,
and prints `Appended <event_type>` in green.

## `research-domain show PROJECTION_NAME`

Replays the stream and prints one projection as a two-column table (`Key`,
`Value`), titled with the projection name.

```
research-domain show source_coverage --dsn postgresql://...
```

Internally this connects, calls `ResearchRuntime.catch_up()` (which invalidates
and recomputes every registered projection whose event types were appended
since the last catch-up), then `get_projection(projection_name)`.

### Registered projections

`ResearchRuntime` (`research_domain/runtime.py`) registers three projections,
each built by a `build_*_catalog` function in `research_domain/projections.py`
and bound to the event types that invalidate it:

| Projection name | Invalidated by | Catalog builder |
|---|---|---|
| `source_coverage` | `claim.proposed` | `build_source_coverage_catalog` |
| `contradiction_map` | `claim.refuted` | `build_contradiction_map_catalog` |
| `claim_dependency_graph` | `claim.corrected` | `build_claim_dependency_catalog` |

Asking `show` for a projection name that isn't one of these three raises
whatever `ResearchRuntime.get_projection` raises for an unregistered name
(a `KeyError`-style lookup failure) — there is no CLI-level allow-list
separate from what's registered on the runtime.

## Error behavior summary

| Condition | Result |
|---|---|
| No `--dsn` and no `DATABASE_URL` | `click.ClickException`, nonzero exit, no connection attempted |
| Malformed `PAYLOAD_JSON` on `append` | `click.ClickException`, nonzero exit, no connection attempted |
| `EVENT_TYPE` not in the event registry | `append_event` still succeeds — the registry gates autonomy tiers, not append validity; nothing in `cli.py` rejects an unregistered type |
| `PROJECTION_NAME` not registered on `show` | Runtime-level lookup failure surfaces as an uncaught exception |

## Related documents

- `substrate/README.md` — the primitives `ResearchRuntime` is built from
  (`RuntimeBase`, `ProjectionCatalog`, `EventTypeRegistry`) and the
  "building a new domain" walkthrough this CLI is a worked example of.
- `docs/explanation/architecture-boundaries.md` — why `research_domain` may
  only import `substrate`'s top-level package, not its submodules.
