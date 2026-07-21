# Knowledge Graph Design

Status: approved, ready for planning
Date: 2026-07-20

## Motivation

Novelizer's canon already models the story world through structured aggregates —
characters, threads, secrets, themes, promises, arcs, world entries — each with its
own event types and a `CanonIndexer` that embeds current records into Chroma for the
`search_canon` tool. What canon does *not* capture is the long tail of world detail
that prose introduces incidentally and that never becomes a first-class aggregate: a
tavern mentioned once, a minor faction, an object passed between two characters, or
the fact that a place is "across the river from" another place. Agents currently have
no way to recall these details except by re-reading chapter prose directly.

This design adds a knowledge graph — entities and relations extracted from both
structured canon events and chapter prose — surfaced to agents through the existing
`search_canon` tool.

This was originally scoped as a port of a similar system in the user's `~/life`
project (`pipeline/knowledge.py`), which turned out to be SQLite-backed (not Neo4j,
as misremembered) with LLM extraction, bitemporal properties, HDBSCAN clustering, and
a WebGL visualization. After review, this design borrows the entity/relation
extraction idea but not the visualization, bitemporal properties, or standalone
storage — those don't fit novelizer's event-sourced, agent-context-first use case.

## Architecture

A new `KGProjector`, structurally identical to `store/indexer.py`'s `CanonIndexer`:
event-cursor driven, reading events since a persisted `last_sequence` cursor, called
from the same periodic catch-up loop in `runtime.py` (alongside
`index_catch_up`). Failure-tolerant by the same contract as `CanonIndexer`: an
extraction failure (LLM outage, malformed response) logs a warning and leaves the
cursor in place, so the next catch-up retries. Never mutates `world.db`'s event log,
only its own projection tables.

## Data Model

New tables in `world.db` (not a separate file — see Storage Location below):

```sql
CREATE TABLE kg_entities (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL,
    entity_type TEXT NOT NULL,       -- free-typed, LLM-assigned (no fixed enum)
    description TEXT DEFAULT '',
    canon_id TEXT,                   -- nullable FK to character/world_entry/etc. id
    first_seen INTEGER NOT NULL,     -- event sequence
    last_seen INTEGER NOT NULL,
    UNIQUE(name, entity_type)
);

CREATE TABLE kg_relations (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    source_id INTEGER NOT NULL REFERENCES kg_entities(id),
    target_id INTEGER NOT NULL REFERENCES kg_entities(id),
    relation_type TEXT NOT NULL,
    first_seen INTEGER NOT NULL,
    last_seen INTEGER NOT NULL,
    UNIQUE(source_id, target_id, relation_type)
);

CREATE TABLE kg_entity_mentions (
    entity_id INTEGER NOT NULL REFERENCES kg_entities(id),
    event_fingerprint TEXT NOT NULL,
    PRIMARY KEY(entity_id, event_fingerprint)
);
```

No bitemporal `valid_from`/`valid_to` property tracking — that's real-world-calendar
modeling the `~/life` system needs and novelizer's fictional timelines don't (a
story's "now" is whatever chapter the Author is drafting, not a wall-clock date).

### Storage location: `world.db`, not a separate file

Every other projection (characters, threads, event log itself) lives in `world.db`,
built by `projector.py`. The KG follows that precedent: one file, one backup unit,
consistent with how the rest of canon already works. This was a considered
alternative (a separate `knowledge.db`, mirroring Chroma's separate `chroma/` dir, to
isolate occasionally-noisy LLM-extracted data from canon) but the event-cursor pattern
already gives clean rebuildability without needing file isolation — wiping and
re-deriving `kg_entities`/`kg_relations` is just resetting the cursor, the same as
re-embedding is for Chroma.

## Extraction

Two sources, matching the two kinds of information canon holds:

**Structured canon events** (`character.created`, `character.updated`,
`world_entry.created`, `world_entry.superseded`, `thread.planted`, `secret.created`,
etc.) — deterministic upserts, no LLM call. These events already carry structured
fields; the KG just mirrors them as entities/relations and sets `canon_id` to link
back to the authoritative aggregate.

**Chapter prose** (`chapter.created`, `chapter.revised`) — one LLM extraction call
per chapter over the full chapter text, prompted to extract entities (any type: place,
faction, item, creature, event, etc. — freeform, not a fixed enum) and relations
between them. This is where the long tail comes from: incidental detail prose
introduces that no aggregate captures.

### Entity linking

Extraction attempts to match extracted entity names against existing character and
world_entry names (case-insensitive). A match sets `canon_id`, and that aggregate
stays the authoritative source for its own fields (name, role, description owned by
the character record, not overwritten by KG extraction). No match creates a new
freestanding KG entity. This prevents "Eldara" existing as both a `Character` record
and an unrelated graph node with no link between them.

### Reflow, not accumulation

On `chapter.revised`, mentions tied to that chapter's previous fingerprint are
cleared before re-extraction runs. The graph reflects current canon state, not a
running log of every draft that ever existed — an entity or relation that only
existed in a since-rewritten paragraph disappears on reflow, same as the paragraph
did. This mirrors how `CanonIndexer` re-embeds on `CHAPTER_REVISED` rather than
stacking stale embeddings.

## Agent-Facing Surface

No new tool. `search_canon` (`novelizer/canon_fs/search.py`) gains a new hit `kind`:
`"entity"`. Since KG entities have no `canon_fs` file to point at (unlike chapters,
characters, etc., which resolve to a path an agent can then read), the hit line
inlines what a file-read would otherwise supply: name, type, description, and its
top relations, directly in the result line. This follows the precedent already set
for `arc`, which has no backing file and instead shows `(no file — cite id)` — entity
hits go one step further and put the actually-useful content inline since there's
nothing to read after the hit.

Example hit line:

```
(entity) The Salted Gull tavern [id: 42] — a dockside tavern in Corvane where sailors
trade rumors. Relations: located_in Corvane, frequented_by Mateo, frequented_by Yuki.
```

## Non-Goals

- **No dedicated graph-query tool** (e.g. `query_world_graph`). Reflow-then-search
  through the existing `search_canon` surface was chosen specifically to avoid adding
  a second retrieval surface for agents to learn; if graph-shaped queries (multi-hop
  traversal) turn out to be needed later, that's a future extension, not part of this
  design.
- **No visualization.** No Mission Control tab, no WebGL graph view. This design is
  agent-context-first; a Director-facing view is a separate, later design if wanted.
- **No relation to Causeway.** The existing causal graph faculty (cause → effect
  between story events) stays a separate faculty. This KG is entity-relational
  (who/what/where), not causal.
- **No bitemporal property tracking**, per Data Model above.

## Testing

Following the project's TDD/property-based testing conventions
([[engineering-principles]]):

- `KGProjector.catch_up()` is idempotent: running it twice with no new events produces
  no changes.
- Structured-event upserts are deterministic and don't require an LLM (fast, no
  mocking needed for the structured half of extraction).
- Chapter-prose extraction tests mock the LLM extraction call, covering: first
  extraction on `chapter.created`, reflow-clears-and-re-extracts on
  `chapter.revised`, and failure-tolerance (cursor doesn't advance on extraction
  error).
- Entity linking: property-based test that a KG entity whose name matches an existing
  character (case-insensitive) always gets `canon_id` set, and never creates a
  duplicate unlinked entity for the same name+type.
- `search_canon` entity-kind hits: format test confirming relations are inlined
  correctly, and a cap/truncation test matching the existing `SEARCH_RESULT_CAP`
  behavior.
