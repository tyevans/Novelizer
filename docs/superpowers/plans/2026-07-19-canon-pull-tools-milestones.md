# Canon Pull Tools — Milestone Ladder

Spec: `docs/superpowers/specs/2026-07-19-canon-pull-tools-design.md`

Process per milestone: **plan → red/green TDD execution → code review → merge**,
then plan the next milestone. Plans live beside this file as
`2026-07-19-canon-pull-tools-mN.md`.

| # | Milestone | Deliverable | Depends on |
|---|-----------|-------------|------------|
| CPT-M1 | canon_fs foundations (pure) | `novelizer/canon_fs/paths.py` (slugs, virtual-tree path index) + `render.py` (one renderer per record type), unit + property tested, no DB | — |
| CPT-M2 | CanonBackend | `BackendProtocol` impl over `ReadStore` (async `als`/`aread`/`agrep`/`aglob`; write/edit refuse with intent-path message), integration-tested against seeded ReadStore | M1 |
| CPT-M3 | search_canon | Incremental projector-side embedding index over all canon kinds + backfill + the `search_canon(query, kinds?)` LangChain tool | M1 |
| CPT-M4 | Phase-a wiring | Author + Continuity Checker runners get backend + tool; chapter push replaced by `id | title | status | cast` map; retrieval instruction in system prompts; per-agent settings flags; `execute` middleware excluded; tool-call telemetry in Engine Room | M2, M3 |
| CPT-M5 | Phase-c chat personas | Chat runner gets backend + tool + map diet, same flags/telemetry | M4 |
| CPT-M6 | Phase-b all agents | Remaining five scheduled agents flipped on; `write_todos` scoped to Author only; docs/QUICKSTART updated | M4 |

Standing rules: all test runs in a worktree (never the main checkout);
writes to canon stay on the event-sourced intent path throughout — no
milestone touches the write path.

## Status

- **CPT-M5: delivered** (2026-07-19). Chat personas pull canon: chat runners
  build with `CanonBackend` + `search_canon` + graph-scope telemetry callbacks
  when `chat_tools_enabled` (default on), story-context prose excerpts replaced
  by the chapter map in pull mode, `write_todos` excluded from chat via the new
  `ExcludeToolsMiddleware` (novelizer-owned; no deepagents private imports).

- **CPT-M4: delivered** (2026-07-19). Phase-a pull agents are live: Author
  and Continuity Checker run with `CanonBackend` + `search_canon` when
  `author_tools_enabled`/`checker_tools_enabled` are on (the default),
  chapter-prose push replaced by an id/title/status/cast map plus a
  retrieval instruction (byte-identical legacy prompts when flags are off),
  and tool calls stream through telemetry into the Engine Room
  (`⚒`-prefixed lines). deepagents auto-filters the `execute` tool for
  non-sandbox backends, so no middleware surgery was needed; `write_todos`
  scoping remains CPT-M6.

- **CPT-M3: delivered** (2026-07-19). Incremental projector-side canon
  embedding index, backfill-on-`Runtime.start()`, `Runtime.index_catch_up()`
  (None-safe, never raises), the `search_canon` LangChain tool, and the TUI
  tick hook (`_projector_loop` now also awaits `index_catch_up()` each cycle
  so embeddings stay current during a live session) are all in place and
  pinned by tests.

  Acceptance-run flags to watch for:
  - test with the embed endpoint deliberately down — the per-tick retry has
    no backoff, so a sustained outage means `catch_up` re-attempts (and
    logs a warning) every single tick until the endpoint returns.
  - first-run backfill on a large story is serial (one record at a time,
    no batching/concurrency) — expect a noticeable delay before the index
    is fully warm on an existing story with a big canon.
