# Novelizer documentation

Welcome. This folder is the documentation for **Novelizer** — a director's control room
for an autonomous writers' room, where a cast of AI agents builds a living world and writes
a novel inside it while you watch and steer from a terminal UI. If you want the product
overview first, read the [root README](../README.md); this page is the map of the docs
themselves.

The docs follow the [Diátaxis](https://diataxis.fr/) framework, which sorts documentation by
what you actually need in the moment:

- **Tutorials** are learning-oriented — a guided first run, start to finish.
- **How-to guides** are task-oriented — the steps to accomplish one specific thing.
- **Reference** is information-oriented — every command, setting, and default, looked up as
  needed.
- **Explanation** is understanding-oriented — the *why* behind the architecture.

Below the four quadrants you'll also find getting-started entry points, the project's
milestone history, and the internal/contributor material (design specs, implementation
plans, and the docs backlog).

## New here? Start here

1. [Quickstart](QUICKSTART.md) — get installed and running in the shortest path.
2. [Tutorial: your first story](tutorial/first-story.md) — go from nothing to a finished
   first chapter, learning the room as you go.
3. [How the room works](explanation/how-the-room-works.md) — once it's running, understand
   what you're watching and why it's built this way.

## Getting started

- [Quickstart](QUICKSTART.md) — install-to-first-run: set up an LLM endpoint, create a
  story, and boot Mission Control. For anyone who just wants it working.
- [Example configuration](examples/config.example.toml) — a fully annotated
  `config.toml` you can copy to `~/.config/novelizer/` and edit.

## Tutorials (learning-oriented)

- [Your first story](tutorial/first-story.md) — a start-to-finish walkthrough: install,
  point at an LLM, create a story, plant a seed, and watch the room draft your opening
  chapter. Read this first if you're new.

## How-to guides (task-oriented)

- [Connect a local LLM](how-to/connect-a-local-llm.md) — point Novelizer at a local,
  OpenAI-compatible model server (llama.cpp, Ollama, vLLM, LM Studio), verify the
  connection, and tune the settings that matter most for local models.
- [Wire a new domain onto tui_kit](how-to/wire-a-new-domain-onto-tui_kit.md) — for
  developers: reuse the domain-agnostic `tui_kit` "watch N agents run" console to build a
  live UI for a domain of your own.

## Reference (information-oriented)

- [Configuration](reference/configuration.md) — every setting, where it may be set, its
  default, and how changes take effect. The layering rules and the full settings table.
- [novelizer CLI](reference/novelizer-cli.md) — the `novelizer` console command: booting
  the TUI and every headless subcommand that operates against a story's event store.
- [research-domain CLI](reference/research-domain-cli.md) — the `research-domain` command
  for the synthetic proof-domain built on `substrate` (`append` and `show`).
- [Speech attribution: `chapter.attributed` and `speech_segments`](reference/speech-attribution.md)
  — the event payload and projection table that record who is speaking in a chapter.
- [The Attributor](reference/attributor-agent.md) — the agent that formalizes the Author's
  inline speaker markup into clean prose plus a segment list.

## Explanation (understanding-oriented)

- [How the room works](explanation/how-the-room-works.md) — the architecture of the
  autonomous writers' room: why the agents share one append-only event log instead of
  calling each other, why nothing becomes canon except through a single gated commit seam,
  and how the scheduler stays a plain cadence-and-readiness loop.
- [Architecture boundaries](explanation/architecture-boundaries.md) — the package-level
  import boundaries across the four root packages (`substrate`, `novelizer`,
  `research_domain`, `tui_kit`) and why they're drawn where they are.
- [Why attribution is authored, not inferred](explanation/speech-attribution-inline.md) —
  why the Author marks speakers inline instead of a later pass inferring them, and why
  clean prose is canon with the annotation derived from it.

## Project history and roadmap

The milestone breakdowns record how the room was built, milestone by milestone — useful
context for understanding why subsystems exist and what each one delivered.

- [M1 · The Room Assembles](submilestones/M1-the-room-assembles.md) — the agents, the
  scheduler, the autonomy dial + approval queue, and the Mission Control TUI.
- [M2 · Voices](submilestones/M2-voices.md) — voice as a first-class, configurable
  material: prose voice, agent personalities, and character voices.
- [M3 · Shape & Threads](submilestones/M3-shape-and-threads.md) — the Story Brain's first
  faculty: narrative shape, tension/pacing, and the thread ledger.
- [M4 · Knowledge & Cause](submilestones/M4-knowledge-and-cause.md) — the Story Brain's
  knowledge (who-knows-what) and cause-and-effect faculties.
- [M5 · Finish](submilestones/M5-finish.md) — the closeout milestone: reliability, prose
  mining, themes, packaging, and docs.

## Internal and contributor docs

These are written for people working *on* Novelizer rather than *with* it.

- [Documentation backlog](documentation-backlog.md) — the ranked, per-package Diátaxis
  backlog of docs that don't exist yet, with a note on how to pick up an item.
- [Testing the TUI](TESTING-TUI.md) — practical notes on the test layers and how to run the
  full suite without a full-suite run wedging your night.
- [Agent prompting redesign](agent-prompting/README.md) — the research digests, per-agent
  redesign proposals, and shared architecture brief behind how every agent is prompted.
- [Design specs](superpowers/specs/) — the spec/design record for each feature: what was
  designed and why, before it was built.
- [Implementation plans](superpowers/plans/) — the step-by-step plans each feature was
  executed from, paired with the specs above.
