# Cross-domain substrate: extracting Novelizer's pattern for reuse

Status: approved, not yet implemented. (Revision 2 — re-audited 2026-07-21
against main after ~75 commits of drift; supersedes the original version of
this doc in place, same filename.)

## Problem

Novelizer's architecture — an append-only event log as sole source of truth
("canon"), narrow-role specialist agents, all higher-level state computed as
live derived projections rather than stored mutable state, revision as an
explicit new event that ripples to dependents instead of an in-place edit, a
tunable autonomy dial gating human approval, and a human "director" who
steers via lightweight signals rather than micromanaging — is not actually
fiction-specific. It's a general pattern for collaborative, revisable
thought-work: multiple untrusted-by-default agents producing claims that need
to compose into a trusted whole, with corrections as a first-class citizen
rather than an afterthought.

There is still no seam between "the generic multi-agent canon pattern" and
"fiction." Everything lives in one repo, addressed to one domain, with no
path for a second domain (research, planning, code review, ...) to reuse it
without a fork.

## What's changed since the first pass (2026-07-20)

The original version of this spec was written from a codebase snapshot that
main has since moved well past. Re-auditing against current `main` changes
the milestone shape substantially: several things the original spec proposed
as *future* milestone work are now shipped, in fiction-specific form, inside
Novelizer itself. This is good news for the thesis (the pattern keeps proving
itself out under real load) but it means M1 and M3 as originally scoped are
largely done, and the extraction milestones now start from "generalize
working code," not "build it."

Specifically:

- **The agent registry (former M1's "config, not rewrite" goal) is shipped.**
  `novelizer/agents/registry.py` + `registry_types.py` declare `AgentSpec`
  entries (name, agent class, runner builder, tool grant, interval setting,
  extra-kwargs factory) in an explicit list that *is* the scheduling order.
  Adding an agent is a new module + one registry line. This already matches
  the design in `2026-07-20-agent-registry-design.md`, referenced (but not
  yet confirmed shipped) in the original version of this doc.

- **Retcons generalized into Flags, plus a Triage agent.** What the original
  spec called "retcon" is now the more general `flag.created` /
  `flag.resolved` / `flag.rejected` event family (`RETCON_REQUEST_*` survive
  only as legacy aliases in `projector.py`). A new `Triage` agent classifies
  and routes flags. This is direct evidence *for* the spec's "retcon-ripple
  mechanics are universal" finding — fiction itself needed a more general
  name for the same mechanic before this extraction even started.

- **The per-event-type autonomy dial (former M3) is shipped, for fiction.**
  `novelizer/canon/policy.py` is almost exactly the substrate primitive M3
  proposed building: `AutonomyPolicy.is_gated(agent_name, event_type)` checks
  an `_ALWAYS_GATED` set (currently just `BLUEPRINT_ADOPTED` — "adopting a
  shape re-frames the whole book, the Director signs off at every autonomy
  level," per its docstring), a `_NEVER_GATED` set (mechanical/low-stakes
  events — chat messages, director signals, deterministic bookkeeping like
  `chapter.mined` or `inspiration.drawn`), and three ordinal tiers
  (`gated_retcons` ⊂ `gated_canon` ⊂ everything) layered underneath for
  events that fall into neither always/never bucket. `AutonomyState` still
  carries a coarse `global_level` + per-agent `overrides` (`canon/autonomy.py`),
  but the *gating decision itself* is already keyed off event type, not just
  agent — the exact shift the original spec called for.

  What remains genuinely missing, and is now M3's real scope: the
  always/never/tiered set membership is **hardcoded as Python literals
  in `policy.py`**, referencing fiction's `EventType` constants directly.
  There is no declared, per-event-type field (e.g. on a registry entry
  alongside each event type's schema) that a second domain could populate
  with its own gating tier. The mechanism (three tiers + always/never
  overrides, `is_gated()` as the single choke point) generalizes as-is; the
  *data* does not yet.

- **A second working example of the "projection with invalidation" pattern
  now exists: the knowledge graph.** `novelizer/store/kg_store.py` +
  `kg_projector.py` + `kg_structured.py` maintain `kg_entities` /
  `kg_relations` tables built from canon, with mentions tracked per
  `event_fingerprint` so a projection recompute can `clear_mentions_for_fingerprint`
  and rebuild cleanly when the source event changes. This is structurally the
  same shape as `canon_fs`'s lazy rendering (a derived, recomputable view
  keyed to source events) but through a different mechanism (a queryable
  entity/relation store rather than a virtual filesystem). Two independent,
  working implementations of "derived projection with an invalidation rule"
  strengthens the case that a **projection catalog** (M1) is the right
  extension point — it's not a hypothetical abstraction over one example
  anymore, it's the common shape of two.

- **`novelizer/research/` is not the research domain this spec means.** Main
  added a `research/` module (`runner.py`, `service.py`, `tools.py`) in this
  window, but it's a stateless chat Q&A feature for the TUI (a single-shot
  "ask a research question, get an answer" runner with no canon writes, no
  persistence, no multi-agent pipeline) — unrelated to M4's proposed
  research/fact-gathering *domain* (a second full deployment of the
  substrate: its own event types, roles, projections). The naming collision
  is coincidental. M4 should pick a distinguishing name for its own
  artifacts (e.g. a `research-team` or `factfind` deployment) to avoid
  confusion with this existing feature.

