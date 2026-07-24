# World-Content Curation — Design

**Date:** 2026-07-23
**Status:** Approved (design); ready for implementation plan
**Scope:** Stage 1 (reactive world-entry curation). Stage 2 (proactive sweep) is designed-for but not built.

## Problem

Prose gets curated; the world does not. Chapters have a real in-place revise loop
(`CHAPTER_REVISED`, stable id, bounded by `revision_count`) and characters have
`CHARACTER_UPDATED`. World entries have the weakest mutation vocabulary of the three:
they can only be **created** (World Architect, additive-only) or **superseded**
(Retconner, and only reactively, when a contradiction flag fires). There is no
in-place edit, no deletion, and no consolidation. World entries only grow.

There is also no stored notion of **relevance** for world entries. Staleness tracking
exists only for threads. Relevance is computed at query time as vector distance in the
Chroma pull-mode search; push-mode simply inlines the first 20 active entries by
insertion order. Nothing curates which world content still earns its place.

The goal: let the agents already in the loop **flag** world content that needs
attention, and give a new **Curator** agent the ability to **revise, reclassify,
merge, or retire** it.

## Decisions

Four forks were resolved during brainstorming:

1. **Agent-driven via the existing flag mechanism**, not human/director-driven. Agents
   raise flags on world content; a consumer resolves them. (Mirrors the existing
   contradiction → Retconner path, generalized.)
2. **Operations:** revise prose, reclassify (domain/tags), merge overlapping entries,
   retire (delete) an entry. **No formal hierarchy/ordering** — the world is retrieved
   by semantic relevance, not browsed as a tree, so an ordering field would be
   machinery nothing reads. `split` and `relocate` were considered and deferred as the
   two fiddliest verbs; both are cheap to add later.
3. **Staged reactive-then-proactive.** Stage 1 is reactive (agents flag what they trip
   over). Stage 2 adds a proactive sweep for staleness/bloat that reactive flagging
   structurally misses. Only Stage 1 is built now.
4. **A dedicated Curator agent**, not a generalized Retconner. The Retconner's identity
   is "smallest possible amendment to fix a contradiction"; curation is a broader,
   editorial job, and Stage 2's whole-world sweep is a different behavior that wants its
   own home. The Curator is a near-clone of the proven Retconner pattern.

## The event model — one new event

Every mutation is an appended event; the Postgres event log rejects UPDATE/DELETE via
triggers, so a "delete" is a tombstone, never a destructive write. Everything the design
needs composes from the existing `WORLD_ENTRY_CREATED` + `WORLD_ENTRY_SUPERSEDED` plus a
single new tombstone:

| Operation      | Expressed as |
|----------------|--------------|
| Revise prose   | `SUPERSEDED` — new entry, improved `body`, `supersedes_id` → old |
| Reclassify     | `SUPERSEDED` — same body, changed `domain`/`tags` |
| Merge          | `SUPERSEDED` (primary → consolidated) + `RETIRED` (each absorbed source) |
| Retire         | `RETIRED` (target) |

The only net-new event is **`WORLD_ENTRY_RETIRED`**, carrying `{entry_id, reason,
flag_id}`. On projection it flips the read-model row to a new
`canon_status="retired"` — distinct from `superseded` (which implies a named successor;
retired means "gone, no successor"). The event log keeps the full body forever for
provenance; the read model and indexes drop it.

**Identity choice:** world entries keep the **supersede-with-new-id** model for
revisions rather than switching to chapter-style in-place stable ids. This matches the
proven Retconner path, preserves a provenance chain for free, and the `supersedes_id`
chain is the "lineage" Stage 2's last-referenced tracking will hang onto. The cost —
a world entry's id changes on every revision — is contained: nothing in canon references
world entries by stable id except open flags, and the Curator resolves those in the same
pass.

## Flag categories & who raises them (reactive)

`Flag.category` is already a free-form string, so new categories need **zero schema
change**. Three new categories, all routed to the Curator via one-line additions to
Triage's `_CATEGORY_OWNERS`:

- **`world_craft`** — an entry's prose is weak, bloated, or muddled (revise/trim).
- **`world_relevance`** — an entry or a detail no longer serves the story (retire,
  trim, or reclassify).
- **`world_redundancy`** — an entry overlaps another (merge).

The category does not pre-decide the verb — it routes to the Curator, which chooses the
action at resolve time from the flag + the entry. Categories exist for readiness scoring
and observability, not to branch logic.

**Raisers** (all via the existing `_commit_flag_drafts` helper — no new plumbing):

- **World Architect** — `world_redundancy` (it already reads canon before adding, so it
  can flag overlap instead of blindly appending) and `world_relevance`
  (misclassification of `domain`/`tags`).
- **Continuity Checker** — `world_relevance` when an entry has drifted from where the
  story actually went. (A hard factual contradiction still goes to the Retconner.)
- **Editor / Author** — `world_craft` when a world entry they pull for context reads as
  weak or bloated.

**In-scope bonus fix:** Triage currently routes `worldbuilding → world_architect`, but
World Architect never consumes those flags, so they dead-letter open forever. Since the
Curator is now the real world-content consumer, reassign `worldbuilding → curator`,
closing that latent leak.

## The Curator agent

Modeled on the Retconner — same `readiness → poll → lane-guard → work →
commit/decline` skeleton, registered in the agent roster so the generic
readiness-sorted scheduler picks it up (no scheduler changes).

- **`readiness()`** — counts open flags in `{world_craft, world_relevance,
  world_redundancy, worldbuilding}`, scaled like the Retconner's `/3`.
