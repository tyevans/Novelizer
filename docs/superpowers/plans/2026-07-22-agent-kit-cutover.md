# Novelizer Cutover onto agent_kit — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Delete every novelizer-side duplicate of agent_kit machinery (BaseAgent generic half, scheduler.py, agents/llm.py, run_context.py, ExcludeToolsMiddleware, five telemetry payload models) and make novelizer import the kit — behavior-preserving, import-level surgery only.

**Architecture:** Six stages, each an independently mergeable commit: (1) run_context delete-and-migrate (atomic — ContextVar identity), (2) middleware trim, (3) llm delete-and-migrate, (4) telemetry vocabulary re-export, (5) scheduler delete + override_provider wiring + parity-test retirement, (6) BaseAgent subclassing + prompt-constant debt settlement, then docs + full-suite sweep. Spec: `docs/superpowers/specs/2026-07-22-agent-kit-cutover-design.md`.

**Tech Stack:** Python 3.13, agent_kit (already on main), pytest (`asyncio_mode=auto`), import-linter.

## Global Constraints

- This campaign MODIFIES novelizer/ (that is the point). Instead, these are now frozen: `agent_kit/` **source** (tests under `tests/agent_kit/` may gain ported tests), `substrate/`, `research_domain/`, `tui_kit/`. If a kit defect blocks a stage, report BLOCKED — do not patch agent_kit.
- No behavior changes: no agent logic, no event-type strings, no settings, no prompts content. Import-level surgery + the one subclass.
- Work only in the worktree `/home/ty/workspace/novelizer/.claude/worktrees/agent-kit-cutover`. NEVER run tests in the main checkout.
- Run tests with `uv run pytest <paths> -W error -q`; docker is available (postgres tests actually run). Targeted scopes per task; full suite in the final task.
- Novelizer may import `agent_kit` top-level ONLY (the existing "agent_kit package boundary" import-linter contract forbids `agent_kit.*` submodule imports — every migrated import in this plan is `from agent_kit import ...`). Run `uv run lint-imports` in every task.
- Commit per task; messages end with: `Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>`.
- Refactor-task test discipline: run the named suites BEFORE your change (must be green — establishes base), then AFTER (must be green with the same or expected-adjusted counts). New tests specified in a task follow red/green as usual.

---

### Task 1: run_context — delete and migrate (atomic, identity-critical)

**Files:**
- Delete: `novelizer/run_context.py`
- Modify (the complete importer set): `novelizer/agents/base.py`, `novelizer/canon/committer.py`, `novelizer/telemetry/recorder.py`, `novelizer/telemetry/callbacks.py`, `tests/agents/test_base.py`, `tests/canon/test_committer.py`, `tests/tui/test_engine_room.py`, `tests/telemetry/test_recorder.py`, `tests/telemetry/test_callbacks.py`

**Interfaces:**
- Consumes: `agent_kit.current_run_id`, `agent_kit.current_agent_name` (same names/defaults as the deleted module).
- Produces: every novelizer reader/writer of run-context now holds agent_kit's ContextVar objects. Task 6's BaseAgent cutover depends on this (the kit's `run_once` sets agent_kit's vars).

WHY ATOMIC: these are ContextVar objects — correlation only works if setters (`run_once`) and readers (committer, recorder, callbacks) hold the SAME objects. All importers move in one commit; no shim, no partial state.

- [ ] **Step 1: Baseline green**

Run: `uv run pytest tests/agents/test_base.py tests/canon/test_committer.py tests/telemetry/ tests/tui/test_engine_room.py -W error -q`
Expected: all pass. Record the count.

- [ ] **Step 2: Migrate every importer**

In each of the nine files, the import is some form of
`from novelizer.run_context import current_run_id, current_agent_name` (names vary per file — some import only one). Rewrite each to import the same names from `agent_kit`:

```python
from agent_kit import current_agent_name, current_run_id
```

(Keep exactly the names each file actually imports.) Then delete `novelizer/run_context.py`.