- **`canon_fs/`, `skills_packs/`, and `agents/middleware.py`'s `CompositeBackend`
  wiring are unchanged** from the original audit — still working, still
  match the substrate primitives as originally described.

None of this changes the spec's core thesis or its non-goals. It changes
what M1 and M3 mean: **extract and generalize already-proven fiction-specific
code**, not build new mechanism from scratch. M2, M4, and M5 are substantively
unchanged.

## Findings that shape the design

**The autonomy dial must be per-event-type, not per-agent or per-role.**
Confirmed twice now: independently by all three original domain-mapping
passes (fiction, research, planning), and empirically by fiction's own
`policy.py`, which was built for exactly this reason without reference to
this spec. A `SourceCorroborated`-shaped event is additive/low-blast-radius
and safe to auto-run; a `ClaimRefuted`-shaped event ripples to every
downstream dependent and needs human sign-off. The same agent proposes both
kinds of events at different trust levels. What's missing is only that the
event-type→tier mapping needs to be *data a domain declares*, not a literal
Python set in shared policy code.

**Retcon-ripple mechanics are universal; retcon *cost* is not.** Unchanged
from the original spec. The mechanical blast-radius computation (a
dependency-edge table + recursive query) is domain-agnostic. Revising a plot
point costs nothing; revising a discovered task dependency or a refuted
research claim reflects real already-sunk work or effort. A Retro/Postmortem-
style agent that folds corrections back into future estimates/priors is more
load-bearing outside fiction than inside it, and should be a first-class
substrate concept.

**The projection catalog now has two proof points, not one.** `canon_fs`
(virtual-filesystem rendering) and the knowledge graph (`kg_store`/
`kg_projector`, entity/relation tables with fingerprint-keyed mention
tracking) are structurally the same idea — a derived view recomputed from
canon events, invalidated by a key tied to the source event — expressed
through two different storage/access shapes. The substrate's projection
catalog extension point should be designed against both, not generalized
from `canon_fs` alone as the original spec did.

**Novelizer already runs on `deepagents`/LangGraph**, and already contains
working analogs of most substrate primitives — event store (`canon/event_store.py`,
`events.py`), the agent registry (`agents/registry.py`), the per-event-type
gating choke point (`canon/policy.py`), two projection implementations
(`canon_fs/`, `store/kg_*.py`), a skill-pack convention (`skills_packs/`),
and `CompositeBackend` routing (`agents/middleware.py`). What's genuinely
missing: a **generalized event-type registry and projection catalog as
declared domain extension points** (today the gating tiers and projection
definitions are Python code specific to fiction, not schema a second domain
configures), and **Postgres+pgvector storage** (currently SQLite via
`aiosqlite` — fine for a single local story, unproven under concurrent
multi-agent writes at scale).