- **`poll()`** — pulls open curation flags, picks the first non-deferred as target.
- **lane guard** — `related_entry_ids` must name at least one *active* world entry.
  Non-world target → `_decline("out_of_lane")`. Already-gone target →
  `_decline("stale_target")`.
- **`work()`** — sends the flag + the target entry (body/tags/domain) + any named
  sibling entries to the LLM → a structured `CurationDecision`.
- **`commit()`** — translates the decision to events, then `FLAG_RESOLVED`.
- **`decline()`** — `FLAG_REJECTED`, `failed_attempts += 1`, escalate at 3 (existing).

### `CurationDecision` schema

One action per resolve, each a small independently-testable translation to events:

| action       | emits |
|--------------|-------|
| `revise`     | `SUPERSEDED` (target → new body) |
| `reclassify` | `SUPERSEDED` (metadata only: domain/tags) |
| `merge`      | `SUPERSEDED` (primary → consolidated) + `RETIRED` (each absorbed source) |
| `retire`     | `RETIRED` (target) |
| `reject`     | decline path — flag isn't actionable |

### Guardrails

The real risk is the LLM picking the wrong verb, not any single verb's mechanics.

- **Retire is the last resort.** Prompt bias: prefer trim/revise/reclassify; retire only
  when the entry clearly no longer serves the story *and* isn't load-bearing. Stage 1
  can't verify "load-bearing" — see Limitations.
- **Anti-thrash.** A deferral set (as the Retconner already uses) stops the Curator
  acting twice on the same lineage in one run; `failed_attempts ≥ 3` escalates. Prevents
  flag → revise → re-flag → revise ping-pong.
- **Reference integrity is free.** After a merge/retire, any other open flag pointing at
  a now-gone entry becomes stale, and Triage's existing aging sweeps it to `stale`. No
  dangling work to hand-manage.
- **Append-only & retry-safe.** Every action is appended events; a resolved flag is no
  longer `open`, so rate-limit re-runs can't double-apply.

## Relevance wiring

How curation moves the relevance needle:

- **Projector** gets one new handler: `RETIRED` → flip the read-model row to
  `canon_status="retired"`, dropping it from the active list. `SUPERSEDED`
  (revise/reclassify/merge) is already handled.
- **Indexer** — `RETIRED` reuses the exact removal path supersede already uses: delete
  the id from the Chroma `world_entries` collection and from the KG. No new removal
  machinery.
- **`reclassify` is the relevance lever in action:** a `SUPERSEDED` with new
  `domain`/`tags` and the same body → reindexed with new metadata → semantic search *and*
  push-mode ordering surface the entry differently. This is how curation changes what
  future agents see.
- **`merge`** — consolidated entry stays active; absorbed sources go to
  `retired`/`superseded` → gone from both the active list and the index in one pass.

## Testing

Per the project's non-negotiable event-sourcing + property-based TDD. Tests are written
red/green as the work proceeds; the **full-suite run and code review are held to the very
end of the workstream**, not run per-section.

- **Projector (property):** a retired entry never appears in
  `list_world_entries(active)` regardless of prior event history; its id is removed from
  the index; supersede chains stay intact.
- **Curator:** open curation flag → valid `CurationDecision` → correct event set →
  `FLAG_RESOLVED`. Decline → `FLAG_REJECTED` + escalation at 3. Lane guard: non-world
  target → `out_of_lane`; already-gone target → `stale_target`.
- **Per-verb translation:** revise/reclassify/merge/retire each emit exactly the right
  events.
- **Merge (property):** exactly one active consolidated entry afterward; all sources
  removed from active + index; other open flags at gone entries age to `stale`.
- **Append-only invariant:** resolved flags aren't reprocessed; no UPDATE/DELETE hits the
  event log.
- **Raiser dedup:** agents don't double-raise the same flag description.

## Error handling & limitations

- Flag target missing / already superseded / retired → `stale_target` decline, no crash.
- Invalid LLM decision (retire with no reason, merge naming <2 or non-existent entries) →
  schema validation forces a retry via the existing structured-output path.
- **Stated limitation:** the Curator can't know an entry is load-bearing, so a bad
  `retire` is possible. Stage 1 mitigates this only with prompt caution. The genuine fix
  is Stage 2's last-referenced signal.

## Stage 2 — designed-for, not built

- **Last-referenced tracking** — a projection recording which world entries were
  pulled/cited during chapter drafting → "last referenced at chapter N" per lineage. The
  missing signal that makes both *staleness* and *load-bearing* checkable.
- **Proactive sweep** — the Curator's `readiness()` rises on a cadence (e.g. every N
  chapters) to review the whole world; embedding-cluster overlap detection auto-raises
  `world_redundancy`; staleness auto-raises `world_relevance`. Sits on the Stage-1
  Curator with no rework.
- **Deferred verbs** — `split` and `relocate`, each a one-verb addition on the existing
  event vocabulary.

## Files touched (anticipated)

- `novelizer/canon/events.py` — add `WORLD_ENTRY_RETIRED`.
- `novelizer/canon/projector.py` — `RETIRED` handler; `retired` canon_status.
- `novelizer/store/indexer.py` — remove retired id from Chroma + KG (reuse supersede path).
- `novelizer/agents/schemas.py` — `CurationDecision`.
- `novelizer/agents/curator.py` — new agent (near-clone of `retconner.py`).
- `novelizer/agents/triage.py` — `_CATEGORY_OWNERS`: add curation categories + reassign
  `worldbuilding` to curator.
- `novelizer/agents/{world_architect,continuity_checker,editor,author}.py` — raise the
  new categories.
- Agent roster/builder — register the Curator.
- Tests alongside each.
