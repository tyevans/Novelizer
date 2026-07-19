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