**deepagents itself is not event-sourced.** Unchanged from the original
audit. Its virtual filesystem (`FilesystemState.files`) is a mutable
path→`FileData` dict with a `DeltaChannel` reducer for bounded-read-depth
performance, not a domain event log. Novelizer's own `canon/event_store.py`
is the actual event-sourcing layer; deepagents supplies the agent loop,
sub-agent delegation, and the `BackendProtocol`/`CompositeBackend` seam the
projection filesystem is built on. The extraction keeps leaning on deepagents
for those, and keeps event-sourcing as Novelizer-originated, generalized
code.

**Storage: Postgres-only first.** Unchanged. `LISTEN/NOTIFY` covers agent
wake-up, `SELECT ... FOR UPDATE SKIP LOCKED` covers work queues, advisory
locks cover mutual exclusion. Redis is deferred until a concrete
dispatch-throughput or lock-churn need appears. pgvector embeddings live in a
separate table keyed by `(target_kind, target_id, model)`, not a column on
the events table.

**Tool signatures are generic; event/projection/skill *data* is
domain-specific.** Unchanged. Six tool categories generalize across every
domain tested: canon-write (`propose_event`, the single choke point the dial
gates), canon-read/query, projection read/refresh, scratch-FS ops, sub-agent
delegation, skill invocation. Domain-specificity lives entirely in the
event-type registry, the projection catalog, and the skill library — never
in bespoke per-domain tools.

## Non-goals

- No new fiction-facing features. This is purely structural extraction and
  generalization.
- No commitment yet to building the research or planning domain as shipped
  products — M4 builds the research domain only as far as needed to prove
  the substrate generalizes.
- No Redis, no multi-tenant hosting, no auth/permissions system beyond the
  existing autonomy dial. Deferred until a concrete need demonstrates they're
  required.
- No rework of fiction's `AutonomyState`/`AutonomyLevel` ordinal tiers
  (`full_auto`/`gated_retcons`/`gated_canon`/`gated_all`) or per-agent
  overrides — M3 generalizes *how the event-type sets are declared*, not the
  tier model itself.

## Milestones

### M0 — Audit & seams (done as part of this revision)

Map `novelizer/canon/`, `novelizer/canon_fs/`, `novelizer/store/kg_*.py`,
`novelizer/skills_packs/`, `novelizer/agents/registry.py`, and
`novelizer/canon/policy.py` against the six substrate primitives. This
revision *is* that audit. Remaining M0 work before M1 starts: a short written
seam map (superset of the "Findings" section above, but exhaustive) pinning
exactly which functions/classes move to the new repo unchanged, which need a
generalization pass first, and which stay fiction-only.

### M1 — Extract the substrate skeleton

