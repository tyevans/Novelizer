<!--
verified against (2026-07-19): novelizer/director/cli.py (full read), novelizer/tui/app.py
(BINDINGS + command dispatch), novelizer/director/commands.py (dispatch()), 
novelizer/tui/settings_screen.py, novelizer/tui/setup_wizard.py, novelizer/tui/widgets/
(browser_model.py, roster.py, thread_board.py, story_shape.py, who_knows_what.py,
causeway.py, engine_room.py), docs/superpowers/specs/2026-07-17-novelizer-vision-design.md,
docs/submilestones/M5-finish.md, docs/MILESTONES.md
-->

# Novelizer

Novelizer is a **director's control room for an autonomous writers' room**: seven AI
agents — Author, Editor, World Architect, Character Keeper, Continuity Checker, Retconner,
and Structure Analyst — collaboratively build a living world and write a novel inside it,
coordinating exclusively through an append-only event log that is the world's sole source
of truth (its canon). A terminal Mission Control UI shows the room at work live: chapters
drafting, threads and secrets bookkeeping themselves, retcons getting filed and resolved,
and a **Story Brain** deriving narrative shape — tension and pacing, thread staleness,
who-knows-what, cause and effect — from that same canon. The human director steers with
seeds and focus signals, adjudicates retcons, dials how much autonomy the room gets, and
reads the book as it emerges.

The project is feature-complete through milestone M5 (see
[`docs/MILESTONES.md`](docs/MILESTONES.md) for the full roadmap and closeout notes) — this
README describes the shipped product. New here? Start with
[`docs/QUICKSTART.md`](docs/QUICKSTART.md) for the exact install-to-first-run path.

## Installation

For contributors working in this checkout:

```bash
uv sync
```

To install the `novelizer` command itself (end users, or anyone who just wants the binary
on `PATH` without a dev environment), install this checkout as a uv tool:

```bash
uv tool install .
```

There is no published PyPI package yet, so `uv tool install novelizer` (without a path)
does not currently work — install from a local checkout as above until publication lands.

## Requirements

