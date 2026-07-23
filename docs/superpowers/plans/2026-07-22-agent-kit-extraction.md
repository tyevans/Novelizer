# Agent Kit Extraction + Research Domain Live Agents — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Extract novelizer's agent-loop machinery (BaseAgent, Scheduler, LLM runner construction, machinery-telemetry vocabulary) into a new standalone `agent_kit/` package, then make `research_domain`'s extractor/verifier/retractor roles real LLM agents running on it against a local document corpus.

**Architecture:** `agent_kit/` is the third extraction (after `substrate/` and `tui_kit/`): verbatim behavioral copies of novelizer's generic loop/scheduler halves with exactly three corrected seams (constructor drops unused stores, telemetry becomes an injected protocol, scheduler override becomes an injectable callable). Novelizer is NOT modified — its own copies keep running; a parity test keeps the copies honest. `research_domain` composes `agent_kit` + `substrate`: three BaseAgent subclasses (extractor/verifier/retractor) poll `ResearchRuntime` state + a filesystem corpus, do LLM work via deepagents runners with read-only tools, and commit by appending the four existing research event types. Spec: `docs/superpowers/specs/2026-07-22-agent-kit-extraction-design.md`.

**Tech Stack:** Python 3.13, pydantic v2, langchain/langchain-openai/deepagents (confined to `agent_kit/llm.py` + `agent_kit/middleware.py`), click + rich (CLI), asyncpg/Postgres via substrate, pytest (`asyncio_mode=auto`) + hypothesis, import-linter.

## Global Constraints

- **Never modify anything under `novelizer/`.** Zero import changes, zero behavior changes, zero test changes there. (The one designed exception: `tests/agent_kit/test_scheduler_parity.py` *imports* from novelizer to compare traces — tests are exempt from import contracts.)
- `agent_kit` must import nothing from `novelizer`, `substrate`, `research_domain`, or `tui_kit` (enforced via import-linter, Task 2).
- All commands run from the worktree root (`/home/ty/workspace/novelizer/.claude/worktrees/agent-kit-extraction`). NEVER run tests in the main checkout (`/home/ty/workspace/novelizer`) — standing DB-lock rule.
- Run tests with `uv run pytest <path> -W error -q`. Targeted scopes, not the full suite, until the final task.
- TDD: red before green for every behavior. Property-based tests (hypothesis) where the plan shows them.
- Commit after every green cycle; conventional-commit style (`feat(agent_kit): ...`, `test(research-domain): ...`). Every commit message ends with `Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>`.
- No new dependencies — everything needed (hypothesis, import-linter, langchain, deepagents, click, rich) is already in `pyproject.toml`.
- Numbers that must match novelizer exactly: `PASS_BACKOFF_MULTIPLIER = 3`, `GRAPH_RECURSION_LIMIT = 100`, `CONTEXT_WINDOW_TOKENS = 128_000`, scheduler defaults `tick_sleep=1.0`, `max_concurrent_agents=2`.

---

### Task 1: agent_kit core — run_context, telemetry, BaseAgent

**Files:**
- Create: `agent_kit/__init__.py` (empty for now; public API lands in Task 4)
- Create: `agent_kit/run_context.py`
- Create: `agent_kit/telemetry.py`
- Create: `agent_kit/base.py`
- Create: `tests/agent_kit/__init__.py` (empty)
- Test: `tests/agent_kit/test_base.py`

**Interfaces:**
- Consumes: nothing (standalone package).
- Produces (used by Tasks 2, 5–11):
  - `agent_kit.base.BaseAgent(runner, interval: int, name: str | None = None, personality: str = "")` with attributes `name`, `interval`, `personality`, `paused`, `telemetry` (None default), `_runner`; methods `pause()`, `resume()`, `ready_for_interval(now: float) -> bool`, `mark_ran(now: float)`, `seconds_until_ready(now: float) -> float`, `note_pass(now: float | None = None)`, `async readiness() -> float`, `async run_once()`, `async _run()`, `async _fingerprint() -> tuple | None`, `async _gate_on_watermark(score: float) -> float`, `async _record_watermark()`, `_clear_watermark()`, `_guarded_line(label, value) -> str` (static).
  - `agent_kit.base.Runner` protocol: `async ainvoke(self, inputs: dict) -> dict`.
  - `agent_kit.base.PASS_BACKOFF_MULTIPLIER = 3`.
  - `agent_kit.telemetry.TelemetryEventType` constants: `AGENT_RUN_STARTED = "agent.run_started"`, `AGENT_RUN_FINISHED = "agent.run_finished"`, `AGENT_RUN_FAILED = "agent.run_failed"`, `SCHEDULER_PICKED = "scheduler.picked"`, `SCHEDULER_ELIGIBILITY_CHANGED = "scheduler.eligibility_changed"`.
  - `agent_kit.telemetry` pydantic models: `AgentRunStarted(run_id, agent_name)`, `AgentRunFinished(run_id, agent_name, duration_s)`, `AgentRunFailed(run_id, agent_name, error_type, error_message, phase, duration_s)`, `SchedulerPicked(agent_name)`, `SchedulerEligibilityChanged(agent_name, eligible, reason)`.
  - `agent_kit.telemetry.TelemetryEmitter` protocol: `async emit(event_type: str, aggregate_id: str, payload) -> None`; `in_llm_call(run_id: str) -> bool`.
  - `agent_kit.run_context.current_run_id: ContextVar[str | None]`, `agent_kit.run_context.current_agent_name: ContextVar[str]`.

- [ ] **Step 1: Write the failing tests**

Create `tests/agent_kit/__init__.py` (empty file) and `tests/agent_kit/test_base.py`:

```python
from __future__ import annotations
import time

import pytest
from hypothesis import given, strategies as st

from agent_kit.base import BaseAgent, PASS_BACKOFF_MULTIPLIER
from agent_kit.run_context import current_agent_name, current_run_id
from agent_kit.telemetry import TelemetryEventType


class NullRunner:
    async def ainvoke(self, inputs: dict) -> dict:
        return {}


class FakeEmitter:
    """Records every emit; in_llm_call is scriptable for phase tests."""

    def __init__(self, llm_call_run_ids: set[str] | None = None) -> None:
        self.events: list[tuple[str, str, object]] = []
        self._llm = llm_call_run_ids or set()

    async def emit(self, event_type: str, aggregate_id: str, payload) -> None:
        self.events.append((event_type, aggregate_id, payload))

    def in_llm_call(self, run_id: str) -> bool:
        return run_id in self._llm or "*" in self._llm


# --- interval / backoff machinery ---------------------------------------

@given(interval=st.integers(min_value=1, max_value=10_000),
       elapsed=st.floats(min_value=0, max_value=100_000, allow_nan=False))
def test_ready_for_interval_iff_interval_elapsed(interval, elapsed):
    agent = BaseAgent(NullRunner(), interval=interval)
    agent.mark_ran(1000.0)
    now = 1000.0 + elapsed
    assert agent.ready_for_interval(now) == (elapsed >= interval)


@given(interval=st.integers(min_value=1, max_value=10_000),
       elapsed=st.floats(min_value=0, max_value=100_000, allow_nan=False))
def test_seconds_until_ready_zero_iff_ready(interval, elapsed):
    agent = BaseAgent(NullRunner(), interval=interval)
    agent.mark_ran(1000.0)
    now = 1000.0 + elapsed
    remaining = agent.seconds_until_ready(now)
    assert remaining >= 0.0
    assert (remaining == 0.0) == agent.ready_for_interval(now)


@given(interval=st.integers(min_value=1, max_value=10_000))
def test_note_pass_backs_off_multiplier_intervals(interval):
    agent = BaseAgent(NullRunner(), interval=interval)
    agent.mark_ran(1000.0)
    agent.note_pass(now=1000.0)
    just_before = 1000.0 + interval * PASS_BACKOFF_MULTIPLIER - 0.001
    at = 1000.0 + interval * PASS_BACKOFF_MULTIPLIER
    assert not agent.ready_for_interval(just_before)
    assert agent.ready_for_interval(at)


def test_note_pass_defaults_to_monotonic_now():
    agent = BaseAgent(NullRunner(), interval=10)
    agent.note_pass()
    assert not agent.ready_for_interval(time.monotonic())


def test_pause_resume_flag():
    agent = BaseAgent(NullRunner(), interval=1)
    assert agent.paused is False
    agent.pause()
    assert agent.paused is True
    agent.resume()
    assert agent.paused is False


def test_constructor_sets_name_and_personality():
    agent = BaseAgent(NullRunner(), interval=5, name="scout", personality="terse")
    assert agent.name == "scout"
    assert agent.personality == "terse"
    assert agent.interval == 5
    assert agent.telemetry is None


def test_guarded_line():
    assert BaseAgent._guarded_line("Mood", "wry") == "\n\nMood: wry"
    assert BaseAgent._guarded_line("Mood", "") == ""


# --- watermark gating -----------------------------------------------------

class FingerprintAgent(BaseAgent):
    def __init__(self, fp):
        super().__init__(NullRunner(), interval=1, name="fp")
        self.fp = fp

    async def _fingerprint(self):
        return self.fp


async def test_watermark_none_never_gates():
    agent = FingerprintAgent(None)
    assert await agent._gate_on_watermark(0.7) == 0.7
    await agent._record_watermark()
    assert await agent._gate_on_watermark(0.7) == 0.7


async def test_watermark_gates_unchanged_fingerprint_and_rearms():
    agent = FingerprintAgent(("a",))
    assert await agent._gate_on_watermark(0.7) == 0.7
    await agent._record_watermark()
    assert await agent._gate_on_watermark(0.7) == 0.0
    agent.fp = ("b",)
    assert await agent._gate_on_watermark(0.7) == 0.7
    agent.fp = ("a",)
    agent._clear_watermark()
    assert await agent._gate_on_watermark(0.7) == 0.7


# --- run_once telemetry bracketing ---------------------------------------

class RecordingAgent(BaseAgent):
    def __init__(self, fail: Exception | None = None):
        super().__init__(NullRunner(), interval=1, name="rec")
        self._fail = fail
        self.seen_run_id: str | None = None
        self.seen_agent_name: str | None = None

    async def _run(self):
        self.seen_run_id = current_run_id.get()
        self.seen_agent_name = current_agent_name.get()
        if self._fail:
            raise self._fail


async def test_run_once_success_emits_started_then_finished():
    agent = RecordingAgent()
    emitter = FakeEmitter()
    agent.telemetry = emitter
    await agent.run_once()
    types = [e[0] for e in emitter.events]
    assert types == [TelemetryEventType.AGENT_RUN_STARTED, TelemetryEventType.AGENT_RUN_FINISHED]
    started, finished = emitter.events[0][2], emitter.events[1][2]
    assert started.agent_name == "rec"
    assert finished.run_id == started.run_id
    assert finished.duration_s >= 0.0
    # contextvars visible inside _run, reset after
    assert agent.seen_run_id == started.run_id
    assert agent.seen_agent_name == "rec"
    assert current_run_id.get() is None
    assert current_agent_name.get() == ""


async def test_run_once_failure_emits_failed_and_reraises():
    agent = RecordingAgent(fail=ValueError("boom"))
    emitter = FakeEmitter()
    agent.telemetry = emitter
    with pytest.raises(ValueError, match="boom"):
        await agent.run_once()
    types = [e[0] for e in emitter.events]
    assert types == [TelemetryEventType.AGENT_RUN_STARTED, TelemetryEventType.AGENT_RUN_FAILED]
    failed = emitter.events[1][2]
    assert failed.error_type == "ValueError"
    assert failed.error_message == "boom"
    assert failed.phase == "agent"


async def test_run_once_failure_phase_llm_call_when_recorder_says_so():
    agent = RecordingAgent(fail=RuntimeError("llm died"))
    agent.telemetry = FakeEmitter(llm_call_run_ids={"*"})
    with pytest.raises(RuntimeError):
        await agent.run_once()
    failed = agent.telemetry.events[1][2]
    assert failed.phase == "llm_call"


async def test_run_once_without_telemetry_is_silent_noop():
    agent = RecordingAgent()
    await agent.run_once()  # must not raise
    assert agent.seen_agent_name == "rec"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/agent_kit/test_base.py -W error -q`
Expected: collection error — `ModuleNotFoundError: No module named 'agent_kit'`.

- [ ] **Step 3: Implement the three modules**

Create `agent_kit/__init__.py` (empty file for now — public API is Task 4).

Create `agent_kit/run_context.py`:

```python
"""Ambient identity of the agent run currently executing.

Deliberately dependency-free so any layer (storage, telemetry, tools) can
read these without importing the rest of the kit.
"""
from __future__ import annotations
from contextvars import ContextVar

current_run_id: ContextVar[str | None] = ContextVar("current_run_id", default=None)
current_agent_name: ContextVar[str] = ContextVar("current_agent_name", default="")
```

Create `agent_kit/telemetry.py`:

```python
"""Machinery-telemetry vocabulary for the agent loop and scheduler.

These five event types (and their payload shapes) are what BaseAgent.run_once
and Scheduler emit. The emitter itself is injected — see TelemetryEmitter.
Payload field names match novelizer's telemetry vocabulary so existing
recorders and tui_kit adapters understand them unchanged.
"""
from __future__ import annotations
from typing import Protocol

from pydantic import BaseModel


class TelemetryEventType:
    SCHEDULER_PICKED = "scheduler.picked"
    SCHEDULER_ELIGIBILITY_CHANGED = "scheduler.eligibility_changed"
    AGENT_RUN_STARTED = "agent.run_started"
    AGENT_RUN_FINISHED = "agent.run_finished"
    AGENT_RUN_FAILED = "agent.run_failed"


class SchedulerPicked(BaseModel):
    agent_name: str


class SchedulerEligibilityChanged(BaseModel):
    """Emitted on change of an agent's (eligible, reason) pair — never per tick."""

    agent_name: str
    eligible: bool
    reason: str  # "paused" | "running" | "interval not elapsed" | "readiness 0" | "ready"


class AgentRunStarted(BaseModel):
    run_id: str
    agent_name: str


class AgentRunFinished(BaseModel):
    run_id: str
    agent_name: str
    duration_s: float


class AgentRunFailed(BaseModel):
    run_id: str
    agent_name: str
    error_type: str
    error_message: str
    phase: str  # "llm_call" if the crash happened inside an open LLM call, else "agent"
    duration_s: float


class TelemetryEmitter(Protocol):
    """What the loop needs from a telemetry recorder. Injected post-
    construction (agent.telemetry defaults to None = silent)."""

    async def emit(self, event_type: str, aggregate_id: str, payload) -> None: ...

    def in_llm_call(self, run_id: str) -> bool: ...
```

Create `agent_kit/base.py`:

