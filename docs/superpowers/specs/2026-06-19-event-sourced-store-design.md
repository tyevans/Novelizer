# Novelizer: Event-Sourced Store Design

**Date:** 2026-06-19
**Status:** Approved

## Overview

Replace the current shared-state polling store with an event-sourced architecture. All world state changes are recorded as immutable events in an append-only log. Materialized projections derived from the log serve as the read layer. Each agent tracks its own offset into the log with a configurable catch-up strategy.

No existing data migration is required — this replaces the system before any production data exists.

---

## Architecture

Three components replace the current `Store` facade:

1. **EventStore** — append-only write interface + raw event reads
2. **Projector** — async loop that materializes events into read tables and ChromaDB
3. **ReadStore** — query interface over projection tables (same API agents use today)

---

## Event Log

Single `events` table in SQLite — the sole source of truth.

```
events
  id            TEXT PRIMARY KEY   (uuid)
  sequence      INTEGER NOT NULL   (autoincrement, global ordering)
  event_type    TEXT NOT NULL      (e.g. "world_entry.created")
  aggregate_id  TEXT NOT NULL      (id of the entity this event concerns)
  payload       TEXT NOT NULL      (full JSON of the entity at time of event)
  created_at    TEXT NOT NULL      (ISO 8601 UTC)
```

### Event Types

Following `<domain>.<verb>` convention:

| Domain | Verbs |
|---|---|
| `world_entry` | `created`, `superseded` |
| `character` | `created`, `updated` |
| `chapter` | `created`, `status_changed` |
| `retcon_request` | `created`, `resolved`, `rejected` |
| `director_signal` | `created`, `consumed` |

All existing pydantic entity models are unchanged — they become event payloads. The event log is complete: DirectorSignals are events, not a separate command channel.

---

## Projections

The Projector maintains one materialized read table per entity type:

```
world_entries_current      — latest active WorldEntry per aggregate_id
characters_current         — latest active Character per aggregate_id
chapters_current           — latest Chapter per aggregate_id
retcon_requests_current    — latest RetconRequest per aggregate_id
director_signals_current   — unconsumed DirectorSignals
```

Each table stores the current JSON blob plus indexed columns for fast filtering (status, domain, etc.) — same shape as the existing tables, populated exclusively by the Projector.

The Projector's position is tracked in:

```
projector_state
  id                TEXT PRIMARY KEY   (always "singleton")
  last_sequence     INTEGER NOT NULL
```

The Projector is the only writer to projection tables and to ChromaDB. Agents and the CLI never write there directly.

---

## Agent Offsets & Catch-up Strategies

```
agent_offsets
  agent_name          TEXT PRIMARY KEY
  last_sequence       INTEGER NOT NULL
  catch_up_strategy   TEXT NOT NULL    (full | skip | snapshot)
```

### Strategies

**`full`** — on resume, process every missed event in order before the next work cycle. For agents that must see everything to function correctly.
- ContinuityChecker, Retconner

**`skip`** — on resume, advance offset to current sequence head and start fresh. For agents that work entirely from projections and don't need event history.
- Author, Editor

**`snapshot`** — on resume, pass missed events to the LLM for summarization; inject the summary as context for the next work cycle, then advance the offset. For agents that benefit from knowing what changed but don't need per-event processing.
- WorldArchitect, CharacterKeeper

---

## Write Path: EventStore

```python
class EventStore:
    async def append(self, event_type: str, aggregate_id: str, payload: BaseModel) -> Event
    async def events_since(self, sequence: int, event_types: list[str] | None = None) -> list[Event]
```

`append()` writes to the `events` table and returns the new Event. `events_since()` supports optional type filtering — agents pass their `subscribed_event_types` to cheaply discard irrelevant events.

All current `store.save_*` calls in agents and the CLI are replaced with `event_store.append(...)`.

---

## Read Path: ReadStore

Same query interface agents use today — all reads hit projection tables:

```python
class ReadStore:
    async def list_world_entries(self, domain=None) -> list[WorldEntry]
    async def list_characters() -> list[Character]
    async def list_chapters(status=None) -> list[Chapter]
    async def get_chapter(id) -> Chapter | None
    async def list_retcon_requests(status=None) -> list[RetconRequest]
    async def list_unconsumed_signals(target_agent=None) -> list[DirectorSignal]
    async def get_agent_offset(agent_name: str) -> AgentOffset
    async def set_agent_offset(agent_name: str, sequence: int) -> None
    # Semantic search delegates to ChromaDB (unchanged)
    async def semantic_world(query, n=5) -> list[WorldEntry]
    async def semantic_characters(query, n=5) -> list[Character]
    async def semantic_chapters(query, n=5) -> list[Chapter]
```

---

## BaseAgent Changes

Two additions to `BaseAgent`:

```python
subscribed_event_types: list[str] = []  # empty = receive all events

async def on_events(self, events: list[Event]) -> None:
    pass  # override for full/snapshot strategies
```

`on_events()` is called before `poll()` with any new events since the agent's last offset. The Scheduler calls `set_agent_offset()` after each successful `run_once()`.

The existing `poll() → work() → commit()` lifecycle is unchanged.

---

## Projector

An async loop (not an agent — no LLM, no readiness score, no offset of its own).

- Default tick interval: 500ms
- On each tick: reads events since `projector_state.last_sequence`, upserts projection tables, updates ChromaDB embeddings, advances `last_sequence`
- On `world_entry.superseded`: removes old embedding from ChromaDB, upserts new entry
- Runs independently of the Scheduler

---

## Startup Sequence

1. `EventStore` and `ReadStore` initialize — SQLite tables created if missing
2. Projector catches up — processes all events from `projector_state.last_sequence` to head, including ChromaDB updates
3. Scheduler starts — agents begin running against consistent projections

On crash/restart, the Projector always replays any gap before the first agent tick.

---

## Director CLI Changes

Write commands switch from `store.save_*` to `event_store.append(...)`:
- `seed`, `focus` → append `director_signal.created`
- `finalize` → append `chapter.status_changed`
- `retcon-approve`, `retcon-reject` → append `retcon_request.resolved` / `retcon_request.rejected`

Read commands (`chapters`, `retcons`, `read`) are unchanged — they query the ReadStore.

---

## What Is Replaced

| Current | Replacement |
|---|---|
| `novelizer/store/db.py` (WorldDB) | `EventStore` + `ReadStore` + projection tables |
| `novelizer/store/queries.py` (Store facade) | Split into `EventStore` and `ReadStore` |
| Direct writes in agents (`store.save_*`) | `event_store.append(...)` |
| Direct ChromaDB writes in Store | Projector owns all ChromaDB writes |

`novelizer/store/models.py` and `novelizer/store/embeddings.py` are unchanged.

---

## Out of Scope

- Event log compaction or archival
- Multi-projector parallelism
- Event schema versioning / migrations
- External event streaming (Kafka, Redis Streams)
