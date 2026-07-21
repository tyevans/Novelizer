# Talk to the Project Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a "Talk to the Project" screen — a REPL-style, session-only research assistant that answers free-form questions about the story by reading canon and running brain diagnostics on request, one blocking turn at a time.

**Architecture:** A new read-only bounded context `novelizer/research/` (tool wrappers around `novelizer/brain/*`, a neutral deep-agent runner, and a stateless `ResearchService.ask()`), wired onto `Runtime` the same way chat is, plus a new `ResearchScreen` reachable via `ctrl+r` and the command palette. No events, no persistence, no intents — this context never writes to canon.

**Tech Stack:** Python, `deepagents` (`create_deep_agent`), Textual, pytest + pytest-asyncio.

## Global Constraints

- No event sourcing, no projection, no read-model table for research — the transcript is in-memory screen state only (spec §Decisions 3).
- The research runner never proposes or commits intents; `response_format` is a plain-answer schema, not `ChatReply` (spec §runner.py).
- One turn in flight at a time: the `ResearchScreen` input is disabled while a question is being answered (spec §Decisions 4).
- Entry point is `ctrl+r` (all single-letter bindings are already taken) plus a "Talk to the Project" command-palette entry (spec §Decisions 5).
- Tool wrappers take no required arguments beyond `ReadStore` and format results as agent-readable text; an empty-findings result must say so explicitly, not return silently (spec §tools.py).

