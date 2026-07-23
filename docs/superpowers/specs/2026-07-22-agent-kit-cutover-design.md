# Novelizer Cutover onto agent_kit — Design

Status: accepted (follow-on to 2026-07-22-agent-kit-extraction-design.md; full-autonomy execution authorized)
Date: 2026-07-22

## Problem

The agent-kit extraction campaign (merged e373fe0) deliberately left novelizer
running on its own copies of the loop machinery, with a parity test
(`tests/agent_kit/test_scheduler_parity.py`) keeping the copies honest. The
duplicates, measured in current source:

- `novelizer/agents/base.py` — the generic loop half (~lines 20–168 of 309):
  interval/backoff, watermarking, `run_once()` telemetry bracketing. Kit
  equivalent: `agent_kit.BaseAgent`.
- `novelizer/scheduler.py` — 215 lines, whole file. Kit: `agent_kit.Scheduler`
  (line-for-line parity confirmed by review + parity test; only seam is the
  Director-override lookup vs `override_provider`).
- `novelizer/agents/llm.py` — 71 lines, whole file. Kit: `agent_kit.build_chat_model`
  (identical except the kit parameterizes `context_window_tokens=128_000`).
- `novelizer/run_context.py` — 10 lines, whole file. Kit: `agent_kit.run_context`.
- `novelizer/agents/middleware.py` — `ExcludeToolsMiddleware` + `_tool_name`
  (~35 of 71 lines; `TodoContextMiddleware` is novelizer-specific and stays).
- `novelizer/telemetry/events.py` — the five machinery payload models +
  five event-type constants duplicated in `agent_kit.telemetry` (the LLM/tool
  vocabulary and bus-only models stay novelizer-side).

This campaign deletes every duplicate and makes novelizer consumer #2 of
agent_kit — full parity (one shared implementation) and the real proof of
generality (two production consumers). ~480 lines of duplication removed;
three whole files deleted.

Import legality is already in place: the existing import-linter contracts
allow `novelizer` to import `agent_kit` top-level (only `agent_kit.*`
submodules are forbidden). `novelizer/telemetry/recorder.py`'s
`TelemetryRecorder` already satisfies `agent_kit.TelemetryEmitter`
structurally (`async emit(event_type, aggregate_id, payload)` at line 33,
`in_llm_call(run_id)` at line 49).

## Design principle

Behavior-preserving, import-level surgery only. No agent's logic changes; no
event-type string changes; no settings changes. Where novelizer's module
legitimately survives (base.py, middleware.py, telemetry/events.py), it
re-exports the kit names so its many importers stay unchanged; where a module
is 100% duplicate (scheduler.py, llm.py, run_context.py), it is deleted and
its importers migrated.

## Stage 1: run_context — delete and migrate (identity-critical)

`novelizer/run_context.py` is deleted; every importer switches to
`from agent_kit import current_run_id, current_agent_name`.

