# Novelizer: Product Vision & End-State Design

**Date:** 2026-07-17
**Status:** Approved
**Supersedes:** extends (does not replace) the agent-system design (2026-06-15) and event-sourced store design (2026-06-19)

## Vision

Novelizer is a **director's control room for an autonomous writers' room**. Six AI agents with configurable personalities collaboratively build a living world and write a novel inside it, coordinating exclusively through an append-only event log — the world's canon. The human director watches from a Mission Control TUI: steering with seeds and focus signals, adjudicating retcons, tuning how much autonomy the room gets, and reading the book as it emerges.

Underneath, a **story brain** understands the narrative as *shape*, not just facts: act structure and tension, narrative threads and their payoffs, what each character knows and when, cause-and-effect chains, and thematic development.

The product is novel on three axes:

1. **Emergence you can steer** — an adjustable autonomy dial between "terrarium you watch" and "room where nothing enters canon without your sign-off."
2. **Story understanding as auditable canon** — narrative intelligence is recorded as events (`thread.planted`, `secret.learned`), queryable and visualizable, not vibes inside a prompt.
3. **Voice as a first-class material** — the prose voice, the agents' personalities, and each character's voice are all configurable, inspectable, and enforced.

---

## Engineering Principles

These are core to how the system is built, not aspirations:

- **Event sourcing.** The event log is the sole source of truth for world/story state. All writes are appends; all reads come from projections; projections are disposable and rebuildable. No component ever mutates canon in place.
- **Domain-driven design.** The system is organized into bounded contexts (below) with explicit boundaries and a ubiquitous language (canon, chapter, thread, secret, retcon, signal, voicing, casting note). Domain events are the contracts between contexts. Aggregates own their invariants.
- **SOLID.** Applied at module boundaries: agents depend on `ReadStore`/`EventStore` abstractions, never on SQLite or ChromaDB directly; the TUI depends on projections and the event stream, never on agent internals; new agents, voicings, and brain faculties are added by extension, not modification.
- **Red/green TDD.** Every feature starts with a failing test. Tests are as black-box as possible — they exercise public interfaces and assert on observable events/projections, not internals. Parametrized where cases enumerate; property-based (Hypothesis) where invariants generalize — the event log and Projector are prime property-test targets (append-only, monotonic sequence, replay idempotency, projection/rebuild equivalence).
- **Spec-first, reviewed.** Each milestone gets a spec and an implementation plan before code, and code review before merge.

### Bounded Contexts

| Context | Responsibility | Owns |
|---|---|---|
| **World Canon** | The event-sourced record of everything true in the story world | EventStore, Projector, ReadStore, all domain events |
| **Agent Roster** | The six deep agents and their execution | deepagents graphs, LangGraph checkpoints, per-agent model config |
| **Story Brain** | Narrative intelligence derived from canon | analyzers, brain projections (shape, threads, knowledge, causality) |
| **Direction** | The human interface | TUI, CLI, approval queue, autonomy dial, voice-pack management |

Contexts communicate only through domain events and read-side queries. LangGraph checkpointing state is *agent plumbing*, never canon.

---

## Architecture

Five layers:

```
┌─────────────────────────────────────────────────────┐
│  Direction: Mission Control TUI (Textual) + CLI     │
└─────────────────────────────────────────────────────┘
              ▲ tails event log / queries projections
┌─────────────────────────────────────────────────────┐
│  Story Brain: analyzers → brain projections          │
├─────────────────────────────────────────────────────┤
│  Scheduler + Autonomy Dial (approval gating)         │
├─────────────────────────────────────────────────────┤
│  Agent Roster: 6 deep agents (deepagents/LangGraph)  │
└─────────────────────────────────────────────────────┘
              ▼ append events   ▲ read projections
┌─────────────────────────────────────────────────────┐
│  World Canon: EventStore → Projector → ReadStore     │
│  (SQLite event log · projection tables · ChromaDB)   │
└─────────────────────────────────────────────────────┘
```

### World Canon

The 2026-06-19 event-sourced store design is implemented as specified: append-only `events` table as sole source of truth, Projector materializing projection tables and ChromaDB embeddings, ReadStore as the query interface, per-agent offsets with `full`/`skip`/`snapshot` catch-up strategies.

Extensions to that spec:

