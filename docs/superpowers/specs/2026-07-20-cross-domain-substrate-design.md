# Cross-domain substrate: extracting Novelizer's pattern for reuse

Status: approved, not yet implemented.

## Problem

Novelizer's architecture — an append-only event log as sole source of truth
("canon"), narrow-role specialist agents, all higher-level state computed as
live derived projections rather than stored mutable state, revision as an
explicit new event ("retcon") that ripples to dependents instead of an
in-place edit, a tunable per-role autonomy dial gating human approval, and a
human "director" who steers via lightweight signals rather than
micromanaging — is not actually fiction-specific. It's a general pattern for
collaborative, revisable thought-work: multiple untrusted-by-default agents
producing claims that need to compose into a trusted whole, with corrections
as a first-class citizen rather than an afterthought.

A design brainstorm explored this pattern applied to research/fact-gathering
teams and project planning/tasking, then four parallel research agents dug
into: how the pattern maps to project planning specifically; how the
`deepagents` framework (which Novelizer already runs on) implements
filesystem/sub-agent/skills primitives under the hood; how Postgres+pgvector
and Redis would serve as a backing store; and what a generic tool/skill
loadout looks like across domains. Findings converged on a small number of
concrete, load-bearing refinements (below) and confirmed that Novelizer
already contains working, non-hypothetical implementations of most of the
substrate — this is an extraction-and-generalization problem, not a
from-scratch build.

Currently there is no seam between "the generic multi-agent canon pattern"
and "fiction." Everything lives in one repo, addressed to one domain, with
no path for a second domain (research, planning, code review, ...) to reuse
it without a fork.

## Findings that shape the design

**The autonomy dial must be per-event-type, not per-agent or per-role.**
All three domain-mapping passes (fiction, research, planning) converged on
this independently. A `SourceCorroborated` or `EstimateRevised(minor)` event
is additive/low-blast-radius and safe to auto-run; a `ClaimRefuted` or
`DependencyDiscovered` event ripples to every downstream dependent and needs
human sign-off. The same agent proposes both kinds of events at different
trust levels. A global per-agent dial is simultaneously too loose for
high-stakes event types and too strict for low-stakes ones.

**Retcon-ripple mechanics are universal; retcon *cost* is not.** The
mechanical blast-radius computation (a dependency-edge table + recursive
query) is domain-agnostic. But revising a plot point costs nothing; revising
a discovered task dependency or a refuted research claim reflects real
already-sunk work or effort. This means a Retro/Postmortem-style agent that
folds corrections back into future estimates/priors is more load-bearing
outside fiction than inside it, and should be a first-class substrate
concept, not a nice-to-have.

**Novelizer already runs on `deepagents`/LangGraph**, and already contains
working analogs of most substrate primitives:
- `novelizer/canon/` (`event_store.py`, `events.py`) — the append-only log.
- `novelizer/canon_fs/` — a **read-only, lazily-rendered virtual-filesystem
  projection of canon** (`backend.py`, `render.py`, `outline_render.py`,
  `search.py`) — exactly the `/canon/**` mount design this session's research
  independently proposed, already implemented.
- `novelizer/skills_packs/` — an existing skill-library convention
  (outlining, promise-payoff, scene-sequel, character-arcs, pacing).
- `novelizer/agents/middleware.py` — already wires deepagents'
  `CompositeBackend` (confirmed in `runtime.py:111-116`) to route
  `/scratch` vs. projection paths.
- `novelizer/agents/registry.py` (per `2026-07-20-agent-registry-design.md`)
  already anticipates this: it was scoped narrowly to fiction with the
  explicit goal that "a future domain ... becomes a config problem later, not
  a rewrite."

What's genuinely missing: a **generalized event-type registry and projection
catalog as the domain extension points** (currently domain knowledge is
embedded directly in agent code, not declared), **Postgres+pgvector storage**
(currently SQLite via `aiosqlite` — fine for a single local story, not
proven under concurrent multi-agent writes at scale), and the **per-event-
type autonomy dial** (current dial is coarser).

**deepagents itself is not event-sourced.** Its virtual filesystem
(`FilesystemState.files`) is a mutable path→`FileData` dict with a
`DeltaChannel` reducer that stores deltas/snapshots as a *performance*
optimization for bounded read depth — not a domain event log. Novelizer's
own `canon/event_store.py` is the actual event-sourcing layer; deepagents
supplies the agent loop, sub-agent delegation (`task` tool, stateless
one-shot, isolated context), and the `CompositeBackend`/`BackendProtocol`
seam the projection filesystem is built on. The substrate extraction should
keep leaning on deepagents for those, and keep the event-sourcing layer as
Novelizer-originated, generalized code.