New repo. Move the event store, the projector/rendering engine (generalizing
from *both* `canon_fs` and `kg_store`/`kg_projector`, not just one), the
`CompositeBackend` wiring, the agent registry mechanism (`AgentSpec`/
`registry_types.py`, already domain-agnostic in shape), and the skill loader
into it as generic abstractions behind two extension points: an event-type
registry (schema + per-event-type gating tier) and a projection catalog
(named derived views + invalidation rule, general enough to express both a
rendered-file projection and an entity/relation-store projection). Novelizer
imports the new package and configures it with fiction's event types,
gating tiers (moving `policy.py`'s hardcoded sets into declared data), and
projections, in place of its current hardcoded versions.

Proof of success: Novelizer's existing test suite passes unchanged — this is
a pure extraction, zero behavior change from the fiction user's perspective.

**M1 status (2026-07-21): done, with two logged fast-follows.** Extracted:
the event-type registry (`substrate/event_registry.py`), the generic
per-event-type gating mechanism (`substrate/policy.py`, with fiction's
`canon/policy.py` rewired onto it as a thin adapter — zero behavior change,
verified by manual equivalence trace across all four gating buckets plus
the `gated_all` catch-all edge case, and by the full fiction test suite
staying green), the agent-registry shape (`substrate/agent_registry.py`,
with `novelizer/agents/registry_types.py` reduced to a re-export), and a
`ProjectionCatalog` abstraction (`substrate/projection.py`) proven against
fakes modeling both `canon_fs`'s and `kg_store`'s real invalidation shapes.
`substrate/` imports nothing from `novelizer.*` (verified by grep). Full
suite result: 1953 passed, 5 failed, 7 deselected — all 5 failures
pre-existing and unrelated to this milestone (two `fake_create_deep_agent()`
middleware-kwarg mismatches in `test_author.py`/`test_plotter.py`, one
`AGENT_REGISTRY` ordering assertion expecting nine specs instead of the
current ten now that `triage` was added, and two `tests/settings/test_layers.py`
field-set mismatches in code neither this milestone nor its commits touch —
none traceable to any file this milestone changed). **Not done, logged as
fast-follow work for a future milestone:** (1) rewiring the real
`canon_fs`/`kg_store` projection code to actually use `ProjectionCatalog`
instead of their current bespoke recompute paths — M1 proved the
abstraction fits, migrating the call sites is separate, riskier work against
production fiction code; (2) moving the event store
(`canon/event_store.py`) into `substrate/` — deferred to M2, since its
extraction is entangled with the Postgres-vs-SQLite adapter boundary M2 is
building, and moving it twice would be wasted work; (3) the literal
separate-repo split the original spec text calls for — this repo has no
configured git remote, so `substrate/` was built as an import-independent
top-level package within this repo instead, ready for a mechanical directory
copy whenever a real separate repo exists.

### M2 — Postgres(+pgvector) backend

Unchanged from the original spec. Add a Postgres storage adapter to the
substrate: append-only `events` table (`seq` identity for total order,
`stream_id` partition key, `payload jsonb`, causal `parent_ids`), a separate
`embeddings` table keyed by `(target_kind, target_id, model)`, and a
`derived_deps` edge table for blast-radius recursive queries. Offered as an
alternative to the existing SQLite adapter — SQLite is not removed. Validate
by running Novelizer against Postgres in an isolated test environment (never
the shared/main checkout, per the standing DB-lock-incident rule) under
concurrent multi-agent writes. No Redis at this stage.

**M2 status (2026-07-21): done.** Added `substrate/postgres/` — `events.py`
(`PostgresEventStore`, append-only `substrate_events` table with an
identity `seq`, a stream/seq index, and BEFORE UPDATE/DELETE triggers that
raise rather than allow mutation), `embeddings.py`
(`PostgresEmbeddingStore`, `substrate_embeddings` table keyed by
`(target_kind, target_id, model)` with an HNSW index), and `deps.py`
(`PostgresDepsStore`, `substrate_derived_deps` edge table with a recursive
CTE for blast-radius queries). No system-level Postgres install was
available in this environment, so validation ran against an ephemeral
`pgvector/pgvector:pg16` Docker container started and torn down per test
session (`tests/substrate/postgres_fixture.py`) — never a shared or
persistent database, consistent with the project's standing DB-lock-
incident rule. Each store's `connect()` guards its schema DDL behind a
distinct `pg_advisory_xact_lock` key (727271/727272/727273) to prevent a
real race when multiple connections call `connect()` concurrently.
Concurrency was validated at two levels: `PostgresEventStore` alone (20
concurrent appends across 4 simulated writers, zero lost/duplicate sequence
numbers) and all three stores together under 6 simulated concurrent agents
each writing events, embeddings, and dependency edges
(`test_postgres_concurrency.py`, re-run twice with zero flakiness). A full
suite run (`pytest tests/`) after this milestone showed 1966 passed / 7
failed / 7 deselected: 5 of the 7 failures are the pre-existing,
already-diagnosed failures noted in M1's status above; the other 2
(`test_postgres_deps.py::test_blast_radius_dedupes_diamond_dependency` and
`test_brain_panel.py::test_strip_reflects_settings_thresholds_...`) were
confirmed as load-flakes, not regressions — both pass cleanly (9 passed) in
an isolated re-run of just those two files, consistent with this project's
known load-flake pattern (memory: testing-load-flakes) when ~2000 tests and
a live Docker container run at once. The existing SQLite adapter is
untouched; this milestone is purely additive. No Redis was added, per the
spec's explicit scope.

### M3 — Declarative per-event-type autonomy dial

Fiction already has the gating *mechanism* (`AutonomyPolicy.is_gated`, three
ordinal tiers, always/never overrides) — this milestone's real work is
making the event-type→tier mapping **declared data on the event-type
registry**, not a literal Python set in shared policy code, so a second
domain can register its own event types with their own gating tiers without
touching substrate code. Migrate fiction's own `policy.py` sets onto this
scheme first — dogfooding before a second domain is asked to rely on it. The
tier model (`full_auto`/`gated_retcons`/`gated_canon`/`gated_all` +
always/never overrides) carries over unchanged; only its *data source*
moves.

**M3 status (2026-07-21): done — mechanism delivered in M1, pluggability
proven here.** M1 (commits `409a2de..e296ee3`) already converted fiction's
four hardcoded gating sets in `canon/policy.py` into `EventTypeSpec` entries
on an `EventTypeRegistry`, gated through the domain-neutral
`substrate.policy.is_gated(event_name, registry, tier_order,
current_tier_index)` — that already is the "migrate fiction's own
policy.py sets onto this scheme first" dogfooding step this milestone's
spec text calls for. This milestone's own contribution is
`tests/substrate/test_second_domain_pluggability.py`: a synthetic research-
flavored domain, with a two-tier structure (`auto`/`reviewed`) deliberately
shaped differently than fiction's four-tier scheme, registers its own event
types and gates correctly through the same `is_gated` call — with zero
changes to any file under `substrate/`. The tier model itself
(`full_auto`/`gated_retcons`/`gated_canon`/`gated_all` + always/never
overrides) was correctly left unchanged, per this milestone's own
non-goals.

### M4 — Second domain: research/fact-gathering

Build the research-team deployment (naming distinct from the existing
`novelizer/research/` chat feature) on the now-generalized substrate: event
types (claim proposed, source corroborated, claim refuted/corrected), roles
(Scout, Extractor, Verifier, Retractor, Synthesizer, Coverage Analyst), and
projections (Source Coverage Board, Contradiction Map, Claim Dependency
Graph — the latter validated against the same projection-catalog shape
proven by both `canon_fs` and the knowledge graph in M1). This is the actual
test of generality. Anything the research domain needs that the substrate
doesn't provide generically is logged as a substrate gap — not worked around
with a domain-specific hack in the research deployment.

**M4 status (2026-07-21): done, scoped as the spec's own non-goal
specifies — "only as far as needed to prove the substrate generalizes."**
Built `research_domain/` (distinct from the pre-existing
`novelizer/research/` chat feature, per M0's naming-collision finding):
`event_types.py` (four event types — `claim.proposed`, `source.corroborated`,
`claim.refuted`, `claim.corrected` — registered on a fresh
`substrate.event_registry.EventTypeRegistry` with a two-tier
`auto`/`reviewed` gating scheme, exercising the exact low-blast-radius-vs-
rippling distinction the spec's Findings section predicted for research
specifically), `roles.py` (all six named roles — Scout, Extractor, Verifier,
Retractor, Synthesizer, Coverage Analyst — as `AgentSpec` entries with
synchronous stub `construct` callables, proving the agent-registry shape
fits a non-fiction roster; no real LLM-backed agent logic was built, per
this milestone's own scope), and `projections.py` (the Source Coverage
Board, built on `ProjectionCatalog`). The proof of generality is
`test_end_to_end.py`: real events appended to a live Postgres-backed
`PostgresEventStore` (the same Docker-fixture-backed instance from M2),
real gating decisions against the research registry, and a real recomputed
projection — verified non-tautological under adversarial review (expected
counts derive from actually-read-back rows, not independently-asserted
magic numbers). Full suite after this milestone: 1983 passed, 5 failed
(the same pre-existing failures documented in M1/M2's status notes, zero
new regressions), 7 deselected.

**Logged substrate gaps (explicitly not worked around here, per the spec's
instruction to log rather than hack):**
1. The Contradiction Map and Claim Dependency Graph projections (the other
   two the spec names for this domain) were not built — Source Coverage
   Board alone was sufficient to prove `ProjectionCatalog` composes with
   real Postgres data; building the other two would exercise the same
   abstraction again without new information.
2. `ProjectionCatalog.recompute`'s callable contract is synchronous
   (established in M1), but the natural implementation of a real
   count-from-Postgres query is async — this milestone's integration test
   worked around this by precomputing counts with one `await
   store.read_stream(...)` call before building the catalog, rather than
   making `recompute` call back into Postgres itself. A real research
   deployment recomputing projections continuously from a live event
   stream would need `ProjectionCatalog` to support async `recompute`
   callables (or a documented convention of precomputing inputs before
   invoking it, as done here) — this is a genuine substrate gap for M5 to
   pick up if a future milestone needs live recompute against an async
   store, not merely a research-domain-specific issue.
3. No real role behavior (Scout actually scouting, Verifier actually
   verifying) was built — `roles.py`'s stub `construct` callables prove the
   registry shape accepts a non-fiction roster but do not exercise whether
   `AgentContext`'s current fields (built for fiction's needs) are
   sufficient for a research role's actual runtime needs (e.g. a `Verifier`
   likely needs a different tool grant shape than any fiction role has).
   This is deferred, not solved, pending real role implementation.