- **New event domains** for the story brain: `thread.*` (planted, touched, paid_off, abandoned), `secret.*` (created, learned, revealed), `theme.*` (introduced, developed), `annotation.*` (structure/tension scores).
- **Proposal gating**: when the autonomy dial gates an agent, its output is appended as a `*.proposed` event. Director approval appends the corresponding committed event; rejection appends `*.rejected`. Proposals are canon (auditable) but projections treat only committed events as world truth.
- **Embeddings** move from the Ollama SDK to OpenAI-compatible embedding endpoints.

### Agent Roster (deepagents on LangGraph)

The six-agent roster is retained — World Architect, Character Keeper, Author, Editor, Continuity Checker, Retconner — rebuilt on LangChain's **deepagents** library over LangGraph, replacing pydantic-graph entirely.

- Each agent is a deep agent with planning, scratch filesystem memory, and subagents where the role benefits (e.g. Author spawning a per-scene drafting subagent; Continuity spawning parallel checkers).
- **All model access is via OpenAI-compatible endpoints** (llama.cpp, vLLM, hosted providers behind compat shims). Model, endpoint, and sampling parameters are configurable per agent.
- LangGraph checkpointing persists agent execution state only. Agents never message each other; the event log remains the only coordination channel.
- The `poll → work → commit` cycle survives conceptually: poll = read projections + consume events since offset; work = deepagents graph run; commit = append validated events.
- Agents emit story-brain events as part of their work (Author emits `thread.touched`, `secret.learned` alongside `chapter.created`), keeping narrative intelligence in-band.

### Story Brain

Four faculties. Each is a projection + an analyzer + a TUI view. Analyzers are hybrid: deterministic bookkeeping wherever possible, LLM calls only where judgment is required. Analyzers backfill what agents fail to emit — the brain converges on complete coverage even with imperfect agents.

| Faculty | Deterministic part | LLM part | TUI view |
|---|---|---|---|
| **Structure** | act/chapter registry, word counts, scene inventory | tension & pacing scores, sag/spike detection | Story Shape |
| **Threads** | planted/touched/stale bookkeeping from `thread.*` events | detecting plants and payoffs in prose | Thread Board |
| **Knowledge** | secret × character matrix from `secret.*` events | extracting who-learned-what from prose; leak detection | Who-Knows-What |
| **Causality** | cause→effect edges between events | inferring implicit causal links; paradox candidates | Causeway |

Brain output flows two directions: **into agents** (relevant brain context injected into prompts — Author sees stale threads, Editor sees pacing flags) and **into the TUI** (the four views). Theme/motif tracking lives in the Story Browser rather than as a fifth view.

### Scheduler & Autonomy Dial

The readiness-scored async scheduler from the 2026-06-15 design is retained (readiness checks, priority heuristics, min-intervals, director overrides).

The **autonomy dial** is new: a global trust level with per-agent overrides, determining which event types an agent may commit directly vs. must propose. Example ladder: `full-auto` → everything commits; `gated:retcons` → retcon resolutions and supersedes require approval; `gated:canon` → world entries and character updates also queue; `gated:all` → nothing enters canon unapproved. The dial is adjustable live from the TUI and is itself recorded as a `director_signal` event.

### Voicing System

Three kinds of voicing, all stored as human-editable TOML **voice packs** — git-friendly, shareable, written as natural-language *casting notes* rather than parameter lists:

- **Prose voice profiles** — diction, register, POV discipline, rhythm. One active profile per story, overridable per chapter. The Author writes in the active profile; the Editor enforces it (style drift becomes an editorial note).
- **Agent personalities** — one casting note per roster member, coloring both how the agent works and how it speaks in the activity feed. The room reads differently depending on the cast.
- **Character voice cards** — dialogue patterns, vocabulary, verbal tics per character. Primarily *system-built*: the Character Keeper grows them as characters accrue chapters; the Editor and Continuity Checker enforce them. Director-editable.

The TUI provides a voice-pack browser/picker and can scaffold a new pack from a one-line prompt. Files remain the source of truth; pack selection/changes are recorded as events.

### Direction: Mission Control TUI

Built with Textual. The TUI is a *reader of canon*: everything it renders comes from tailing the event log and querying projections.

**Home screen — Mission Control** (persistent dashboard):