**Scope note (plan-time trim from the spec's tool list):** the spec names nine brain modules as candidates. This plan implements six whose inputs are plain `ReadStore` reads — stale threads, leaks, paradoxes, promise ledger, beat drift, and completion status. `sag_spike`, `arc_alignment`, and `resolution_pacing` follow the exact same wrapper pattern and can be added later the same way; `theme_similarity` doesn't fit the no-args shape at all (it compares a *candidate* theme against existing ones, not a housekeeping scan) and is left out of scope per the spec's own exclusions.

---

### Task 1: Research diagnostic tool wrappers

**Files:**
- Create: `novelizer/research/__init__.py`
- Create: `novelizer/research/tools.py`
- Test: `tests/research/__init__.py`
- Test: `tests/research/test_tools.py`

**Interfaces:**
- Consumes: `novelizer.canon.read_store.ReadStore` (methods: `list_threads`, `list_chapters`, `list_secret_references`, `knowledge_matrix`, `list_causal_edges`, `list_promises`, `get_active_blueprint`, `list_beats`, `list_arcs`); `novelizer.brain.staleness.stale_threads`, `novelizer.brain.leaks.find_leaks`/`leak_description`, `novelizer.brain.paradoxes.find_paradoxes`/`paradox_description`, `novelizer.brain.ledger.overdue_promises`/`due_promises`, `novelizer.brain.beat_drift.beat_drifts`, `novelizer.brain.completion.completion_status`.
- Produces: six async functions, each `async def check_X(read) -> str`:
  `check_stale_threads`, `check_leaks`, `check_paradoxes`,
  `check_promise_ledger`, `check_beat_drift`, `check_completion`. Task 2
  wraps these as deep-agent tools by name.

- [ ] **Step 1: Write the failing tests**

Create `tests/research/__init__.py` (empty file).

Create `tests/research/test_tools.py`:

```python
import pytest
from novelizer.store.models import (
    ArcRecord, BeatRecord, BlueprintRecord, CausalEdgeRecord, Chapter, PromiseRecord, PromiseState,
    SecretReferenceRecord, ThreadRecord, ThreadState,
)
from novelizer.research import tools


class _FakeReadStore:
    def __init__(
        self, *, threads=None, chapters=None, secret_refs=None, matrix=None, edges=None,
        promises=None, blueprint=None, beats=None, arcs=None,
    ):
        self._threads = threads or []
        self._chapters = chapters or []
        self._secret_refs = secret_refs or []
        self._matrix = matrix or {}
        self._edges = edges or []
        self._promises = promises or []
        self._blueprint = blueprint
        self._beats = beats or []
        self._arcs = arcs or []

    async def list_threads(self): return self._threads
    async def list_chapters(self, status=None): return self._chapters
    async def list_secret_references(self, secret_id=None): return self._secret_refs
    async def knowledge_matrix(self): return self._matrix
    async def list_causal_edges(self): return self._edges
    async def list_promises(self): return self._promises
    async def get_active_blueprint(self): return self._blueprint
    async def list_beats(self): return self._beats
    async def list_arcs(self, active_only=False): return self._arcs


def _chapters(n):
    return [Chapter(id=f"c{i}", title=str(i), prose="p") for i in range(n)]


@pytest.mark.asyncio
async def test_check_stale_threads_reports_none_when_nothing_stale():
    read = _FakeReadStore(chapters=_chapters(1))
    result = await tools.check_stale_threads(read)
    assert result == "No stale threads."


@pytest.mark.asyncio
async def test_check_stale_threads_lists_stale_thread_ids():
    chs = _chapters(5)
    thread = ThreadRecord(id="t1", name="The Debt", state=ThreadState.planted, last_chapter_id="c0")
    read = _FakeReadStore(threads=[thread], chapters=chs)
    result = await tools.check_stale_threads(read)
    assert "t1" in result and "The Debt" in result


@pytest.mark.asyncio
async def test_check_leaks_reports_none_when_no_leaks():
    read = _FakeReadStore()
    result = await tools.check_leaks(read)
    assert result == "No leaks found."


@pytest.mark.asyncio
async def test_check_leaks_lists_a_leak():
    ref = SecretReferenceRecord(secret_id="s1", character_id="char1", chapter_id="c1")
    read = _FakeReadStore(secret_refs=[ref], matrix={})
    result = await tools.check_leaks(read)
    assert "s1" in result and "char1" in result


@pytest.mark.asyncio
async def test_check_paradoxes_reports_none_when_no_paradoxes():
    read = _FakeReadStore()
    result = await tools.check_paradoxes(read)
    assert result == "No paradoxes found."


@pytest.mark.asyncio
async def test_check_paradoxes_lists_an_ordering_violation():
    chs = _chapters(3)  # c0, c1, c2
    edge = CausalEdgeRecord(cause_chapter_id="c2", effect_chapter_id="c0")
    read = _FakeReadStore(edges=[edge], chapters=chs)
    result = await tools.check_paradoxes(read)
    assert "c2" in result and "c0" in result


@pytest.mark.asyncio
async def test_check_promise_ledger_reports_none_when_empty():
    read = _FakeReadStore(chapters=_chapters(1))
    result = await tools.check_promise_ledger(read)
    assert result == "No overdue or due promises."


@pytest.mark.asyncio
async def test_check_promise_ledger_lists_overdue_promise():
    chs = _chapters(10)
    promise = PromiseRecord(
        id="p1", name="The Letter", state=PromiseState.open, window_lo=1, window_hi=3,
    )
    read = _FakeReadStore(promises=[promise], chapters=chs)
    result = await tools.check_promise_ledger(read)
    assert "p1" in result and "OVERDUE" in result


@pytest.mark.asyncio
async def test_check_beat_drift_reports_none_without_a_blueprint():
    read = _FakeReadStore(chapters=_chapters(1))
    result = await tools.check_beat_drift(read)
    assert result == "No beat drift (no adopted blueprint)."


@pytest.mark.asyncio
async def test_check_beat_drift_lists_a_late_beat():
    chs = _chapters(20)
    blueprint = BlueprintRecord(id="bp1", target_chapter_count=20)
    beat = BeatRecord(id="b1", name="Midpoint", ideal_pct=0.1, tolerance_pct=0.05)
    read = _FakeReadStore(blueprint=blueprint, beats=[beat], chapters=chs)
    result = await tools.check_beat_drift(read)
    assert "Midpoint" in result and "late" in result.lower()


@pytest.mark.asyncio
async def test_check_completion_reports_no_blueprint():
    read = _FakeReadStore()
    result = await tools.check_completion(read)
    assert result == "No adopted blueprint yet."


@pytest.mark.asyncio
async def test_check_completion_reports_incomplete_status():
    chs = _chapters(3)
    blueprint = BlueprintRecord(id="bp1", target_chapter_count=10)
    beat = BeatRecord(id="b1", name="Midpoint", ideal_pct=0.5, tolerance_pct=0.1)
    read = _FakeReadStore(blueprint=blueprint, beats=[beat], chapters=chs)
    result = await tools.check_completion(read)
    assert "not complete" in result.lower()
    assert "Midpoint" in result
```

Field names above (`ThreadRecord`, `PromiseRecord`, `BlueprintRecord`,
`BeatRecord`, `CausalEdgeRecord`, `SecretReferenceRecord`) match
`novelizer/store/models.py` and the existing `tests/brain/test_*.py`
fixtures — if a field name mismatch surfaces when running the tests, check
`novelizer/store/models.py` for the exact field, don't guess.

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/research/test_tools.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'novelizer.research'`

- [ ] **Step 3: Write the implementation**

Create `novelizer/research/__init__.py` (empty file).

Create `novelizer/research/tools.py`:

```python
from __future__ import annotations
from novelizer.brain.beat_drift import beat_drifts
from novelizer.brain.completion import completion_status
from novelizer.brain.leaks import find_leaks, leak_description
from novelizer.brain.ledger import due_promises, overdue_promises
from novelizer.brain.paradoxes import find_paradoxes, paradox_description
from novelizer.brain.staleness import stale_threads


async def check_stale_threads(read) -> str:
    threads = await read.list_threads()
    chapters = await read.list_chapters()
    stale = stale_threads(threads, chapters)
    if not stale:
        return "No stale threads."
    lines = "\n".join(f"- {t.name} (id:{t.id})" for t in stale)
    return f"Stale threads:\n{lines}"


async def check_leaks(read) -> str:
    references = await read.list_secret_references()
    matrix = await read.knowledge_matrix()
    leaks = find_leaks(references, matrix)
    if not leaks:
        return "No leaks found."
    lines = "\n".join(f"- {leak_description(leak)}" for leak in leaks)
    return f"Leaks:\n{lines}"


async def check_paradoxes(read) -> str:
    edges = await read.list_causal_edges()
    chapter_order = [c.id for c in await read.list_chapters()]
    paradoxes = find_paradoxes(edges, chapter_order)
    if not paradoxes:
        return "No paradoxes found."
    lines = "\n".join(f"- {paradox_description(p)}" for p in paradoxes)
    return f"Paradoxes:\n{lines}"


async def check_promise_ledger(read) -> str:
    promises = await read.list_promises()
    chapters = await read.list_chapters()
    overdue = overdue_promises(promises, chapters)
    due = due_promises(promises, chapters)
    if not overdue and not due:
        return "No overdue or due promises."
    lines = [f"- {p.name} (id:{p.id}) — OVERDUE — window closed ch {p.window_hi}" for p in overdue]
    lines += [f"- {p.name} (id:{p.id}) — due ch {p.window_lo}-{p.window_hi}" for p in due]
    return "Promise ledger:\n" + "\n".join(lines)


async def check_beat_drift(read) -> str:
    blueprint = await read.get_active_blueprint()
    if blueprint is None:
        return "No beat drift (no adopted blueprint)."
    beats = await read.list_beats()
    chapters = await read.list_chapters()
    drifts = beat_drifts(blueprint, beats, chapters)
    if not drifts:
        return "No beat drift."
    lines = "\n".join(f"- {d.detail}" for d in drifts)
    return f"Beat drift:\n{lines}"


async def check_completion(read) -> str:
    blueprint = await read.get_active_blueprint()
    if blueprint is None:
        return "No adopted blueprint yet."
    beats = await read.list_beats()
    promises = await read.list_promises()
    arcs = await read.list_arcs()
    chapters = await read.list_chapters()
    status = completion_status(blueprint, beats, promises, arcs, chapters)
    if status.complete:
        return "Complete: every beat fulfilled, every promise settled, every arc resolved."
    lines = "\n".join(f"- {b}" for b in status.blockers)
    return (
        f"Not complete ({status.beats_fulfilled}/{status.beats_total} beats, "
        f"{status.promises_open} promises open, {status.arcs_unresolved} arcs unresolved):\n{lines}"
    )
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/research/test_tools.py -v`
Expected: PASS (all 12 tests)

- [ ] **Step 5: Commit**

```bash
git add novelizer/research/__init__.py novelizer/research/tools.py tests/research/__init__.py tests/research/test_tools.py
git commit -m "feat(research): add read-only brain diagnostic tool wrappers"
```

---

### Task 2: Research answer schema and deep-agent runner

**Files:**
- Create: `novelizer/research/schemas.py`
- Create: `novelizer/research/runner.py`
- Test: `tests/research/test_runner.py`

**Interfaces:**
- Consumes: `novelizer.research.tools.check_stale_threads` etc. (Task 1);
  `deepagents.create_deep_agent`; `novelizer.agents.llm.build_chat_model`;
  `novelizer.agents.middleware.ExcludeToolsMiddleware`.
- Produces: `ResearchAnswer(BaseModel)` with field `answer_text: str`;
  `build_research_runner(settings, callbacks=None, backend=None,
  tools=None)` — a graph with `.ainvoke({"messages": [...]}) ->
  {"structured_response": ResearchAnswer}`, same call shape as
  `build_chat_runner`. Task 3 (`ResearchService`) calls this via a
  `runner_for` callable, mirroring `Runtime._chat_runner_for`.

- [ ] **Step 1: Write the failing tests**

Create `tests/research/test_runner.py`:

```python
class FakeSettings:
    agent_model = "local-model"
    llm_base_url = None
    llm_api_key = "test-key"
    agent_temperature = 0.7
    llm_max_tokens = None


def test_build_research_runner_bare_stays_constructible(monkeypatch):
    from novelizer.research import runner as runner_mod

    captured = {}

    class FakeGraph:
        pass

    def fake_create_deep_agent(*, model, system_prompt, response_format, backend=None, tools=None, skills=None, middleware=None):
        captured["kwargs"] = {"system_prompt": system_prompt, "backend": backend, "tools": tools}
        return FakeGraph()

    import deepagents
    monkeypatch.setattr(deepagents, "create_deep_agent", fake_create_deep_agent)

    graph = runner_mod.build_research_runner(FakeSettings())

    assert graph is not None
    assert captured["kwargs"]["backend"] is None
    assert captured["kwargs"]["tools"] is None
    assert "never modify canon" in captured["kwargs"]["system_prompt"]


def test_build_research_runner_with_backend_includes_diagnostic_tools(monkeypatch):
    from novelizer.research import runner as runner_mod
    from novelizer.canon_fs.backend import CanonBackend

    captured = {}

    class FakeGraph:
        def with_config(self, config):
            captured["config"] = config
            return self

    def fake_create_deep_agent(*, model, system_prompt, response_format, backend=None, tools=None, skills=None, middleware=None):
        captured["tools"] = tools
        captured["response_format"] = response_format
        return FakeGraph()

    import deepagents
    monkeypatch.setattr(deepagents, "create_deep_agent", fake_create_deep_agent)

    backend = CanonBackend(read_store=None)
    graph = runner_mod.build_research_runner(FakeSettings(), backend=backend, tools=["search_canon_stub"])

    from novelizer.research.schemas import ResearchAnswer
    assert captured["response_format"] is ResearchAnswer
    tool_names = {getattr(t, "name", t) for t in captured["tools"]}
    assert "search_canon_stub" in tool_names
    assert {"check_stale_threads", "check_leaks", "check_paradoxes",
            "check_promise_ledger", "check_beat_drift", "check_completion"} <= tool_names
    assert captured["config"]["recursion_limit"] == 100
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/research/test_runner.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'novelizer.research.runner'`

- [ ] **Step 3: Write the implementation**

Create `novelizer/research/schemas.py`:

```python
from __future__ import annotations
from pydantic import BaseModel


class ResearchAnswer(BaseModel):
    """The research agent's reply to a single Director question. Read-only
    by construction — this schema carries no intents, no proposal fields;
    the research context never writes to canon."""

    answer_text: str
```

Create `novelizer/research/runner.py`:

```python
from __future__ import annotations
from langchain_core.tools import tool
from novelizer.agents.base import GRAPH_RECURSION_LIMIT
from novelizer.research.schemas import ResearchAnswer
from novelizer.research.tools import (
    check_beat_drift, check_completion, check_leaks, check_paradoxes,
    check_promise_ledger, check_stale_threads,
)

RESEARCH_SYSTEM_PROMPT = """## Role
You are a research analyst for this story's canon. The Director is asking you
questions about the project — answer precisely, cite chapter/thread/secret/
promise ids you find rather than describing things vaguely, and use the
diagnostic tools when a question calls for actually checking something (e.g.
"are there any leaked secrets?", "is anything overdue?") rather than just
narrating what you already know. You never modify canon: you have no tools
that write, and you never propose changes — you only answer."""


def _make_diagnostic_tools(read_store):
    @tool(name="check_stale_threads")
    async def check_stale_threads_tool() -> str:
        """Check for story threads that have gone stale (no touch in
        several chapters and not yet paid off or abandoned)."""
        return await check_stale_threads(read_store)

    @tool(name="check_leaks")
    async def check_leaks_tool() -> str:
        """Check for secret leaks: a character referencing a secret they
        haven't learned or that hasn't been revealed."""
        return await check_leaks(read_store)

    @tool(name="check_paradoxes")
    async def check_paradoxes_tool() -> str:
        """Check the causal graph for ordering violations or cycles."""
        return await check_paradoxes(read_store)

    @tool(name="check_promise_ledger")
    async def check_promise_ledger_tool() -> str:
        """Check for promises (Chekhov's guns, foreshadowing) that are
        overdue or due for payoff."""
        return await check_promise_ledger(read_store)

    @tool(name="check_beat_drift")
    async def check_beat_drift_tool() -> str:
        """Check whether the adopted blueprint's beats are landing inside
        their expected chapter windows."""
        return await check_beat_drift(read_store)

    @tool(name="check_completion")
    async def check_completion_tool() -> str:
        """Check whether the adopted blueprint's shape is fully realized
        (every beat fulfilled, every promise settled, every arc resolved)."""
        return await check_completion(read_store)

    return [
        check_stale_threads_tool, check_leaks_tool, check_paradoxes_tool,
        check_promise_ledger_tool, check_beat_drift_tool, check_completion_tool,
    ]


def build_research_runner(settings, callbacks=None, backend=None, tools=None, read_store=None):
    from deepagents import create_deep_agent
    from novelizer.agents.llm import build_chat_model
    from novelizer.agents.middleware import ExcludeToolsMiddleware

    model = build_chat_model(
        settings.agent_model, settings.llm_base_url, settings.llm_api_key,
        settings.agent_temperature, max_tokens=settings.llm_max_tokens,
        callbacks=None, streaming=callbacks is not None,
    )
    if backend is not None:
        all_tools = list(tools or []) + _make_diagnostic_tools(read_store)
        graph = create_deep_agent(
            model=model, system_prompt=RESEARCH_SYSTEM_PROMPT, response_format=ResearchAnswer,
            backend=backend, tools=all_tools,
            middleware=[ExcludeToolsMiddleware(excluded=frozenset({"write_todos"}))],
        )
        config = {"recursion_limit": GRAPH_RECURSION_LIMIT}
        if callbacks:
            config["callbacks"] = callbacks
        return graph.with_config(config)
    graph = create_deep_agent(model=model, system_prompt=RESEARCH_SYSTEM_PROMPT, response_format=ResearchAnswer)
    if callbacks:
        return graph.with_config({"callbacks": callbacks})
    return graph
```

Note: `check_stale_threads_tool` etc. close over `read_store`, so
`_make_diagnostic_tools` must be called with the real `ReadStore` at
runner-build time (Task 4 wires this). Each tool's explicit `@tool(name=
"check_X")` is what the assertion in Step 1's second test reads via
`getattr(t, "name", t)` — the tool names the agent sees match this plan's
`check_*` naming exactly, independent of the wrapper function's own name.

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/research/test_runner.py -v`
Expected: PASS (2 tests)

- [ ] **Step 5: Commit**

```bash
git add novelizer/research/schemas.py novelizer/research/runner.py tests/research/test_runner.py
git commit -m "feat(research): add ResearchAnswer schema and deep-agent runner"
```

---

### Task 3: ResearchService

**Files:**
- Create: `novelizer/research/service.py`
- Test: `tests/research/test_service.py`

**Interfaces:**
- Consumes: `build_research_runner`-shaped `runner_for: Callable[[],
  Runner]` where `Runner.ainvoke(inputs: dict) -> dict` (same `Runner`
  protocol as `novelizer.agents.base.Runner`); `ResearchAnswer` (Task 2).
- Produces: `ResearchService(runner_for)` with `async def ask(self,
  question: str, history: list[tuple[str, str]]) -> str`. `history` is
  `[(role, text), ...]` with `role` one of `"you"`/`"project"`. Raises
  `ResearchAnswerError` (a `RuntimeError` subclass) when the runner returns
  no structured response — Task 5's screen catches this. Task 4 wires this
  onto `Runtime.research`.

- [ ] **Step 1: Write the failing tests**

Create `tests/research/test_service.py`:

```python
import pytest
from novelizer.research.schemas import ResearchAnswer
from novelizer.research.service import ResearchAnswerError, ResearchService


class _R:
    def __init__(self, out):
        self._out = out
        self.calls = []

    async def ainvoke(self, inputs):
        self.calls.append(inputs)
        return {"structured_response": self._out}


class _Empty:
    async def ainvoke(self, inputs):
        return {"structured_response": None}


@pytest.mark.asyncio
async def test_ask_returns_runner_text_verbatim():
    runner = _R(ResearchAnswer(answer_text="Three threads are currently stale."))
    service = ResearchService(lambda: runner)

    answer = await service.ask("Anything stale?", history=[])

    assert answer == "Three threads are currently stale."


@pytest.mark.asyncio
async def test_ask_includes_history_and_question_in_the_prompt():
    runner = _R(ResearchAnswer(answer_text="ok"))
    service = ResearchService(lambda: runner)

    await service.ask(
        "and the paradoxes?",
        history=[("you", "any leaks?"), ("project", "No leaks found.")],
    )

    prompt = runner.calls[0]["messages"][0]["content"]
    assert "any leaks?" in prompt
    assert "No leaks found." in prompt
    assert "and the paradoxes?" in prompt


@pytest.mark.asyncio
async def test_ask_raises_when_runner_returns_no_structured_response():
    service = ResearchService(lambda: _Empty())

    with pytest.raises(ResearchAnswerError):
        await service.ask("anything?", history=[])
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/research/test_service.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'novelizer.research.service'`

- [ ] **Step 3: Write the implementation**

Create `novelizer/research/service.py`:

```python
from __future__ import annotations
from typing import Callable


class ResearchAnswerError(RuntimeError):
    """The research runner returned no structured answer."""


def _transcript_block(history: list[tuple[str, str]]) -> str:
    if not history:
        return "(no research conversation yet)"
    lines = [f"{role}: {text}" for role, text in history[-20:]]
    return "\n".join(lines)


class ResearchService:
    """Stateless entry point for the research bounded context. The caller
    (ResearchScreen) owns conversation history; this service never persists
    anything and never writes to canon."""

    def __init__(self, runner_for: Callable) -> None:
        self._runner_for = runner_for

    async def ask(self, question: str, history: list[tuple[str, str]]) -> str:
        prompt = (
            f"Research conversation so far:\n{_transcript_block(history)}\n\n"
            f"New question: {question}"
        )
        runner = self._runner_for()
        result = await runner.ainvoke({"messages": [{"role": "user", "content": prompt}]})
        answer = result.get("structured_response")
        if answer is None:
            raise ResearchAnswerError("research runner returned no structured answer")
        return answer.answer_text
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/research/test_service.py -v`
Expected: PASS (3 tests)

- [ ] **Step 5: Commit**

```bash
git add novelizer/research/service.py tests/research/test_service.py
git commit -m "feat(research): add stateless ResearchService.ask()"
```

---

### Task 4: Wire ResearchService onto Runtime

**Files:**
- Modify: `novelizer/runtime.py`
- Test: `tests/research/test_service.py` (extend with a Runtime-backed test)

**Interfaces:**
- Consumes: `novelizer.research.service.ResearchService` (Task 3),
  `novelizer.research.runner.build_research_runner` (Task 2),
  `Runtime._canon_backend`/`Runtime._canon_tools` (already built in
  `_phase_a_toolkit`, existing code), `Runtime._runners` (existing
  fake-injection dict, key convention `"research"`).
- Produces: `Runtime.research: ResearchService`, constructed in
  `Runtime.start()` right after `self.chat = ChatService(...)`. Task 6's
  `NovelizerApp` calls `self.runtime.research.ask(...)`.

- [ ] **Step 1: Write the failing test**

Append to `tests/research/test_service.py`:

```python
import os
import tempfile
from novelizer.settings import EffectiveSettings as Settings
from novelizer.runtime import Runtime


@pytest.fixture
def db_path():
    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    yield path
    os.unlink(path)


@pytest.mark.asyncio
async def test_runtime_wires_a_research_service(db_path):
    runner = _R(ResearchAnswer(answer_text="No leaks found."))
    settings = Settings(db_path=db_path, projector_interval=0.05)
    rt = Runtime(settings, runners={"research": runner})
    await rt.start()
    try:
        answer = await rt.research.ask("any leaks?", history=[])
        assert answer == "No leaks found."
    finally:
        await rt.close()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/research/test_service.py::test_runtime_wires_a_research_service -v`
Expected: FAIL — `AttributeError: 'Runtime' object has no attribute 'research'`

- [ ] **Step 3: Write the implementation**

In `novelizer/runtime.py`, add a lazy runner accessor next to
`_chat_runner_for` (same file, right after its `return
self._chat_runner_cache[key]` line):

```python
    def _research_runner_for(self):
        """Lazy research runner, built once and cached. Injected fakes use
        key 'research' in the runners dict."""
        if self._runners is not None and "research" in self._runners:
            return self._runners["research"]
        if self._research_runner_cache is None:
            from novelizer.research.runner import build_research_runner
            self._research_runner_cache = build_research_runner(
                self.settings, callbacks=self._llm_callbacks,
                backend=self._canon_backend, tools=self._canon_tools,
                read_store=self.read,
            )
        return self._research_runner_cache
```

In `Runtime.__init__`, next to `self._chat_runner_cache: dict[str, object]
= {}`, add:

```python
        self._research_runner_cache = None
```

In `Runtime.start()`, immediately after the existing `self.chat =
ChatService(...)` block, add:

```python
        from novelizer.research.service import ResearchService
        self.research = ResearchService(self._research_runner_for)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/research/test_service.py -v`
Expected: PASS (4 tests)

Then run the full existing runtime/chat suite to confirm no regression:

Run: `pytest tests/chat tests/research -v`
Expected: PASS (all)

- [ ] **Step 5: Commit**

```bash
git add novelizer/runtime.py tests/research/test_service.py
git commit -m "feat(research): wire ResearchService onto Runtime"
```

---

### Task 5: ResearchScreen

**Files:**
- Create: `novelizer/tui/research_screen.py`
- Test: `tests/tui/test_research_screen.py`

**Interfaces:**
- Consumes: `runtime.research.ask(question, history) -> str`
  (`ResearchService`, Task 4); `novelizer.research.service.ResearchAnswerError`.
- Produces: `ResearchScreen(runtime)`, a Textual `Screen` with widget ids
  `#research_log` (`RichLog`) and `#research_input` (`Input`). Task 6
  pushes this screen from `NovelizerApp`.

- [ ] **Step 1: Write the failing tests**

Create `tests/tui/test_research_screen.py`:

```python
import os
import tempfile
import pytest
from textual.widgets import Input, RichLog
from novelizer.settings import EffectiveSettings as Settings
from novelizer.runtime import Runtime
from novelizer.tui.research_screen import ResearchScreen
from novelizer.research.schemas import ResearchAnswer


class _R:
    def __init__(self, out, delay_event=None):
        self._out = out
        self._delay_event = delay_event

    async def ainvoke(self, inputs):
        if self._delay_event is not None:
            await self._delay_event.wait()
        return {"structured_response": self._out}


class _Boom:
    async def ainvoke(self, inputs):
        raise RuntimeError("endpoint down")


@pytest.fixture
def db_path():
    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    yield path
    os.unlink(path)


async def _runtime(path, runner):
    settings = Settings(db_path=path, projector_interval=0.05)
    rt = Runtime(settings, runners={"research": runner})
    await rt.start()
    return rt


@pytest.mark.asyncio
async def test_submitting_a_question_disables_input_then_shows_answer(db_path):
    rt = await _runtime(db_path, _R(ResearchAnswer(answer_text="No leaks found.")))
    try:
        screen = ResearchScreen(rt)
        from textual.app import App

        class _TestApp(App):
            def on_mount(self):
                self.push_screen(screen)

        app = _TestApp()
        async with app.run_test() as pilot:
            await pilot.pause()
            input_widget = screen.query_one("#research_input", Input)
            input_widget.value = "any leaks?"
            await screen.on_input_submitted(type("E", (), {"input": input_widget, "value": "any leaks?"})())
            await pilot.pause(0.3)
            log_text = screen.query_one("#research_log", RichLog)
            assert "No leaks found." in "\n".join(str(line) for line in log_text.lines)
            assert input_widget.disabled is False
    finally:
        await rt.close()


@pytest.mark.asyncio
async def test_second_submit_while_pending_is_a_no_op(db_path):
    import asyncio
    gate = asyncio.Event()
    rt = await _runtime(db_path, _R(ResearchAnswer(answer_text="answer"), delay_event=gate))
    try:
        screen = ResearchScreen(rt)
        from textual.app import App

        class _TestApp(App):
            def on_mount(self):
                self.push_screen(screen)

        app = _TestApp()
        async with app.run_test() as pilot:
            await pilot.pause()
            input_widget = screen.query_one("#research_input", Input)
            input_widget.value = "q1"
            await screen.on_input_submitted(type("E", (), {"input": input_widget, "value": "q1"})())
            await pilot.pause(0.05)
            assert input_widget.disabled is True
            input_widget.value = "q2"
            await screen.on_input_submitted(type("E", (), {"input": input_widget, "value": "q2"})())
            await pilot.pause(0.05)
            assert screen._pending is True
            gate.set()
            await pilot.pause(0.3)
            log = screen.query_one("#research_log", RichLog)
            joined = "\n".join(str(line) for line in log.lines)
            assert joined.count("q2") == 0  # the second question was dropped, not queued
    finally:
        await rt.close()


@pytest.mark.asyncio
async def test_failed_runner_shows_warning_and_reenables_input(db_path):
    rt = await _runtime(db_path, _Boom())
    try:
        screen = ResearchScreen(rt)
        from textual.app import App

        class _TestApp(App):
            def on_mount(self):
                self.push_screen(screen)

        app = _TestApp()
        async with app.run_test() as pilot:
            await pilot.pause()
            input_widget = screen.query_one("#research_input", Input)
            input_widget.value = "boom?"
            await screen.on_input_submitted(type("E", (), {"input": input_widget, "value": "boom?"})())
            await pilot.pause(0.3)
            log = screen.query_one("#research_log", RichLog)
            joined = "\n".join(str(line) for line in log.lines)
            assert "research failed" in joined
            assert input_widget.disabled is False
    finally:
        await rt.close()
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/tui/test_research_screen.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'novelizer.tui.research_screen'`

- [ ] **Step 3: Write the implementation**

Create `novelizer/tui/research_screen.py`:

```python
from __future__ import annotations
import logging
from textual.app import ComposeResult
from textual.screen import Screen
from textual.widgets import Footer, Input, RichLog

from novelizer.research.service import ResearchAnswerError

logger = logging.getLogger(__name__)


class ResearchScreen(Screen):
    """Session-only, single-conversation research REPL: submit a question,
    the input disables while the research agent works, the answer appends
    when it's ready. No persistence — this screen's transcript is its own
    in-memory state."""

    BINDINGS = [("escape", "back", "Mission Control")]

    def __init__(self, runtime) -> None:
        super().__init__()
        self.runtime = runtime
        self._history: list[tuple[str, str]] = []
        self._pending = False

    def compose(self) -> ComposeResult:
        log = RichLog(highlight=False, markup=False, id="research_log")
        log.border_title = "TALK TO THE PROJECT"
        yield log
        yield Input(id="research_input", placeholder="ask about the project…", compact=True)
        yield Footer()

    async def on_mount(self) -> None:
        self.set_focus(self.query_one("#research_input", Input))

    async def on_input_submitted(self, event) -> None:
        if event.input.id != "research_input":
            return
        if self._pending:
            return  # one turn at a time — drop a second submit while busy
        text = event.value.strip()
        event.input.value = ""
        if not text:
            return
        self._pending = True
        event.input.disabled = True
        log = self.query_one("#research_log", RichLog)
        log.write(f"You: {text}")
        log.write("… researching")
        self.run_worker(self._ask(text), exclusive=True)

    async def _ask(self, question: str) -> None:
        log = self.query_one("#research_log", RichLog)
        input_widget = self.query_one("#research_input", Input)
        try:
            answer = await self.runtime.research.ask(question, self._history)
            self._history.append(("you", question))
            self._history.append(("project", answer))
            log.write(f"Project: {answer}")
        except ResearchAnswerError as e:
            log.write(f"⚠ research failed: {e}")
        except Exception as e:
            logger.warning("research turn failed: %s", e)
            log.write(f"⚠ research failed: {e}")
        finally:
            self._pending = False
            input_widget.disabled = False
            self.set_focus(input_widget)

    def action_back(self) -> None:
        self.app.pop_screen()
```

`RichLog.write` appends a `Strip`-rendered line; `RichLog.lines` (used by
the tests) exposes the rendered line cache — if the installed Textual
version stores rendered content differently, check `RichLog`'s public API
(`textual.widgets.RichLog`) for the equivalent accessor and adjust the test
assertions' access path accordingly; the behavior under test (does the text
"No leaks found." appear, does `input_widget.disabled` toggle correctly) is
what must hold, not the exact attribute name.

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/tui/test_research_screen.py -v`
Expected: PASS (3 tests)

- [ ] **Step 5: Commit**

```bash
git add novelizer/tui/research_screen.py tests/tui/test_research_screen.py
git commit -m "feat(tui): add ResearchScreen for Talk to the Project"
```

---

### Task 6: App wiring — keybinding and command palette entry

**Files:**
- Modify: `novelizer/tui/app.py`
- Test: `tests/tui/test_app_commands.py` (extend)

**Interfaces:**
- Consumes: `novelizer.tui.research_screen.ResearchScreen` (Task 5).
- Produces: `NovelizerApp.action_talk_to_project()`; `ctrl+r` binding;
  `AppCommand("talk_to_project", ..., _app_open_research)` entry in
  `APP_COMMANDS`. No new interfaces consumed by later tasks — this is the
  last task.

- [ ] **Step 1: Write the failing test**

Append to `tests/tui/test_app_commands.py`:

```python
@pytest.mark.asyncio
async def test_ctrl_r_opens_research_screen():
    from novelizer.tui.research_screen import ResearchScreen

    fd, path = tempfile.mkstemp(suffix=".db"); os.close(fd)
    settings = Settings(db_path=path, projector_interval=0.1)
    rt = Runtime(settings, runners=_runners())
    await rt.start()
    app = NovelizerApp(rt)
    try:
        async with app.run_test() as pilot:
            await pilot.press("ctrl+r")
            await pilot.pause(0.1)
            assert isinstance(app.screen, ResearchScreen)
    finally:
        await rt.close(); os.unlink(path)