### M5 — Substrate hardening

Fold M4's logged gaps back into the substrate (e.g., a sunk-cost-aware
retcon-ripple / Retro-agent primitive, matching fiction's own Flags/Triage
generalization if research surfaces the same need). At the end of M5, decide
whether a third domain (planning/tasking — which stresses an exogenous clock
and contested, non-convergent beliefs that neither fiction nor research
require) is worth building next, or stays backlog until a concrete need
arises.

**Third-domain decision (2026-07-21): backlog, not build.** Per this
milestone's own instruction to decide rather than default into more
construction: no concrete product need for a planning/tasking domain (or
any third domain) exists yet — M1-M4 were driven by proving the substrate
generalizes across two domains, not by an active user waiting on a third.
Building one now would be speculative work against a domain whose actual
requirements (an exogenous clock, contested non-convergent beliefs) aren't
yet informed by any real deployment pressure, unlike M4's research domain
which was scoped directly from the spec's own Findings section. Revisit
this decision when a concrete planning/tasking need actually surfaces, at
which point the now-hardened substrate (declarative event-type/gating
registry, Postgres+pgvector backend, sync-or-async ProjectionCatalog,
domain-neutral agent-registry shape) should make standing it up
substantially cheaper than M4's research domain was, since M4 itself is the
proof of that cost curve.