**This must land as one commit covering all importers simultaneously.** These
are ContextVar objects: correlation only works if the code *setting* them
(`run_once()`) and the code *reading* them (canon committer, telemetry
recorder/callbacks) hold the same objects. A partial migration would split
readers and writers across two distinct ContextVar pairs and silently break
run-id correlation. (After Stage 5, the setter is `agent_kit.BaseAgent.run_once`,
which uses agent_kit's vars — so novelizer's readers must be on agent_kit's
vars by then; doing it first, atomically, keeps every intermediate state
correct because novelizer's own `run_once` also imports from the same place.)

Importers to migrate (grep `from novelizer.run_context` /
`from novelizer import run_context`, currently: `novelizer/agents/base.py`,
`novelizer/canon/committer.py`-side readers, `novelizer/telemetry/*` — the
implementation task greps and migrates the exact current set, plus any test
importers).

## Stage 2: middleware + llm — delete duplicates, migrate importers

- `novelizer/agents/middleware.py` keeps `TodoContextMiddleware` and
  `_format_todos`; deletes `ExcludeToolsMiddleware` and `_tool_name` (only
  the deleted class used it). Importers of `ExcludeToolsMiddleware`
  (`novelizer/research/runner.py`, `novelizer/chat/runners.py`, others per
  grep) switch to `from agent_kit import ExcludeToolsMiddleware`.
- `novelizer/agents/llm.py` is deleted. Importers of `build_chat_model` /
  `CONTEXT_WINDOW_TOKENS` switch to `from agent_kit import ...`. Call sites
  need no argument changes (the kit's extra `context_window_tokens` parameter
  defaults to the same 128_000 novelizer's constant baked in).
- `tests/agents/test_llm.py`: any assertion not already covered by
  `tests/agent_kit/test_llm.py` is ported there; the rest of the file is
  deleted with the module it tested. (Implementer compares the two files
  and reports what was ported vs. dropped.)

## Stage 3: telemetry vocabulary — re-export from the kit

`novelizer/telemetry/events.py`:

- Deletes its `SchedulerPicked`, `SchedulerEligibilityChanged`,
  `AgentRunStarted`, `AgentRunFinished`, `AgentRunFailed` model definitions
  and re-exports the agent_kit classes under the same names
  (`from agent_kit import AgentRunStarted, ...`) — so its ~15 importers
  (recorder, callbacks, tui adapter, tests) are untouched and isinstance
  identity unifies on the kit classes.
- `TelemetryEventType` becomes
  `class TelemetryEventType(agent_kit.TelemetryEventType):` keeping only the
  LLM_CALL_*/TOOL_CALL_* constants in the body. Every persisted event-type
  string is unchanged by construction (same literal values); a test asserts
  the five shared constants equal their agent_kit counterparts.
- `LlmCall*`, `ToolCall*`, `TokenDelta`, `ToolSummaryReady` stay exactly
  where they are — they belong to the recorder-side vocabulary, whose
  extraction is a future campaign (Non-goals).

## Stage 4: scheduler — delete, wire override_provider, retire parity test

- `novelizer/runtime.py` (the single construction site, line ~257) switches
  to `agent_kit.Scheduler`:

  ```python
  def _make_override_provider(read_store):
      async def provider() -> str | None:
          signals = await read_store.list_unconsumed_signals()
          return next(
              (s.target_agent for s in signals
               if s.kind == SignalKind.override and s.target_agent),
              None,
          )
      return provider

  self.scheduler = Scheduler(
      self.agents,
      max_concurrent_agents=s.max_concurrent_agents,
      telemetry=self.telemetry,
      override_provider=_make_override_provider(self.read),
  )
  ```

  `_make_override_provider` lives in `novelizer/runtime.py` (module level) —
  it is the one piece of genuinely novelizer-specific scheduler logic and
  reproduces the deleted override branch's exact semantics (first unconsumed
  override signal with a target wins).
- `novelizer/scheduler.py` is deleted.
- `tests/test_scheduler.py`: dispatch-mechanics tests that duplicate
  `tests/agent_kit/test_scheduler.py` are deleted; the override-signal test
  is rewritten as an integration test of `_make_override_provider` +
  `agent_kit.Scheduler` (StubRead + DirectorSignal → the named agent is
  dispatched first). Any other novelizer-specific assertions (status wiring
  used by the TUI, drain semantics a TUI test depends on) are preserved
  against the kit scheduler. Other test files importing
  `novelizer.scheduler` migrate their import.
- `tests/agent_kit/test_scheduler_parity.py` is deleted **in the same
  commit** as `novelizer/scheduler.py` — with one implementation left, it
  has nothing to compare and would not even import.

## Stage 5: BaseAgent — subclass, and settle the prompt-constant debt

`novelizer/agents/base.py` becomes:

```python
from agent_kit import BaseAgent as KitBaseAgent, Runner  # Runner re-exported

class BaseAgent(KitBaseAgent):
    def __init__(self, runner, read_store, committer, interval,
                 name=None, personality=""):
        super().__init__(runner, interval, name=name, personality=personality)
        self._read = read_store
        self._committer = committer
```

- The constructor keyword signature is preserved exactly (tests construct
  with `runner=None, read_store=None, committer=None, interval=10, name="x"`).
- The generic half (interval/backoff, watermark, `note_pass`, `run_once`,
  `_emit_telemetry`, `_guarded_line`, `readiness`, `_run`) is deleted —
  inherited from the kit. `ChapterDraft` and all ten fiction commit helpers
  plus `_consume_signals`/`_remark` stay verbatim.
- `PASS_BACKOFF_MULTIPLIER` re-export dropped if nothing imports it via base
  (grep decides; if imported, re-export from agent_kit).
- **Prompt-constant migration** (base.py's own comment mandates this):
  `character_keeper.py`, `continuity_checker.py`, `world_architect.py` switch
  `DEFAULT_PASS_REMARK`/`PASS_PROMPT_INSTRUCTION` imports to
  `novelizer.agents.prompts`, and `GRAPH_RECURSION_LIMIT` to
  `from agent_kit import GRAPH_RECURSION_LIMIT` (they're touching those
  import lines anyway). base.py drops all three re-exports (lines 24–32).
  Every other `GRAPH_RECURSION_LIMIT` importer (`novelizer/research/runner.py`,
  others per grep) migrates the same way.
- The kit's `clock` seam is not exposed by the subclass (production uses the
  default `time.monotonic`; nothing in novelizer injects clocks into agents).
- Behavioral invariants inherited unchanged: novelizer agents keep watermark
  gating and `note_pass` — the kit ships both; the research domain's
  fruitless-set choice is not imposed on novelizer (Non-goals).

## Stage 6: docs + verification

- `agent_kit/README.md`'s "Relationship to novelizer" section is rewritten:
  novelizer now consumes the kit; the parity test is gone; the remaining
  novelizer-side telemetry (LLM/tool vocabulary + recorder) is named as the
  next extraction candidate.
- Verification sweep (all in the worktree, never the main checkout):
  - `uv run pytest -W error` — the full suite (post speed-fix ~127s), TUI
    pilot tests via the wedge recipe in `docs/TESTING-TUI.md`; compare any
    TUI flakes against base parity per the standing load-flakes convention.
  - `uv run lint-imports` — all contracts KEPT.
  - Duplication tombstones: `novelizer/scheduler.py`, `novelizer/agents/llm.py`,
    `novelizer/run_context.py` do not exist; `grep -rn "class Scheduler" --include='*.py' novelizer/`
    empty; `grep -rn "def build_chat_model" novelizer/` empty;
    `grep -rn "ContextVar(" novelizer/` empty;
    `grep -rn "class AgentRunStarted\|class SchedulerPicked" novelizer/` empty;
    `grep -rn "DEFAULT_PASS_REMARK" novelizer/agents/base.py` empty.
  - `tests/agent_kit/` and `tests/research_domain/` still green (kit consumers
    unaffected).

## Compatibility analysis (why this is behavior-preserving)

- **Telemetry**: recorder persists event-type *strings*; every shared
  constant has an identical literal in both classes, asserted by test.
  Payload re-export makes novelizer's names the kit classes — same fields,
  same pydantic behavior, isinstance unified.
- **Scheduler**: parity test held dispatch traces identical through the
  extraction campaign; the override_provider closure reproduces the deleted
  read_store branch exactly; `status()` dict shape is identical (TUI safe).
- **BaseAgent**: kit copy was reviewed line-for-line against novelizer's at
  extraction; the subclass restores the exact constructor surface. The one
  deliberate difference (kit `note_pass` reads `self._clock`, default
  `time.monotonic`) is invisible in production.
- **run_context**: single-commit migration preserves ContextVar identity at
  every merge point.

## Non-goals

- No extraction of the telemetry recorder, callbacks, LLM/tool-call or
  bus-only event vocabulary (next campaign).
- No behavior/pattern changes to any novelizer agent (watermark gating and
  `note_pass` stay; no fruitless-set migration).
- No Engine Room / tui_kit wiring for research_domain (separate follow-on).
- No changes to `agent_kit/` source (consumer-side campaign only; if a kit
  defect blocks cutover, that's a finding to surface, not silently patch).
- No canon-store/RuntimeBase unification; no settings or CLI changes.
- Live acceptance (a real story run in the TUI post-cutover) remains with
  the user, per the standing convention — the suite plus TUI pilot wedge is
  this campaign's merge bar.

## Sequencing (mergeable, ordered)

1. Stage 1 (run_context, atomic).
2. Stage 2 (middleware + llm).
3. Stage 3 (telemetry vocabulary).
4. Stage 4 (scheduler + override provider + parity-test retirement).
5. Stage 5 (BaseAgent subclass + prompt-constant/GRAPH_RECURSION_LIMIT migration).
6. Stage 6 (docs + full verification sweep).

Each stage runs the targeted suites it touches plus `lint-imports`; the full
suite runs at Stage 6 (and any earlier stage the implementer judges risky).
