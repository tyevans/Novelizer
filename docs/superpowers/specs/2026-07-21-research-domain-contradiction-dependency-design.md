# Research domain — Contradiction Map & Claim Dependency Graph projections

Status: implemented (2026-07-21).

## Problem

M4 of the cross-domain substrate arc (`2026-07-20-cross-domain-substrate-design.md`)
proved the substrate generalizes to a second, non-fiction domain by building
`research_domain/` on top of `substrate/`: event types (`claim.proposed`,
`source.corroborated`, `claim.refuted`, `claim.corrected`), six stub agent
roles, and one real projection (Source Coverage Board). M4 was deliberately
scoped to prove the pattern, not build the full research deployment, and
logged two projections it didn't build as gaps: the Contradiction Map and
the Claim Dependency Graph.

Those two projections can't be built on the current event payloads. Claims
carry no `claim_id` today (`claim.proposed` only has `source_id` and
`text`), and `claim.refuted`/`claim.corrected` are registered by name in
`EventTypeRegistry` with no payload schema at all — there is no way for one
claim to reference another. Building the two logged-gap projections
requires defining real payload schemas first.

This work is scoped as "projections first, roles later" — it does not touch
the six stub agent roles in `research_domain/roles.py`. Real role behavior
stays a separate, later milestone.

## Design

### Payload schemas

New module `research_domain/events.py`, following the same `pydantic.BaseModel`
convention as fiction's `novelizer/canon/events.py`:

- `ClaimProposed`: `claim_id: str`, `source_id: str`, `text: str`
- `SourceCorroborated`: `source_id: str`, `claim_id: str`
- `ClaimRefuted`: `claim_id: str`, `target_claim_id: str`, `reason: str`
- `ClaimCorrected`: `claim_id: str`, `target_claim_id: str`, `reason: str`

`ClaimRefuted` and `ClaimCorrected` share an identical shape — both are a
directed edge from the new/acting claim (`claim_id`) to the claim it acts on
(`target_claim_id`). The distinction is purely semantic (contradicts vs.
supersedes), carried by which event type fired, not by field shape.

`EventTypeRegistry` gating in `research_domain/event_types.py` is unchanged —
these are payload additions, not new event types or new gating tiers.

### Projections

Both projections follow the same "precompute outside, dict-lookup inside"
pattern M4's `source_coverage` established (`ProjectionCatalog.recompute_dirty`
is `async def` per M5's fix, but the registered `recompute` callable itself
can stay sync — a plain dict lookup — as long as the async Postgres read
happens once, before building the catalog, not inside `recompute`).

- **Contradiction Map** (`build_contradiction_map_catalog`): keyed by
  `claim_id`, value = list of `claim_id`s that refute it. Built only from
  `claim.refuted` events; `claim.corrected` events are ignored by this
  projection.
- **Claim Dependency Graph** (`build_claim_dependency_catalog`): keyed by
  `claim_id`, value = list of `claim_id`s it supersedes (i.e., the claims it
  was corrected from). Built only from `claim.corrected` events;
  `claim.refuted` events are ignored by this projection.

Both live in `research_domain/projections.py` alongside the existing
`build_source_coverage_catalog`, using the same `ProjectionSpec` /
`ProjectionCatalog` primitives from `substrate/projection.py` — no changes
to `substrate/` itself, consistent with M4 and M5's finding that the
substrate primitives already generalize; only domain data was missing.

### Testing

Extend `tests/research_domain/test_end_to_end.py` (real Postgres-backed,
per the project's standing red/green + property-based TDD principle) with:
- A chain of claims where one refutes another, asserting the Contradiction
  Map projection recomputes the correct edge list.
- A chain of claims where one corrects/supersedes another, asserting the
  Claim Dependency Graph projection recomputes the correct edge list.
- A claim with no refutations/corrections, asserting it does not appear (or
  appears with an empty list — decided at implementation time to match
  whatever convention `source_coverage` already uses for zero-count keys).

New unit-level tests for the payload schemas belong in
`tests/research_domain/test_event_types.py` (schema validation) and
`tests/research_domain/test_projections.py` (projection logic against
in-memory fixtures, without Postgres), matching the existing split between
those two test files and `test_end_to_end.py`.

## Non-goals

- No changes to `research_domain/roles.py` — stub `construct` callables stay
  stubs. Real agent behavior (a role that actually proposes/refutes claims)
  is deferred to a later milestone, per the earlier "projections first,
  roles later" scoping decision.
- No UI or visualization of either graph.
- No cycle-detection, topological sort, or other graph algorithms beyond
  returning raw edge lists — if a real deployment later needs those, they
  can be layered on top of the projection's output without changing the
  projection itself.
- No changes to `substrate/` — this milestone's own finding (consistent with
  M4/M5) is that the substrate primitives already suffice; only
  domain-specific payload/projection code is added.

## Testing plan

Full `tests/research_domain/` suite plus the extended `test_end_to_end.py`
must pass against a live Postgres instance (same Docker-fixture pattern as
M2/M4), consistent with this project's "no milestone is done without its
stated proof passing" standard.