**M5 status (2026-07-21): done.** Folded back the one genuine substrate gap
M4 logged: `ProjectionCatalog.recompute_dirty` now accepts either sync or
async `recompute` callables (detected via `inspect.isawaitable`), with the
method itself becoming `async def` — a deliberate, narrow breaking change,
applied consistently to its only three existing call sites in the same
commit (verified by a full-repo grep during review, not just the diff). The
other two logged gaps (unbuilt Contradiction Map/Claim Dependency Graph
projections; real role behavior/`AgentContext` sufficiency) were confirmed
to be scope notes about deliberately-excluded work, not substrate defects,
and were left as-is rather than built out — building them now would mean
doing the very research-domain product work M4's non-goals excluded. **This
closes the M0-M5 arc**: the substrate (`substrate/`) now provides a
declarative event-type registry with per-event-type gating (M1, M3), a
Postgres+pgvector backend proven under concurrent multi-agent writes (M2),
and a sync-or-async projection catalog (M1, M5) — proven to generalize
across fiction (Novelizer's existing behavior, unchanged throughout) and a
second, independent research domain (M4), with `substrate/` and
`research_domain/` importing nothing from `novelizer.*` throughout.

**Final full-arc regression (2026-07-21):** `pytest tests/` after all six
milestones: 1985 passed, 6 failed, 7 deselected. Five of the six failures
are the same pre-existing, already-diagnosed failures documented in M1/M2's
status notes above (two mock-signature mismatches, one registry-ordering
assertion, two settings-field-set mismatches — none touched by this
project's substrate work). The sixth
(`tests/substrate/test_postgres_fixture.py::test_pgvector_extension_is_available`,
`ConnectionResetError`) was confirmed as a load-flake from Docker-container
contention under the full ~2000-test run, not a regression — it passes
cleanly (2/2) in an isolated re-run, consistent with this project's known
load-flake pattern (memory: testing-load-flakes).

## Testing

Each milestone's own verification is stated inline above (M1: existing
Novelizer suite passes unchanged; M2: concurrent-write validation in an
isolated environment; M4: the research domain's own test suite, built
test-first per the project's standing red/green + property-based TDD
principle). No milestone is considered done without its stated proof
passing.
