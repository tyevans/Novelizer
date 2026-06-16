# Novelizer: Collaborative World-Building Agent System

**Date:** 2026-06-15
**Status:** Approved

## Overview

A system of autonomous, specialized pydantic-graph agents that collaboratively build an ever-expanding living world and tell consistent stories within it. Agents run continuously, each on their own cadence, communicating exclusively through a shared persistent world store. A human director can be as hands-off or hands-on as desired — setting narrative direction, injecting seeds, or simply watching the world grow.

The existing novelizer codebase (LightRAG, NetworkX, TUI) is replaced entirely.

---

## Architecture

Four layers:

1. **World Store** — shared persistent brain (SQLite + ChromaDB)
2. **Agent Layer** — six independent pydantic-graph instances
3. **Scheduler** — async loop managing agent cadence and priority
4. **Director Interface** — CLI (and optional TUI) for human direction

---

## World Store

### Structured Layer (SQLite, via pydantic models)

All writes are append-only. Nothing is deleted — only superseded via `supersedes_id`. The canonical view of any entity is the latest non-superseded record.

**WorldEntry**
- `id`, `created_at`, `supersedes_id`
- `domain`: `physical | social | metaphysical | historical | other`
- `title`: str
- `body`: str (free-form lore)
- `canon_status`: `active | superseded | contested`
- `tags`: list[str]

**Character**
- `id`, `created_at`, `supersedes_id`
- `name`, `aliases`: list[str]
- `traits`, `motivations`, `backstory`: str
- `arc_status`: str (current narrative position)
- `relationships`: list[CharacterRelationship] (foreign keys)
- `canon_status`: `active | superseded`

**Event**
- `id`, `created_at`
- `story_time`: str (in-world timestamp/era)
- `title`, `description`: str
- `participant_ids`: list[Character.id]
- `location_id`: WorldEntry.id (must be domain=physical)
- `consequences`: str

**Chapter**
- `id`, `created_at`, `supersedes_id`
- `title`: str
- `prose`: str
- `event_ids`: list[Event.id]
- `character_ids`: list[Character.id]
- `editorial_status`: `draft | reviewed | final`
- `editor_notes`: str | None

**RetconRequest**
- `id`, `created_at`
- `description`: str (what contradiction was found)
- `conflicting_entry_ids`: list[str] (any entity type)
- `proposed_resolution`: str
- `status`: `open | resolved | rejected`
- `resolved_by`: str | None (agent name)

**DirectorSignal**
- `id`, `created_at`
- `kind`: `seed | focus | override | note`
- `body`: str
- `target_agent`: str | None (None = broadcast)
- `consumed`: bool

### Semantic Layer (ChromaDB + Ollama embeddings)

Every `WorldEntry`, `Character`, and `Chapter` body is embedded on write into ChromaDB. Agents query it with natural language to retrieve relevant context without scanning the full database. Collections: `world_entries`, `characters`, `chapters`.

---

## Agent Layer

Each agent is an independent pydantic-graph instance running as an asyncio task. All six share this state machine:

```
Idle → Polling → Working → Committing → Idle
```

- **Idle** — sleeping until the scheduler wakes the agent
- **Polling** — semantic + structured queries to build relevant context
- **Working** — LLM call(s) with agent-specific system prompt and role
- **Committing** — validates LLM output as pydantic models, writes to world store, emits signals

### Agent Roster

**World Architect**
- Focus: lore, geography, factions, cosmology, history, rules of the world
- Polling: identifies domains with thin or no coverage
- Working: generates new `WorldEntry` records expanding underrepresented domains
- Signals: none (pure producer)

**Character Keeper**
- Focus: character profiles, arcs, and behavioral consistency
- Polling: reads recent events and authored chapters
- Working: updates `Character.arc_status`, flags behavioral drift as `RetconRequest`
- Signals: emits `RetconRequest` on contradiction

**Author**
- Focus: prose generation
- Polling: fetches unwritten narrative beats from events + `DirectorSignal` (focus/seed kinds)
- Working: writes a `Chapter` draft grounded in current world and character state
- Signals: none (pure producer of draft chapters)