- [ ] **Step 3: Verify no stragglers**

Run: `grep -rn "run_context" --include='*.py' novelizer/ tests/ | grep -v __pycache__ | grep -v agent_kit`
Expected: no output referencing `novelizer.run_context` (hits on `agent_kit.run_context`-provided names via top-level import are fine; the module path `novelizer.run_context` must be gone). Also: `test -f novelizer/run_context.py && echo STILL-THERE` prints nothing.

- [ ] **Step 4: Re-run the suites**

Run: `uv run pytest tests/agents/test_base.py tests/canon/test_committer.py tests/telemetry/ tests/tui/test_engine_room.py -W error -q && uv run lint-imports`
Expected: same pass count as Step 1; contracts KEPT.

- [ ] **Step 5: Commit**

```bash
git add -A novelizer/run_context.py novelizer/agents/base.py novelizer/canon/committer.py novelizer/telemetry/recorder.py novelizer/telemetry/callbacks.py tests/agents/test_base.py tests/canon/test_committer.py tests/tui/test_engine_room.py tests/telemetry/test_recorder.py tests/telemetry/test_callbacks.py
git commit -m "refactor(novelizer): adopt agent_kit run context, delete novelizer/run_context.py

Atomic across all importers: these are ContextVar objects, and run-id
correlation requires setters and readers to hold the same objects.

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
```

---

### Task 2: middleware — keep TodoContextMiddleware, adopt the kit's ExcludeToolsMiddleware

**Files:**
- Modify: `novelizer/agents/middleware.py` (delete `ExcludeToolsMiddleware` + `_tool_name`; keep `TodoContextMiddleware` + `_format_todos`)
- Modify (importers of `ExcludeToolsMiddleware`): `novelizer/agents/retconner.py`, `plotter.py`, `editor.py`, `character_keeper.py`, `continuity_checker.py`, `triage.py`, `structure_analyst.py`, `world_architect.py`, `novelizer/chat/runners.py`, `novelizer/research/runner.py`
- Modify tests: `tests/agents/test_middleware.py`, `tests/agents/test_todos_scoping.py` (only their `ExcludeToolsMiddleware` import lines, if present — read them first; `TodoContextMiddleware` imports stay on `novelizer.agents.middleware`)

**Interfaces:**
- Consumes: `agent_kit.ExcludeToolsMiddleware` (identical class, verified at extraction).
- Produces: `novelizer/agents/middleware.py` contains ONLY novelizer-specific middleware.

- [ ] **Step 1: Baseline green**

Run: `uv run pytest tests/agents/test_middleware.py tests/agents/test_todos_scoping.py tests/chat/ -W error -q`
Expected: all pass; record count.

- [ ] **Step 2: Migrate importers**

In each of the ten source files, the import is `from novelizer.agents.middleware import ExcludeToolsMiddleware` (sometimes alongside `TodoContextMiddleware` — split those: `TodoContextMiddleware` stays imported from `novelizer.agents.middleware`, `ExcludeToolsMiddleware` moves):

```python
from agent_kit import ExcludeToolsMiddleware
from novelizer.agents.middleware import TodoContextMiddleware  # only where used
```

Then in `novelizer/agents/middleware.py`, delete the `ExcludeToolsMiddleware` class and `_tool_name` (nothing else uses it), keeping the module docstring, `_format_todos`, and `TodoContextMiddleware` intact. Update the two test files' imports the same way if they import `ExcludeToolsMiddleware`.

- [ ] **Step 3: Verify + re-run**

Run: `grep -rn "class ExcludeToolsMiddleware\|def _tool_name" novelizer/ --include='*.py' | grep -v __pycache__`
Expected: empty.
Run: `uv run pytest tests/agents/test_middleware.py tests/agents/test_todos_scoping.py tests/chat/ -W error -q && uv run lint-imports`
Expected: same count as Step 1; contracts KEPT.

- [ ] **Step 4: Commit**

