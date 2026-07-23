# agent_kit

Domain-neutral agent execution machinery — the third extraction from
novelizer, after `substrate/` (event sourcing) and `tui_kit/` (TUI). See
`docs/superpowers/specs/2026-07-22-agent-kit-extraction-design.md` for the
extraction history and the three corrected seams.

## Primitives

- **BaseAgent** (`agent_kit.BaseAgent`) — the poll/work/commit loop
  chassis: interval/backoff scheduling, `note_pass()` triple-backoff,
  fingerprint watermarking, an injectable clock (keeps backoff and a clock-injected Scheduler on one timeline), and `run_once()` which brackets your `_run()`
  with machinery telemetry and ambient run context.
- **Scheduler** (`agent_kit.Scheduler`) — readiness-sorted dispatch pool
  with a concurrency cap, pause/resume, eligibility tracing, and an
  injectable `override_provider` for domains with a priority channel.
- **Telemetry vocabulary** (`agent_kit.TelemetryEventType` + payload
  models) — the five machinery events the loop and scheduler emit;
  recorders implement the `TelemetryEmitter` protocol and are injected
  post-construction (`agent.telemetry = recorder`; None = silent).
- **Runner construction** (`agent_kit.build_chat_model`,
  `agent_kit.build_agent_runner`) — an OpenAI-compatible chat model
  (reasoning-delta aware, context-window profiled) wrapped in a deepagents
  graph with your system prompt, pydantic response format, and tools. The
  langchain/deepagents dependency lives here and nowhere else.

## Building an agent

The pattern `research_domain/agents.py` follows:

1. Subclass `BaseAgent`; store your domain deps yourself (the base takes
   only `runner`, `interval`, `name`, `personality`).
2. Implement `readiness()`: return your score when there is workable
   backlog, else 0.0. Two idling patterns are available: fingerprint
   watermarking (`_fingerprint()` + `_gate_on_watermark(score)`) for
   agents whose whole backlog is one unit of work, or a fruitless set
   (examined items that yielded nothing, subtracted from the queue) for
   agents that work a queue item-by-item — the research agents use the
   latter to avoid head-of-line blocking.
3. Put poll/work/commit in `_run()`: read state, one `ainvoke` on the
   runner, validate the structured response, commit events. Call
   `note_pass()` when you examined fresh state and chose not to act.
4. Drive any number of agents with `Scheduler([agents...])`.

## Import rule

Import from `agent_kit` directly (`from agent_kit import BaseAgent`),
never from a submodule. agent_kit itself imports nothing from `novelizer`,
`substrate`, `research_domain`, or `tui_kit`. Both rules are enforced by
import-linter contracts — see `[tool.importlinter]` in `pyproject.toml`.

## Relationship to novelizer

novelizer runs on this kit: its `BaseAgent` subclasses `agent_kit.BaseAgent`
(adding the fiction-side read/commit surface), its runtime constructs
`agent_kit.Scheduler` with a Director-override `override_provider`, and its
telemetry module re-exports the kit's machinery vocabulary. The extraction-era
duplicates (and the scheduler parity test that guarded them) are gone as of
the cutover campaign — see
`docs/superpowers/specs/2026-07-22-agent-kit-cutover-design.md`. Still
novelizer-side, next in line for extraction: the telemetry recorder and the
LLM/tool-call event vocabulary it emits.