- Python 3.13+
- A running **OpenAI-compatible** LLM server (e.g. [llama.cpp](https://github.com/ggml-org/llama.cpp)'s
  `llama-server`, [vLLM](https://github.com/vllm-project/vllm), LM Studio, etc.) reachable
  at the configured base URL. No cloud API key is required for local servers.

## Configuration

Settings layer in this order (later wins): built-in defaults ← global config ←
story config ← `NOVELIZER_*` environment variables.

- **Global:** `~/.config/novelizer/config.toml` — see
  `docs/examples/config.example.toml` for a documented example.
- **Per story:** each story is a self-contained directory
  (`world.db`, `chroma/`, `story.toml`). `story.toml` can override voice,
  models, temperatures, and cadence for that story. Secrets are never valid
  in `story.toml`.
- **Env:** any setting, e.g. `NOVELIZER_AUTHOR_MODEL=qwen3`.

On first launch (no global config yet), novelizer opens a setup wizard: point
it at your OpenAI-compatible endpoint, test the connection, and pick models
from the endpoint's live model list. After setup — and on every later
launch — a story picker lists the stories in `default_stories_dir`
(most recent first, last-opened preselected) and can create new ones.
`novelizer --story path/to/story/` skips the picker. A legacy flat
`stories/world.db` triggers a one-time migration offer; declining is
remembered.

Inside the TUI, `:settings` opens a settings screen showing every setting
with its effective value and source layer (default / global / story / env).
Edits write straight to `config.toml` / `story.toml` — hand-edits to those
files while novelizer runs are picked up the same way. Cadence, voice, and
temperature changes apply live (voice and temperature affect the next
draft); endpoint and model changes are marked "restart required".
Generated chapters record the model, temperature, voice pack, and prose
profile they were written under.

## Usage

Launch the Mission Control TUI (live event feed of the world as it's authored):

```bash
novelizer
```

### Mission Control

The left column stacks the live feed and the Story Brain panes; the right column is the
Story Browser and its detail pane:
- **Activity Feed** (`#feed`) — real-time event log of agent actions (chapters drafted, retcons filed, etc.), plus in-personality remarks from the room
- **Story Browser** (`#browser`) — chapters, characters, world entries, retcons, and themes, each a section in the tree; click to inspect
- **Detail Pane** (`#detail`) — full text of the selected item from the browser
- **Story Brain views** (below the feed) — see the dedicated section below
- **Status bar** — current autonomy level and a one-line command reference
- **Activity strip** — a one-line "what's happening right now" summary (see Engine Room, below)

Command palette (focus with `Ctrl+K`, then type — the `:` prefix shown in the status bar is
cosmetic, `Ctrl+K` alone focuses the input):
- `seed <text>` — inject a narrative seed
- `focus <agent>` — inject a focus/steer signal that agents pick up as context on their next run
- `pause <agent>` — pause an agent
- `resume <agent>` — resume a paused agent
- `autonomy <level> [agent]` — set the global autonomy level, or a per-agent override
- `approve <id>` / `reject <id>` — resolve a pending proposal
- `settings` — open the settings screen (see Configuration, above)

Toggle Room drill-in view (full-screen feed, agents speaking in their cast personalities)
with `r`; toggle Reading mode (clean chapter-prose view) with `v`. Room and Reading are
mutually exclusive with each other and with the normal two-column layout.

Toggle the Engine Room view with `e` — a live machinery pane (which agent is running,
the model's tokens streaming in, call vitals) over a durable trace of every run, LLM
call, and scheduler decision (stored in `telemetry.db` beside the story; deleting it
loses machinery history and nothing else). Inside the Engine Room, `p` toggles
inspection of the exact prompt for the call in flight (off by default). The one-line
activity strip above the command bar shows the same signal at a glance: `▶ author ·
drafting · 3.4k tok · 52s` while a run is live, `idle · next: editor in 12s` between
runs, and a red crash notice if an agent fails.

### Story Brain views

The Story Brain derives narrative shape from canon with no separate source of truth —
every view below reads straight from the event log's projections, the same data the agent
prompts see. All four sit stacked in the left column of Mission Control, below the feed:

- **Thread Board** — every plot thread, its state (`planted → touched* →
  paid_off|abandoned`), and a `STALE` marker once three chapters have passed with no
  touch/pay-off/abandonment.
- **Story Shape** — every scored chapter's tension and pacing label (from the Structure
  Analyst agent), with `SAG`/`SPIKE` markers.
- **Who-Knows-What** — the secret × character knowledge matrix: which characters have
  learned which secrets, and whether each has been revealed.
- **Causeway** — the causal-edge ledger: declared `(cause chapter, effect chapter, note)`
  relationships between chapters.

Theme and motif tracking is not a fifth Brain view — themes live as a section in the Story
Browser (chapters / characters / world / retcons / **themes**) alongside the rest of the
canon, per the vision doc's design.

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
# TUI command input (focus with `Ctrl+K`):
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

The active pack and profile can also be switched without restarting: open `:settings` in
the TUI, select the `voice_pack`/`prose_profile` row, and enter a new path/name — both
apply live, affecting the next draft (they are not in the settings screen's
restart-required set).

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

Recasting an agent (editing its entry in `[agent_personalities]` in the active voice pack
file directly — there is no in-TUI personality editor) changes both what it says in the
feed and how it approaches its next turn of work. The pack is only re-read when the
`voice_pack`/`prose_profile` *setting* changes (via `:settings` or config edit), not on a
timer, so editing personality text in place inside the same pack file takes effect the
next time the app restarts or the settings screen re-points at that path.

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

Activate it via `:settings` (set `voice_pack` to the file you scaffolded into and
`prose_profile` to the new profile's name — see above) or via `NOVELIZER_VOICE_PACK`/
`NOVELIZER_PROSE_PROFILE`. There is deliberately no in-TUI voice-editing/scaffolding pane
— `voice-scaffold` plus the settings screen is the whole casting flow; LLM-expanded
scaffolded profiles remain out of scope.

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
a known-active id, the intent is downgraded to a touch. Story Brain surfaces built on this
bookkeeping — staleness detection, the Story Shape/Thread Board TUI views, and prompt
injection of stale threads back to the Author — are described in the next two sections.

### Staleness & pacing analysis (Story Brain, Phase 1 continued)

Two deterministic functions in `novelizer/brain/` derive narrative signal from
canon with no LLM call: `staleness.is_thread_stale`/`stale_threads` (a thread
is stale once 3 chapters have passed since its last plant/touch, with no
pay-off/abandon in between) and `sag_spike.detect_sag_spike` (flags a chapter
whose tension score deviates sharply from the surrounding average). Both are
pure functions over `ReadStore` data, computed live rather than persisted, so
every consumer — agent prompts and TUI views alike — shares one answer.

A 7th scheduled agent, the **Structure Analyst**, produces the tension/pacing
scores those functions consume: it reads recently-drafted, not-yet-scored
chapters and asks the LLM for a `tension` (0.0–1.0) and `pacing_label` per
chapter, committing `annotation.structure_scored` events — never gated by
autonomy level, same as thread bookkeeping. It participates in the same
readiness-scored scheduler tick as the other six agents, with its own
interval (`NOVELIZER_STRUCTURE_ANALYST_INTERVAL`, default 180s).

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
is a strict, never-deduped append of every declared edge. Leak (a secret referenced by a
character who never learned it) and paradox detection run over these ledgers; findings
surface as retcon requests in the approval queue and are visible live in the Who-Knows-What
and Causeway Story Brain views.

The Continuity Checker also runs a **prose-mining pass**: alongside its free-text review,
it asks the LLM which secret uses/learns/reveals, thread touches, and causal links the
prose *shows* but the log has no covering event for yet, and commits those as ordinary
events tagged `source="mined"` — the same deterministic leak/paradox detectors then see
mined facts exactly as they'd see agent-declared ones. A mined `secret.revealed` never
auto-commits at any autonomy level (a prose-inferred reveal is less trustworthy than a
declared one); it always escalates to a retcon request instead.

## Architecture

- **`novelizer/canon/`** — World Canon bounded context: `EventStore` (append-only log, sole
  source of truth), `Projector` (sole writer of read-side projections), `ReadStore` (query
  interface over projections).
- **`novelizer/agents/`** — Agent Roster bounded context: the seven scheduled agents (Author,
  Editor, World Architect, Character Keeper, Continuity Checker, Retconner, Structure
  Analyst), each a deepagents `Runner` against the configured OpenAI-compatible endpoint,
  coordinated only through the event log.
- **`novelizer/brain/`** — Story Brain: deterministic analyzers (staleness, sag/spike,
  leak/paradox detection) and prompt-context builders over `ReadStore` projections.
- **`novelizer/tui/`** — Mission Control TUI (Textual): the full multi-pane dashboard,
  Story Brain views, Engine Room, settings screen, setup wizard, and story picker.
- **`novelizer/director/`** — CLI entry point (`novelizer` plus its subcommands — see
  Usage, above).
- **`novelizer/runtime.py`** — Wires the above into a running `Runtime` (store + projector +
  read store + scheduler + all seven agents), shared by the TUI and CLI.

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