```bash
git add -A novelizer/agents/ novelizer/chat/runners.py novelizer/research/runner.py tests/agents/test_middleware.py tests/agents/test_todos_scoping.py
git commit -m "refactor(novelizer): adopt agent_kit ExcludeToolsMiddleware

middleware.py keeps only the novelizer-specific TodoContextMiddleware.

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
```

---

### Task 3: llm — delete novelizer/agents/llm.py, migrate all callers

**Files:**
- Delete: `novelizer/agents/llm.py`
- Modify (function-local `from novelizer.agents.llm import build_chat_model` → `from agent_kit import build_chat_model`): `novelizer/agents/author.py:349`, `character_keeper.py:281`, `continuity_checker.py:523,553`, `editor.py:300`, `kg_extraction.py:25`, `plotter.py:297`, `retconner.py:174`, `structure_analyst.py:169`, `triage.py:154`, `world_architect.py:158`, `novelizer/chat/runners.py:24`, `novelizer/research/runner.py:64`
- Modify (module-level): `novelizer/tui/tool_summarizer.py:3`
- Modify: `tests/agents/test_llm.py` (import + monkeypatch retarget; two tests move out)
- Modify: `tests/agent_kit/test_llm.py` (receives the two ported reasoning-delta tests)
- Modify (import lines only, read first to see what they reference): `tests/agents/test_bare_branch_callbacks.py`, `tests/agents/test_tool_telemetry_integration.py`, `tests/chat/test_runners.py`
- Modify: `novelizer/agents/middleware.py:50` (comment references the deleted file — repoint the comment text to `agent_kit/llm.py`)