**Storage: Postgres-only first.** `LISTEN/NOTIFY` covers agent wake-up,
`SELECT ... FOR UPDATE SKIP LOCKED` covers work queues, advisory locks cover
mutual exclusion. Redis is deferred until a concrete dispatch-throughput or
lock-churn need appears — not built preemptively. pgvector embeddings live
in a separate table keyed by `(target_kind, target_id, model)`, not a column
on the events table: raw events embed once and are never touched again
(immutable); a "current view" claim's embedding is a projection artifact,
re-embedded when the projection recomputes.

**Tool signatures are generic; event/projection/skill *data* is domain-
specific.** Six tool categories generalize across every domain tested:
canon-write (`propose_event`, the single choke point the dial gates),
canon-read/query, projection read/refresh, scratch-FS ops, sub-agent
delegation, and skill invocation. Domain-specificity lives entirely in the
event-type registry, the projection catalog, and the skill library — never
in bespoke per-domain tools (e.g. no `fact_check()` tool; that's a skill a
role invokes). Skills should be global and agent-selected by description
match, shared across roles, not siloed per role — a "diff two sources for
contradiction" procedure is useful to a research Verifier and a fiction
Continuity Checker alike.

## Non-goals

- No new fiction-facing features. This is purely structural extraction and
  generalization.
- No commitment yet to building the research or planning domain as shipped
  products — M4 builds the research domain only as far as needed to prove
  the substrate generalizes, not as a polished standalone product.
- No Redis, no multi-tenant hosting, no auth/permissions system beyond the
  existing autonomy dial. Those are explicitly deferred until a concrete need
  demonstrates they're required.

## Milestones

### M0 — Audit & seams

Map `novelizer/canon/`, `novelizer/canon_fs/`, `novelizer/skills_packs/`, and
`novelizer/agents/middleware.py` against the six substrate primitives.
Produce a seam map: which parts are already generic, which are hardcoded to
fiction (event type names, projection names, the current dial's grain), and
exactly where the event-type-registry / projection-catalog extension points
need to be cut in. No code changes — output is documentation only.

### M1 — Extract the substrate skeleton

New repo. Move the event store, projection/rendering engine, `CompositeBackend`
wiring, and skill loader into it as generic abstractions behind two extension
points: an event-type registry (schema + per-event-type dial policy) and a
projection catalog (named derived views + invalidation rule). Novelizer
imports the new package and configures it with fiction's event types and
projections, in place of its current hardcoded versions.

Proof of success: Novelizer's existing test suite passes unchanged — this is
a pure extraction, zero behavior change from the fiction user's perspective.

### M2 — Postgres(+pgvector) backend

Add a Postgres storage adapter to the substrate: append-only `events` table
(`seq` identity for total order, `stream_id` partition key, `payload jsonb`,
causal `parent_ids`), a separate `embeddings` table keyed by
`(target_kind, target_id, model)`, and a `derived_deps` edge table for
blast-radius recursive queries. Offered as an alternative to the existing
SQLite adapter — SQLite is not removed. Validate by running Novelizer against
Postgres in an isolated test environment (never the shared/main checkout,
per the standing DB-lock-incident rule) under concurrent multi-agent writes.
No Redis at this stage.

### M3 — Per-event-type autonomy dial

Generalize the current dial into a per-event-type policy (schema declares,
per event type, whether it's free-run or requires approval). Migrate
Novelizer's own fiction event types onto this scheme first — dogfooding
before a second domain is asked to rely on it. This directly encodes the
"low-blast-radius vs. rippling" distinction found in all three domain
mappings.

### M4 — Second domain: research/fact-gathering

Build the research deployment on the now-generalized substrate: event types
(claim proposed, source corroborated, claim refuted/corrected), roles (Scout,
Extractor, Verifier, Retractor, Synthesizer, Coverage Analyst), and
projections (Source Coverage Board, Contradiction Map, Claim Dependency
Graph). This is the actual test of generality. Anything the research domain
needs that the substrate doesn't provide generically is logged as a
substrate gap — not worked around with a domain-specific hack in the
research deployment.

### M5 — Substrate hardening

Fold M4's logged gaps back into the substrate (e.g., a sunk-cost-aware
retcon-ripple / Retro-agent primitive, if research surfaces the same need
planning-mapper predicted). At the end of M5, decide whether a third domain
(planning/tasking — which stresses an exogenous clock and contested,
non-convergent beliefs that neither fiction nor research require) is worth
building next, or stays backlog until a concrete need arises.

## Testing

Each milestone's own verification is stated inline above (M1: existing
Novelizer suite passes unchanged; M2: concurrent-write validation in an
isolated environment; M4: the research domain's own test suite, built
test-first per the project's standing red/green + property-based TDD
principle). No milestone is considered done without its stated proof
passing.