```python
from __future__ import annotations
import time
import uuid
from typing import Protocol

from agent_kit.run_context import current_agent_name, current_run_id
from agent_kit.telemetry import (
    AgentRunFailed,
    AgentRunFinished,
    AgentRunStarted,
    TelemetryEventType,
)

# An agent that ran on fresh material but explicitly chose not to act steps
# back for this many intervals instead of one, freeing dispatch slots.
PASS_BACKOFF_MULTIPLIER = 3


class Runner(Protocol):
    async def ainvoke(self, inputs: dict) -> dict: ...


class BaseAgent:
    """Generic poll/work/commit loop chassis, extracted from novelizer's
    BaseAgent (its fiction-specific commit helpers stay behind). Behavior is
    verbatim; the constructor drops the read_store/committer the generic
    half never used, and telemetry is an injected TelemetryEmitter."""

    name: str = "agent"

    def __init__(
        self,
        runner,
        interval: int,
        name: str | None = None,
        personality: str = "",
    ) -> None:
        self._runner = runner
        self.interval = interval
        if name is not None:
            self.name = name
        self.personality = personality
        self.paused = False
        self._last_run = 0.0
        self._backoff_until = 0.0
        self._last_fingerprint: tuple | None = None
        self.telemetry = None  # TelemetryEmitter; injected post-construction

    @staticmethod
    def _guarded_line(label: str, value: str) -> str:
        """Return an optional "\\n\\n{label}: {value}" line, or "" if value is falsy."""
        return f"\n\n{label}: {value}" if value else ""

    def pause(self) -> None:
        self.paused = True

    def resume(self) -> None:
        self.paused = False

    def ready_for_interval(self, now: float) -> bool:
        return (now - self._last_run) >= self.interval and now >= self._backoff_until

    def mark_ran(self, now: float) -> None:
        self._last_run = now

    def seconds_until_ready(self, now: float) -> float:
        return max(0.0, self.interval - (now - self._last_run), self._backoff_until - now)

    def note_pass(self, now: float | None = None) -> None:
        """Record an explicit "nothing to do" verdict: back off for
        PASS_BACKOFF_MULTIPLIER intervals instead of one. Same clock family
        as the scheduler's default (time.monotonic)."""
        if now is None:
            now = time.monotonic()
        self._backoff_until = now + self.interval * PASS_BACKOFF_MULTIPLIER

    async def _fingerprint(self) -> tuple | None:
        """External state this agent's work depends on. None (default)
        disables watermarking. Subclasses return a small tuple; captured
        AFTER the agent's own commits, so its own writes never re-trigger it."""
        return None

    async def _gate_on_watermark(self, score: float) -> float:
        fp = await self._fingerprint()
        if fp is not None and fp == self._last_fingerprint:
            return 0.0
        return score

    async def _record_watermark(self) -> None:
        self._last_fingerprint = await self._fingerprint()

    def _clear_watermark(self) -> None:
        self._last_fingerprint = None

    async def readiness(self) -> float:
        return 0.0

    async def _run(self) -> None:
        """Subclasses put their poll/work/commit body here; run_once brackets
        it with machinery telemetry and ambient run context."""

    async def run_once(self) -> None:
        run_id = str(uuid.uuid4())
        started = time.monotonic()
        rid_token = current_run_id.set(run_id)
        name_token = current_agent_name.set(self.name)
        await self._emit_telemetry(
            TelemetryEventType.AGENT_RUN_STARTED, run_id,
            AgentRunStarted(run_id=run_id, agent_name=self.name),
        )
        try:
            await self._run()
        except Exception as e:
            phase = "llm_call" if (self.telemetry and self.telemetry.in_llm_call(run_id)) else "agent"
            await self._emit_telemetry(
                TelemetryEventType.AGENT_RUN_FAILED, run_id,
                AgentRunFailed(run_id=run_id, agent_name=self.name,
                               error_type=type(e).__name__, error_message=str(e),
                               phase=phase, duration_s=time.monotonic() - started),
            )
            raise
        else:
            await self._emit_telemetry(
                TelemetryEventType.AGENT_RUN_FINISHED, run_id,
                AgentRunFinished(run_id=run_id, agent_name=self.name,
                                 duration_s=time.monotonic() - started),
            )
        finally:
            current_run_id.reset(rid_token)
            current_agent_name.reset(name_token)

    async def _emit_telemetry(self, event_type: str, aggregate_id: str, payload) -> None:
        if self.telemetry is None:
            return
        await self.telemetry.emit(event_type, aggregate_id, payload)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/agent_kit/test_base.py -W error -q`
Expected: all PASS.

- [ ] **Step 5: Commit**

```bash
git add agent_kit/ tests/agent_kit/
git commit -m "feat(agent_kit): extract BaseAgent loop, telemetry vocabulary, run context

Verbatim behavioral copy of novelizer's generic loop half; constructor
drops unused stores, telemetry injected via TelemetryEmitter protocol.

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
```

---

### Task 2: import-linter contracts for agent_kit

**Files:**
- Modify: `pyproject.toml` (the `[tool.importlinter]` section at the bottom)
- Test: existing `tests/substrate/test_import_boundary.py` (no changes — it shells `lint-imports`, which picks up new contracts automatically)

**Interfaces:**
- Consumes: `agent_kit` package from Task 1.
- Produces: enforced boundaries every later task must respect.

- [ ] **Step 1: Add contracts (red = contract that would fail if violated)**

In `pyproject.toml`, change the `root_packages` line and append two contracts after the existing `tui_kit independence` contract:

```toml
[tool.importlinter]
root_packages = ["substrate", "novelizer", "research_domain", "tui_kit", "agent_kit"]
```

Append at the end of the file:

```toml
[[tool.importlinter.contracts]]
name = "agent_kit independence"
type = "forbidden"
source_modules = ["agent_kit"]
forbidden_modules = ["novelizer", "substrate", "research_domain", "tui_kit"]

[[tool.importlinter.contracts]]
name = "agent_kit package boundary"
type = "forbidden"
source_modules = ["novelizer", "research_domain"]
allow_indirect_imports = "true"
forbidden_modules = [
    "agent_kit.base",
    "agent_kit.scheduler",
    "agent_kit.telemetry",
    "agent_kit.run_context",
    "agent_kit.llm",
    "agent_kit.middleware",
]
```