- **Activity feed** (left, largest): live agent activity rendered from events.
- **Story browser** (right): chapters, characters, world entries, retcons, threads/themes — drill into any record.
- **Agent roster strip**: per-agent status (working/idle/paused/flagged) at a glance.
- **Status bar**: autonomy dial state + always-available command line (`:seed`, `:focus`, `:pause`, …).

**Drill-in views** (keystroke away, esc back):

- **The Room** — full-screen feed where agents speak in their cast personalities; rich inline cards (chapter drafts, retcon debates) expand in place.
- **Story Shape, Thread Board, Who-Knows-What, Causeway** — the four brain views (priority in that order).
- **Chapter reader** — clean prose reading mode.
- **Approval queue** — pending proposals when the dial gates; approve/reject with context shown.

The click CLI is retained for scripting and headless direction; TUI and CLI share the same command layer.

---

## Testing Strategy

- **Fake LLM endpoint**: because all model access is OpenAI-compatible, tests run against a local fake server (or stub transport) returning canned completions — agent code contains no test seams.
- **Property-based (Hypothesis)**: event log invariants (append-only, monotonic sequence), Projector idempotency (replaying any prefix twice = replaying once), projection rebuild equivalence (rebuild-from-zero == incremental).
- **Parametrized black-box agent tests**: recorded context in → asserted event shapes out, across case tables per agent.
- **TUI tests**: Textual's pilot harness for interaction flows; feed rendering tested as pure event → renderable transformations.
- **Red/green discipline**: failing test first, for every feature, per the engineering principles above.

## Error Handling

- **Agent crash**: isolated — a supervisor restarts the agent; canon is never corrupted because incomplete work is never appended.
- **LLM endpoint failure**: exponential backoff; agent marked *paused (endpoint down)* in the roster strip with reason visible.
- **Malformed LLM output**: pydantic validation with bounded retries; on exhaustion the attempt is dead-lettered as a visible feed item — never silently dropped.
- **Projector crash**: replay from `projector_state.last_sequence` on startup, before any agent ticks (per the store spec).
- **TUI disconnect/restart**: stateless reader — reopens, tails from the current head, backfills the visible window from projections.

---

## Milestones

Each milestone ends with a runnable, watchable product. Each gets its own spec + implementation plan + TDD + review cycle.

### M0 · Heartbeat — the spine
Event store + Projector + ReadStore built to spec (with proposal-gating event shapes defined, even if unused). Author rebuilt as a deepagents agent against an OpenAI-compat endpoint. Skeletal Textual TUI tailing the event log as a live feed.
**Done when:** you run `novelizer`, and watch chapters appear in real time.

### M1 · The Room Assembles — full roster
All six agents ported to deepagents. Scheduler with readiness scoring. Autonomy dial + approval queue. Story browser pane, agent roster strip, command palette. Full Mission Control layout, plain-voiced.
**Done when:** the whole room runs unattended; you can gate, approve, seed, and browse from the TUI.

### M2 · Voices
Voice-pack system: prose profiles + agent personalities, TOML casting notes, in-TUI browser and scaffolding. The Room drill-in view. Character voice cards v1 (Keeper builds them, Editor cites them).
**Done when:** recasting the Editor's personality visibly changes the feed, and switching prose profiles visibly changes the next chapter.

### M3 · Shape & Threads — story brain phase 1
Structure + Threads faculties: `thread.*` and `annotation.*` events, analyzers, Story Shape and Thread Board views. Brain context injection into Author/Editor prompts.
**Done when:** a deliberately stale thread surfaces on the Thread Board and the Author, unprompted, picks it back up.

### M4 · Knowledge & Cause — story brain phase 2
Knowledge + Causality faculties: `secret.*` events, who-knows-what matrix, causal graph, Who-Knows-What and Causeway views. Continuity Checker upgraded to flag knowledge leaks and causal paradoxes as first-class retcon requests.
**Done when:** a planted knowledge leak ("she never learned this") is caught and routed to the retcon queue automatically.

### M5 · Finish
Theme/motif tracking in the Story Browser. Character-voice enforcement maturity. UX polish pass across all views. Performance work, packaging, user docs.
**Done when:** a stranger can install it, cast a room, seed a world, and read a coherent novella a day later.

---

## Out of Scope

- Multi-user / collaborative direction
- Web interface
- Export to publishing formats (beyond plain markdown chapter dumps)
- Agent self-modification / meta-reasoning about the agent system
- Event log compaction, schema versioning, external streaming (unchanged from store spec)