```

(`test_app_commands_cover_every_binding_action` and
`test_command_provider_discovers_every_registered_command`, both already in
this file, will automatically start covering the new binding/command once
Step 3 below adds them — no edits needed to those two tests.)

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/tui/test_app_commands.py::test_ctrl_r_opens_research_screen -v`
Expected: FAIL — `AttributeError: 'NovelizerApp' object has no attribute 'action_talk_to_project'` (or binding not found)

- [ ] **Step 3: Write the implementation**

In `novelizer/tui/app.py`, add the import near the other screen imports
(alongside the existing `ChatScreen` import):

```python
from novelizer.tui.research_screen import ResearchScreen
```

Add `("ctrl+r", "talk_to_project", "Talk to Project")` to
`NovelizerApp.BINDINGS` (after the `("6", "brain_tab('tab_arcs')", "Arcs")`
entry, before `("q", "quit", "Quit")`):

```python
        ("ctrl+r", "talk_to_project", "Talk to Project"),
```

Add the module-level helper next to `_app_open_settings`:

```python
def _app_open_research(app: NovelizerApp) -> None:
    app.push_screen(ResearchScreen(app.runtime))
```

Add it to `APP_COMMANDS` (next to the `"settings"` entry):

```python
    AppCommand("talk_to_project", "Talk to the Project (research)", _app_open_research),
```

Add the action method next to `action_toggle_prompt`:

```python
    def action_talk_to_project(self) -> None:
        _app_open_research(self)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/tui/test_app_commands.py -v`
Expected: PASS (all tests in this file, including the pre-existing
`test_app_commands_cover_every_binding_action` and
`test_command_provider_discovers_every_registered_command`)

Then run the full TUI + research suite to confirm no regressions:

Run: `pytest tests/tui tests/research tests/chat -v`
Expected: PASS (all)

- [ ] **Step 5: Commit**

```bash
git add novelizer/tui/app.py tests/tui/test_app_commands.py
git commit -m "feat(tui): wire ctrl+r and command palette entry for Talk to the Project"
```

---

## Post-plan verification

After all six tasks are committed, run the full test suite once (per house
rule: never run test suites in the main checkout — this plan already
executes inside an isolated worktree, so this is safe here):

Run: `pytest -x -q`
Expected: PASS, no regressions in `tests/chat`, `tests/tui`, `tests/brain`,
or the new `tests/research`.