**Editor**
- Focus: prose quality and coherence
- Polling: fetches oldest `Chapter` with `editorial_status=draft`
- Working: critiques the chapter, either promotes to `reviewed` or returns with notes
- Signals: emits `DirectorSignal(kind=note)` addressed to Author when sending back

**Continuity Checker**
- Focus: contradiction detection across all world data and prose
- Polling: semantic similarity search across all collections for near-contradictions
- Working: evaluates candidate contradictions, decides if they are genuine conflicts
- Signals: emits `RetconRequest` on confirmed contradiction

**Retconner**
- Focus: resolving open contradictions
- Polling: fetches oldest open `RetconRequest`
- Working: proposes resolution, writes amended entries with `supersedes_id` pointing to conflicting originals
- Signals: marks `RetconRequest.status = resolved`

### Inter-Agent Communication

**Agents do not call each other directly.** All coordination is through world store state. An agent's output becomes another agent's input on the next poll cycle.

---

## Scheduler

A single async loop with a priority queue. Wakes agents based on:

**Store state signals** (readiness checks):
- Each agent exposes a `readiness_check() -> float` (0.0–1.0) — a lightweight store query returning how much work is available
- Scheduler runs the highest-scoring ready agent
- Agents with non-overlapping store concerns (e.g. World Architect + Editor) can run concurrently

**Director signals:**
- `DirectorSignal(kind=override, target_agent=X)` bumps agent X to the front of the queue

**Rate limiting:**
- Each agent has a configurable `min_interval` (default: Author=5min, Continuity Checker=15min, others=2min)
- Prevents any one agent from monopolizing the LLM

**Example priority heuristics:**
- `RetconRequest` count > 3 → boost Retconner
- No `Chapter` with `editorial_status=draft` → boost Author over Editor
- `DirectorSignal` with `kind=seed` unconsumed → boost World Architect

---

## Director Interface

### CLI

Two modes:

**Live mode** (`novelizer run`): streams a feed of agent activity and produced content. Human can watch passively or interrupt with `Ctrl+C` to enter command mode.

**Command mode** (`novelizer <command>`):

| Command | Effect |
|---|---|
| `novelizer seed "<text>"` | Injects `DirectorSignal(kind=seed)` |
| `novelizer focus <entity>` | Injects `DirectorSignal(kind=focus, body=entity)` |
| `novelizer pause <agent>` | Pauses a specific agent |
| `novelizer resume <agent>` | Resumes a paused agent |
| `novelizer retcons` | Lists open `RetconRequest` records |
| `novelizer retcon approve <id>` | Marks retcon resolved, director-confirmed |
| `novelizer retcon reject <id>` | Marks retcon rejected |
| `novelizer chapters` | Lists chapters by editorial status |
| `novelizer read <chapter_id>` | Prints chapter prose |
| `novelizer finalize <chapter_id>` | Promotes chapter to `final` |

### TUI (optional)

A Textual-based interface providing a split view: agent activity feed on one side, world store browser (characters, lore, chapters, retcons) on the other. Wraps the same CLI commands.

---

## Technology Stack

| Layer | Technology |
|---|---|
| Agent graphs | `pydantic-graph` |
| Data models | `pydantic` v2 |
| Structured store | SQLite via `aiosqlite` |
| Vector store | ChromaDB (embedded) |
| Embeddings | Ollama (local) |
| LLM calls | Ollama (local, configurable model per agent) |
| Async runtime | `asyncio` |
| CLI | `click` + `rich` |
| TUI (optional) | `textual` |

---

## What Is Replaced

The following from the existing codebase are removed:
- LightRAG integration (`enhanced_rag_engine.py`, `ai_consistency_validator.py`, `suggestion_engine.py`)
- NetworkX knowledge graph (`knowledge_graph.py`)
- OpenAI dependency
- Existing story manager, models, and TUI

The `pydantic`, `ollama`, `click`, `rich`, and `textual` dependencies are retained.

---

## Out of Scope

- Multi-user / collaborative director access
- Web interface
- Export to publishing formats
- Agent self-modification or meta-reasoning about the agent system itself