**Interfaces:**
- Consumes: `agent_kit.build_chat_model` (call-compatible: the kit's extra `context_window_tokens` param defaults to the same 128_000; no call site passes it).
- Produces: `build_chat_model` exists only in agent_kit; monkeypatching pattern for tests is `monkeypatch.setattr(agent_kit, "build_chat_model", fake)`.

Note on why the monkeypatch retarget works: agents import `build_chat_model` lazily *inside* their builder functions, so the from-import resolves against `agent_kit`'s module attribute at call time — patching `agent_kit.build_chat_model` intercepts every builder.

- [ ] **Step 1: Baseline green**

Run: `uv run pytest tests/agents/test_llm.py tests/agents/test_bare_branch_callbacks.py tests/agents/test_tool_telemetry_integration.py tests/chat/test_runners.py tests/agent_kit/test_llm.py -W error -q`
Expected: all pass; record count.

- [ ] **Step 2: Migrate source callers and delete the module**

Each function-local import becomes `from agent_kit import build_chat_model` (same position, function-local stays function-local); `tool_summarizer.py`'s module-level import likewise. Delete `novelizer/agents/llm.py`. Fix the `middleware.py:50` comment to reference `agent_kit/llm.py`.

- [ ] **Step 3: Migrate the tests**

In `tests/agents/test_llm.py`:
- Delete these tests (already covered by `tests/agent_kit/test_llm.py`): `test_build_chat_model_targets_given_model_and_endpoint`, `test_build_chat_model_caps_max_tokens`, `test_build_chat_model_with_callbacks_enables_streaming`, `test_build_chat_model_without_callbacks_keeps_current_defaults`.
- MOVE these two into `tests/agent_kit/test_llm.py` (they test kit code and are not yet covered there); they use that file's existing `from agent_kit.llm import ... build_chat_model` import, no new import needed:

```python
def test_reasoning_content_is_recovered_from_the_raw_streamed_delta():
    """Plain ChatOpenAI silently drops non-standard streamed fields like
    reasoning_content -- _ReasoningAwareChatOpenAI must lift it back onto the
    chunk so a telemetry callback can see it via additional_kwargs."""
    from langchain_core.messages import AIMessageChunk
    m = build_chat_model("my-model", "http://localhost:1234/v1", "key")
    raw_chunk = {
        "choices": [{"index": 0, "delta": {"content": "The sea",
                                           "reasoning_content": "pondering the tide"},
                    "finish_reason": None}],
    }
    gen_chunk = m._convert_chunk_to_generation_chunk(raw_chunk, AIMessageChunk, None)
    assert gen_chunk.message.content == "The sea"
    assert gen_chunk.message.additional_kwargs["reasoning_content"] == "pondering the tide"


def test_reasoning_content_absent_leaves_chunk_unaffected():
    from langchain_core.messages import AIMessageChunk
    m = build_chat_model("my-model", "http://localhost:1234/v1", "key")
    raw_chunk = {"choices": [{"index": 0, "delta": {"content": "hi"}, "finish_reason": None}]}
    gen_chunk = m._convert_chunk_to_generation_chunk(raw_chunk, AIMessageChunk, None)
    assert "reasoning_content" not in gen_chunk.message.additional_kwargs
```

- Keep (they test novelizer's runner builders, not the kit): `test_runner_builders_pass_llm_max_tokens` and `test_every_builder_accepts_a_callbacks_kwarg`. Retarget the monkeypatch:

```python
import agent_kit

def fake_build(model, base_url, api_key, temperature=0.8, max_tokens=None,
               callbacks=None, streaming=None, context_window_tokens=128_000):
    captured["max_tokens"] = max_tokens
    return object()

monkeypatch.setattr(agent_kit, "build_chat_model", fake_build)
```

(delete the now-unused `import novelizer.agents.llm as llm_mod` line).
- In the other three test files, retarget any `novelizer.agents.llm` import/monkeypatch the same way (read each first; some may only monkeypatch — same `agent_kit` target).

- [ ] **Step 4: Verify + re-run**

Run: `grep -rn "novelizer.agents.llm\|novelizer.agents import llm" --include='*.py' novelizer/ tests/ | grep -v __pycache__`
Expected: empty.
Run: `uv run pytest tests/agents/ tests/chat/ tests/agent_kit/test_llm.py tests/tui/test_telemetry_adapter.py -W error -q && uv run lint-imports`
Expected: green (count = Step 1 minus the 4 deleted duplicates, plus 2 in agent_kit); contracts KEPT.

- [ ] **Step 5: Commit**

```bash
git add -A
git commit -m "refactor(novelizer): adopt agent_kit build_chat_model, delete agents/llm.py

Reasoning-delta recovery tests move to tests/agent_kit (they test kit
code); novelizer keeps only its runner-builder integration tests, with
monkeypatches retargeted at agent_kit.

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
```

---

### Task 4: telemetry vocabulary — re-export the five shared payloads + subclass the event-type constants

**Files:**
- Modify: `novelizer/telemetry/events.py:1-52` (the class header + five models)
- Test: `tests/telemetry/test_events.py` (add one equality test; read the file first and keep everything else)

**Interfaces:**
- Consumes: `agent_kit.TelemetryEventType`, `AgentRunStarted`, `AgentRunFinished`, `AgentRunFailed`, `SchedulerPicked`, `SchedulerEligibilityChanged`.
- Produces: `novelizer.telemetry.events` re-exports those exact classes (identity-unified); `TelemetryEventType` keeps all eleven constants with unchanged string values. All ~15 downstream importers (recorder, callbacks, tui adapter, tests) are untouched.

- [ ] **Step 1: Write the failing test** (append to `tests/telemetry/test_events.py`)

```python
def test_machinery_vocabulary_is_shared_with_agent_kit():
    """The five loop/scheduler event types and payload models must BE the
    agent_kit objects (identity, not just equal shapes) — recorders and
    tui adapters must agree with what agent_kit.BaseAgent/Scheduler emit."""
    import agent_kit
    from novelizer.telemetry import events

    assert events.AgentRunStarted is agent_kit.AgentRunStarted
    assert events.AgentRunFinished is agent_kit.AgentRunFinished
    assert events.AgentRunFailed is agent_kit.AgentRunFailed
    assert events.SchedulerPicked is agent_kit.SchedulerPicked
    assert events.SchedulerEligibilityChanged is agent_kit.SchedulerEligibilityChanged
    assert issubclass(events.TelemetryEventType, agent_kit.TelemetryEventType)
    for const in ("SCHEDULER_PICKED", "SCHEDULER_ELIGIBILITY_CHANGED",
                  "AGENT_RUN_STARTED", "AGENT_RUN_FINISHED", "AGENT_RUN_FAILED"):
        assert getattr(events.TelemetryEventType, const) == getattr(
            agent_kit.TelemetryEventType, const)
```

- [ ] **Step 2: Run to verify it fails**

Run: `uv run pytest tests/telemetry/test_events.py -W error -q`
Expected: the new test FAILS on the first `is` assertion (novelizer defines its own class today); pre-existing tests pass.

- [ ] **Step 3: Restructure `novelizer/telemetry/events.py`**

Replace lines 1 through the end of the `AgentRunFailed` model (currently ~line 52) with:

```python
from __future__ import annotations
from pydantic import BaseModel

from agent_kit import (
    AgentRunFailed,
    AgentRunFinished,
    AgentRunStarted,
    SchedulerEligibilityChanged,
    SchedulerPicked,
    TelemetryEventType as _MachineryEventType,
)

class TelemetryEventType(_MachineryEventType):
    """Machinery event vocabulary. Persisted to telemetry.db (a separate
    EventStore), never to the domain log. The five loop/scheduler constants
    come from agent_kit (same strings, shared with every kit consumer); the
    LLM/tool-call vocabulary below is recorder-side and stays here until the
    recorder extraction campaign."""

    LLM_CALL_STARTED = "llm.call_started"
    LLM_CALL_FINISHED = "llm.call_finished"
    LLM_CALL_FAILED = "llm.call_failed"
    TOOL_CALL_STARTED = "tool.call_started"
    TOOL_CALL_FINISHED = "tool.call_finished"
    TOOL_CALL_FAILED = "tool.call_failed"
```

Everything from `class LlmCallStarted` down is unchanged. Do not add an `__all__` (the file has none today; the re-import lines alone make the names importable exactly as before).

- [ ] **Step 4: Run to verify green**

Run: `uv run pytest tests/telemetry/ tests/tui/ -W error -q && uv run lint-imports`
Expected: all pass (TUI adapters consume the re-exported classes transparently); contracts KEPT. Note any TUI pilot flake and re-run that test alone before treating it as real (standing load-flakes convention).

- [ ] **Step 5: Commit**

```bash
git add novelizer/telemetry/events.py tests/telemetry/test_events.py
git commit -m "refactor(novelizer): share machinery telemetry vocabulary with agent_kit

Five payload models re-exported (identity-unified); TelemetryEventType
subclasses the kit's constants and keeps the recorder-side LLM/tool
vocabulary until the recorder extraction.

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
```

---

### Task 5: scheduler — delete, wire override_provider, retire the parity test

**Files:**
- Delete: `novelizer/scheduler.py`
- Delete: `tests/agent_kit/test_scheduler_parity.py` (same commit — with one implementation left it cannot even import)
- Modify: `novelizer/runtime.py` (~line 250-262: import + construction site + new helper)
- Modify: `tests/test_scheduler.py` (migrate to agent_kit.Scheduler)
- Check-and-migrate imports: `grep -rln "novelizer.scheduler" tests/ novelizer/` currently lists only runtime.py + the two test files above.

**Interfaces:**
- Consumes: `agent_kit.Scheduler` (identical defaults/semantics; seam = `override_provider`).
- Produces: `novelizer.runtime._make_override_provider(read_store) -> Callable[[], Awaitable[str | None]]` — module-level, used by Runtime and by the override integration test.

- [ ] **Step 1: Baseline green**

Run: `uv run pytest tests/test_scheduler.py tests/test_runtime.py -W error -q`
Expected: all pass; record count.

- [ ] **Step 2: Rewire `novelizer/runtime.py`**

Replace `from novelizer.scheduler import Scheduler` with `from agent_kit import Scheduler`. Add at module level (near the other helpers):

```python
def _make_override_provider(read_store):
    """agent_kit.Scheduler's override seam, carrying novelizer's Director
    override semantics: the first unconsumed override signal naming a target
    agent wins (exactly the branch the deleted novelizer/scheduler.py had
    inline)."""
    async def provider() -> str | None:
        signals = await read_store.list_unconsumed_signals()
        return next(
            (s.target_agent for s in signals
             if s.kind == SignalKind.override and s.target_agent),
            None,
        )
    return provider
```

(`SignalKind` is already imported in runtime.py — verify; add `from novelizer.store.models import SignalKind` if not.) Change the construction site:

```python
self.scheduler = Scheduler(
    self.agents,
    max_concurrent_agents=s.max_concurrent_agents, telemetry=self.telemetry,
    override_provider=_make_override_provider(self.read),
)
```

Then delete `novelizer/scheduler.py` and `tests/agent_kit/test_scheduler_parity.py`.

- [ ] **Step 3: Migrate `tests/test_scheduler.py`**

Keep every test — several encode live-incident regressions (author-502 hot-loop, crash-consumes-interval) worth running against the shared implementation. Mechanical changes only:
- `from novelizer.scheduler import Scheduler` → `from agent_kit import Scheduler`.
- Every `Scheduler([...], StubRead(), ...)` → `Scheduler([...], ...)` (drop the positional read_store; keep all kwargs).
- `test_override_signal_forces_agent` becomes the integration test of the new helper:

```python
async def test_override_signal_forces_agent():
    from novelizer.runtime import _make_override_provider
    from novelizer.store.models import DirectorSignal, SignalKind
    a = StubAgent("a", 0.9); b = StubAgent("b", 0.1)
    sig = DirectorSignal(kind=SignalKind.override, body="", target_agent="b")
    sched = Scheduler([a, b], clock=lambda: 1000.0, max_concurrent_agents=1,
                      override_provider=_make_override_provider(StubRead([sig])))
    assert await sched.tick() == ["b"]
    await _drain(sched)
```

- `StubRead` stays (only the override test uses it now — if nothing else references it after migration, keep it anyway for that test).

- [ ] **Step 4: Verify + re-run**

Run: `grep -rn "novelizer.scheduler" --include='*.py' novelizer/ tests/ | grep -v __pycache__` → empty; `test -f novelizer/scheduler.py && echo STILL-THERE` → nothing.
Run: `uv run pytest tests/test_scheduler.py tests/test_runtime.py tests/agent_kit/ tests/tui/test_app_layout.py -W error -q && uv run lint-imports`
Expected: green (agent_kit count drops by the deleted parity test); contracts KEPT.

- [ ] **Step 5: Commit**

```bash
git add -A
git commit -m "refactor(novelizer): run on agent_kit.Scheduler, delete novelizer/scheduler.py

Director override wired via _make_override_provider; scheduler tests
migrate to the shared implementation (keeping the live-incident
regressions); parity test retired with the duplicate it guarded.

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
```

---

### Task 6: BaseAgent — subclass the kit, settle the prompt-constant debt

**Files:**
- Modify: `novelizer/agents/base.py:1-168` (imports + generic half → subclass; fiction half from `ChapterDraft` and `_consume_signals` down unchanged)
- Modify (import lines): `novelizer/agents/character_keeper.py:3`, `continuity_checker.py:3`, `world_architect.py:2` (prompt constants → `novelizer.agents.prompts`), and every `GRAPH_RECURSION_LIMIT` importer: `author.py:3`, `retconner.py`, `editor.py`, `plotter.py`, `triage.py`, `structure_analyst.py`, `character_keeper.py`, `continuity_checker.py`, `world_architect.py`, `novelizer/chat/runners.py`, `novelizer/research/runner.py` (→ `from agent_kit import GRAPH_RECURSION_LIMIT`)
- Test (no changes expected): `tests/agents/test_base.py` — its constructions use keyword args the subclass preserves; `PASS_BACKOFF_MULTIPLIER` import keeps working via re-export.

**Interfaces:**
- Consumes: `agent_kit.BaseAgent`, `agent_kit.Runner`, `agent_kit.PASS_BACKOFF_MULTIPLIER`, `agent_kit.GRAPH_RECURSION_LIMIT`.
- Produces: `novelizer.agents.base.BaseAgent(runner, read_store, committer, interval, name=None, personality="")` — subclass with the exact historical signature; `Runner` and `PASS_BACKOFF_MULTIPLIER` still importable from `novelizer.agents.base`; `GRAPH_RECURSION_LIMIT` and the two prompt constants no longer are.

- [ ] **Step 1: Baseline green**

Run: `uv run pytest tests/agents/ -W error -q`
Expected: all pass; record count.

- [ ] **Step 2: Rewrite base.py's top half**

The file from line 1 down to (not including) the `class ChapterDraft(BaseModel):` block becomes:

```python
from __future__ import annotations

from agent_kit import BaseAgent as _KitBaseAgent, PASS_BACKOFF_MULTIPLIER, Runner  # noqa: F401 — Runner and PASS_BACKOFF_MULTIPLIER re-exported for agent imports
from pydantic import BaseModel, Field
from novelizer.canon.events import EventType, AgentRemark
from novelizer.agents.schemas import (
    ThreadIntent, KnowledgeIntent, CausalIntent, ThemeIntent, PromiseIntent,
    BlueprintPlan, BriefIntent, BeatIntent, ResolutionPlanIntent, ArcIntent, FlagDraft,
)
from novelizer.store.models import ChapterBriefRecord, Flag, FlagStatus
from novelizer.agents import intents as intent_helpers
```

(`logging`/`logger` are dropped too — the surviving fiction half never references `logger`; verify with `grep -n "logger" novelizer/agents/base.py` after the rewrite and keep it only if a surviving line uses it.)

(`ChapterDraft` and its docstring stay verbatim.) Then the class header and constructor replace the entire generic half (old lines 56–168):

```python
class BaseAgent(_KitBaseAgent):
    """Novelizer's agent chassis: agent_kit's loop (intervals, backoff,
    watermarking, run_once telemetry bracketing) plus the fiction-side
    read/commit surface every novelizer agent shares."""

    def __init__(
        self,
        runner,
        read_store,
        committer,
        interval: int,
        name: str | None = None,
        personality: str = "",
    ) -> None:
        super().__init__(runner, interval, name=name, personality=personality)
        self._read = read_store
        self._committer = committer
```

Everything from `async def _consume_signals` down stays byte-identical (all ten `_commit_*` helpers, `_remark`, `_commit_flag_drafts`). Deleted along with the generic half: the `GRAPH_RECURSION_LIMIT`/`PASS_BACKOFF_MULTIPLIER` definitions, the `DEFAULT_PASS_REMARK`/`PASS_PROMPT_INSTRUCTION` re-exports (and the `from novelizer.agents import prompts`, `time`, `uuid`, `Protocol`, telemetry, and run-context imports the generic half needed — keep only what the surviving half uses; `logging` stays for `logger`).

- [ ] **Step 3: Migrate the agent import lines**

The three prompt-constant importers change from
`from novelizer.agents.base import BaseAgent, Runner, DEFAULT_PASS_REMARK, PASS_PROMPT_INSTRUCTION, GRAPH_RECURSION_LIMIT` to:

```python
from novelizer.agents.base import BaseAgent, Runner
from novelizer.agents.prompts import DEFAULT_PASS_REMARK, PASS_PROMPT_INSTRUCTION
from agent_kit import GRAPH_RECURSION_LIMIT
```

Every other `GRAPH_RECURSION_LIMIT` importer (list above; e.g. `author.py:3` `from novelizer.agents.base import BaseAgent, ChapterDraft, Runner, GRAPH_RECURSION_LIMIT`) splits the same way, keeping its other base names:

```python
from novelizer.agents.base import BaseAgent, ChapterDraft, Runner
from agent_kit import GRAPH_RECURSION_LIMIT
```

`novelizer/research/runner.py` currently has `from novelizer.agents.base import GRAPH_RECURSION_LIMIT` → `from agent_kit import GRAPH_RECURSION_LIMIT`.

- [ ] **Step 4: Verify + re-run**

Run: `grep -rn "GRAPH_RECURSION_LIMIT\|DEFAULT_PASS_REMARK\|PASS_PROMPT_INSTRUCTION" novelizer/agents/base.py` → empty; `grep -rn "from novelizer.agents.base import.*GRAPH_RECURSION_LIMIT" novelizer/ tests/ --include='*.py' | grep -v __pycache__` → empty.
Run: `uv run pytest tests/agents/ tests/test_runtime.py tests/research/ tests/chat/ -W error -q && uv run lint-imports`
Expected: same counts as baselines; contracts KEPT.

- [ ] **Step 5: Commit**

```bash
git add -A
git commit -m "refactor(novelizer): BaseAgent subclasses agent_kit.BaseAgent

Generic loop half (interval/backoff, watermarking, run_once telemetry
bracketing) deleted — inherited from the kit. Fiction commit helpers
unchanged. Settles the flagged debt: DEFAULT_PASS_REMARK /
PASS_PROMPT_INSTRUCTION now imported from prompts, GRAPH_RECURSION_LIMIT
from agent_kit, re-exports dropped.

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
```

---

### Task 7: docs + full verification sweep

**Files:**
- Modify: `agent_kit/README.md` ("Relationship to novelizer" section)
- Test: full suite (no new tests)

- [ ] **Step 1: Rewrite the README section**

Replace the "Relationship to novelizer" section body with:

```markdown
novelizer runs on this kit: its `BaseAgent` subclasses `agent_kit.BaseAgent`
(adding the fiction-side read/commit surface), its runtime constructs
`agent_kit.Scheduler` with a Director-override `override_provider`, and its
telemetry module re-exports the kit's machinery vocabulary. The extraction-era
duplicates (and the scheduler parity test that guarded them) are gone as of
the cutover campaign — see
`docs/superpowers/specs/2026-07-22-agent-kit-cutover-design.md`. Still
novelizer-side, next in line for extraction: the telemetry recorder and the
LLM/tool-call event vocabulary it emits.
```

- [ ] **Step 2: Full verification sweep**

Read `docs/TESTING-TUI.md` first and follow its pytest wedge recipe for the TUI pilot tests. Then run (generous timeouts; docker available):

```bash
uv run pytest -W error -q          # full suite, ~127s
uv run lint-imports
```

Expected: full suite green (TUI pilot flakes: re-run the failing test in isolation and compare against base parity per docs/TESTING-TUI.md before treating as real). All contracts KEPT.

Duplication tombstones — every command must produce EMPTY output:

```bash
ls novelizer/scheduler.py novelizer/agents/llm.py novelizer/run_context.py 2>/dev/null
grep -rn "class Scheduler" --include='*.py' novelizer/
grep -rn "def build_chat_model" --include='*.py' novelizer/
grep -rn "ContextVar(" --include='*.py' novelizer/
grep -rn "class AgentRunStarted\|class SchedulerPicked\|class AgentRunFinished\|class AgentRunFailed\|class SchedulerEligibilityChanged" --include='*.py' novelizer/
grep -rn "class ExcludeToolsMiddleware" --include='*.py' novelizer/
ls tests/agent_kit/test_scheduler_parity.py 2>/dev/null
```

- [ ] **Step 3: Commit**

```bash
git add agent_kit/README.md
git commit -m "docs(agent_kit): novelizer is now a kit consumer — update README

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
```