(`agent_kit.scheduler`, `agent_kit.llm`, `agent_kit.middleware` don't exist yet — import-linter forbidden contracts tolerate nonexistent forbidden modules; if the installed version errors on them instead, trim the list to existing modules here and extend it in Tasks 3 and 4 when those modules land.)

- [ ] **Step 2: Run the boundary check**

Run: `uv run lint-imports`
Expected: all contracts KEPT (agent_kit imports nothing forbidden).

Run: `uv run pytest tests/substrate/test_import_boundary.py -W error -q`
Expected: PASS.

- [ ] **Step 3: Commit**

```bash
git add pyproject.toml
git commit -m "chore(agent_kit): enforce independence + package boundary via import-linter

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
```

---

### Task 3: agent_kit.scheduler — Scheduler with injectable override_provider

**Files:**
- Create: `agent_kit/scheduler.py`
- Test: `tests/agent_kit/test_scheduler.py`
- Test: `tests/agent_kit/test_scheduler_parity.py`

**Interfaces:**
- Consumes: `agent_kit.telemetry` (`TelemetryEventType`, `SchedulerPicked`, `SchedulerEligibilityChanged`) from Task 1. Duck-typed agents exposing `name`, `paused`, `pause()`, `resume()`, `readiness()`, `ready_for_interval(now)`, `mark_ran(now)`, `seconds_until_ready(now)`, `run_once()` (BaseAgent satisfies all).
- Produces (used by Tasks 10, 11): `agent_kit.scheduler.Scheduler(agents, tick_sleep=1.0, clock=time.monotonic, max_concurrent_agents=2, telemetry=None, override_provider=None)`; methods `async tick() -> list[str]`, `async drain_in_flight()`, `async run()`, `stop()`, `status() -> list[dict]`, `pause_agent(name)`, `resume_agent(name)`, `pause_all() -> list[str]`, `resume_agents(names)`. `override_provider` is `Callable[[], Awaitable[str | None]]` — returns an agent name to dispatch first, or None.

- [ ] **Step 1: Write the failing tests**

Create `tests/agent_kit/test_scheduler.py`:

```python
from __future__ import annotations
import asyncio

from agent_kit.scheduler import Scheduler
from agent_kit.telemetry import TelemetryEventType


class StubAgent:
    def __init__(self, name, score, interval=0):
        self.name = name; self._score = score; self.interval = interval
        self.paused = False; self._last = -999; self.ran = 0
    async def readiness(self): return self._score
    def ready_for_interval(self, now): return (now - self._last) >= self.interval
    def mark_ran(self, now): self._last = now; self.ran += 1
    def seconds_until_ready(self, now): return max(0.0, self.interval - (now - self._last))
    async def run_once(self): pass
    def pause(self): self.paused = True
    def resume(self): self.paused = False


class CrashingAgent(StubAgent):
    async def run_once(self): raise RuntimeError("kaput")


class FakeEmitter:
    def __init__(self):
        self.events = []
    async def emit(self, event_type, aggregate_id, payload):
        self.events.append((event_type, aggregate_id, payload))
    def in_llm_call(self, run_id): return False


async def test_runs_highest_readiness_first():
    a = StubAgent("a", 0.2); b = StubAgent("b", 0.9)
    sched = Scheduler([a, b], clock=lambda: 1000.0, max_concurrent_agents=1)
    assert await sched.tick() == ["b"]
    await sched.drain_in_flight()
    assert b.ran == 1 and a.ran == 0


async def test_skips_paused_and_zero_score():
    a = StubAgent("a", 0.0); b = StubAgent("b", 0.5)
    b.pause()
    sched = Scheduler([a, b], clock=lambda: 1000.0)
    assert await sched.tick() == []


async def test_respects_interval_and_concurrency_cap():
    a = StubAgent("a", 0.9); b = StubAgent("b", 0.8); c = StubAgent("c", 0.7)
    sched = Scheduler([a, b, c], clock=lambda: 1000.0, max_concurrent_agents=2)
    assert await sched.tick() == ["a", "b"]
    await sched.drain_in_flight()


async def test_override_provider_dispatches_named_agent_first():
    a = StubAgent("a", 0.9); b = StubAgent("b", 0.1)
    async def provider(): return "b"
    sched = Scheduler([a, b], clock=lambda: 1000.0,
                      max_concurrent_agents=1, override_provider=provider)
    assert await sched.tick() == ["b"]
    await sched.drain_in_flight()


async def test_no_override_provider_means_pure_readiness_order():
    a = StubAgent("a", 0.3); b = StubAgent("b", 0.6)
    sched = Scheduler([a, b], clock=lambda: 1000.0, max_concurrent_agents=1)
    assert await sched.tick() == ["b"]
    await sched.drain_in_flight()


async def test_crash_consumes_interval_and_records_error():
    a = CrashingAgent("a", 0.9, interval=10)
    sched = Scheduler([a], clock=lambda: 1000.0)
    assert await sched.tick() == ["a"]
    await sched.drain_in_flight()
    status = {s["name"]: s for s in sched.status()}
    assert "RuntimeError: kaput" in status["a"]["last_error"]
    assert status["a"]["run_count"] == 1
    # interval consumed: not eligible again at the same clock
    assert await sched.tick() == []


async def test_success_clears_last_error_and_marks_last_completed():
    a = StubAgent("a", 0.9)
    sched = Scheduler([a], clock=lambda: 1000.0)
    await sched.tick(); await sched.drain_in_flight()
    status = {s["name"]: s for s in sched.status()}
    assert status["a"]["last_error"] is None
    assert status["a"]["last_completed"] is True


async def test_eligibility_emitted_on_change_only():
    a = StubAgent("a", 0.0)
    emitter = FakeEmitter()
    sched = Scheduler([a], clock=lambda: 1000.0, telemetry=emitter)
    await sched.tick()
    await sched.tick()
    elig = [e for e in emitter.events
            if e[0] == TelemetryEventType.SCHEDULER_ELIGIBILITY_CHANGED]
    assert len(elig) == 1  # second identical state emits nothing
    assert elig[0][2].reason == "readiness 0"


async def test_picked_emitted_per_dispatch():
    a = StubAgent("a", 0.9)
    emitter = FakeEmitter()
    sched = Scheduler([a], clock=lambda: 1000.0, telemetry=emitter)
    await sched.tick(); await sched.drain_in_flight()
    picked = [e for e in emitter.events if e[0] == TelemetryEventType.SCHEDULER_PICKED]
    assert len(picked) == 1 and picked[0][2].agent_name == "a"


async def test_pause_all_returns_only_newly_paused():
    a = StubAgent("a", 0.1); b = StubAgent("b", 0.1)
    b.pause()
    sched = Scheduler([a, b], clock=lambda: 1000.0)
    assert sched.pause_all() == ["a"]
    sched.resume_agents(["a"])
    assert a.paused is False and b.paused is True


async def test_genuine_concurrency_up_to_cap():
    log = []
    class SlowAgent(StubAgent):
        async def run_once(self):
            loop = asyncio.get_event_loop()
            start = loop.time(); await asyncio.sleep(0.05); log.append((self.name, start, loop.time()))
    a = SlowAgent("a", 0.9); b = SlowAgent("b", 0.8)
    sched = Scheduler([a, b], clock=lambda: 1000.0, max_concurrent_agents=2)
    await sched.tick(); await sched.drain_in_flight()
    (n1, s1, e1), (n2, s2, e2) = sorted(log, key=lambda x: x[1])
    assert s2 < e1  # overlapping, not sequential
```

Create `tests/agent_kit/test_scheduler_parity.py`:

```python
"""Behavioral parity between agent_kit.Scheduler and novelizer's Scheduler.

The temporary-copy honesty check: until the novelizer cutover campaign,
identical scripted scenarios must produce identical dispatch traces. Tests
are exempt from import contracts, so importing novelizer here is fine.
"""
from __future__ import annotations

from agent_kit.scheduler import Scheduler as KitScheduler
from novelizer.scheduler import Scheduler as NovelizerScheduler


class StubAgent:
    def __init__(self, name, scores, interval=5):
        self.name = name; self._scores = list(scores); self.interval = interval
        self.paused = False; self._last = -999; self.ran = 0
    async def readiness(self):
        return self._scores.pop(0) if self._scores else 0.0
    def ready_for_interval(self, now): return (now - self._last) >= self.interval
    def mark_ran(self, now): self._last = now; self.ran += 1
    def seconds_until_ready(self, now): return max(0.0, self.interval - (now - self._last))
    async def run_once(self): pass
    def pause(self): self.paused = True
    def resume(self): self.paused = False


class StubRead:
    async def list_unconsumed_signals(self, target_agent=None): return []


SCENARIO = [
    ("a", [0.9, 0.1, 0.5, 0.0]),
    ("b", [0.2, 0.8, 0.5, 0.0]),
    ("c", [0.0, 0.0, 0.9, 0.9]),
]
CLOCKS = [1000.0, 1000.0, 1006.0, 1012.0]


async def _trace(make_sched):
    agents = [StubAgent(n, list(s)) for n, s in SCENARIO]
    trace = []
    for t in CLOCKS:
        sched = make_sched(agents, t)
        trace.append(await sched.tick())
        await sched.drain_in_flight()
    return trace


async def test_identical_dispatch_traces():
    kit = await _trace(lambda ags, t: KitScheduler(
        ags, clock=lambda: t, max_concurrent_agents=2))
    nov = await _trace(lambda ags, t: NovelizerScheduler(
        ags, StubRead(), clock=lambda: t, max_concurrent_agents=2))
    assert kit == nov
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/agent_kit/test_scheduler.py tests/agent_kit/test_scheduler_parity.py -W error -q`
Expected: `ModuleNotFoundError: No module named 'agent_kit.scheduler'`.

- [ ] **Step 3: Implement `agent_kit/scheduler.py`**

```python
from __future__ import annotations
import asyncio
import logging
import time
from typing import Awaitable, Callable, Sequence

from agent_kit.telemetry import (
    SchedulerEligibilityChanged,
    SchedulerPicked,
    TelemetryEventType,
)

logger = logging.getLogger(__name__)


class Scheduler:
    """Readiness-sorted dispatch pool, extracted verbatim from novelizer's
    Scheduler. The one seam change: the Director-signal override lookup is
    an injectable override_provider (async () -> agent name | None) instead
    of a read_store query — domains without a Director pass nothing."""

    def __init__(
        self,
        agents: Sequence,
        tick_sleep: float = 1.0,
        clock=time.monotonic,
        max_concurrent_agents: int = 2,
        telemetry=None,
        override_provider: Callable[[], Awaitable[str | None]] | None = None,
    ) -> None:
        self._agents = list(agents)
        self._tick_sleep = tick_sleep
        self._clock = clock
        self._telemetry = telemetry
        self._override_provider = override_provider
        self._running = False
        self._max_concurrent = max_concurrent_agents
        self._in_flight: dict[str, asyncio.Task] = {}
        self._last_error: dict[str, str] = {}
        self._eligibility: dict[str, tuple[bool, str]] = {}
        self._last_completed: str | None = None
        # Incremented on every completed run (success or failure). Lets
        # callers distinguish "still the same stale error" from "ran again
        # and failed again" without relying on error-message content.
        self._run_count: dict[str, int] = {}

    def pause_agent(self, name: str) -> None:
        for a in self._agents:
            if a.name == name:
                a.pause()

    def resume_agent(self, name: str) -> None:
        for a in self._agents:
            if a.name == name:
                a.resume()

    def pause_all(self) -> list[str]:
        """Pause every not-yet-paused agent. Returns the names actually
        paused by this call, so a caller can resume only those later
        without clobbering agents that were already individually paused."""
        paused = []
        for a in self._agents:
            if not a.paused:
                a.pause()
                paused.append(a.name)
        return paused

    def resume_agents(self, names) -> None:
        for a in self._agents:
            if a.name in names:
                a.resume()

    def status(self) -> list:
        now = self._clock()
        return [
            {
                "name": a.name,
                "paused": a.paused,
                "running": a.name in self._in_flight,
                "last_error": self._last_error.get(a.name),
                "last_completed": a.name == self._last_completed,
                "run_count": self._run_count.get(a.name, 0),
                "next_ready_in": a.seconds_until_ready(now) if hasattr(a, "seconds_until_ready") else 0.0,
            }
            for a in self._agents
        ]

    async def tick(self) -> list[str]:
        """Fill free dispatch-pool slots from the readiness-sorted eligible
        list. Does NOT await dispatched agents to completion -- it returns as
        soon as tasks are created, so the tick cadence becomes the dispatch
        cadence, not a wait-for-completion cadence."""
        now = self._clock()
        free_slots = self._max_concurrent - len(self._in_flight)
        if free_slots <= 0:
            await self._emit_eligibility(now, scores={})
            return []
        override = await self._override_provider() if self._override_provider else None
        eligible = [
            a for a in self._agents
            if not a.paused and a.name not in self._in_flight and a.ready_for_interval(now)
        ]
        if not eligible:
            await self._emit_eligibility(now, scores={})
            return []

        to_dispatch: list = []
        if override:
            for a in eligible:
                if a.name == override:
                    to_dispatch.append(a)
                    eligible = [x for x in eligible if x.name != override]
                    break

        scores: dict[str, float] = {}
        if len(to_dispatch) < free_slots:
            scored = [(await a.readiness(), a) for a in eligible]
            scored.sort(key=lambda x: x[0], reverse=True)
            scores = {a.name: s for s, a in scored}
            for score, a in scored:
                if len(to_dispatch) >= free_slots:
                    break
                if score > 0.0:
                    to_dispatch.append(a)
        await self._emit_eligibility(now, scores)

        dispatched: list[str] = []
        for a in to_dispatch:
            if self._telemetry is not None:
                await self._telemetry.emit(
                    TelemetryEventType.SCHEDULER_PICKED, a.name,
                    SchedulerPicked(agent_name=a.name),
                )
            task = asyncio.create_task(self._run(a, now))
            # Retrieve (and discard) the exception so fire-and-forget crashes
            # don't log "Task exception was never retrieved"; failures are
            # recorded via _last_error inside _run and re-raised within the
            # task for direct awaiters.
            task.add_done_callback(lambda t: t.exception() if not t.cancelled() else None)
            self._in_flight[a.name] = task
            dispatched.append(a.name)
        return dispatched

    async def _emit_eligibility(self, now: float, scores: dict[str, float]) -> None:
        """One eligibility_changed per agent per state *change* — quiet log,
        not a per-tick heartbeat."""
        if self._telemetry is None:
            return
        for a in self._agents:
            if a.paused:
                state = (False, "paused")
            elif a.name in self._in_flight:
                state = (False, "running")
            elif not a.ready_for_interval(now):
                state = (False, "interval not elapsed")
            elif a.name in scores and scores[a.name] <= 0.0:
                state = (False, "readiness 0")
            else:
                state = (True, "ready")
            if self._eligibility.get(a.name) != state:
                self._eligibility[a.name] = state
                await self._telemetry.emit(
                    TelemetryEventType.SCHEDULER_ELIGIBILITY_CHANGED, a.name,
                    SchedulerEligibilityChanged(agent_name=a.name, eligible=state[0], reason=state[1]),
                )

    async def drain_in_flight(self) -> None:
        """Await every currently in-flight dispatched task to completion.
        Task exceptions are swallowed here (already recorded via
        ``_last_error`` inside ``_run``)."""
        tasks = list(self._in_flight.values())
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)

    async def _run(self, agent, now: float) -> None:
        logger.info("scheduler: running %s", agent.name)
        try:
            await agent.run_once()
        except Exception as e:
            self._last_error[agent.name] = f"{type(e).__name__}: {e}"
            raise
        else:
            self._last_error.pop(agent.name, None)
        finally:
            # mark_ran even on failure: a crashing agent must consume its
            # interval (backoff) instead of staying eligible and hot-looping,
            # which starves every other agent of scheduler slots.
            agent.mark_ran(now)
            self._in_flight.pop(agent.name, None)
            self._run_count[agent.name] = self._run_count.get(agent.name, 0) + 1
            # Sticky display marker, distinct from the honest in-flight
            # "running" flag.
            self._last_completed = agent.name

    async def run(self) -> None:
        self._running = True
        while self._running:
            try:
                await self.tick()
            except Exception:
                logger.exception("scheduler: error in tick")
            await asyncio.sleep(self._tick_sleep)

    def stop(self) -> None:
        self._running = False
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/agent_kit/ -W error -q`
Expected: all PASS (base + scheduler + parity).

Run: `uv run lint-imports`
Expected: contracts KEPT.

- [ ] **Step 5: Commit**

```bash
git add agent_kit/scheduler.py tests/agent_kit/test_scheduler.py tests/agent_kit/test_scheduler_parity.py
git commit -m "feat(agent_kit): extract Scheduler with injectable override_provider

Verbatim dispatch-pool mechanics; Director-signal lookup becomes an
injected async callable. Parity test locks dispatch traces to novelizer's
Scheduler until the cutover campaign.

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
```

---

### Task 4: agent_kit.llm + middleware + public API

**Files:**
- Create: `agent_kit/llm.py`
- Create: `agent_kit/middleware.py`
- Modify: `agent_kit/__init__.py`
- Test: `tests/agent_kit/test_llm.py`
- Test: `tests/agent_kit/test_public_api.py`

**Interfaces:**
- Consumes: nothing from earlier tasks (llm/middleware are leaf modules).
- Produces (used by Tasks 9–11):
  - `agent_kit.llm.build_chat_model(model: str, base_url: str, api_key: str, temperature: float = 0.8, max_tokens: int | None = None, callbacks=None, streaming=None, context_window_tokens: int = 128_000)` → a LangChain chat model.
  - `agent_kit.llm.build_agent_runner(*, model, system_prompt: str, response_format, tools=None, middleware=None, backend=None, callbacks=None, recursion_limit: int = 100)` → a deepagents graph (satisfies the `Runner` protocol).
  - `agent_kit.llm.GRAPH_RECURSION_LIMIT = 100`, `agent_kit.llm.CONTEXT_WINDOW_TOKENS = 128_000`.
  - `agent_kit.middleware.ExcludeToolsMiddleware(excluded: frozenset[str])`.
  - `agent_kit.__all__` public API (import everything from `agent_kit` top level).

- [ ] **Step 1: Write the failing tests**

Create `tests/agent_kit/test_llm.py`:

```python
from __future__ import annotations

from agent_kit.llm import CONTEXT_WINDOW_TOKENS, GRAPH_RECURSION_LIMIT, build_chat_model


def test_recursion_limit_and_context_window_defaults():
    assert GRAPH_RECURSION_LIMIT == 100
    assert CONTEXT_WINDOW_TOKENS == 128_000


def test_build_chat_model_stamps_profile_and_params():
    m = build_chat_model("test-model", "http://localhost:9999/v1", "key",
                         temperature=0.3, max_tokens=512)
    assert m.temperature == 0.3
    assert m.max_tokens == 512
    assert m.profile["max_input_tokens"] == CONTEXT_WINDOW_TOKENS
    assert m.streaming is False  # no callbacks -> streaming off


def test_callbacks_imply_streaming_and_explicit_flag_decouples():
    class Cb:  # never invoked — construction-level test only
        pass
    with_cb = build_chat_model("m", "http://localhost:9999/v1", "k", callbacks=[Cb()])
    assert with_cb.streaming is True
    decoupled = build_chat_model("m", "http://localhost:9999/v1", "k",
                                 callbacks=[Cb()], streaming=False)
    assert decoupled.streaming is False


def test_context_window_parameterized():
    m = build_chat_model("m", "http://localhost:9999/v1", "k",
                         context_window_tokens=32_000)
    assert m.profile["max_input_tokens"] == 32_000
```

Create `tests/agent_kit/test_public_api.py`:

```python
from __future__ import annotations

import agent_kit

EXPECTED = {
    "BaseAgent", "Runner", "PASS_BACKOFF_MULTIPLIER",
    "Scheduler",
    "TelemetryEventType", "TelemetryEmitter",
    "AgentRunStarted", "AgentRunFinished", "AgentRunFailed",
    "SchedulerPicked", "SchedulerEligibilityChanged",
    "current_run_id", "current_agent_name",
    "build_chat_model", "build_agent_runner",
    "GRAPH_RECURSION_LIMIT", "CONTEXT_WINDOW_TOKENS",
    "ExcludeToolsMiddleware",
}


def test_all_matches_expected_surface():
    assert set(agent_kit.__all__) == EXPECTED


def test_every_name_importable():
    for name in agent_kit.__all__:
        assert getattr(agent_kit, name) is not None
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/agent_kit/test_llm.py tests/agent_kit/test_public_api.py -W error -q`
Expected: `ModuleNotFoundError` / empty `__all__` failures.

- [ ] **Step 3: Implement**

Create `agent_kit/middleware.py` (copy of novelizer's generic middleware, minus the novelizer-specific `TodoContextMiddleware`):

```python
from __future__ import annotations

from typing import Any

from langchain.agents.middleware import AgentMiddleware


def _tool_name(tool: Any) -> str:
    name = getattr(tool, "name", None)
    if name is not None:
        return name
    return tool.get("name", "")


class ExcludeToolsMiddleware(AgentMiddleware[Any, Any, Any]):
    """Strip named tools from the model request before the model sees them.

    Placed after deepagents' tool-injecting middleware so it can remove
    built-ins like `write_todos`."""

    def __init__(self, *, excluded: frozenset[str]) -> None:
        self._excluded = excluded

    def wrap_model_call(self, request, handler):
        if self._excluded:
            filtered = [t for t in request.tools if _tool_name(t) not in self._excluded]
            request = request.override(tools=filtered)
        return handler(request)

    async def awrap_model_call(self, request, handler):
        if self._excluded:
            filtered = [t for t in request.tools if _tool_name(t) not in self._excluded]
            request = request.override(tools=filtered)
        return await handler(request)
```

Create `agent_kit/llm.py`:

```python
from __future__ import annotations

from langchain_openai import ChatOpenAI

# Default context window for local OpenAI-compatible endpoints. deepagents'
# create_deep_agent always attaches SummarizationMiddleware; without a model
# profile it falls back to a fixed 170k-token trigger — past a 128k window,
# so compaction would never fire before a request overflows. Stamping
# max_input_tokens switches deepagents onto its fraction-based defaults
# (trigger at 85%, keep last 10%), sized for the actual window.
CONTEXT_WINDOW_TOKENS = 128_000

# Tool-heavy passes can exceed LangGraph's default of 25; 50 still tripped
# in practice, so give agent graphs generous headroom.
GRAPH_RECURSION_LIMIT = 100


class _ReasoningAwareChatOpenAI(ChatOpenAI):
    """ChatOpenAI, plus surfacing provider-specific reasoning/thinking deltas
    into additional_kwargs.

    Plain ChatOpenAI targets the official OpenAI API only: non-standard
    streamed fields like reasoning_content (what vLLM and other local
    reasoning-enabled OpenAI-compatible servers send) are silently dropped in
    _convert_delta_to_message_chunk before a callback ever sees them. This
    override re-reads the raw delta dict streamed alongside content and
    stashes reasoning_content (or the `reasoning` key some proxies use) back
    onto the chunk, so a telemetry callback's on_llm_new_token can read it
    via chunk.message.additional_kwargs."""

    def _convert_chunk_to_generation_chunk(self, chunk, default_chunk_class, base_generation_info):
        generation_chunk = super()._convert_chunk_to_generation_chunk(
            chunk, default_chunk_class, base_generation_info)
        if generation_chunk is None:
            return generation_chunk
        choices = chunk.get("choices") or chunk.get("chunk", {}).get("choices", [])
        if not choices:
            return generation_chunk
        delta = choices[0].get("delta") or {}
        reasoning = delta.get("reasoning_content") or delta.get("reasoning")
        if reasoning:
            generation_chunk.message.additional_kwargs["reasoning_content"] = reasoning
        return generation_chunk


def build_chat_model(
    model: str, base_url: str, api_key: str, temperature: float = 0.8,
    max_tokens: int | None = None, callbacks=None, streaming=None,
    context_window_tokens: int = CONTEXT_WINDOW_TOKENS,
):
    """Build a LangChain chat model bound to an OpenAI-compatible endpoint.

    max_tokens caps generation per request: an uncapped local model can
    ramble past a proxy's request timeout and never return, which the caller
    sees as a hang.

    callbacks (telemetry handlers) imply streaming=True by default — token-
    by-token delivery is what makes on_llm_new_token fire. Pass `streaming`
    explicitly to decouple the two.
    """
    if streaming is None:
        streaming = callbacks is not None
    return _ReasoningAwareChatOpenAI(
        model=model,
        base_url=base_url,
        api_key=api_key,
        temperature=temperature,
        max_tokens=max_tokens,
        callbacks=callbacks,
        streaming=streaming,
        profile={"max_input_tokens": context_window_tokens},
    )


def build_agent_runner(
    *, model, system_prompt: str, response_format, tools=None,
    middleware=None, backend=None, callbacks=None,
    recursion_limit: int = GRAPH_RECURSION_LIMIT,
):
    """Build a deepagents graph satisfying the Runner protocol: the generic
    form of the per-domain runner builders. Callers pass their system
    prompt, a pydantic response_format, and their tools; the result's
    ainvoke returns a dict whose "structured_response" key carries the
    parsed response_format instance."""
    from deepagents import create_deep_agent

    kwargs: dict = {
        "model": model,
        "system_prompt": system_prompt,
        "response_format": response_format,
    }
    if tools is not None:
        kwargs["tools"] = list(tools)
    if middleware is not None:
        kwargs["middleware"] = list(middleware)
    if backend is not None:
        kwargs["backend"] = backend
    graph = create_deep_agent(**kwargs)
    config: dict = {"recursion_limit": recursion_limit}
    if callbacks:
        config["callbacks"] = callbacks
    return graph.with_config(config)
```

Replace `agent_kit/__init__.py` contents:

```python
"""agent_kit: domain-neutral agent execution machinery.

The third extraction (after substrate/ and tui_kit/): the BaseAgent
poll/work/commit loop chassis, the readiness-sorted Scheduler, LLM runner
construction, and the machinery-telemetry vocabulary — extracted from
novelizer's shape, consumed first by research_domain.

Import from this top level only; submodule imports are forbidden by an
import-linter contract (see pyproject.toml).
"""
from agent_kit.base import PASS_BACKOFF_MULTIPLIER, BaseAgent, Runner
from agent_kit.llm import (
    CONTEXT_WINDOW_TOKENS,
    GRAPH_RECURSION_LIMIT,
    build_agent_runner,
    build_chat_model,
)
from agent_kit.middleware import ExcludeToolsMiddleware
from agent_kit.run_context import current_agent_name, current_run_id
from agent_kit.scheduler import Scheduler
from agent_kit.telemetry import (
    AgentRunFailed,
    AgentRunFinished,
    AgentRunStarted,
    SchedulerEligibilityChanged,
    SchedulerPicked,
    TelemetryEmitter,
    TelemetryEventType,
)

__all__ = [
    "BaseAgent",
    "Runner",
    "PASS_BACKOFF_MULTIPLIER",
    "Scheduler",
    "TelemetryEventType",
    "TelemetryEmitter",
    "AgentRunStarted",
    "AgentRunFinished",
    "AgentRunFailed",
    "SchedulerPicked",
    "SchedulerEligibilityChanged",
    "current_run_id",
    "current_agent_name",
    "build_chat_model",
    "build_agent_runner",
    "GRAPH_RECURSION_LIMIT",
    "CONTEXT_WINDOW_TOKENS",
    "ExcludeToolsMiddleware",
]
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/agent_kit/ -W error -q && uv run lint-imports`
Expected: all PASS; contracts KEPT.

Note: if `-W error` turns a langchain/deepagents deprecation warning at import time into a failure, add the narrowest possible `filterwarnings` ini entry for that specific message to `[tool.pytest.ini_options]` rather than dropping `-W error` (check how existing novelizer tests that import langchain handle it first and copy that approach).

- [ ] **Step 5: Commit**

```bash
git add agent_kit/ tests/agent_kit/test_llm.py tests/agent_kit/test_public_api.py
git commit -m "feat(agent_kit): add LLM runner construction, middleware, public API

build_chat_model (reasoning-aware ChatOpenAI, parameterized context
window) + build_agent_runner (deepagents) + ExcludeToolsMiddleware;
explicit __all__ surface with public-api test.

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
```

---

### Task 5: research_domain runtime extensions — claims registry, accessors, lock

**Files:**
- Modify: `research_domain/runtime.py`
- Test: `tests/research_domain/test_runtime_registries.py` (new)

**Interfaces:**
- Consumes: existing `ResearchRuntime` (see file), `substrate.RuntimeBase`.
- Produces (used by Tasks 7–11), all on `ResearchRuntime`:
  - `list_claims() -> list[dict]` — each `{"claim_id": str, "source_id": str, "text": str}`, in stream order.
  - `get_claim(claim_id: str) -> dict | None`
  - `claimed_source_ids() -> set[str]` — sources with at least one `claim.proposed`.
  - `corroborators_for(claim_id: str) -> list[str]` — source_ids from `source.corroborated`.
  - `refuters_for(claim_id: str) -> list[str]` — claim_ids refuting this claim.
  - `superseders_for(claim_id: str) -> list[str]` — claim_ids superseding this claim.
  - `contradiction_targets() -> list[str]` — claim_ids having at least one refuter.
  - `async append_events(events: list[tuple[str, dict]]) -> None` — batch append + single catch_up, serialized under the runtime lock.
  - `catch_up()` / `append_event()` now serialized under an internal `asyncio.Lock`.

- [ ] **Step 1: Write the failing tests**

Create `tests/research_domain/test_runtime_registries.py`:

```python
from __future__ import annotations
import asyncio

from research_domain.runtime import ResearchRuntime

from tests.substrate.postgres_fixture import postgres_dsn  # noqa: F401


async def test_claims_and_corroborator_registries_refresh(postgres_dsn):
    runtime = ResearchRuntime(postgres_dsn, stream="registry-test-stream")
    await runtime.connect()
    try:
        await runtime.append_events([
            ("claim.proposed", {"claim_id": "c1", "source_id": "a.md", "text": "The sky is blue."}),
            ("claim.proposed", {"claim_id": "c2", "source_id": "b.md", "text": "The sky is green."}),
            ("source.corroborated", {"source_id": "b.md", "claim_id": "c1"}),
            ("claim.refuted", {"claim_id": "c2", "target_claim_id": "c1", "reason": "colors differ"}),
        ])
        claims = runtime.list_claims()
        assert [c["claim_id"] for c in claims] == ["c1", "c2"]
        assert runtime.get_claim("c1")["text"] == "The sky is blue."
        assert runtime.get_claim("missing") is None
        assert runtime.claimed_source_ids() == {"a.md", "b.md"}
        assert runtime.corroborators_for("c1") == ["b.md"]
        assert runtime.corroborators_for("c2") == []
        assert runtime.refuters_for("c1") == ["c2"]
        assert runtime.contradiction_targets() == ["c1"]
        assert runtime.superseders_for("c1") == []
    finally:
        await runtime.close()


async def test_append_events_batches_with_single_visible_catchup(postgres_dsn):
    runtime = ResearchRuntime(postgres_dsn, stream="registry-batch-stream")
    await runtime.connect()
    try:
        await runtime.append_events([
            ("claim.proposed", {"claim_id": "c1", "source_id": "a.md", "text": "t1"}),
            ("claim.proposed", {"claim_id": "c2", "source_id": "a.md", "text": "t2"}),
        ])
        assert runtime.get_projection("source_coverage") == {"a.md": 2}
    finally:
        await runtime.close()


async def test_concurrent_appends_serialize_without_corruption(postgres_dsn):
    runtime = ResearchRuntime(postgres_dsn, stream="registry-lock-stream")
    await runtime.connect()
    try:
        async def writer(i: int):
            await runtime.append_event(
                "claim.proposed",
                {"claim_id": f"c{i}", "source_id": f"s{i}.md", "text": f"t{i}"})
        await asyncio.gather(*(writer(i) for i in range(6)))
        assert len(runtime.list_claims()) == 6
        assert runtime.claimed_source_ids() == {f"s{i}.md" for i in range(6)}
    finally:
        await runtime.close()
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/research_domain/test_runtime_registries.py -W error -q`
Expected: FAIL — `AttributeError: 'ResearchRuntime' object has no attribute 'append_events'` (or docker skip if docker unavailable — in that case note the skip and rely on Step 4's assertion run once docker is present; the implementation below is still required).

- [ ] **Step 3: Extend `research_domain/runtime.py`**

Replace the file with:

```python
# research_domain/runtime.py
from __future__ import annotations

import asyncio

from substrate import PostgresEventStore, RuntimeBase
from research_domain.projections import (
    build_claim_dependency_catalog,
    build_contradiction_map_catalog,
    build_source_coverage_catalog,
)


class ResearchRuntime(RuntimeBase):
    """Wires the three research_domain projections to a Postgres-backed
    RuntimeBase. Lookup dicts are refreshed from the full event stream on
    every catch_up() so the catalogs' recompute closures always see current
    data -- see the class-level note on _refresh_lookup_dicts.

    All mutation paths (catch_up, append_event, append_events) are
    serialized under one asyncio.Lock: multiple agents share a single
    runtime instance under a concurrency-2 scheduler, and
    _refresh_lookup_dicts clears shared dicts mid-refresh — the lock keeps
    an agent from reading half-cleared state through another's refresh."""

    def __init__(self, dsn: str, stream: str = "research-stream") -> None:
        super().__init__(PostgresEventStore(dsn), stream)

        self._refresh_lock = asyncio.Lock()

        # These dicts are mutated in place (never rebound) by
        # _refresh_lookup_dicts, so the closures below -- captured once here
        # and handed to the projection catalogs -- always see current data.
        self._counts_by_source: dict[str, int] = {}
        self._refuters_by_target: dict[str, list[str]] = {}
        self._superseders_by_target: dict[str, list[str]] = {}
        self._claims_by_id: dict[str, dict] = {}
        self._corroborators_by_claim: dict[str, list[str]] = {}

        source_coverage = build_source_coverage_catalog(
            lambda source_id: self._counts_by_source[source_id]
        )
        contradiction_map = build_contradiction_map_catalog(
            lambda target_claim_id: self._refuters_by_target[target_claim_id]
        )
        claim_dependency_graph = build_claim_dependency_catalog(
            lambda target_claim_id: self._superseders_by_target[target_claim_id]
        )

        self.register_projection(source_coverage, "source_coverage", {"claim.proposed"})
        self.register_projection(contradiction_map, "contradiction_map", {"claim.refuted"})
        self.register_projection(
            claim_dependency_graph, "claim_dependency_graph", {"claim.corrected"}
        )

    # --- read accessors (plain reads of the last-refreshed state) ---------

    def list_claims(self) -> list[dict]:
        return list(self._claims_by_id.values())

    def get_claim(self, claim_id: str) -> dict | None:
        return self._claims_by_id.get(claim_id)

    def claimed_source_ids(self) -> set[str]:
        return set(self._counts_by_source)

    def corroborators_for(self, claim_id: str) -> list[str]:
        return list(self._corroborators_by_claim.get(claim_id, []))

    def refuters_for(self, claim_id: str) -> list[str]:
        return list(self._refuters_by_target.get(claim_id, []))

    def superseders_for(self, claim_id: str) -> list[str]:
        return list(self._superseders_by_target.get(claim_id, []))

    def contradiction_targets(self) -> list[str]:
        return list(self._refuters_by_target)

    # --- refresh / mutation paths -----------------------------------------

    async def _refresh_lookup_dicts(self) -> None:
        events = await self._event_store.read_stream(self._stream)
        self._counts_by_source.clear()
        self._refuters_by_target.clear()
        self._superseders_by_target.clear()
        self._claims_by_id.clear()
        self._corroborators_by_claim.clear()
        for event in events:
            payload = event["payload"]
            if event["event_type"] == "claim.proposed":
                source_id = payload["source_id"]
                self._counts_by_source[source_id] = self._counts_by_source.get(source_id, 0) + 1
                self._claims_by_id[payload["claim_id"]] = {
                    "claim_id": payload["claim_id"],
                    "source_id": source_id,
                    "text": payload["text"],
                }
            elif event["event_type"] == "source.corroborated":
                self._corroborators_by_claim.setdefault(payload["claim_id"], []).append(
                    payload["source_id"]
                )
            elif event["event_type"] == "claim.refuted":
                target_id = payload["target_claim_id"]
                self._refuters_by_target.setdefault(target_id, []).append(payload["claim_id"])
            elif event["event_type"] == "claim.corrected":
                target_id = payload["target_claim_id"]
                self._superseders_by_target.setdefault(target_id, []).append(payload["claim_id"])

    async def _catch_up_inner(self) -> None:
        await self._refresh_lookup_dicts()
        await super().catch_up()

    async def catch_up(self) -> None:
        async with self._refresh_lock:
            await self._catch_up_inner()

    async def append_event(self, event_type: str, payload: dict) -> None:
        async with self._refresh_lock:
            await self.append(event_type, payload)
            await self._catch_up_inner()

    async def append_events(self, events: list[tuple[str, dict]]) -> None:
        """Batch append + one catch_up: the agent commit path — several
        events land atomically-enough (one refresh) under the lock."""
        async with self._refresh_lock:
            for event_type, payload in events:
                await self.append(event_type, payload)
            await self._catch_up_inner()
```

Note the ordering trap this preserves: `claim.proposed` for the same `claim_id` twice would overwrite in `_claims_by_id` — acceptable; claim_ids are minted uuid4 at commit time (Task 8) so collisions don't occur in practice.

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/research_domain/ -W error -q`
Expected: new tests PASS (or SKIP without docker — if skipped, run `uv run pytest tests/research_domain/test_runtime_registries.py -W error -q -rs` and confirm the only skips are docker-related); pre-existing research_domain tests still PASS.

- [ ] **Step 5: Commit**

```bash
git add research_domain/runtime.py tests/research_domain/test_runtime_registries.py
git commit -m "feat(research-domain): claims/corroborator registries, batch append, runtime lock

Read accessors for agents and tools; append_events batches events under
one refresh; asyncio.Lock serializes catch_up/append across the
scheduler's dispatch pool.

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
```

---

### Task 6: research_domain corpus reader

**Files:**
- Create: `research_domain/corpus.py`
- Test: `tests/research_domain/test_corpus.py`

**Interfaces:**
- Consumes: nothing.
- Produces (used by Tasks 7–11): `research_domain.corpus.CorpusReader(root: Path | str)` with `list_documents() -> list[str]` (sorted posix relative paths, `.md`/`.txt` only, hidden dirs/files skipped; the relative path IS the source_id) and `read_document(source_id: str) -> str` (raises `FileNotFoundError` for missing docs).

- [ ] **Step 1: Write the failing tests**

Create `tests/research_domain/test_corpus.py`:

```python
from __future__ import annotations
from pathlib import Path

import pytest

from research_domain.corpus import CorpusReader


def _make_corpus(root: Path) -> None:
    (root / "notes").mkdir()
    (root / ".hidden").mkdir()
    (root / "a.md").write_text("alpha claims", encoding="utf-8")
    (root / "notes" / "b.txt").write_text("beta claims", encoding="utf-8")
    (root / "notes" / "c.py").write_text("not a document", encoding="utf-8")
    (root / ".hidden" / "d.md").write_text("hidden", encoding="utf-8")
    (root / ".dotfile.md").write_text("hidden file", encoding="utf-8")


def test_lists_only_md_and_txt_sorted_skipping_hidden(tmp_path):
    _make_corpus(tmp_path)
    reader = CorpusReader(tmp_path)
    assert reader.list_documents() == ["a.md", "notes/b.txt"]


def test_source_ids_are_stable_across_calls(tmp_path):
    _make_corpus(tmp_path)
    reader = CorpusReader(tmp_path)
    assert reader.list_documents() == reader.list_documents()


def test_read_document_by_source_id(tmp_path):
    _make_corpus(tmp_path)
    reader = CorpusReader(tmp_path)
    assert reader.read_document("notes/b.txt") == "beta claims"


def test_read_missing_document_raises(tmp_path):
    _make_corpus(tmp_path)
    reader = CorpusReader(tmp_path)
    with pytest.raises(FileNotFoundError):
        reader.read_document("nope.md")


def test_empty_corpus_lists_nothing(tmp_path):
    assert CorpusReader(tmp_path).list_documents() == []
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/research_domain/test_corpus.py -W error -q`
Expected: `ModuleNotFoundError: No module named 'research_domain.corpus'`.

- [ ] **Step 3: Implement `research_domain/corpus.py`**

```python
from __future__ import annotations

from pathlib import Path

_DOC_SUFFIXES = {".md", ".txt"}


class CorpusReader:
    """Filesystem document corpus: a directory of .md/.txt files. The posix
    relative path of each file is its source_id — stable, human-readable,
    and exactly what claim.proposed payloads carry."""

    def __init__(self, root: Path | str) -> None:
        self._root = Path(root)

    def list_documents(self) -> list[str]:
        docs: list[str] = []
        for path in sorted(self._root.rglob("*")):
            if not path.is_file() or path.suffix.lower() not in _DOC_SUFFIXES:
                continue
            rel = path.relative_to(self._root)
            if any(part.startswith(".") for part in rel.parts):
                continue
            docs.append(rel.as_posix())
        return docs

    def read_document(self, source_id: str) -> str:
        return (self._root / source_id).read_text(encoding="utf-8")
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/research_domain/test_corpus.py -W error -q`
Expected: all PASS.

- [ ] **Step 5: Commit**

```bash
git add research_domain/corpus.py tests/research_domain/test_corpus.py
git commit -m "feat(research-domain): filesystem CorpusReader (relative path = source_id)

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
```

---

### Task 7: research_domain schemas + read-only tools

**Files:**
- Create: `research_domain/schemas.py`
- Create: `research_domain/tools.py`
- Test: `tests/research_domain/test_schemas.py`
- Test: `tests/research_domain/test_tools.py`

**Interfaces:**
- Consumes: `CorpusReader` (Task 6); `ResearchRuntime.list_claims()/get_claim()` (Task 5).
- Produces (used by Tasks 8–11):
  - `research_domain.schemas`: `ClaimDraft(text: str)`; `ExtractorOutput(claims: list[ClaimDraft])`; `RefutationDraft(source_id: str, counter_text: str, reason: str)`; `VerificationDraft(claim_id: str, corroborating_source_ids: list[str] = [], refutation: RefutationDraft | None = None)`; `VerifierOutput(verdicts: list[VerificationDraft])`; `CorrectionDraft(superseding_claim_id: str, target_claim_id: str, reason: str)`; `RetractorOutput(corrections: list[CorrectionDraft])` — all pydantic BaseModel with `default_factory=list` for list fields.
  - `research_domain.tools.make_corpus_tools(reader) -> list` (`list_documents`, `read_document` langchain tools) and `make_claim_tools(runtime) -> list` (`list_claims`, `get_claim` langchain tools). All read-only, all returning strings.

- [ ] **Step 1: Write the failing tests**

Create `tests/research_domain/test_schemas.py`:

```python
from __future__ import annotations

from research_domain.schemas import (
    ClaimDraft,
    CorrectionDraft,
    ExtractorOutput,
    RefutationDraft,
    RetractorOutput,
    VerificationDraft,
    VerifierOutput,
)


def test_extractor_output_defaults_empty():
    assert ExtractorOutput().claims == []
    out = ExtractorOutput(claims=[ClaimDraft(text="water boils at 100C")])
    assert out.claims[0].text == "water boils at 100C"


def test_verification_draft_defaults():
    v = VerificationDraft(claim_id="c1")
    assert v.corroborating_source_ids == []
    assert v.refutation is None
    withref = VerificationDraft(
        claim_id="c1",
        refutation=RefutationDraft(source_id="b.md", counter_text="no", reason="contradicts"),
    )
    assert withref.refutation.source_id == "b.md"
    assert VerifierOutput().verdicts == []


def test_retractor_output_roundtrip():
    out = RetractorOutput(corrections=[
        CorrectionDraft(superseding_claim_id="c2", target_claim_id="c1", reason="newer data")])
    assert out.corrections[0].target_claim_id == "c1"
    assert RetractorOutput().corrections == []
```

Create `tests/research_domain/test_tools.py`:

```python
from __future__ import annotations

from research_domain.corpus import CorpusReader
from research_domain.tools import make_claim_tools, make_corpus_tools


class StubRuntime:
    def __init__(self, claims):
        self._claims = claims
    def list_claims(self):
        return list(self._claims)
    def get_claim(self, claim_id):
        return next((c for c in self._claims if c["claim_id"] == claim_id), None)


def _tool_by_name(tools, name):
    return next(t for t in tools if t.name == name)


async def test_corpus_tools_list_and_read(tmp_path):
    (tmp_path / "a.md").write_text("alpha text", encoding="utf-8")
    tools = make_corpus_tools(CorpusReader(tmp_path))
    listed = await _tool_by_name(tools, "list_documents").ainvoke({})
    assert "a.md" in listed
    content = await _tool_by_name(tools, "read_document").ainvoke({"source_id": "a.md"})
    assert content == "alpha text"


async def test_read_document_missing_returns_message_not_raise(tmp_path):
    tools = make_corpus_tools(CorpusReader(tmp_path))
    out = await _tool_by_name(tools, "read_document").ainvoke({"source_id": "nope.md"})
    assert "no such document" in out


async def test_claim_tools_list_and_get():
    rt = StubRuntime([{"claim_id": "c1", "source_id": "a.md", "text": "sky is blue"}])
    tools = make_claim_tools(rt)
    listed = await _tool_by_name(tools, "list_claims").ainvoke({})
    assert "c1" in listed and "sky is blue" in listed
    got = await _tool_by_name(tools, "get_claim").ainvoke({"claim_id": "c1"})
    assert "a.md" in got
    missing = await _tool_by_name(tools, "get_claim").ainvoke({"claim_id": "zz"})
    assert "no such claim" in missing


async def test_empty_states_render_placeholders(tmp_path):
    corpus_tools = make_corpus_tools(CorpusReader(tmp_path))
    assert "empty" in await _tool_by_name(corpus_tools, "list_documents").ainvoke({})
    claim_tools = make_claim_tools(StubRuntime([]))
    assert "no claims" in await _tool_by_name(claim_tools, "list_claims").ainvoke({})
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/research_domain/test_schemas.py tests/research_domain/test_tools.py -W error -q`
Expected: `ModuleNotFoundError` for both modules.

- [ ] **Step 3: Implement**

Create `research_domain/schemas.py`:

```python
"""Structured outputs for the research agents. IDs are never minted by the
LLM: ClaimDraft carries only text; claim_ids are uuid4-minted at commit
time by the agent, and Verifier/Retractor reference existing ids they saw
via tools or the prompt."""
from __future__ import annotations

from pydantic import BaseModel, Field


class ClaimDraft(BaseModel):
    text: str


class ExtractorOutput(BaseModel):
    claims: list[ClaimDraft] = Field(default_factory=list)


class RefutationDraft(BaseModel):
    source_id: str
    counter_text: str
    reason: str


class VerificationDraft(BaseModel):
    claim_id: str
    corroborating_source_ids: list[str] = Field(default_factory=list)
    refutation: RefutationDraft | None = None


class VerifierOutput(BaseModel):
    verdicts: list[VerificationDraft] = Field(default_factory=list)


class CorrectionDraft(BaseModel):
    superseding_claim_id: str
    target_claim_id: str
    reason: str


class RetractorOutput(BaseModel):
    corrections: list[CorrectionDraft] = Field(default_factory=list)
```

Create `research_domain/tools.py`:

```python
"""Read-only langchain tools over the corpus and the runtime's claim state.

Writes never happen through tools: structured output carries proposals,
and the agent's commit validates and appends events (the same intent
pattern novelizer's agents use)."""
from __future__ import annotations

from langchain_core.tools import tool

from research_domain.corpus import CorpusReader


def make_corpus_tools(reader: CorpusReader) -> list:
    @tool("list_documents")
    def list_documents_tool() -> str:
        """List every document source_id in the research corpus."""
        docs = reader.list_documents()
        return "\n".join(docs) if docs else "(corpus is empty)"

    @tool("read_document")
    def read_document_tool(source_id: str) -> str:
        """Read a corpus document's full text by its source_id."""
        try:
            return reader.read_document(source_id)
        except (FileNotFoundError, OSError):
            return f"(no such document: {source_id})"

    return [list_documents_tool, read_document_tool]


def make_claim_tools(runtime) -> list:
    @tool("list_claims")
    def list_claims_tool() -> str:
        """List all current claims as `claim_id [source_id]: text` lines."""
        claims = runtime.list_claims()
        if not claims:
            return "(no claims yet)"
        return "\n".join(
            f"{c['claim_id']} [{c['source_id']}]: {c['text']}" for c in claims
        )

    @tool("get_claim")
    def get_claim_tool(claim_id: str) -> str:
        """Get one claim by its claim_id."""
        c = runtime.get_claim(claim_id)
        if c is None:
            return f"(no such claim: {claim_id})"
        return f"{c['claim_id']} [{c['source_id']}]: {c['text']}"

    return [list_claims_tool, get_claim_tool]
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/research_domain/test_schemas.py tests/research_domain/test_tools.py -W error -q`
Expected: all PASS.

- [ ] **Step 5: Commit**

```bash
git add research_domain/schemas.py research_domain/tools.py tests/research_domain/test_schemas.py tests/research_domain/test_tools.py
git commit -m "feat(research-domain): structured-output schemas + read-only corpus/claim tools

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
```

---

### Task 8: ExtractorAgent

**Files:**
- Create: `research_domain/agents.py`
- Test: `tests/research_domain/test_extractor_agent.py`

**Interfaces:**
- Consumes: `agent_kit.BaseAgent`; `ResearchRuntime` accessors + `append_events` (Task 5); `CorpusReader` (Task 6); `ExtractorOutput`/`ClaimDraft` (Task 7).
- Produces (used by Tasks 10, 11):
  - `research_domain.agents.ExtractorAgent(runner, runtime, corpus, interval: int = 60, personality: str = "")` — BaseAgent subclass, `name = "extractor"`.
  - `research_domain.agents.StructuredResponseError(RuntimeError)` — raised when a runner returns no structured response.
  - Module helper `_normalize(text: str) -> str` (lowercased, whitespace-collapsed) used for dedup.
  - Prompt convention consumed by tests and fakes: the extractor's user message contains the line `SOURCE_ID: {source_id}` followed by the document text.
  - **The fruitless-set pattern (all three agents use it):** each agent keeps an in-memory set of items it examined that yielded no events (`_fruitless` docs for the extractor; `_inconclusive` claims for the verifier; `_stood` targets for the retractor). The workable queue is `pending minus fruitless`; readiness is `score` when the workable queue is non-empty, else `0.0`. This avoids head-of-line blocking: a fruitless item at the queue head can never gate items behind it (which whole-set watermark gating would cause). The sets are in-memory only — a process restart re-examines fruitless items, which is safe because a fruitless run commits nothing (idempotent). The kit's watermark machinery is NOT used by these agents; it remains a kit feature (tested in Task 1, used by novelizer).

- [ ] **Step 1: Write the failing tests**

Create `tests/research_domain/test_extractor_agent.py`:

```python
from __future__ import annotations

from research_domain.agents import ExtractorAgent
from research_domain.corpus import CorpusReader
from research_domain.runtime import ResearchRuntime
from research_domain.schemas import ClaimDraft, ExtractorOutput

from tests.substrate.postgres_fixture import postgres_dsn  # noqa: F401


class FakeExtractorRunner:
    """Returns scripted claims per source_id, parsed from the prompt's
    SOURCE_ID line (the prompt convention the real runner also sees)."""

    def __init__(self, claims_by_source: dict[str, list[str]]):
        self._by_source = claims_by_source
        self.calls: list[str] = []

    async def ainvoke(self, inputs: dict) -> dict:
        prompt = inputs["messages"][0]["content"]
        source_id = next(
            line.split("SOURCE_ID:", 1)[1].strip()
            for line in prompt.splitlines() if line.startswith("SOURCE_ID:")
        )
        self.calls.append(source_id)
        return {"structured_response": ExtractorOutput(
            claims=[ClaimDraft(text=t) for t in self._by_source.get(source_id, [])])}


def _corpus(tmp_path):
    (tmp_path / "a.md").write_text("water boils at 100C at sea level", encoding="utf-8")
    (tmp_path / "b.md").write_text("water boils at 90C everywhere", encoding="utf-8")
    return CorpusReader(tmp_path)


async def test_readiness_zero_when_no_pending_docs(tmp_path, postgres_dsn):
    runtime = ResearchRuntime(postgres_dsn, stream="ext-idle-stream")
    await runtime.connect()
    try:
        await runtime.catch_up()
        agent = ExtractorAgent(FakeExtractorRunner({}), runtime, CorpusReader(tmp_path))
        assert await agent.readiness() == 0.0
    finally:
        await runtime.close()


async def test_extracts_one_doc_per_run_and_commits_claims(tmp_path, postgres_dsn):
    runtime = ResearchRuntime(postgres_dsn, stream="ext-commit-stream")
    await runtime.connect()
    try:
        await runtime.catch_up()
        corpus = _corpus(tmp_path)
        runner = FakeExtractorRunner({
            "a.md": ["water boils at 100C at sea level"],
            "b.md": ["water boils at 90C everywhere"],
        })
        agent = ExtractorAgent(runner, runtime, corpus)
        assert await agent.readiness() == 0.7

        await agent.run_once()
        assert runner.calls == ["a.md"]  # one doc per run, oldest-sorted first
        claims = runtime.list_claims()
        assert len(claims) == 1 and claims[0]["source_id"] == "a.md"
        assert claims[0]["claim_id"]  # minted, non-empty

        await agent.run_once()
        assert runner.calls == ["a.md", "b.md"]
        assert runtime.claimed_source_ids() == {"a.md", "b.md"}
        # backlog drained -> readiness gated to 0
        assert await agent.readiness() == 0.0
    finally:
        await runtime.close()


async def test_zero_claim_doc_goes_fruitless_without_blocking_new_docs(tmp_path, postgres_dsn):
    runtime = ResearchRuntime(postgres_dsn, stream="ext-zero-stream")
    await runtime.connect()
    try:
        await runtime.catch_up()
        (tmp_path / "empty.md").write_text("nothing factual here", encoding="utf-8")
        runner = FakeExtractorRunner({"empty.md": [], "new.md": ["fresh fact"]})
        agent = ExtractorAgent(runner, runtime, CorpusReader(tmp_path))
        await agent.run_once()
        assert runtime.list_claims() == []
        # doc examined and fruitless -> workable queue empty -> idle
        assert await agent.readiness() == 0.0
        # a new doc becomes workable immediately...
        (tmp_path / "new.md").write_text("fresh fact", encoding="utf-8")
        assert await agent.readiness() == 0.7
        # ...and the next run processes new.md, NOT empty.md again
        await agent.run_once()
        assert runner.calls == ["empty.md", "new.md"]
        assert runtime.claimed_source_ids() == {"new.md"}
    finally:
        await runtime.close()


async def test_dedups_duplicate_claim_texts_within_one_output(tmp_path, postgres_dsn):
    runtime = ResearchRuntime(postgres_dsn, stream="ext-dedup-stream")
    await runtime.connect()
    try:
        await runtime.catch_up()
        (tmp_path / "a.md").write_text("stuff", encoding="utf-8")
        # runner proposes the same claim twice (case/spacing variants) plus one distinct
        runner = FakeExtractorRunner({"a.md": [
            "Water boils at 100C at sea level",
            "water  boils at 100c AT SEA LEVEL",
            "salt raises the boiling point",
        ]})
        agent = ExtractorAgent(runner, runtime, CorpusReader(tmp_path))
        await agent.run_once()
        texts = sorted(c["text"] for c in runtime.list_claims())
        assert len(texts) == 2  # normalized duplicate collapsed
        assert "salt raises the boiling point" in texts
    finally:
        await runtime.close()
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/research_domain/test_extractor_agent.py -W error -q`
Expected: `ModuleNotFoundError: No module named 'research_domain.agents'` (or docker skip; implementation still required).

- [ ] **Step 3: Implement `research_domain/agents.py` (extractor only; verifier/retractor come in Task 9)**

```python
"""The three live research agents: extractor, verifier, retractor.

Each subclasses agent_kit.BaseAgent: poll (read runtime + corpus state),
work (one LLM call via the injected runner), commit (validate structured
output, append events via runtime.append_events).

Quiet-when-done uses the fruitless-set pattern, not watermark gating: an
examined item that yielded no events joins an in-memory set subtracted
from the workable queue, so it can never head-of-line-block items behind
it. The sets are process-local; a restart re-examines fruitless items,
which is safe because fruitless runs commit nothing."""
from __future__ import annotations

import uuid

from agent_kit import BaseAgent

from research_domain.corpus import CorpusReader
from research_domain.runtime import ResearchRuntime
from research_domain.schemas import ExtractorOutput


class StructuredResponseError(RuntimeError):
    """The runner returned no structured response."""


def _normalize(text: str) -> str:
    return " ".join(text.lower().split())


def _structured(result: dict, model_cls):
    raw = result.get("structured_response")
    if raw is None:
        raise StructuredResponseError(f"runner returned no {model_cls.__name__}")
    if isinstance(raw, model_cls):
        return raw
    return model_cls.model_validate(raw)


EXTRACTOR_PROMPT = """Extract every distinct factual claim from the document below.
A claim is a single, checkable assertion — not an opinion, not a heading.
Return each claim as its own entry, phrased as one standalone sentence.

SOURCE_ID: {source_id}

DOCUMENT:
{text}"""


class ExtractorAgent(BaseAgent):
    name = "extractor"

    def __init__(
        self,
        runner,
        runtime: ResearchRuntime,
        corpus: CorpusReader,
        interval: int = 60,
        personality: str = "",
    ) -> None:
        super().__init__(runner, interval, name="extractor", personality=personality)
        self._runtime = runtime
        self._corpus = corpus
        # Docs examined that yielded zero claims. In-memory by design: a
        # restart re-examines them, which commits nothing (idempotent).
        self._fruitless: set[str] = set()

    def _workable(self) -> list[str]:
        claimed = self._runtime.claimed_source_ids()
        return [
            d for d in self._corpus.list_documents()
            if d not in claimed and d not in self._fruitless
        ]

    async def readiness(self) -> float:
        return 0.7 if self._workable() else 0.0

    async def _run(self) -> None:
        workable = self._workable()
        if not workable:
            return
        source_id = workable[0]
        text = self._corpus.read_document(source_id)
        prompt = EXTRACTOR_PROMPT.format(source_id=source_id, text=text)
        result = await self._runner.ainvoke(
            {"messages": [{"role": "user", "content": prompt}]}
        )
        output = _structured(result, ExtractorOutput)

        existing = {
            (c["source_id"], _normalize(c["text"])) for c in self._runtime.list_claims()
        }
        events: list[tuple[str, dict]] = []
        for draft in output.claims:
            key = (source_id, _normalize(draft.text))
            if key in existing:
                continue
            existing.add(key)
            events.append((
                "claim.proposed",
                {"claim_id": uuid.uuid4().hex, "source_id": source_id, "text": draft.text},
            ))
        if events:
            await self._runtime.append_events(events)
        else:
            self._fruitless.add(source_id)
            self.note_pass()
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/research_domain/test_extractor_agent.py -W error -q && uv run lint-imports`
Expected: PASS (or docker skips only); contracts KEPT.

- [ ] **Step 5: Commit**

```bash
git add research_domain/agents.py tests/research_domain/test_extractor_agent.py
git commit -m "feat(research-domain): ExtractorAgent — first live agent on agent_kit

One doc per run, commit-time text dedup, minted claim_ids; fruitless-set
idling keeps zero-claim docs from blocking or re-running.

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
```

---

### Task 9: VerifierAgent + RetractorAgent + roles de-stub

**Files:**
- Modify: `research_domain/agents.py` (append the two classes + prompts)
- Modify: `research_domain/roles.py`
- Test: `tests/research_domain/test_verifier_agent.py`
- Test: `tests/research_domain/test_retractor_agent.py`
- Test: `tests/research_domain/test_roles.py` (existing — extend, don't rewrite: keep existing stub assertions for the three still-stubbed roles)

**Interfaces:**
- Consumes: everything Task 8 produced; `VerifierOutput`/`RetractorOutput` etc. (Task 7); runtime accessors (Task 5).
- Produces (used by Tasks 10, 11):
  - `research_domain.agents.VerifierAgent(runner, runtime, corpus, interval: int = 60, personality: str = "")`, `name = "verifier"`, readiness 0.6. Prompt contains `CLAIM_ID: {claim_id}` line.
  - `research_domain.agents.RetractorAgent(runner, runtime, interval: int = 60, personality: str = "")`, `name = "retractor"`, readiness 0.5. Prompt contains `TARGET_CLAIM_ID: {claim_id}` line.
  - `research_domain.roles.build_live_agents(runtime, corpus, runner_factory) -> list` where `runner_factory(role_name: str) -> runner`; returns `[ExtractorAgent, VerifierAgent, RetractorAgent]`.

- [ ] **Step 1: Write the failing tests**

Create `tests/research_domain/test_verifier_agent.py`:

```python
from __future__ import annotations

from research_domain.agents import VerifierAgent
from research_domain.corpus import CorpusReader
from research_domain.runtime import ResearchRuntime
from research_domain.schemas import RefutationDraft, VerificationDraft, VerifierOutput

from tests.substrate.postgres_fixture import postgres_dsn  # noqa: F401


class FakeVerifierRunner:
    """Scripted verdict per claim_id, parsed from the prompt's CLAIM_ID line."""

    def __init__(self, verdicts: dict[str, VerificationDraft]):
        self._verdicts = verdicts
        self.calls: list[str] = []

    async def ainvoke(self, inputs: dict) -> dict:
        prompt = inputs["messages"][0]["content"]
        claim_id = next(
            line.split("CLAIM_ID:", 1)[1].strip()
            for line in prompt.splitlines() if line.startswith("CLAIM_ID:")
        )
        self.calls.append(claim_id)
        verdict = self._verdicts.get(claim_id, VerificationDraft(claim_id=claim_id))
        return {"structured_response": VerifierOutput(verdicts=[verdict])}


async def _seed(runtime):
    await runtime.append_events([
        ("claim.proposed", {"claim_id": "c1", "source_id": "a.md", "text": "boils at 100C"}),
    ])


async def test_corroboration_appends_and_dedups(tmp_path, postgres_dsn):
    (tmp_path / "a.md").write_text("x", encoding="utf-8")
    (tmp_path / "b.md").write_text("y", encoding="utf-8")
    runtime = ResearchRuntime(postgres_dsn, stream="ver-corr-stream")
    await runtime.connect()
    try:
        await _seed(runtime)
        runner = FakeVerifierRunner({"c1": VerificationDraft(
            claim_id="c1",
            corroborating_source_ids=["b.md", "b.md", "a.md"])})  # dup + self
        agent = VerifierAgent(runner, runtime, CorpusReader(tmp_path))
        assert await agent.readiness() == 0.6
        await agent.run_once()
        # self-corroboration (a.md) skipped, duplicate collapsed
        assert runtime.corroborators_for("c1") == ["b.md"]
        # verified claim leaves the pending set
        assert await agent.readiness() == 0.0
    finally:
        await runtime.close()


async def test_refutation_mints_counter_claim_and_refuted_event(tmp_path, postgres_dsn):
    (tmp_path / "a.md").write_text("x", encoding="utf-8")
    (tmp_path / "b.md").write_text("y", encoding="utf-8")
    runtime = ResearchRuntime(postgres_dsn, stream="ver-refute-stream")
    await runtime.connect()
    try:
        await _seed(runtime)
        runner = FakeVerifierRunner({"c1": VerificationDraft(
            claim_id="c1",
            refutation=RefutationDraft(source_id="b.md", counter_text="boils at 90C",
                                       reason="direct contradiction"))})
        agent = VerifierAgent(runner, runtime, CorpusReader(tmp_path))
        await agent.run_once()
        refuters = runtime.refuters_for("c1")
        assert len(refuters) == 1
        counter = runtime.get_claim(refuters[0])
        assert counter["source_id"] == "b.md" and counter["text"] == "boils at 90C"
        assert runtime.contradiction_targets() == ["c1"]
    finally:
        await runtime.close()


async def test_inconclusive_verdict_idles_without_blocking_later_claims(tmp_path, postgres_dsn):
    (tmp_path / "a.md").write_text("x", encoding="utf-8")
    (tmp_path / "b.md").write_text("y", encoding="utf-8")
    runtime = ResearchRuntime(postgres_dsn, stream="ver-inconclusive-stream")
    await runtime.connect()
    try:
        await _seed(runtime)
        runner = FakeVerifierRunner({"c2": VerificationDraft(
            claim_id="c2", corroborating_source_ids=["a.md"])})
        agent = VerifierAgent(runner, runtime, CorpusReader(tmp_path))
        await agent.run_once()  # c1 -> empty verdict -> inconclusive
        assert runtime.corroborators_for("c1") == []
        assert await agent.readiness() == 0.0  # inconclusive set drains workable queue
        # a later claim is NOT blocked behind the inconclusive one
        await runtime.append_event(
            "claim.proposed", {"claim_id": "c2", "source_id": "b.md", "text": "later"})
        assert await agent.readiness() == 0.6
        await agent.run_once()
        assert runner.calls == ["c1", "c2"]
        assert runtime.corroborators_for("c2") == ["a.md"]
    finally:
        await runtime.close()
```

Create `tests/research_domain/test_retractor_agent.py`:

```python
from __future__ import annotations

from research_domain.agents import RetractorAgent
from research_domain.runtime import ResearchRuntime
from research_domain.schemas import CorrectionDraft, RetractorOutput

from tests.substrate.postgres_fixture import postgres_dsn  # noqa: F401


class FakeRetractorRunner:
    def __init__(self, corrections: dict[str, CorrectionDraft | None]):
        self._corrections = corrections
        self.calls: list[str] = []

    async def ainvoke(self, inputs: dict) -> dict:
        prompt = inputs["messages"][0]["content"]
        target = next(
            line.split("TARGET_CLAIM_ID:", 1)[1].strip()
            for line in prompt.splitlines() if line.startswith("TARGET_CLAIM_ID:")
        )
        self.calls.append(target)
        c = self._corrections.get(target)
        return {"structured_response": RetractorOutput(corrections=[c] if c else [])}


async def _seed_contradiction(runtime):
    await runtime.append_events([
        ("claim.proposed", {"claim_id": "c1", "source_id": "a.md", "text": "100C"}),
        ("claim.proposed", {"claim_id": "c2", "source_id": "b.md", "text": "90C"}),
        ("claim.refuted", {"claim_id": "c2", "target_claim_id": "c1", "reason": "differs"}),
    ])


async def test_correction_appended_for_valid_superseder(postgres_dsn):
    runtime = ResearchRuntime(postgres_dsn, stream="ret-valid-stream")
    await runtime.connect()
    try:
        await _seed_contradiction(runtime)
        runner = FakeRetractorRunner({"c1": CorrectionDraft(
            superseding_claim_id="c2", target_claim_id="c1", reason="b.md is newer")})
        agent = RetractorAgent(runner, runtime)
        assert await agent.readiness() == 0.5
        await agent.run_once()
        assert runtime.superseders_for("c1") == ["c2"]
        assert runtime.get_projection("claim_dependency_graph") == {"c1": ["c2"]}
        # resolved contradiction leaves pending -> readiness 0
        assert await agent.readiness() == 0.0
    finally:
        await runtime.close()


async def test_invalid_superseder_rejected(postgres_dsn):
    runtime = ResearchRuntime(postgres_dsn, stream="ret-invalid-stream")
    await runtime.connect()
    try:
        await _seed_contradiction(runtime)
        # c9 is not a refuter of c1 -> must be dropped at commit validation
        runner = FakeRetractorRunner({"c1": CorrectionDraft(
            superseding_claim_id="c9", target_claim_id="c1", reason="bogus")})
        agent = RetractorAgent(runner, runtime)
        await agent.run_once()
        assert runtime.superseders_for("c1") == []
        assert await agent.readiness() == 0.0  # fruitless run -> target leaves workable queue
    finally:
        await runtime.close()


async def test_original_stands_verdict_idles_without_blocking_later_targets(postgres_dsn):
    runtime = ResearchRuntime(postgres_dsn, stream="ret-stands-stream")
    await runtime.connect()
    try:
        await _seed_contradiction(runtime)
        runner = FakeRetractorRunner({
            "c1": None,  # model says original stands
            "c3": CorrectionDraft(
                superseding_claim_id="c4", target_claim_id="c3", reason="newer"),
        })
        agent = RetractorAgent(runner, runtime)
        await agent.run_once()
        assert runtime.superseders_for("c1") == []
        assert await agent.readiness() == 0.0  # stood target leaves workable queue
        # a later contradiction is NOT blocked behind the stood one
        await runtime.append_events([
            ("claim.proposed", {"claim_id": "c3", "source_id": "c.md", "text": "80C"}),
            ("claim.proposed", {"claim_id": "c4", "source_id": "d.md", "text": "85C"}),
            ("claim.refuted", {"claim_id": "c4", "target_claim_id": "c3", "reason": "x"}),
        ])
        assert await agent.readiness() == 0.5
        await agent.run_once()
        assert runner.calls == ["c1", "c3"]
        assert runtime.superseders_for("c3") == ["c4"]
    finally:
        await runtime.close()
```

Extend `tests/research_domain/test_roles.py` — add (keeping existing tests):

```python
from research_domain.agents import ExtractorAgent, RetractorAgent, VerifierAgent
from research_domain.roles import build_live_agents


class _NullRunner:
    async def ainvoke(self, inputs: dict) -> dict:
        return {}


def test_build_live_agents_returns_wired_trio(tmp_path):
    class StubRuntime:  # construction wiring only, no I/O
        pass
    agents = build_live_agents(StubRuntime(), object(), lambda role: _NullRunner())
    assert [type(a) for a in agents] == [ExtractorAgent, VerifierAgent, RetractorAgent]
    assert [a.name for a in agents] == ["extractor", "verifier", "retractor"]
```

(If the existing `test_roles.py` asserts all six roles are stubs, update only those assertions to reflect that scout/synthesizer/coverage_analyst remain stubs; the trio's `AgentSpec.construct` entries now point at real constructors — see Step 3.)

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/research_domain/test_verifier_agent.py tests/research_domain/test_retractor_agent.py tests/research_domain/test_roles.py -W error -q`
Expected: ImportError for `VerifierAgent` / `build_live_agents`.

- [ ] **Step 3: Implement**

Append to `research_domain/agents.py` (after ExtractorAgent):

```python
from research_domain.schemas import RetractorOutput, VerifierOutput  # move to top imports


VERIFIER_PROMPT = """Verify the following claim against the rest of the corpus.
Use the tools to read other documents. For the claim below, report:
- corroborating_source_ids: other documents that independently support it
  (never the claim's own source).
- refutation: if some document contradicts it, give that document's
  source_id, a one-sentence counter_text stating what that document
  asserts instead, and a short reason.
If the corpus neither supports nor contradicts it, return an empty verdict.

CLAIM_ID: {claim_id}
CLAIM_SOURCE: {source_id}
CLAIM: {text}

OTHER DOCUMENTS: {other_docs}"""


class VerifierAgent(BaseAgent):
    name = "verifier"

    def __init__(
        self,
        runner,
        runtime: ResearchRuntime,
        corpus: CorpusReader,
        interval: int = 60,
        personality: str = "",
    ) -> None:
        super().__init__(runner, interval, name="verifier", personality=personality)
        self._runtime = runtime
        self._corpus = corpus
        # Claims examined with an empty verdict (corpus is silent on them).
        # In-memory by design: a restart re-examines them, committing nothing.
        self._inconclusive: set[str] = set()

    def _workable(self) -> list[str]:
        return [
            c["claim_id"]
            for c in self._runtime.list_claims()
            if not self._runtime.corroborators_for(c["claim_id"])
            and not self._runtime.refuters_for(c["claim_id"])
            and c["claim_id"] not in self._inconclusive
        ]

    async def readiness(self) -> float:
        return 0.6 if self._workable() else 0.0

    async def _run(self) -> None:
        workable = self._workable()
        if not workable:
            return
        claim_id = workable[0]
        claim = self._runtime.get_claim(claim_id)
        other_docs = [d for d in self._corpus.list_documents() if d != claim["source_id"]]
        prompt = VERIFIER_PROMPT.format(
            claim_id=claim_id, source_id=claim["source_id"], text=claim["text"],
            other_docs=", ".join(other_docs) if other_docs else "(none)",
        )
        result = await self._runner.ainvoke(
            {"messages": [{"role": "user", "content": prompt}]}
        )
        output = _structured(result, VerifierOutput)

        events: list[tuple[str, dict]] = []
        for verdict in output.verdicts:
            if verdict.claim_id != claim_id:
                continue  # runner answered about something else; drop it
            already = set(self._runtime.corroborators_for(claim_id))
            for source_id in verdict.corroborating_source_ids:
                if source_id == claim["source_id"] or source_id in already:
                    continue
                already.add(source_id)
                events.append((
                    "source.corroborated",
                    {"source_id": source_id, "claim_id": claim_id},
                ))
            if verdict.refutation is not None:
                counter_id = uuid.uuid4().hex
                events.append((
                    "claim.proposed",
                    {"claim_id": counter_id,
                     "source_id": verdict.refutation.source_id,
                     "text": verdict.refutation.counter_text},
                ))
                events.append((
                    "claim.refuted",
                    {"claim_id": counter_id, "target_claim_id": claim_id,
                     "reason": verdict.refutation.reason},
                ))
        if events:
            await self._runtime.append_events(events)
        else:
            self._inconclusive.add(claim_id)
            self.note_pass()


RETRACTOR_PROMPT = """Two claims in the research log contradict each other.
Decide which claim should stand. If a refuting claim should supersede the
target, return a correction naming it; if the original target claim should
stand, return no corrections.

TARGET_CLAIM_ID: {claim_id}
TARGET_CLAIM: {text} (from {source_id})

REFUTING CLAIMS:
{refuters}"""


class RetractorAgent(BaseAgent):
    name = "retractor"

    def __init__(
        self,
        runner,
        runtime: ResearchRuntime,
        interval: int = 60,
        personality: str = "",
    ) -> None:
        super().__init__(runner, interval, name="retractor", personality=personality)
        self._runtime = runtime
        # Targets examined where the original claim stood (or the verdict was
        # invalid). In-memory by design: a restart re-examines, commits nothing.
        self._stood: set[str] = set()

    def _workable(self) -> list[str]:
        return [
            target
            for target in self._runtime.contradiction_targets()
            if not self._runtime.superseders_for(target)
            and target not in self._stood
        ]

    async def readiness(self) -> float:
        return 0.5 if self._workable() else 0.0

    async def _run(self) -> None:
        workable = self._workable()
        if not workable:
            return
        target_id = workable[0]
        target = self._runtime.get_claim(target_id) or {
            "claim_id": target_id, "source_id": "(unknown)", "text": "(unknown claim)"
        }
        refuter_lines = []
        for rid in self._runtime.refuters_for(target_id):
            rc = self._runtime.get_claim(rid)
            if rc:
                refuter_lines.append(f"- {rid}: {rc['text']} (from {rc['source_id']})")
            else:
                refuter_lines.append(f"- {rid}: (unknown claim)")
        prompt = RETRACTOR_PROMPT.format(
            claim_id=target_id, text=target["text"], source_id=target["source_id"],
            refuters="\n".join(refuter_lines),
        )
        result = await self._runner.ainvoke(
            {"messages": [{"role": "user", "content": prompt}]}
        )
        output = _structured(result, RetractorOutput)

        events: list[tuple[str, dict]] = []
        valid_refuters = set(self._runtime.refuters_for(target_id))
        for correction in output.corrections:
            if correction.target_claim_id != target_id:
                continue
            if correction.superseding_claim_id not in valid_refuters:
                continue  # commit-time validation: only an actual refuter may supersede
            if self._runtime.superseders_for(target_id):
                continue  # already superseded (recheck at commit)
            events.append((
                "claim.corrected",
                {"claim_id": correction.superseding_claim_id,
                 "target_claim_id": target_id,
                 "reason": correction.reason},
            ))
        if events:
            await self._runtime.append_events(events)
        else:
            self._stood.add(target_id)
            self.note_pass()
```

(Consolidate the `from research_domain.schemas import ...` imports at the top of the file: `ExtractorOutput, RetractorOutput, VerifierOutput`.)

Replace `research_domain/roles.py`:

```python
from __future__ import annotations
from substrate import AgentSpec

from research_domain.agents import ExtractorAgent, RetractorAgent, VerifierAgent


def _stub_construct(name: str):
    def _construct(ctx):
        return {"role": name, "context": ctx}
    return _construct


def build_live_agents(runtime, corpus, runner_factory) -> list:
    """Construct the live trio in scheduler order. runner_factory(role_name)
    returns a Runner for that role (real deepagents runner in the CLI,
    fakes in tests)."""
    return [
        ExtractorAgent(runner_factory("extractor"), runtime, corpus),
        VerifierAgent(runner_factory("verifier"), runtime, corpus),
        RetractorAgent(runner_factory("retractor"), runtime),
    ]


ROLE_REGISTRY: list[AgentSpec] = [
    AgentSpec(name="scout", tool_grant=None, construct=_stub_construct("scout")),
    AgentSpec(name="extractor", tool_grant=None, construct=ExtractorAgent),
    AgentSpec(name="verifier", tool_grant=None, construct=VerifierAgent),
    AgentSpec(name="retractor", tool_grant=None, construct=RetractorAgent),
    AgentSpec(name="synthesizer", tool_grant=None, construct=_stub_construct("synthesizer")),
    AgentSpec(name="coverage_analyst", tool_grant=None, construct=_stub_construct("coverage_analyst")),
]
```

(If existing `tests/research_domain/test_roles.py` asserts the trio's `construct` returns stub dicts, update those three assertions to check `ROLE_REGISTRY` entries for extractor/verifier/retractor have `construct is ExtractorAgent` etc.; scout/synthesizer/coverage_analyst stub assertions stay.)

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/research_domain/ -W error -q && uv run lint-imports`
Expected: PASS (docker-dependent tests may skip without docker); contracts KEPT.

- [ ] **Step 5: Commit**

```bash
git add research_domain/agents.py research_domain/roles.py tests/research_domain/test_verifier_agent.py tests/research_domain/test_retractor_agent.py tests/research_domain/test_roles.py
git commit -m "feat(research-domain): VerifierAgent + RetractorAgent; de-stub role trio

Verifier corroborates (deduped, never self) or mints a counter-claim and
refutes; Retractor validates supersession at commit. Fruitless sets keep
examined-but-no-action items from blocking the queue.

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
```

---

### Task 10: pipeline integration test — trio converges on a planted contradiction

**Files:**
- Test: `tests/research_domain/test_pipeline_integration.py`

**Interfaces:**
- Consumes: everything from Tasks 5–9 plus `agent_kit.Scheduler`.
- Produces: the proving-ground assertion this whole campaign exists for.

- [ ] **Step 1: Write the test (red only if earlier tasks are broken — this is an integration checkpoint)**

Create `tests/research_domain/test_pipeline_integration.py`:

```python
"""End-to-end proving ground: extractor -> verifier -> retractor driven by
agent_kit.Scheduler over a corpus with a planted contradiction, no LLM
anywhere. Asserts the projections converge to the expected final state."""
from __future__ import annotations

from agent_kit import Scheduler

from research_domain.agents import ExtractorAgent, RetractorAgent, VerifierAgent
from research_domain.corpus import CorpusReader
from research_domain.runtime import ResearchRuntime
from research_domain.schemas import (
    ClaimDraft,
    CorrectionDraft,
    ExtractorOutput,
    RefutationDraft,
    RetractorOutput,
    VerificationDraft,
    VerifierOutput,
)

from tests.substrate.postgres_fixture import postgres_dsn  # noqa: F401

BOIL_A = "water boils at 100C at sea level"
BOIL_B = "water boils at 90C everywhere"
GRAVITY = "objects fall at 9.8 m/s^2"


class FakeExtractorRunner:
    async def ainvoke(self, inputs: dict) -> dict:
        prompt = inputs["messages"][0]["content"]
        source_id = next(
            line.split("SOURCE_ID:", 1)[1].strip()
            for line in prompt.splitlines() if line.startswith("SOURCE_ID:"))
        by_source = {
            "boiling_a.md": [BOIL_A],
            "boiling_b.md": [BOIL_B],
            "gravity.md": [GRAVITY],
        }
        return {"structured_response": ExtractorOutput(
            claims=[ClaimDraft(text=t) for t in by_source.get(source_id, [])])}


class FakeVerifierRunner:
    """Corroborates gravity from boiling_a (scripted); refutes the 100C
    claim from boiling_b; everything else inconclusive."""

    def __init__(self, runtime: ResearchRuntime):
        self._runtime = runtime

    async def ainvoke(self, inputs: dict) -> dict:
        prompt = inputs["messages"][0]["content"]
        claim_id = next(
            line.split("CLAIM_ID:", 1)[1].strip()
            for line in prompt.splitlines() if line.startswith("CLAIM_ID:"))
        claim = self._runtime.get_claim(claim_id)
        if claim and claim["text"] == BOIL_A:
            verdict = VerificationDraft(
                claim_id=claim_id,
                refutation=RefutationDraft(
                    source_id="boiling_b.md", counter_text=BOIL_B,
                    reason="boiling_b.md asserts a different boiling point"))
        elif claim and claim["text"] == GRAVITY:
            verdict = VerificationDraft(
                claim_id=claim_id, corroborating_source_ids=["boiling_a.md"])
        else:
            verdict = VerificationDraft(claim_id=claim_id)
        return {"structured_response": VerifierOutput(verdicts=[verdict])}


class FakeRetractorRunner:
    """Always sides with the refuter (the planted resolution)."""

    def __init__(self, runtime: ResearchRuntime):
        self._runtime = runtime

    async def ainvoke(self, inputs: dict) -> dict:
        prompt = inputs["messages"][0]["content"]
        target = next(
            line.split("TARGET_CLAIM_ID:", 1)[1].strip()
            for line in prompt.splitlines() if line.startswith("TARGET_CLAIM_ID:"))
        refuters = self._runtime.refuters_for(target)
        correction = CorrectionDraft(
            superseding_claim_id=refuters[0], target_claim_id=target,
            reason="refuting source is more specific")
        return {"structured_response": RetractorOutput(corrections=[correction])}


async def test_trio_converges_on_planted_contradiction(tmp_path, postgres_dsn):
    (tmp_path / "boiling_a.md").write_text(BOIL_A, encoding="utf-8")
    (tmp_path / "boiling_b.md").write_text(BOIL_B, encoding="utf-8")
    (tmp_path / "gravity.md").write_text(GRAVITY, encoding="utf-8")

    runtime = ResearchRuntime(postgres_dsn, stream="pipeline-stream")
    await runtime.connect()
    try:
        await runtime.catch_up()
        corpus = CorpusReader(tmp_path)
        agents = [
            ExtractorAgent(FakeExtractorRunner(), runtime, corpus, interval=1),
            VerifierAgent(FakeVerifierRunner(runtime), runtime, corpus, interval=1),
            RetractorAgent(FakeRetractorRunner(runtime), runtime, interval=1),
        ]
        fake_now = [1000.0]
        sched = Scheduler(agents, clock=lambda: fake_now[0], max_concurrent_agents=2)

        def _gravity_corroborated() -> bool:
            claim = next(
                (c for c in runtime.list_claims() if c["text"] == GRAVITY), None)
            return bool(claim and runtime.corroborators_for(claim["claim_id"]))

        for _ in range(40):  # generous tick budget; loop exits early when done
            await sched.tick()
            await sched.drain_in_flight()
            fake_now[0] += 10.0
            done = (
                len(runtime.claimed_source_ids()) >= 3
                and runtime.get_projection("claim_dependency_graph")
                and _gravity_corroborated()
            )
            if done:
                break

        # every doc extracted
        assert runtime.claimed_source_ids() == {"boiling_a.md", "boiling_b.md", "gravity.md"}
        # the contradiction was found and resolved
        [(target, superseders)] = runtime.get_projection("claim_dependency_graph").items()
        assert runtime.get_claim(target)["text"] == BOIL_A
        assert len(superseders) == 1
        assert runtime.get_claim(superseders[0])["text"] == BOIL_B
        # gravity got corroborated
        gravity_claim = next(c for c in runtime.list_claims() if c["text"] == GRAVITY)
        assert runtime.corroborators_for(gravity_claim["claim_id"]) == ["boiling_a.md"]
        # no agent has anything left to do
        for agent in agents:
            assert await agent.readiness() == 0.0
        # scheduler bookkeeping saw every agent complete at least once
        status = {s["name"]: s for s in sched.status()}
        assert all(status[n]["run_count"] >= 1 for n in ("extractor", "verifier", "retractor"))
        assert all(status[n]["last_error"] is None for n in ("extractor", "verifier", "retractor"))
    finally:
        await runtime.close()
```

- [ ] **Step 2: Run the test**

Run: `uv run pytest tests/research_domain/test_pipeline_integration.py -W error -q`
Expected: PASS (docker skip acceptable only if docker genuinely unavailable — this is the campaign's core proof; if it fails, debug with superpowers:systematic-debugging rather than loosening assertions).

- [ ] **Step 3: Commit**

```bash
git add tests/research_domain/test_pipeline_integration.py
git commit -m "test(research-domain): pipeline integration — trio converges on planted contradiction

Extractor -> verifier -> retractor under agent_kit.Scheduler, fake
runners, real Postgres: claims extracted, contradiction found, counter-
claim supersedes, all agents idle at the end.

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
```

---

### Task 11: runner builders + CLI `run` command

**Files:**
- Create: `research_domain/runners.py`
- Modify: `research_domain/cli.py`
- Test: `tests/research_domain/test_runners.py`
- Test: `tests/research_domain/test_cli_run.py`

**Interfaces:**
- Consumes: `agent_kit.build_chat_model`, `build_agent_runner`, `ExcludeToolsMiddleware`, `Scheduler`; `build_live_agents` (Task 9); tools factories (Task 7); `CorpusReader`; `ResearchRuntime`.
- Produces:
  - `research_domain.runners.ModelSettings(model: str, base_url: str, api_key: str, temperature: float = 0.2, max_tokens: int | None = 4096)` (frozen dataclass).
  - `research_domain.runners.build_role_runner(role: str, settings: ModelSettings, tools: list)` — returns a deepagents runner for `"extractor" | "verifier" | "retractor"` (raises `ValueError` otherwise). Role prompts: module constants `EXTRACTOR_SYSTEM_PROMPT`, `VERIFIER_SYSTEM_PROMPT`, `RETRACTOR_SYSTEM_PROMPT`.
  - `research_domain.cli.build_run_components(*, dsn: str, stream: str, corpus_dir: str, settings: ModelSettings | None, interval: int, max_concurrent: int, tick_sleep: float = 1.0, agents=None) -> tuple[ResearchRuntime, Scheduler]` — when `agents` is provided (tests), `settings` may be None and no LLM objects are constructed.
  - CLI command `research-domain run --corpus DIR [--dsn] [--stream] [--model] [--base-url] [--api-key] [--interval] [--max-concurrent] [--max-ticks]`.

- [ ] **Step 1: Write the failing tests**

Create `tests/research_domain/test_runners.py`:

```python
from __future__ import annotations

import pytest

from research_domain.runners import (
    EXTRACTOR_SYSTEM_PROMPT,
    RETRACTOR_SYSTEM_PROMPT,
    VERIFIER_SYSTEM_PROMPT,
    ModelSettings,
    build_role_runner,
)

SETTINGS = ModelSettings(model="test-model", base_url="http://localhost:9999/v1", api_key="k")


def test_prompts_are_distinct_and_role_specific():
    assert "extract" in EXTRACTOR_SYSTEM_PROMPT.lower()
    assert "verif" in VERIFIER_SYSTEM_PROMPT.lower()
    assert len({EXTRACTOR_SYSTEM_PROMPT, VERIFIER_SYSTEM_PROMPT, RETRACTOR_SYSTEM_PROMPT}) == 3


def test_build_role_runner_constructs_for_each_role():
    for role in ("extractor", "verifier", "retractor"):
        runner = build_role_runner(role, SETTINGS, tools=[])
        assert hasattr(runner, "ainvoke")  # satisfies the Runner protocol


def test_unknown_role_raises():
    with pytest.raises(ValueError, match="unknown role"):
        build_role_runner("scout", SETTINGS, tools=[])
```

Create `tests/research_domain/test_cli_run.py`:

```python
from __future__ import annotations

from research_domain.agents import ExtractorAgent
from research_domain.cli import build_run_components
from research_domain.corpus import CorpusReader
from research_domain.runtime import ResearchRuntime
from research_domain.schemas import ClaimDraft, ExtractorOutput

from tests.substrate.postgres_fixture import postgres_dsn  # noqa: F401


class FakeExtractorRunner:
    async def ainvoke(self, inputs: dict) -> dict:
        prompt = inputs["messages"][0]["content"]
        source_id = next(
            line.split("SOURCE_ID:", 1)[1].strip()
            for line in prompt.splitlines() if line.startswith("SOURCE_ID:"))
        return {"structured_response": ExtractorOutput(
            claims=[ClaimDraft(text=f"claim from {source_id}")])}


async def test_build_run_components_with_injected_agents_ticks_headlessly(tmp_path, postgres_dsn):
    (tmp_path / "doc.md").write_text("some fact", encoding="utf-8")

    runtime = ResearchRuntime(postgres_dsn, stream="cli-run-stream")
    corpus = CorpusReader(tmp_path)
    agents = [ExtractorAgent(FakeExtractorRunner(), runtime, corpus, interval=1)]

    built_runtime, scheduler = build_run_components(
        dsn=postgres_dsn, stream="cli-run-stream", corpus_dir=str(tmp_path),
        settings=None, interval=1, max_concurrent=2, agents=agents,
    )
    assert built_runtime is runtime or isinstance(built_runtime, ResearchRuntime)
    # injected-agents path must not have constructed any LLM machinery
    await runtime.connect()
    try:
        await runtime.catch_up()
        await scheduler.tick()
        await scheduler.drain_in_flight()
        assert runtime.claimed_source_ids() == {"doc.md"}
    finally:
        await runtime.close()
```

Note for the implementer: when `agents` is injected, `build_run_components` must use the runtime those agents were built with — simplest contract: when `agents is not None`, the function still constructs `(runtime, scheduler)` but the caller passes agents already bound to their own runtime, and the function's runtime is only used by the `run` command's connect/catch_up/close lifecycle. To keep the test honest and the contract simple, implement it as: `agents is None` → build runtime + corpus + real runners + trio, all bound together; `agents is not None` → build a Scheduler over exactly those agents and return `(agents_runtime_or_new, scheduler)` where the runtime returned is a fresh `ResearchRuntime(dsn, stream)` the CLI will drive. The test above accepts either via the `or isinstance` assertion but exercises ticking through the *injected* agents' runtime.

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/research_domain/test_runners.py tests/research_domain/test_cli_run.py -W error -q`
Expected: `ModuleNotFoundError: No module named 'research_domain.runners'` / ImportError for `build_run_components`.

- [ ] **Step 3: Implement**

Create `research_domain/runners.py`:

```python
"""Per-role deepagents runner builders: thin compositions of
agent_kit.build_chat_model + build_agent_runner with each role's system
prompt and response format."""
from __future__ import annotations

from dataclasses import dataclass

from agent_kit import ExcludeToolsMiddleware, build_agent_runner, build_chat_model

from research_domain.schemas import ExtractorOutput, RetractorOutput, VerifierOutput

EXTRACTOR_SYSTEM_PROMPT = """## Role
You extract factual claims from research corpus documents. A claim is a
single checkable assertion, phrased as one standalone sentence. You never
invent claims the document does not make, and you never modify anything:
your only output is the structured claim list."""

VERIFIER_SYSTEM_PROMPT = """## Role
You verify claims against the rest of the research corpus. Use the tools
to read other documents before answering. Corroborate only when another
document independently supports the claim; refute only when a document
directly contradicts it, citing that document as the counter-claim's
source. When the corpus is silent, say so with an empty verdict."""

RETRACTOR_SYSTEM_PROMPT = """## Role
You resolve recorded contradictions between claims. Given a target claim
and the claims refuting it, decide which should stand. Supersede the
target only when a refuting claim is better supported; otherwise return
no corrections and let the original stand."""

_ROLES = {
    "extractor": (EXTRACTOR_SYSTEM_PROMPT, ExtractorOutput),
    "verifier": (VERIFIER_SYSTEM_PROMPT, VerifierOutput),
    "retractor": (RETRACTOR_SYSTEM_PROMPT, RetractorOutput),
}


@dataclass(frozen=True)
class ModelSettings:
    model: str
    base_url: str
    api_key: str
    temperature: float = 0.2
    max_tokens: int | None = 4096


def build_role_runner(role: str, settings: ModelSettings, tools: list):
    if role not in _ROLES:
        raise ValueError(f"unknown role: {role!r} (expected one of {sorted(_ROLES)})")
    system_prompt, response_format = _ROLES[role]
    model = build_chat_model(
        settings.model, settings.base_url, settings.api_key,
        temperature=settings.temperature, max_tokens=settings.max_tokens,
    )
    return build_agent_runner(
        model=model,
        system_prompt=system_prompt,
        response_format=response_format,
        tools=tools,
        middleware=[ExcludeToolsMiddleware(excluded=frozenset({"write_todos"}))],
    )
```

Modify `research_domain/cli.py` — add imports and the `run` command (existing `append`/`show` commands and helpers stay exactly as they are):

```python
# add to imports at top
from pathlib import Path

from agent_kit import Scheduler

from research_domain.corpus import CorpusReader
from research_domain.roles import build_live_agents
from research_domain.runners import ModelSettings, build_role_runner
from research_domain.tools import make_claim_tools, make_corpus_tools
```

```python
def build_run_components(
    *,
    dsn: str,
    stream: str,
    corpus_dir: str,
    settings: ModelSettings | None,
    interval: int,
    max_concurrent: int,
    tick_sleep: float = 1.0,
    agents=None,
):
    """Construct the runtime + scheduler for a live run. Tests inject
    pre-built `agents` (bound to their own runtime) and pass settings=None;
    the CLI passes settings and lets the real trio be built here."""
    runtime = ResearchRuntime(dsn, stream=stream)
    if agents is None:
        if settings is None:
            raise click.ClickException("model settings required when agents are not injected")
        corpus = CorpusReader(Path(corpus_dir))
        shared_tools = make_corpus_tools(corpus) + make_claim_tools(runtime)
        agents = build_live_agents(
            runtime, corpus, lambda role: build_role_runner(role, settings, shared_tools)
        )
        for agent in agents:
            agent.interval = interval
    scheduler = Scheduler(
        agents, tick_sleep=tick_sleep, max_concurrent_agents=max_concurrent
    )
    return runtime, scheduler


@main.command()
@click.option("--corpus", "corpus_dir", required=True,
              type=click.Path(exists=True, file_okay=False),
              help="Directory of .md/.txt documents to research")
@click.option("--dsn", default=None, help="Postgres DSN (defaults to DATABASE_URL env var)")
@click.option("--stream", default="research-stream", help="Event stream id")
@click.option("--model", envvar="RESEARCH_MODEL", required=True,
              help="Model name (env RESEARCH_MODEL)")
@click.option("--base-url", envvar="LLM_BASE_URL", required=True,
              help="OpenAI-compatible endpoint base URL (env LLM_BASE_URL)")
@click.option("--api-key", envvar="LLM_API_KEY", default="unused",
              help="API key (env LLM_API_KEY)")
@click.option("--interval", default=60, show_default=True,
              help="Per-agent minimum seconds between runs")
@click.option("--max-concurrent", default=2, show_default=True,
              help="Dispatch pool size")
@click.option("--max-ticks", default=None, type=int,
              help="Tick N times then exit (default: run until interrupted)")
def run(corpus_dir, dsn, stream, model, base_url, api_key,
        interval, max_concurrent, max_ticks) -> None:
    """Run the extractor/verifier/retractor trio over a document corpus."""
    resolved_dsn = _resolve_dsn(dsn)
    settings = ModelSettings(model=model, base_url=base_url, api_key=api_key)

    async def _run() -> None:
        runtime, scheduler = build_run_components(
            dsn=resolved_dsn, stream=stream, corpus_dir=corpus_dir,
            settings=settings, interval=interval, max_concurrent=max_concurrent,
        )
        await runtime.connect()
        try:
            await runtime.catch_up()
            console.print(f"[green]research run[/green] corpus={corpus_dir} stream={stream}")
            if max_ticks is None:
                await scheduler.run()
            else:
                for _ in range(max_ticks):
                    await scheduler.tick()
                    await asyncio.sleep(scheduler._tick_sleep)
                await scheduler.drain_in_flight()
        finally:
            await runtime.close()

    asyncio.run(_run())
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/research_domain/ -W error -q && uv run lint-imports`
Expected: PASS (docker skips only where docker is genuinely absent); contracts KEPT.

- [ ] **Step 5: Commit**

```bash
git add research_domain/runners.py research_domain/cli.py tests/research_domain/test_runners.py tests/research_domain/test_cli_run.py
git commit -m "feat(research-domain): role runner builders + CLI run command

research-domain run --corpus DIR drives the live trio via
agent_kit.Scheduler; build_run_components is the injection seam tests
use with fake runners.

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
```

---

### Task 12: docs + final verification sweep

**Files:**
- Create: `agent_kit/README.md`
- Modify: `substrate/README.md` (one cross-link line)
- Test: full targeted sweep (no new tests)

- [ ] **Step 1: Write `agent_kit/README.md`**

```markdown
# agent_kit

Domain-neutral agent execution machinery — the third extraction from
novelizer, after `substrate/` (event sourcing) and `tui_kit/` (TUI). See
`docs/superpowers/specs/2026-07-22-agent-kit-extraction-design.md` for the
extraction history and the three corrected seams.

## Primitives

- **BaseAgent** (`agent_kit.BaseAgent`) — the poll/work/commit loop
  chassis: interval/backoff scheduling, `note_pass()` triple-backoff,
  fingerprint watermarking, and `run_once()` which brackets your `_run()`
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

novelizer still runs on its own in-tree copies of this machinery (by
design — extraction round one left it untouched for stability). A parity
test (`tests/agent_kit/test_scheduler_parity.py`) keeps dispatch behavior
identical until the cutover campaign migrates novelizer onto the kit.
```

- [ ] **Step 2: Add cross-link to `substrate/README.md`**

After the first paragraph of `substrate/README.md`, add:

```markdown
Sibling kits: `tui_kit/` (domain-agnostic TUI) and `agent_kit/`
(domain-neutral agent execution — loop, scheduler, LLM runners; see
`agent_kit/README.md`).
```

- [ ] **Step 3: Full verification sweep**

Run each; all must pass (docker-dependent tests may skip only where docker is genuinely unavailable — report skips explicitly):

```bash
uv run pytest tests/agent_kit/ -W error -q
uv run pytest tests/research_domain/ -W error -q
uv run pytest tests/substrate/ -W error -q
uv run lint-imports
```

Also confirm novelizer is untouched:

```bash
git diff main --stat -- novelizer/
```

Expected: empty output (zero novelizer changes on this branch).

- [ ] **Step 4: Commit**

```bash
git add agent_kit/README.md substrate/README.md
git commit -m "docs(agent_kit): README + substrate cross-link

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
```
