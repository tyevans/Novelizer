# Novelizer

An autonomous world-building and storytelling agent system. An event-sourced world log is
the sole source of truth; an Author agent drafts chapters against it, and a terminal
Mission Control UI shows the story unfold live.

Currently at milestone **M0 (Heartbeat)**: an event-sourced store, an Author agent running
on [deepagents](https://github.com/langchain-ai/deepagents) against an OpenAI-compatible
endpoint, and a skeletal TUI that tails the event log. See
[`docs/MILESTONES.md`](docs/MILESTONES.md) for the roadmap.

## Installation

```bash
uv sync
```

## Requirements

- Python 3.13+
- A running **OpenAI-compatible** LLM server (e.g. [llama.cpp](https://github.com/ggml-org/llama.cpp)'s
  `llama-server`, [vLLM](https://github.com/vllm-project/vllm), LM Studio, etc.) reachable
  at the configured base URL. No cloud API key is required for local servers.

## Configuration

Settings are read from environment variables (prefix `NOVELIZER_`) or a `.env` file:

| Variable | Default | Purpose |
|---|---|---|
| `NOVELIZER_DB_PATH` | `stories/world.db` | SQLite event store / projections path |
| `NOVELIZER_LLM_BASE_URL` | `http://localhost:8080/v1` | OpenAI-compatible endpoint for agents |
| `NOVELIZER_LLM_API_KEY` | `not-needed` | API key sent to the endpoint, if required |
| `NOVELIZER_AUTHOR_MODEL` | `local-model` | Model name passed to the endpoint for the Author |
| `NOVELIZER_AUTHOR_TEMPERATURE` | `0.8` | Sampling temperature for the Author |
| `NOVELIZER_AUTHOR_INTERVAL` | `300` | Seconds between Author drafting passes |
| `NOVELIZER_PROJECTOR_INTERVAL` | `0.5` | Seconds between projector catch-up passes |

## Usage

Launch the Mission Control TUI (live event feed of the world as it's authored):

```bash
novelizer
```

### Mission Control

The dashboard displays four synchronized panes:
- **Activity Feed** (`#feed`, left pane) — real-time event log of agent actions (chapters drafted, retcons filed, etc.)
- **Story Browser** (`#browser`, right pane) — organized chapters, characters, world entries, and retcons; click to inspect
- **Agent Roster** (`#roster`, bottom-left) — agents' names, autonomy status, and current task
- **Detail Pane** (`#detail`, bottom-right) — full text of the selected item from the browser

Command palette (focus with `Ctrl+K` or `:`, then type):
- `seed <text>` — inject a narrative seed
- `focus <agent>` — inject a focus/steer signal that agents pick up as context on their next run
- `pause <agent>` — pause an agent
- `resume <agent>` — resume a paused agent

Toggle Room drill-in view with `r`.

### Autonomy & Approvals

The autonomy dial controls which agent actions require human approval before they update
the canon:

**Autonomy levels:**
- `full_auto` — all agents' events append immediately (default; no approval queue)
- `gated_retcons` — only Retconner output queues as proposals; other agents auto-append
- `gated_canon` — all agents' canon events queue as proposals (chapters, edits, retcons); director signals (seed, focus, pause, resume) auto-append regardless
- `gated_all` — all agent canon-changing events (rare; for testing); director signals are never gated

Set the autonomy level for all agents or for a specific agent:

```bash
# TUI command input (focus with `:` or `Ctrl+K`):
autonomy full_auto
autonomy gated_retcons Editor

# CLI:
novelizer autonomy full_auto
novelizer autonomy gated_retcons Editor
```

When an agent's output is gated, it queues as a proposal in the approval-queue pane
(bottom-right in Mission Control). View and approve/reject from the TUI:

```bash
# TUI command input:
approve <proposal-id>
reject <proposal-id>

# CLI:
novelizer proposals              # list all pending proposals
novelizer approve <proposal-id>
novelizer reject <proposal-id>
```

**Note:** Director signals (`seed`, `focus`, `pause`, `resume`) are never gated,
regardless of autonomy level — they auto-append immediately and act as context for agents
on their next run.

Inject a narrative seed — a director signal the Author will pick up on its next pass:

```bash
novelizer seed "A stranger arrives at the gates at dusk."
```

List chapters and inspect one:

```bash
novelizer chapters
novelizer read <chapter-id>
```

## Voices

Prose voice is data, not code: a **voice pack** is a human-editable TOML file
(`novelizer/voices/default.toml` ships as the default) listing named
**prose profiles** — each a natural-language "casting note" describing the
prose the Author should write in (e.g. `sparse`, `lush`, `plain`).

The active pack and active profile are read from `Settings` at startup:

- `NOVELIZER_VOICE_PACK` — path to a voice-pack TOML file (defaults to the
  shipped pack).
- `NOVELIZER_PROSE_PROFILE` — the profile name within that pack to cast the
  Author (and the Editor's enforcement) in (defaults to `plain`).

Inspect any pack's profiles without starting a run:

```bash
novelizer voices                       # list the active pack's profiles
novelizer voices --pack my-pack.toml   # inspect another pack
```

The active prose profile is chosen per run via `NOVELIZER_PROSE_PROFILE` — restart the
process to switch. Live in-TUI switching of the active profile, and per-agent
personality casting (also carried in the pack format today), arrive in M2.3.

### Personalities & the living feed

Each roster member also has a **personality** — a short casting note from the
active pack's `[agent_personalities]` table (e.g. the Editor's "precise,
unsentimental line editor" vs. the Author's "restless, romantic chronicler").
The personality is injected into that agent's work-time prompt the same way
the prose profile is, and agents may emit a short in-personality remark as
part of their structured output. Remarks are appended to canon as
`agent.remarked` events — feed flavor only, never gated, never projected —
and rendered in the activity feed (and the full-screen Room view, toggled
with `r`) as personality-voiced lines:

```
💬 Editor: "Finally, a clean draft."
💬 Author: "Another storm, another chapter."
```

Recasting an agent (editing its entry in `[agent_personalities]` in the
active voice pack) changes both what it says in the feed and how it
approaches its next turn of work — on the next process start, per M2.2's
scope; live in-TUI recasting lands with the voice browser in M2.3.

### Character voices & the voice browser

The Character Keeper grows a **voice card** per character — dialogue patterns,
vocabulary, and verbal tics — as it reviews recent chapters, revising it
alongside `arc_status`. The Editor cites any voiced characters appearing in
the chapter it's reviewing, flagging drift the same way it flags prose-voice
drift against the active prose profile. Voice cards are visible in the Story
Browser's character detail pane (right pane, click a character):

```
Mira
Traits: stoic
Arc: cracking
Motivations: ...
Voice: Speaks in short, clipped sentences; never says "I love you" outright.

<backstory>
```

Inspect the active pack's prose profiles, agent personalities, and any
characters with a voice card from the CLI:

```bash
novelizer voices
novelizer voices --pack path/to/other_pack.toml
```

Scaffold a brand-new prose profile from a one-line description — no LLM call,
written straight to a user pack file (never the shipped default pack):

```bash
novelizer voice-scaffold brisk "Fast, punchy, present-tense action prose." --pack stories/user_pack.toml
```

Switch to it the same way any prose profile is activated (`NOVELIZER_VOICE_PACK`
and `NOVELIZER_PROSE_PROFILE` in `.env`, per the Configuration table above).
A dedicated in-TUI voice-editing/scaffolding pane, live in-run profile
switching, and LLM-expanded scaffolded profiles are deferred past M2.3.

### The thread ledger (Story Brain, Phase 1)

The Author and Editor can declare plot-thread bookkeeping alongside their
normal output: `plant` a new thread from a freeform name (the system slugs
it into a stable id — e.g. "The Locket's Secret" becomes `the-locket-s-secret`),
or `touch`/`pay_off`/`abandon` an existing thread by citing its id. Thread
events are never gated by autonomy level — they're narrative bookkeeping,
not proposals — and flow straight into a `threads` read table via the same
event-sourced Projector/ReadStore machinery as chapters and characters.

```bash
novelizer proposals   # thread.* never appears here, at any autonomy level
```

A thread's state machine is `planted → touched* → paid_off|abandoned`, with
`paid_off`/`abandoned` absorbing: once a thread is closed, further events
citing its id are recorded in the log but don't reopen it. Thread identity
follows a first-plant-wins rule: re-planting an existing thread id is a
no-op that doesn't change its state, and if an agent's plant collides with
a known-active id, the intent is downgraded to a touch. Story Brain
surfaces (staleness detection, the Story Shape/Thread Board TUI views, and
prompt injection of stale threads back to the Author) are M3.2/M3.3.

### Staleness & pacing analysis (Story Brain, Phase 1 continued)

Two deterministic functions in `novelizer/brain/` derive narrative signal from
canon with no LLM call: `staleness.is_thread_stale`/`stale_threads` (a thread
is stale once 3 chapters have passed since its last plant/touch, with no
pay-off/abandon in between) and `sag_spike.detect_sag_spike` (flags a chapter
whose tension score deviates sharply from the surrounding average). Both are
pure functions over `ReadStore` data, computed live rather than persisted, so
every consumer — agent prompts and TUI views alike, from M3.3 onward — shares
one answer.

A 7th scheduled agent, the **Structure Analyst**, produces the tension/pacing
scores those functions consume: it reads recently-drafted, not-yet-scored
chapters and asks the LLM for a `tension` (0.0–1.0) and `pacing_label` per
chapter, committing `annotation.structure_scored` events — never gated by
autonomy level, same as thread bookkeeping. It participates in the same
readiness-scored scheduler tick as the other six agents, with its own
interval (`NOVELIZER_STRUCTURE_ANALYST_INTERVAL`, default 180s).

Story Shape/Thread Board TUI views and prompt injection of stale threads and
pacing flags back to the Author/Editor are M3.3.

### Story Shape & Thread Board, and brain context in prompts

Mission Control's left column gains two more live panes: **Thread Board**
(every thread, its state, and a `STALE` marker once 3 chapters have passed
with no touch/pay-off/abandonment) and **Story Shape** (every scored
chapter's tension and pacing label, with `SAG`/`SPIKE` markers). Both read
straight from canon and call the exact same pure functions
(`novelizer.brain.staleness.is_thread_stale`, `novelizer.brain.sag_spike.detect_sag_spike`)
that build the notes injected into the Author's and Editor's prompts — so
the room's two views of "what's stale" or "what's sagging" can never
disagree.

The Author sees a **stale threads** note naming each stale thread and the
id it must cite to touch it back (per the thread identity rule — ids are
minted only at plant time, never invented); the Editor sees a **pacing
flags** note naming sagging/spiking chapters. Both notes are empty, and the
prompt is byte-identical to a story with no Story Brain signal, whenever
there's nothing to report — following the exact conditional-injection
pattern `casting_note`/`personality` (M2) and character voices (M2.3)
already established.

The **brain context note builders** live in `novelizer/brain/context.py` as
pure functions (`stale_threads_note` and `pacing_flags_note`) that take
already-fetched domain objects (threads, chapters, structure scores) and
return a formatted string. These same functions power both the notes
injected into agent prompts and the raw data fed to the TUI widgets, ensuring
the two views are always in sync. Author and Editor `poll()` methods fetch
the necessary `ReadStore` data and build the context string at work-time,
appending it conditionally to the prompt (only when non-empty), mirroring the
M2 injection pattern for casting notes and personalities.

**M3 done-when observation:** The live-LLM smoke test (`tests/agents/test_author_live_llm.py`,
marked `@pytest.mark.live_llm` and excluded from default CI runs) was executed
live with a real Author agent against a local inference endpoint. Result: **PASSED**.
The test seeded a fixture with a thread that `StalenessAnalyzer` flagged stale,
ran the real Author with the injected brain-context note, and confirmed it
declared a matching thread-touch intent unprompted — demonstrating the end-to-end
flow from staleness detection through prompt injection to live-LLM reaction.
Test timing: ~9m15s on local inference (model: as configured in `NOVELIZER_AUTHOR_MODEL`).

### Secret & causal-edge ledgers (Story Brain, Phase 2)

The Author and Editor can declare secret bookkeeping alongside their normal
output: `plant` a new secret from a freeform title (slugged into a stable
id, same rule as threads), `learn` an existing secret for a character,
`reveal` a secret publicly, or record a character `uses` an existing secret
in a chapter. CharacterKeeper may only declare `learn` — minting or
revealing a secret is a narrative-authoring act reserved for Author/Editor.
Author and Editor can also declare `causal_intents`: a claimed
`(cause_chapter_id, effect_chapter_id, note)` relationship between two
existing chapters.

```bash
novelizer proposals   # secret.created/learned/referenced and
                       # causal_edge.declared never appear here, at any
                       # autonomy level; secret.revealed can, under
                       # gated_canon or gated_all
```

The knowledge matrix (`ReadStore.knowledge_matrix()`) tracks, per secret,
which characters have `learned` it and whether it has been `revealed`
(revealed is secret-level, set-once state that applies to every character
— including ones created after the reveal — never written per character).
`secret.referenced` events are the durable record of a character using a
secret in a chapter; the causal-edge ledger (`ReadStore.list_causal_edges()`)
is a strict, never-deduped append of every declared edge. Leak/paradox
detection over these ledgers, and their TUI views, are M4.2/M4.3.

## Architecture

- **`novelizer/canon/`** — World Canon bounded context: `EventStore` (append-only log, sole
  source of truth), `Projector` (sole writer of read-side projections), `ReadStore` (query
  interface over projections).
- **`novelizer/agents/`** — Agent Roster bounded context. The Author (M0) drafts chapters via
  a deepagents `Runner` against the configured OpenAI-compatible endpoint; more agents land
  in M1.
- **`novelizer/tui/`** — Mission Control TUI (Textual): a skeletal live feed tailing the
  event log as of M0; full multi-pane layout lands in M1.
- **`novelizer/director/`** — CLI entry point (`novelizer`, `novelizer seed`, `novelizer chapters`,
  `novelizer read`).
- **`novelizer/runtime.py`** — Wires the above into a running `Runtime` (store + projector +
  read store + author), shared by the TUI and CLI.

All contexts communicate only through domain events on the log and read-side queries —
never direct calls into each other's internals.

## Development

Run the test suite:

```bash
uv run pytest
```

By default, tests marked `live_llm` (embedding-store tests and the Author live-LLM smoke test,
which require a running OpenAI-compatible LLM endpoint — see `Settings.llm_base_url`) are
deselected via `addopts = "-m 'not live_llm'"` in `pyproject.toml`, so the default run is green
without any external services. To run them explicitly (with the configured endpoint reachable
and serving the configured models):

```bash
uv run pytest -m live_llm
```
