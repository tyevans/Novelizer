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
- `gated_all` — all agent events, including director signals if they come from agents (rare; for testing)

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

By default, tests marked `ollama` (embedding-store tests that require a locally running
Ollama server) are deselected via `addopts = "-m 'not ollama'"` in `pyproject.toml`, so the
default run is green without any external services. To run them explicitly (with Ollama
running and the configured embedding model pulled):

```bash
uv run pytest -m ollama
```
