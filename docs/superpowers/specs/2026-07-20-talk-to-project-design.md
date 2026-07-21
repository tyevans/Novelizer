# Talk to the Project — Design

**Date:** 2026-07-20
**Status:** Approved (brainstorm complete)

## Summary

Add a "Talk to the Project" view: a REPL-style research assistant, distinct
from the existing per-agent persona chats (`@author`, `@editor`, …). Where
persona chat is an in-character consultation with one of the seven story
agents, this is a neutral analyst that answers free-form questions about the
project by researching canon directly — running consistency/health checks on
request, not just describing them. One turn at a time: ask a question, the
agent researches, the answer appears, then you ask the next one.

## Decisions (locked during brainstorm)

1. **Separate from persona chat.** Not a new addressable agent in the
   `@name` roster; its own screen, entry point, and bounded context. It isn't
   in-character and has no persona.
2. **Tool scope: story canon + brain diagnostics.** Same canon pull tools
   persona chat already uses (`search_canon`, `grep`, `read_file`), plus new
   tool wrappers around `novelizer/brain/*` (staleness, leaks, paradoxes,
   sag_spike, arc_alignment, resolution_pacing, beat_drift,
   theme_similarity, completion) so the agent can actually run those checks
   when asked, not just narrate them from static context.
3. **Session-only persistence.** No event sourcing, no projection, no
   `chat_messages`-style read model. The transcript lives in the screen's
   own state for the current run and is gone on exit. This context is
   strictly read-only — it never writes to canon, so there's nothing to
   replay.
4. **Blocking turn.** Submitting a question disables the input and runs the
   agent in an exclusive worker; no second question can be submitted until
   the first turn resolves. The UI thread itself isn't frozen (Textual
   worker), but the interaction is a strict one-turn-at-a-time REPL, not the
   async/queue-and-notify pattern persona chat uses.
5. **Entry point: command palette + keybinding.** A "Talk to the Project"
   entry in `NovelizerCommandProvider`/`APP_COMMANDS`, plus `ctrl+r` on
   `NovelizerApp` (the single-letter keys are all taken by existing
   bindings).

## Architecture

New bounded context `novelizer/research/`, sibling to `novelizer/chat/` but
read-only and unpersisted.

### `tools.py`

Wraps the pure `novelizer/brain/*` functions as deep-agent tools. Each tool
pulls what it needs from `ReadStore` itself and returns findings formatted
for the agent (the same data shape `continuity_checker` already consumes in
Python, exposed as agent-invokable tools for the first time):

- `check_stale_threads` → wraps `staleness.is_thread_stale` over
  `read.list_threads()` / `read.list_chapters()`.
- `check_leaks` → wraps `leaks.find_leaks` over secret references + the
  knowledge matrix.
- `check_paradoxes` → wraps `paradoxes.find_paradoxes` over causal edges +
  chapter order.
- `check_sag_spike`, `check_arc_alignment`, `check_resolution_pacing`,
  `check_beat_drift`, `check_theme_similarity`, `check_completion` — one
  tool per remaining brain module, same pattern: no required args, pulls its
  own inputs from `ReadStore`, returns a formatted findings list (empty list
  → "no issues found", not silence).

Each wrapper is a thin adapter — no new detection logic, no changes to the
underlying brain modules.

### `runner.py`

`build_research_runner(settings, backend=None, tools=None)`, constructed the
same way `build_chat_runner` is (`create_deep_agent`, canon pull tools when
`backend`/`tools` are provided) but with:

- A neutral system prompt: "you are a research analyst for this story's
  canon; answer precisely, cite chapter/thread/secret ids you find, use the
  diagnostic tools when a question calls for actually checking something
  rather than describing it; you never modify canon."
- Plain-text `response_format` (no `ChatReply`, no intents — this agent
  never proposes or commits anything).
- `write_todos` excluded via the existing `ExcludeToolsMiddleware`, same as
  chat runners.

### `service.py`

```python
class ResearchService:
    def __init__(self, runner_factory: Callable) -> None: ...
    async def ask(self, question: str, history: list[tuple[str, str]]) -> str: ...
```

`ask()` assembles system prompt + last ~20 turns of `history` (passed in by
the caller — the screen owns history, the service is stateless) + the new
question, invokes the runner, and returns its answer text. Runner
construction is memoized on `Runtime` the same way `_chat_runner_for` is,
reusing the same `CanonBackend`/`build_search_canon_tool` wiring already set
up for chat.

## TUI

### `ResearchScreen`

New Textual `Screen` (`novelizer/tui/research_screen.py`). Not built on
`ChatScreen` — no tab strip, single conversation.

- **Transcript**: a `RichLog` (or `Static`-per-turn list) rendering
  `You: …` / `Project: …` turns from a plain `list[tuple[str, str]]` held on
  the screen instance. Session-only: this list is not read from or written
  to any store, and is discarded when the screen is dismounted.
- **Input**: a single `Input` at the bottom of the screen.
- **Turn lifecycle**: on submit, the input is disabled, a "Researching…"
  status line appears, and the question runs via
  `self.run_worker(self._ask(question), exclusive=True)`. While that worker
  is in flight, the input stays disabled — no second question can be queued.
  On completion the answer is appended to the transcript, the status line
  clears, and the input re-enables. `Esc` pops back to mission control;
  popping while a worker is in flight cancels it (no orphaned answer lands
  on a screen the user has left).
- **Entry point**: `NovelizerCommandProvider` gains a "Talk to the Project"
  command; `NovelizerApp.BINDINGS` gains `("ctrl+r", "talk_to_project",
  "Talk to Project")`, pushing `ResearchScreen`.

## Error handling

A runner failure (timeout, empty/malformed response, tool error surfaced up
the graph) appends an ephemeral `⚠ research failed: <err>` line to the
transcript instead of an answer, clears the status line, and re-enables the
input. Nothing is persisted either way, so a failure costs nothing but a
retry — the user just asks again.

## Testing

Red/green TDD, no property-based coverage needed (no event-sourcing, no
ordering invariants — this is a plain request/response path):

- **Unit — tool wrappers**: each `check_*` tool against a fake `ReadStore`
  fixture, asserting it surfaces the same findings the underlying brain
  function would (`find_leaks`, `find_paradoxes`, `is_thread_stale`, etc.),
  formatted as agent-readable text, including the empty/no-issues case.
- **Unit — `ResearchService.ask`**: with a fake runner injected via the same
  `runners`-dict pattern chat tests use — prompt assembly includes the
  passed-in history and the new question; returns the runner's text
  verbatim; a runner exception propagates for the screen to catch (service
  itself does no error formatting).
- **TUI (Textual pilot)**: `ctrl+r` (and the palette command) pushes
  `ResearchScreen`; submitting a question disables the input and shows
  "Researching…"; a second submit attempt while pending is a no-op; on
  worker completion the answer appends and the input re-enables; `Esc` pops
  back to mission control and cancels an in-flight worker; a failed runner
  shows the `⚠` line and re-enables input without appending a false answer.

## Out of scope

- Any persistence of research conversations (event-sourced or otherwise).
- Intents, proposals, or any write path back into canon.
- Multiple concurrent research conversations / tabs.
- Streaming answers.
- Exposing research tools to the existing persona-chat agents (`@author`
  etc.) — this is a separate context with its own runner.
