# Subagent Tooling for Tooled Agents Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Give every tooled agent in `AGENT_REGISTRY` a way to dispatch a shared "researcher" deepagents subagent for delegated canon reads, with delegated tool calls surfaced as indented lines in Engine Room's live stream.

**Architecture:** A `SubagentGrant` (mirrors the existing `ToolGrant`) gates a per-agent settings flag; when enabled, `Runtime._tooled` passes a `subagents=[build_researcher_subagent(name)]` kwarg into that agent's `build_X_runner`, which forwards it to `create_deep_agent`. deepagents stamps every subagent call with `metadata={"lc_agent_name": "researcher"}`; `TelemetryCallbackHandler` reads that metadata into a new `delegate` field on tool/LLM telemetry events, and `engine_room_model.apply_bus_item` renders delegate-tagged tool calls as indented `⚒ ↳` lines.

**Tech Stack:** Python, deepagents (`create_deep_agent`, `SubAgent`), LangChain callbacks, pydantic (telemetry event models), pytest + hypothesis (existing test stack).

## Global Constraints

- Every new/changed settings field follows the existing `*_tools_enabled` naming and layering pattern (`novelizer/settings/models.py`, `layers.py`, `loader.py`) — see Design Decision 5.
- The subagent grant is a *separate* flag from the tools grant, gated independently (Design Decision 5); when the tools grant is off, the subagent grant has no effect — silently, no error (Design Decision 6).
- The researcher subagent always inherits its parent's model and canon-read toolkit — never pass an explicit `model` or a narrower `tools` list in `build_researcher_subagent` (Design Decisions 3–4).
- No new `TelemetryEventType` — `delegate` is an added optional field on the existing `ToolCallStarted/Finished/Failed` and `LlmCallStarted/Finished/Failed` payloads (per the design's Telemetry section).
- `trace_line`/`trace_detail` (the Engine Room trace/detail panel) are explicitly **out of scope** — only the live stream body (`apply_bus_item`/`live_body`) renders delegate indentation.
- Full spec: `docs/superpowers/specs/2026-07-20-subagent-tooling-design.md`.

---

### Task 1: `SubagentGrant` type on the agent registry

**Files:**
- Modify: `novelizer/agents/registry_types.py`
- Test: `tests/agents/test_registry_types.py`

**Interfaces:**
- Produces: `SubagentGrant(enabled_setting: str)` dataclass with `.is_enabled(settings) -> bool`, and `AgentSpec.subagent_grant: SubagentGrant | None = None` (new optional field, defaults preserve every existing `AgentSpec(...)` call site).

- [ ] **Step 1: Write the failing tests**

Add to `tests/agents/test_registry_types.py` (append after the existing `ToolGrant` tests, reusing the file's existing `_Settings` fixture class — extend it with two new attributes):

```python
class _Settings:
    editor_tools_enabled = True
    checker_tools_enabled = False
    editor_subagent_enabled = True
    checker_subagent_enabled = False


def test_subagent_grant_reads_named_setting_true():
    grant = SubagentGrant(enabled_setting="editor_subagent_enabled")
    assert grant.is_enabled(_Settings()) is True


def test_subagent_grant_reads_named_setting_false():
    grant = SubagentGrant(enabled_setting="checker_subagent_enabled")
    assert grant.is_enabled(_Settings()) is False


def test_agent_spec_subagent_grant_defaults_to_none():
    spec = AgentSpec(name="author", tool_grant=None, construct=lambda ctx: None)
    assert spec.subagent_grant is None


def test_agent_spec_accepts_explicit_subagent_grant():
    grant = SubagentGrant(enabled_setting="editor_subagent_enabled")
    spec = AgentSpec(name="editor", tool_grant=None, construct=lambda ctx: None, subagent_grant=grant)
    assert spec.subagent_grant is grant
```

Update the module's import line at the top of the test file:

```python
from novelizer.agents.registry_types import AgentContext, AgentSpec, ToolGrant, SubagentGrant
```

(The existing `_Settings` class in the file only has the two `tools_enabled` attributes — replace its body with the four-attribute version above; the existing `test_tool_grant_reads_named_setting_*` tests keep passing unchanged since those two attributes are untouched.)

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/agents/test_registry_types.py -v`
Expected: FAIL with `ImportError: cannot import name 'SubagentGrant'`

- [ ] **Step 3: Implement `SubagentGrant` and extend `AgentSpec`**

In `novelizer/agents/registry_types.py`, add a new dataclass mirroring `ToolGrant`, and add a field to `AgentSpec`:

```python
@dataclass(frozen=True)
class SubagentGrant:
    """Declares which Settings field gates an agent's subagent-dispatch access."""
    enabled_setting: str

    def is_enabled(self, settings: Any) -> bool:
        return bool(getattr(settings, self.enabled_setting))
```

Place this class immediately after `ToolGrant`. Then update `AgentSpec`:

```python
@dataclass(frozen=True)
class AgentSpec:
    """One fiction-domain agent's declaration: its name, whether it can be
    tooled, and how to build it. `construct` owns full responsibility for
    that agent's actual (possibly non-uniform) constructor signature."""
    name: str
    tool_grant: ToolGrant | None
    construct: Callable[[AgentContext], Any]
    subagent_grant: SubagentGrant | None = None
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/agents/test_registry_types.py -v`
Expected: PASS (all tests, old and new)

- [ ] **Step 5: Commit**

```bash
git add novelizer/agents/registry_types.py tests/agents/test_registry_types.py
git commit -m "feat(agents): add SubagentGrant to the agent registry"
```

---

### Task 2: `build_researcher_subagent` factory

**Files:**
- Create: `novelizer/agents/subagents.py`
- Test: `tests/agents/test_subagents.py`

**Interfaces:**
- Consumes: nothing from other tasks (self-contained factory).
- Produces: `build_researcher_subagent(agent_name: str, extra_instructions: str = "") -> dict` (a deepagents `SubAgent`-shaped dict with keys `name`, `description`, `system_prompt` — no `tools`/`model` keys, so deepagents' own inheritance rules apply). Task 6/7/8 import this.

- [ ] **Step 1: Write the failing test**

Create `tests/agents/test_subagents.py`:

```python
from __future__ import annotations
from novelizer.agents.subagents import build_researcher_subagent, RESEARCHER_SYSTEM_PROMPT


def test_returns_subagent_dict_with_expected_keys():
    spec = build_researcher_subagent("character_keeper")
    assert spec["name"] == "researcher"
    assert isinstance(spec["description"], str) and spec["description"]
    assert spec["system_prompt"].startswith(RESEARCHER_SYSTEM_PROMPT[:20])


def test_system_prompt_mentions_the_dispatching_agent_by_name():
    spec = build_researcher_subagent("continuity_checker")
    assert "continuity_checker" in spec["system_prompt"]


def test_extra_instructions_are_appended_to_the_system_prompt():
    spec = build_researcher_subagent("character_keeper", extra_instructions="\nCheck aliases too.")
    assert spec["system_prompt"].endswith("\nCheck aliases too.")


def test_no_tools_or_model_keys_present():
    """No tools/model keys -- deepagents inherits the parent's canon-read
    toolkit and model when both are omitted from a SubAgent spec."""
    spec = build_researcher_subagent("editor")
    assert "tools" not in spec
    assert "model" not in spec


def test_researcher_name_is_identical_across_dispatching_agents():
    """Same shared identity regardless of parent -- Design Decision 2."""
    a = build_researcher_subagent("author")
    b = build_researcher_subagent("retconner")
    assert a["name"] == b["name"] == "researcher"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/agents/test_subagents.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'novelizer.agents.subagents'`

- [ ] **Step 3: Write the implementation**

Create `novelizer/agents/subagents.py`:

```python
from __future__ import annotations

RESEARCHER_SYSTEM_PROMPT = """You are a research subagent dispatched by another agent to
investigate the story canon on its behalf. You have the same read-only tools your dispatcher
has (ls, read_file, grep, glob, search_canon) over the story canon filesystem.

You are working on behalf of the {agent_name} agent. When given a question or a task, use the
fewest tool calls that answer it fully -- read the specific file, grep for the specific term, or
search_canon for the specific meaning; do not browse broadly. When you have enough to answer,
stop calling tools and return a concise, directly-answering summary, citing the exact file paths
and record ids you consulted. Never invent facts absent from what you actually read. If you
cannot find an answer after a reasonable search, say so plainly rather than guessing."""

RESEARCHER_DESCRIPTION = (
    "Dispatch for a delegated canon-read task -- a specific question you want answered by "
    "reading, grepping, or searching the story canon, rather than reading it yourself. Give it "
    "a precise question (e.g. \"does chapter 12 show Mateo mentioning his debt?\"), not a vague "
    "instruction to browse."
)


def build_researcher_subagent(agent_name: str, extra_instructions: str = "") -> dict:
    """Build the shared 'researcher' SubAgent spec for a tooled agent to dispatch.

    No `tools` or `model` key is set: omitting both means deepagents' create_deep_agent
    inherits the parent's canon-read toolkit and model automatically (Design Decisions 3-4)."""
    system_prompt = RESEARCHER_SYSTEM_PROMPT.format(agent_name=agent_name) + extra_instructions
    return {
        "name": "researcher",
        "description": RESEARCHER_DESCRIPTION,
        "system_prompt": system_prompt,
    }
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/agents/test_subagents.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add novelizer/agents/subagents.py tests/agents/test_subagents.py
git commit -m "feat(agents): add shared researcher subagent factory"
```

---

### Task 3: `delegate` field on telemetry events + callback handler

**Files:**
- Modify: `novelizer/telemetry/events.py`
- Modify: `novelizer/telemetry/callbacks.py`
- Test: `tests/telemetry/test_callbacks.py` (create if it doesn't already exist — check first with `ls tests/telemetry/`)

**Interfaces:**
- Consumes: nothing from other tasks.
- Produces: `delegate: str = ""` field on `ToolCallStarted`, `ToolCallFinished`, `ToolCallFailed`, `LlmCallStarted`, `LlmCallFinished`, `LlmCallFailed`. `TelemetryCallbackHandler` reads `metadata.get("lc_agent_name", "")` from LangChain callback kwargs and stamps it onto every emitted event of those six types. Task 4 (Engine Room rendering) reads this field off `StoredEvent.payload["delegate"]`.

- [ ] **Step 0: Check for an existing test file**

Run: `ls tests/telemetry/`

If `test_callbacks.py` exists, read it first and add the new tests below into it, matching its existing fixture/mocking style. If it doesn't exist, create it fresh per Step 1 below (using a minimal in-memory fake recorder, since `TelemetryCallbackHandler.__init__` only needs an object with `async emit(event_type, run_id, payload)`, `next_call_index(run_id)`, and `publish_token(delta)`).

- [ ] **Step 1: Write the failing tests**

```python
from __future__ import annotations
import pytest
from uuid import uuid4
from novelizer.telemetry.callbacks import TelemetryCallbackHandler
from novelizer.telemetry.events import TelemetryEventType
from novelizer.run_context import current_agent_name, current_run_id


class _FakeRecorder:
    def __init__(self):
        self.events = []

    async def emit(self, event_type, run_id, payload):
        self.events.append((event_type, payload))

    def next_call_index(self, run_id):
        return 1

    def publish_token(self, delta):
        pass


@pytest.fixture
def handler():
    return TelemetryCallbackHandler(_FakeRecorder())


@pytest.mark.asyncio
async def test_tool_start_without_metadata_has_empty_delegate(handler):
    token_run = current_run_id.set("r1")
    token_agent = current_agent_name.set("character_keeper")
    try:
        run_id = uuid4()
        await handler.on_tool_start({"name": "read_file"}, "path", run_id=run_id)
    finally:
        current_run_id.reset(token_run)
        current_agent_name.reset(token_agent)
    et, payload = handler._recorder.events[0]
    assert et == TelemetryEventType.TOOL_CALL_STARTED
    assert payload.delegate == ""


@pytest.mark.asyncio
async def test_tool_start_with_lc_agent_name_metadata_stamps_delegate(handler):
    token_run = current_run_id.set("r1")
    token_agent = current_agent_name.set("character_keeper")
    try:
        run_id = uuid4()
        await handler.on_tool_start(
            {"name": "read_file"}, "path", run_id=run_id,
            metadata={"lc_agent_name": "researcher"},
        )
    finally:
        current_run_id.reset(token_run)
        current_agent_name.reset(token_agent)
    et, payload = handler._recorder.events[0]
    assert payload.delegate == "researcher"
    assert payload.agent_name == "character_keeper"


@pytest.mark.asyncio
async def test_tool_end_carries_the_same_delegate_as_tool_start(handler):
    token_run = current_run_id.set("r1")
    token_agent = current_agent_name.set("character_keeper")
    try:
        run_id = uuid4()
        await handler.on_tool_start(
            {"name": "read_file"}, "path", run_id=run_id,
            metadata={"lc_agent_name": "researcher"},
        )
        await handler.on_tool_end("output", run_id=run_id)
    finally:
        current_run_id.reset(token_run)
        current_agent_name.reset(token_agent)
    et, payload = handler._recorder.events[-1]
    assert et == TelemetryEventType.TOOL_CALL_FINISHED
    assert payload.delegate == "researcher"


@pytest.mark.asyncio
async def test_llm_start_with_lc_agent_name_metadata_stamps_delegate(handler):
    token_run = current_run_id.set("r1")
    token_agent = current_agent_name.set("character_keeper")
    try:
        run_id = uuid4()
        await handler.on_chat_model_start(
            {"kwargs": {"model": "qwen"}}, [[]], run_id=run_id,
            metadata={"lc_agent_name": "researcher"},
        )
    finally:
        current_run_id.reset(token_run)
        current_agent_name.reset(token_agent)
    et, payload = handler._recorder.events[0]
    assert et == TelemetryEventType.LLM_CALL_STARTED
    assert payload.delegate == "researcher"
```

Check whether `pytest-asyncio` is already configured (look for `asyncio_mode` in `pyproject.toml`/`pytest.ini` or existing `@pytest.mark.asyncio` usage elsewhere in `tests/`) before adding the marker — match whatever the rest of the suite already does.

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/telemetry/test_callbacks.py -v`
Expected: FAIL — `AttributeError: 'ToolCallStarted' object has no attribute 'delegate'` (or similar, since the field doesn't exist yet)

- [ ] **Step 3: Add the `delegate` field to the six event payload models**

In `novelizer/telemetry/events.py`, add `delegate: str = ""` to each of:

```python
class LlmCallStarted(BaseModel):
    run_id: str
    agent_name: str
    call_index: int
    model: str
    prompt: str
    delegate: str = ""


class LlmCallFinished(BaseModel):
    run_id: str
    agent_name: str
    call_index: int
    model: str
    duration_s: float
    output_tokens: int
    delegate: str = ""


class LlmCallFailed(BaseModel):
    run_id: str
    agent_name: str
    call_index: int
    model: str
    duration_s: float
    error_type: str
    error_message: str
    delegate: str = ""


class ToolCallStarted(BaseModel):
    run_id: str
    agent_name: str
    tool_name: str
    input_summary: str  # str(tool input), truncated to 300 chars
    delegate: str = ""


class ToolCallFinished(BaseModel):
    run_id: str
    agent_name: str
    tool_name: str
    duration_s: float
    output_chars: int
    delegate: str = ""


class ToolCallFailed(BaseModel):
    run_id: str
    agent_name: str
    tool_name: str
    duration_s: float
    error_type: str
    error_message: str
    delegate: str = ""
```

- [ ] **Step 4: Thread `delegate` through `TelemetryCallbackHandler`**

In `novelizer/telemetry/callbacks.py`:

Add a `delegate` slot to both state classes:

```python
class _CallState:
    __slots__ = ("novelizer_run_id", "agent_name", "call_index", "model", "started", "chunks", "delegate")

    def __init__(self, novelizer_run_id: str, agent_name: str, call_index: int, model: str, delegate: str = "") -> None:
        self.novelizer_run_id = novelizer_run_id
        self.agent_name = agent_name
        self.call_index = call_index
        self.model = model
        self.started = time.monotonic()
        self.chunks = 0
        self.delegate = delegate


class _ToolCallState:
    __slots__ = ("novelizer_run_id", "agent_name", "tool_name", "started", "delegate")

    def __init__(self, novelizer_run_id: str, agent_name: str, tool_name: str, delegate: str = "") -> None:
        self.novelizer_run_id = novelizer_run_id
        self.agent_name = agent_name
        self.tool_name = tool_name
        self.started = time.monotonic()
        self.delegate = delegate
```

Update the LLM-call methods to read and thread `metadata`:

```python
    async def on_chat_model_start(self, serialized: dict, messages, *, run_id: UUID, metadata: dict | None = None, **kwargs: Any) -> None:
        await self._start(serialized, render_messages(messages), run_id, metadata)

    async def on_llm_start(self, serialized: dict, prompts: list[str], *, run_id: UUID, metadata: dict | None = None, **kwargs: Any) -> None:
        await self._start(serialized, "\n\n".join(prompts), run_id, metadata)

    async def _start(self, serialized: dict, prompt: str, lc_run_id: UUID, metadata: dict | None = None) -> None:
        nrun = current_run_id.get() or ""
        skw = (serialized or {}).get("kwargs", {})
        model = skw.get("model_name") or skw.get("model") or ""
        delegate = (metadata or {}).get("lc_agent_name", "")
        state = _CallState(nrun, current_agent_name.get(),
                           self._recorder.next_call_index(nrun), model, delegate)
        self._calls[lc_run_id] = state
        await self._recorder.emit(
            TelemetryEventType.LLM_CALL_STARTED, nrun,
            LlmCallStarted(run_id=nrun, agent_name=state.agent_name,
                           call_index=state.call_index, model=model, prompt=prompt,
                           delegate=delegate),
        )
```

Update `on_llm_end`/`on_llm_error` to pass `delegate=state.delegate`:

```python
    async def on_llm_end(self, response, *, run_id: UUID, **kwargs: Any) -> None:
        state = self._calls.pop(run_id, None)
        if state is None:
            return
        tokens = self._usage_tokens(response)
        await self._recorder.emit(
            TelemetryEventType.LLM_CALL_FINISHED, state.novelizer_run_id,
            LlmCallFinished(run_id=state.novelizer_run_id, agent_name=state.agent_name,
                            call_index=state.call_index, model=state.model,
                            duration_s=time.monotonic() - state.started,
                            output_tokens=tokens if tokens else state.chunks,
                            delegate=state.delegate),
        )

    async def on_llm_error(self, error: BaseException, *, run_id: UUID, **kwargs: Any) -> None:
        state = self._calls.pop(run_id, None)
        if state is None:
            return
        await self._recorder.emit(
            TelemetryEventType.LLM_CALL_FAILED, state.novelizer_run_id,
            LlmCallFailed(run_id=state.novelizer_run_id, agent_name=state.agent_name,
                          call_index=state.call_index, model=state.model,
                          duration_s=time.monotonic() - state.started,
                          error_type=type(error).__name__, error_message=str(error),
                          delegate=state.delegate),
        )
```

Update the tool-call methods:

```python
    async def on_tool_start(self, serialized: dict, input_str: str, *, run_id: UUID, metadata: dict | None = None, **kwargs: Any) -> None:
        nrun = current_run_id.get() or ""
        tool_name = (serialized or {}).get("name", "")
        agent_name = current_agent_name.get()
        delegate = (metadata or {}).get("lc_agent_name", "")
        state = _ToolCallState(nrun, agent_name, tool_name, delegate)
        self._tool_calls[run_id] = state
        await self._recorder.emit(
            TelemetryEventType.TOOL_CALL_STARTED, nrun,
            ToolCallStarted(run_id=nrun, agent_name=agent_name, tool_name=tool_name,
                            input_summary=str(input_str)[:300], delegate=delegate),
        )

    async def on_tool_end(self, output: Any, *, run_id: UUID, **kwargs: Any) -> None:
        state = self._tool_calls.pop(run_id, None)
        if state is None:
            return
        await self._recorder.emit(
            TelemetryEventType.TOOL_CALL_FINISHED, state.novelizer_run_id,
            ToolCallFinished(run_id=state.novelizer_run_id, agent_name=state.agent_name,
                             tool_name=state.tool_name,
                             duration_s=time.monotonic() - state.started,
                             output_chars=len(str(output)), delegate=state.delegate),
        )

    async def on_tool_error(self, error: BaseException, *, run_id: UUID, **kwargs: Any) -> None:
        state = self._tool_calls.pop(run_id, None)
        if state is None:
            return
        await self._recorder.emit(
            TelemetryEventType.TOOL_CALL_FAILED, state.novelizer_run_id,
            ToolCallFailed(run_id=state.novelizer_run_id, agent_name=state.agent_name,
                           tool_name=state.tool_name,
                           duration_s=time.monotonic() - state.started,
                           error_type=type(error).__name__, error_message=str(error),
                           delegate=state.delegate),
        )
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `pytest tests/telemetry/test_callbacks.py -v`
Expected: PASS

Also run the full existing telemetry and agent test suites to confirm no regression (existing tests construct these event models without `delegate`, which is fine since it defaults to `""`):

Run: `pytest tests/telemetry/ tests/agents/ -v`
Expected: PASS

- [ ] **Step 6: Commit**

```bash
git add novelizer/telemetry/events.py novelizer/telemetry/callbacks.py tests/telemetry/test_callbacks.py
git commit -m "feat(telemetry): stamp delegate agent name onto LLM/tool call events"
```

---

### Task 4: Engine Room renders delegated tool calls as indented lines

**Files:**
- Modify: `novelizer/tui/widgets/engine_room_model.py`
- Test: `tests/tui/test_engine_room_model.py`

**Interfaces:**
- Consumes: `payload["delegate"]` on `TOOL_CALL_STARTED`/`FINISHED`/`FAILED` `StoredEvent`s (Task 3).
- Produces: no new public functions — `apply_bus_item` and `stream_line_kind` behavior extended; both already tested/consumed by `EngineRoom` widget code elsewhere, unchanged signatures.

- [ ] **Step 1: Write the failing tests**

Add to `tests/tui/test_engine_room_model.py` (append near the existing `test_apply_bus_item_folds_tool_calls_into_the_live_text_stream` test):

```python
def test_apply_bus_item_indents_delegated_tool_calls():
    s = LiveRunState(status="running", run_id="r1", agent_name="character_keeper")
    started = _ev(6, TelemetryEventType.TOOL_CALL_STARTED,
                  {"run_id": "r1", "agent_name": "character_keeper", "tool_name": "read_file",
                   "input_summary": "/chapters/ch-0012.md", "delegate": "researcher"})
    s = apply_bus_item(s, started, now=1.0)
    assert "⚒ ↳ researcher: read_file(/chapters/ch-0012.md)" in s.text

    finished = _ev(7, TelemetryEventType.TOOL_CALL_FINISHED,
                   {"run_id": "r1", "agent_name": "character_keeper", "tool_name": "read_file",
                    "duration_s": 0.4, "delegate": "researcher"})
    s = apply_bus_item(s, finished, now=2.0)
    assert "done in 0.4s" in s.text


def test_apply_bus_item_parent_tool_calls_are_not_indented():
    """Regression: a tool call with no delegate renders exactly as before."""
    s = LiveRunState(status="running", run_id="r1", agent_name="character_keeper")
    started = _ev(6, TelemetryEventType.TOOL_CALL_STARTED,
                  {"run_id": "r1", "agent_name": "character_keeper", "tool_name": "task",
                   "input_summary": "researcher: find X"})
    s = apply_bus_item(s, started, now=1.0)
    assert "⚒ task(researcher: find X)" in s.text
    assert "↳" not in s.text


def test_stream_line_kind_classifies_delegated_tool_lines_as_tool():
    assert stream_line_kind("    ⚒ ↳ researcher: read_file(/x.md)") == "tool"


def test_apply_bus_item_indents_delegated_tool_call_failure():
    s = LiveRunState(status="running", run_id="r1", agent_name="character_keeper")
    failed = _ev(8, TelemetryEventType.TOOL_CALL_FAILED,
                {"run_id": "r1", "agent_name": "character_keeper", "tool_name": "grep",
                 "error_type": "ValueError", "delegate": "researcher"})
    s = apply_bus_item(s, failed, now=1.0)
    assert "✗ ValueError" in s.text
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/tui/test_engine_room_model.py -v`
Expected: FAIL on `test_apply_bus_item_indents_delegated_tool_calls` and `test_stream_line_kind_classifies_delegated_tool_lines_as_tool` (current code renders `⚒ read_file(...)` unindented regardless of `delegate`)

- [ ] **Step 3: Implement the indented rendering**

In `novelizer/tui/widgets/engine_room_model.py`, replace the three `TOOL_CALL_*` branches inside `apply_bus_item` (currently around line 97-103):

```python
    if et == TelemetryEventType.TOOL_CALL_STARTED:
        summary = str(p.get("input_summary", "")).replace("\n", "␤")[:120]
        delegate = p.get("delegate", "")
        if delegate:
            return _append(state, f"\n    ⚒ ↳ {delegate}: {p.get('tool_name', '?')}({summary})\n")
        return _append(state, f"\n⚒ {p.get('tool_name', '?')}({summary})\n")
    if et == TelemetryEventType.TOOL_CALL_FINISHED:
        indent = "       " if p.get("delegate") else "   "
        return _append(state, f"{indent}↳ done in {p.get('duration_s', 0):.1f}s\n")
    if et == TelemetryEventType.TOOL_CALL_FAILED:
        indent = "       " if p.get("delegate") else "   "
        return _append(state, f"{indent}↳ ✗ {p.get('error_type', '?')}\n")
```

`stream_line_kind` already classifies any line whose *stripped* text starts with `⚒` as `"tool"` (see `s.startswith("⚒")` at the top of its checks) — the new `⚒ ↳ researcher: ...` line satisfies that after `.strip()` removes the leading spaces, so no change is needed there. Verify this by re-reading `stream_line_kind`'s body before editing it; only touch it if the test from Step 1 still fails after the `apply_bus_item` change.

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/tui/test_engine_room_model.py -v`
Expected: PASS (all tests, old and new)

- [ ] **Step 5: Commit**

```bash
git add novelizer/tui/widgets/engine_room_model.py tests/tui/test_engine_room_model.py
git commit -m "feat(tui): render delegated subagent tool calls as indented Engine Room lines"
```

---

### Task 5: Settings flags for subagent access (all 9 agents)

**Files:**
- Modify: `novelizer/settings/models.py`
- Modify: `novelizer/settings/layers.py`
- Modify: `novelizer/settings/loader.py`
- Test: `tests/settings/test_models.py` (check `tests/settings/` first for the right existing file to extend — if none fits, create `tests/settings/test_subagent_settings.py`)

**Interfaces:**
- Consumes: nothing from other tasks.
- Produces: nine new boolean settings fields, default `False`: `world_architect_subagent_enabled`, `character_keeper_subagent_enabled`, `editor_subagent_enabled`, `retconner_subagent_enabled`, `structure_analyst_subagent_enabled`, `plotter_subagent_enabled`, `author_subagent_enabled`, `checker_subagent_enabled`, `triage_subagent_enabled`. Task 7/8 read these via `ctx.settings.<name>`.

- [ ] **Step 1: Write the failing test**

Run `ls tests/settings/` first to see what exists, then create (or extend) `tests/settings/test_subagent_settings.py`:

```python
from __future__ import annotations
from novelizer.settings.models import EffectiveSettings, STORY_OVERRIDABLE_KEYS
from novelizer.settings.layers import GlobalConfig, StoryConfig
from novelizer.settings.loader import EnvOverrides

_SUBAGENT_FLAGS = [
    "world_architect_subagent_enabled", "character_keeper_subagent_enabled",
    "editor_subagent_enabled", "retconner_subagent_enabled",
    "structure_analyst_subagent_enabled", "plotter_subagent_enabled",
    "author_subagent_enabled", "checker_subagent_enabled",
]


def test_subagent_flags_default_to_false():
    s = EffectiveSettings()
    for flag in _SUBAGENT_FLAGS + ["triage_subagent_enabled"]:
        assert getattr(s, flag) is False, flag


def test_subagent_flags_are_story_overridable():
    """triage_subagent_enabled is intentionally excluded -- its tools_enabled
    counterpart isn't story-overridable either (settings/loader.py has no
    triage_tools_enabled field), so this mirrors that existing precedent."""
    for flag in _SUBAGENT_FLAGS:
        assert flag in STORY_OVERRIDABLE_KEYS, flag


def test_global_config_accepts_subagent_flags():
    cfg = GlobalConfig(character_keeper_subagent_enabled=True)
    assert cfg.character_keeper_subagent_enabled is True


def test_story_config_accepts_subagent_flags():
    cfg = StoryConfig(character_keeper_subagent_enabled=True)
    assert cfg.character_keeper_subagent_enabled is True


def test_env_overrides_accepts_subagent_flags():
    env = EnvOverrides(character_keeper_subagent_enabled=True)
    assert env.character_keeper_subagent_enabled is True
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/settings/test_subagent_settings.py -v`
Expected: FAIL — `AttributeError` on the first `getattr(s, flag)` call (fields don't exist yet)

- [ ] **Step 3: Add the fields**

In `novelizer/settings/models.py`, add to `STORY_OVERRIDABLE_KEYS` (append inside the existing `frozenset({...})` literal, near the other `*_tools_enabled` entries):

```python
    "world_architect_subagent_enabled", "character_keeper_subagent_enabled",
    "editor_subagent_enabled", "retconner_subagent_enabled", "structure_analyst_subagent_enabled",
    "plotter_subagent_enabled", "author_subagent_enabled", "checker_subagent_enabled",
```

And add the fields themselves to `EffectiveSettings`, right after the existing `triage_tools_enabled: bool = True` line:

```python
    # Subagent (delegated researcher) enablement -- separate from *_tools_enabled
    # per-agent, and only meaningful when the matching tools flag is also on.
    world_architect_subagent_enabled: bool = False
    character_keeper_subagent_enabled: bool = False
    editor_subagent_enabled: bool = False
    retconner_subagent_enabled: bool = False
    structure_analyst_subagent_enabled: bool = False
    plotter_subagent_enabled: bool = False
    author_subagent_enabled: bool = False
    checker_subagent_enabled: bool = False
    triage_subagent_enabled: bool = False
```

In `novelizer/settings/layers.py`, add to **both** `GlobalConfig` and `StoryConfig` (right after each class's existing `plotter_tools_enabled: bool | None = None` line — `triage` is intentionally excluded here, matching that `triage_tools_enabled` is also absent from these two classes):

```python
    world_architect_subagent_enabled: bool | None = None
    character_keeper_subagent_enabled: bool | None = None
    editor_subagent_enabled: bool | None = None
    retconner_subagent_enabled: bool | None = None
    structure_analyst_subagent_enabled: bool | None = None
    plotter_subagent_enabled: bool | None = None
    author_subagent_enabled: bool | None = None
    checker_subagent_enabled: bool | None = None
```

In `novelizer/settings/loader.py`, add the same block to `EnvOverrides` (right after its existing `plotter_tools_enabled: bool | None = None` line):

```python
    world_architect_subagent_enabled: bool | None = None
    character_keeper_subagent_enabled: bool | None = None
    editor_subagent_enabled: bool | None = None
    retconner_subagent_enabled: bool | None = None
    structure_analyst_subagent_enabled: bool | None = None
    plotter_subagent_enabled: bool | None = None
    author_subagent_enabled: bool | None = None
    checker_subagent_enabled: bool | None = None
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/settings/ -v`
Expected: PASS (new tests, plus no regression in the rest of `tests/settings/`)

- [ ] **Step 5: Commit**

```bash
git add novelizer/settings/models.py novelizer/settings/layers.py novelizer/settings/loader.py tests/settings/test_subagent_settings.py
git commit -m "feat(settings): add per-agent subagent-dispatch enablement flags"
```

---

### Task 6: `Runtime._tooled` threads the researcher subagent through

**Files:**
- Modify: `novelizer/runtime.py`
- Test: `tests/test_runtime.py` (read the existing `_tooled`/`_phase_a_toolkit` tests first, if any, to match style — search with `grep -n "_tooled" tests/test_runtime.py`)

**Interfaces:**
- Consumes: `build_researcher_subagent` (Task 2).
- Produces: `Runtime._tooled(builder, enabled, subagent_enabled=False, subagent_agent_name="")` — extended signature, backward compatible (existing two-positional-arg call sites keep working unchanged until Task 7/8 updates them). When `enabled and subagent_enabled`, the wrapped builder is called with an extra `subagents=[build_researcher_subagent(subagent_agent_name)]` kwarg; otherwise `subagents=None`.

- [ ] **Step 1: Write the failing test**

Run: `grep -n "_tooled\|_phase_a_toolkit" tests/test_runtime.py`

Read whatever's there for the existing fixture pattern (likely a `Runtime` instance built with fake settings/stores), then add:

```python
def test_tooled_passes_no_subagents_kwarg_when_subagent_disabled(runtime):  # reuse existing fixture name
    calls = []

    def builder(settings, callbacks=None, backend=None, tools=None, subagents=None):
        calls.append(subagents)
        return "runner"

    wrapped = runtime._tooled(builder, enabled=True, subagent_enabled=False, subagent_agent_name="character_keeper")
    wrapped(runtime.settings)
    assert calls == [None]


def test_tooled_passes_researcher_subagent_when_enabled(runtime):
    calls = []

    def builder(settings, callbacks=None, backend=None, tools=None, subagents=None):
        calls.append(subagents)
        return "runner"

    wrapped = runtime._tooled(builder, enabled=True, subagent_enabled=True, subagent_agent_name="character_keeper")
    wrapped(runtime.settings)
    assert len(calls) == 1
    assert calls[0][0]["name"] == "researcher"


def test_tooled_bare_builder_unchanged_when_tools_disabled(runtime):
    def builder(settings, callbacks=None):
        return "bare-runner"

    wrapped = runtime._tooled(builder, enabled=False, subagent_enabled=True, subagent_agent_name="character_keeper")
    assert wrapped is builder
```

If no `runtime`/equivalent fixture already exists in `tests/test_runtime.py`, build a minimal one inline instead — a `Runtime` constructed with the same fakes the rest of that file already uses (check the file's top-level fixtures/imports before writing this from scratch).

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_runtime.py -k tooled -v`
Expected: FAIL — `TypeError: _tooled() got an unexpected keyword argument 'subagent_enabled'`

- [ ] **Step 3: Implement**

In `novelizer/runtime.py`, replace `_tooled` (currently around line 132-143):

```python
    def _tooled(self, builder, enabled: bool, subagent_enabled: bool = False,
                subagent_agent_name: str = ""):
        """Wrap a runner builder so pull-mode agents keep their canon
        backend/tools on every build -- both the initial start() build and any
        later apply_settings rebuild. Returns a plain builder(settings,
        callbacks=None) callable; when `enabled` is False it's the bare
        builder unchanged.

        subagent_enabled additionally passes a `subagents=[researcher]` kwarg
        through to the builder -- a no-op when `enabled` is False, since a
        subagent with no backend/tools to read from is moot (settings guard,
        subagent-tooling design decision 6)."""
        if not enabled:
            return builder
        from novelizer.agents.subagents import build_researcher_subagent
        backend, tools = self._canon_backend, self._canon_tools
        subagents = [build_researcher_subagent(subagent_agent_name)] if subagent_enabled else None
        return lambda settings, callbacks=None: builder(
            settings, callbacks=callbacks, backend=backend, tools=tools, subagents=subagents,
        )
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_runtime.py -k tooled -v`
Expected: PASS

Then run the full runtime suite to confirm the existing `_tooled` call sites (still passing only two positional args, from every agent's `_construct`, all still unmodified until Task 7/8) keep working:

Run: `pytest tests/test_runtime.py -v`
Expected: PASS — **this will actually FAIL at this point**, because every existing `build_X_runner` function does not yet accept a `subagents` kwarg, and `_tooled`'s wrapped lambda now unconditionally passes `subagents=None` into every tooled builder call. This is expected and intentional: Task 7 (pilot) and Task 8 (rollout) fix this by adding `subagents=None` to every `build_X_runner` signature. Confirm the failure is specifically `TypeError: build_..._runner() got an unexpected keyword argument 'subagents'` on tools-enabled test paths (not something else), then proceed to Task 7 immediately — do not consider Task 6 "done" in the sense of a green full suite; only its own `-k tooled` tests need to be green here.

- [ ] **Step 5: Commit**

```bash
git add novelizer/runtime.py tests/test_runtime.py
git commit -m "feat(runtime): thread researcher subagent through _tooled"
```

---

### Task 7: Wire Character Keeper (pilot)

**Files:**
- Modify: `novelizer/agents/character_keeper.py`
- Test: `tests/agents/test_character_keeper.py`

**Interfaces:**
- Consumes: `build_researcher_subagent` (Task 2), `SubagentGrant` (Task 1), `Runtime._tooled`'s new kwargs (Task 6).
- Produces: `build_character_keeper_runner(settings, callbacks=None, backend=None, tools=None, subagents=None)` — extended signature; `SPEC.subagent_grant` set; `_construct` reads `character_keeper_subagent_enabled`. This is the reference pattern Task 8 repeats for the other 8 agents.

- [ ] **Step 1: Write the failing tests**

Add to `tests/agents/test_character_keeper.py` (near the existing `test_build_character_keeper_runner_tooled_branch_passes_keeper_skills` test):

```python
def test_build_character_keeper_runner_tooled_branch_passes_subagents_through(monkeypatch):
    from novelizer.agents import character_keeper as keeper_mod
    from novelizer.canon_fs.backend import CanonBackend

    captured = {}

    class FakeGraph:
        def with_config(self, config):
            return self

    def fake_create_deep_agent(*, model, system_prompt, response_format, backend=None,
                                tools=None, skills=None, middleware=None, subagents=None):
        captured["subagents"] = subagents
        return FakeGraph()

    import deepagents
    monkeypatch.setattr(deepagents, "create_deep_agent", fake_create_deep_agent)

    backend = CanonBackend(read_store=None)
    researcher = {"name": "researcher", "description": "d", "system_prompt": "p"}
    keeper_mod.build_character_keeper_runner(_FakeSettings(), backend=backend, tools=[],
                                              subagents=[researcher])
    assert captured["subagents"] == [researcher]


def test_construct_reads_subagent_enabled_setting():
    from novelizer.agents.character_keeper import _construct
    from novelizer.agents.registry_types import AgentContext

    seen = {}

    def fake_tooled(builder, enabled, subagent_enabled=False, subagent_agent_name=""):
        seen["enabled"] = enabled
        seen["subagent_enabled"] = subagent_enabled
        seen["subagent_agent_name"] = subagent_agent_name
        return builder

    class _Settings:
        character_keeper_tools_enabled = True
        character_keeper_subagent_enabled = True
        default_agent_interval = 120
        keeper_prose_chars = 6000

    ctx = AgentContext(
        read=None, committer=None, events=None, settings=_Settings(),
        casting_note="", personalities={}, provenance={}, tooled=fake_tooled,
        runner_for=lambda name, builder, fallback_name=None: builder(_Settings()),
    )
    _construct(ctx)
    assert seen == {"enabled": True, "subagent_enabled": True, "subagent_agent_name": "character_keeper"}


def test_spec_carries_subagent_grant():
    from novelizer.agents.character_keeper import SPEC
    assert SPEC.subagent_grant.enabled_setting == "character_keeper_subagent_enabled"
```

The existing `test_build_character_keeper_runner_tooled_branch_passes_keeper_skills` test's `fake_create_deep_agent` doesn't declare a `subagents` parameter — update its signature too (in the same edit pass) to add `subagents=None` alongside its other keyword-only params, matching the new real call shape; it doesn't need to assert on it, just accept it without raising `TypeError`.

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/agents/test_character_keeper.py -v`
Expected: FAIL — `test_build_character_keeper_runner_tooled_branch_passes_subagents_through` fails with `TypeError: build_character_keeper_runner() got an unexpected keyword argument 'subagents'`; `test_construct_reads_subagent_enabled_setting` fails with `AttributeError` on `SPEC.subagent_grant` or a `TypeError` from `fake_tooled` receiving only 2 args; `test_spec_carries_subagent_grant` fails with `AttributeError: NoneType has no attribute 'enabled_setting'`. Also, `test_build_character_keeper_runner_tooled_branch_passes_keeper_skills` now fails with the same `unexpected keyword argument` error once Step 3 changes the real call — confirm this ordering by running the whole file, not just the new tests.

- [ ] **Step 3: Implement**

In `novelizer/agents/character_keeper.py`, change `build_character_keeper_runner`'s signature and its `create_deep_agent` call (currently around line 279-300):

```python
def build_character_keeper_runner(settings, callbacks=None, backend=None, tools=None, subagents=None):
    from deepagents import create_deep_agent
    from novelizer.agents.llm import build_chat_model
    from novelizer.agents.middleware import ExcludeToolsMiddleware
    if backend is not None:
        model = build_chat_model(
            settings.agent_model, settings.llm_base_url, settings.llm_api_key,
            settings.agent_temperature, max_tokens=settings.llm_max_tokens,
            callbacks=None, streaming=callbacks is not None,
        )
        system_prompt = SYSTEM_PROMPT + KEEPER_PULL_NOTE
        graph = create_deep_agent(
            model=model, system_prompt=system_prompt, response_format=KeeperOutput,
            backend=backend, tools=tools, skills=KEEPER_SKILLS, subagents=subagents,
            middleware=[ExcludeToolsMiddleware(excluded=frozenset({"write_todos"}))],
        )
        config = {"recursion_limit": GRAPH_RECURSION_LIMIT}
        if callbacks:
            config["callbacks"] = callbacks
        return graph.with_config(config)
    model = build_chat_model(settings.agent_model, settings.llm_base_url, settings.llm_api_key, settings.agent_temperature, max_tokens=settings.llm_max_tokens, callbacks=callbacks)
    return create_deep_agent(model=model, system_prompt=SYSTEM_PROMPT, response_format=KeeperOutput)
```

Update `_construct` (currently around line 306-317):

```python
def _construct(ctx: AgentContext) -> CharacterKeeper:
    enabled = ctx.settings.character_keeper_tools_enabled
    subagent_enabled = ctx.settings.character_keeper_subagent_enabled
    builder = ctx.tooled(build_character_keeper_runner, enabled, subagent_enabled, "character_keeper")
    runner = ctx.runner_for("character_keeper", builder)
    return CharacterKeeper(
        runner, ctx.read, ctx.committer,
        interval=ctx.settings.default_agent_interval,
        personality=ctx.personalities.get("character_keeper", ""),
        prose_chars=ctx.settings.keeper_prose_chars,
        pull_mode=enabled,
    )
```

Update `SPEC` (currently around line 319-323):

```python
SPEC = AgentSpec(
    name="character_keeper",
    tool_grant=ToolGrant(enabled_setting="character_keeper_tools_enabled"),
    subagent_grant=SubagentGrant(enabled_setting="character_keeper_subagent_enabled"),
    construct=_construct,
)
```

And update the import line just above `_construct` (currently `from novelizer.agents.registry_types import AgentContext, AgentSpec, ToolGrant`):

```python
from novelizer.agents.registry_types import AgentContext, AgentSpec, ToolGrant, SubagentGrant
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/agents/test_character_keeper.py -v`
Expected: PASS (all tests)

Then confirm `tests/test_runtime.py` is green again now that Character Keeper's builder accepts `subagents`:

Run: `pytest tests/test_runtime.py -v`
Expected: Still likely FAIL for the other 8 agents (their builders don't accept `subagents` yet) — Task 8 fixes those. If `tests/test_runtime.py` is fully green already at this point, that's fine too (it may not exercise every agent's tooled path); either outcome is acceptable here.

- [ ] **Step 5: Commit**

```bash
git add novelizer/agents/character_keeper.py tests/agents/test_character_keeper.py
git commit -m "feat(agents): wire subagent dispatch into Character Keeper"
```

---

### Task 8: Roll out subagent wiring to the remaining 8 agents

**Files:**
- Modify: `novelizer/agents/world_architect.py`
- Modify: `novelizer/agents/editor.py`
- Modify: `novelizer/agents/retconner.py`
- Modify: `novelizer/agents/structure_analyst.py`
- Modify: `novelizer/agents/plotter.py`
- Modify: `novelizer/agents/continuity_checker.py`
- Modify: `novelizer/agents/author.py`
- Modify: `novelizer/agents/triage.py`
- Test: `tests/agents/test_world_architect.py`, `tests/agents/test_editor.py`, `tests/agents/test_retconner.py`, `tests/agents/test_structure_analyst.py`, `tests/agents/test_plotter.py`, `tests/agents/test_continuity_checker.py`, `tests/agents/test_author.py`, `tests/agents/test_triage.py` (one test per file, mirroring Task 7's `test_spec_carries_subagent_grant` pattern — check each file exists with that name first via `ls tests/agents/`)

**Interfaces:**
- Consumes: same as Task 7, repeated per agent.
- Produces: same shape as Task 7's Character Keeper wiring, for all 8 remaining agents. Every `build_X_runner(settings, callbacks=None, backend=None, tools=None)` in `AGENT_REGISTRY` now additionally accepts `subagents=None` and forwards it to `create_deep_agent`; every `SPEC` carries a `subagent_grant`.

This task repeats the same three edits (builder signature + `create_deep_agent` call, `_construct`, `SPEC`) eight times. Each agent's setting name and system-prompt variable differ; everything else is mechanical. Do this one agent at a time, in the order below, running that agent's own test file (not the whole suite) between each — the full-suite check happens once at the end in Step 4.

- [ ] **Step 1: World Architect**

Add to `tests/agents/test_world_architect.py`:

```python
def test_spec_carries_subagent_grant():
    from novelizer.agents.world_architect import SPEC
    assert SPEC.subagent_grant.enabled_setting == "world_architect_subagent_enabled"
```

Run: `pytest tests/agents/test_world_architect.py -k subagent_grant -v` — expect FAIL (`AttributeError`).

In `novelizer/agents/world_architect.py`:

```python
def build_world_architect_runner(settings, callbacks=None, backend=None, tools=None, subagents=None):
    from deepagents import create_deep_agent
    from novelizer.agents.llm import build_chat_model
    from novelizer.agents.middleware import ExcludeToolsMiddleware
    if backend is not None:
        model = build_chat_model(
            settings.agent_model, settings.llm_base_url, settings.llm_api_key,
            settings.agent_temperature, max_tokens=settings.llm_max_tokens,
            callbacks=None, streaming=callbacks is not None,
        )
        system_prompt = SYSTEM_PROMPT + RETRIEVAL_NOTE_BASE
        graph = create_deep_agent(
            model=model, system_prompt=system_prompt, response_format=WorldEntriesDraft,
            backend=backend, tools=tools, subagents=subagents,
            middleware=[ExcludeToolsMiddleware(excluded=frozenset({"write_todos"}))],
        )
        config = {"recursion_limit": GRAPH_RECURSION_LIMIT}
        if callbacks:
            config["callbacks"] = callbacks
        return graph.with_config(config)
    model = build_chat_model(settings.agent_model, settings.llm_base_url, settings.llm_api_key, settings.agent_temperature, max_tokens=settings.llm_max_tokens, callbacks=callbacks)
    return create_deep_agent(model=model, system_prompt=SYSTEM_PROMPT, response_format=WorldEntriesDraft)
```

```python
def _construct(ctx: AgentContext) -> WorldArchitect:
    enabled = ctx.settings.world_architect_tools_enabled
    subagent_enabled = ctx.settings.world_architect_subagent_enabled
    builder = ctx.tooled(build_world_architect_runner, enabled, subagent_enabled, "world_architect")
    # ...(keep every remaining line in the existing function body exactly as-is)
```

```python
SPEC = AgentSpec(
    name="world_architect",
    tool_grant=ToolGrant(enabled_setting="world_architect_tools_enabled"),
    subagent_grant=SubagentGrant(enabled_setting="world_architect_subagent_enabled"),
    construct=_construct,
)
```

Update `from novelizer.agents.registry_types import AgentContext, AgentSpec, ToolGrant` to add `, SubagentGrant`.

Run: `pytest tests/agents/test_world_architect.py -v` — expect PASS.

Commit:

```bash
git add novelizer/agents/world_architect.py tests/agents/test_world_architect.py
git commit -m "feat(agents): wire subagent dispatch into World Architect"
```

- [ ] **Step 2: Editor**

Add to `tests/agents/test_editor.py`:

```python
def test_spec_carries_subagent_grant():
    from novelizer.agents.editor import SPEC
    assert SPEC.subagent_grant.enabled_setting == "editor_subagent_enabled"
```

Run: `pytest tests/agents/test_editor.py -k subagent_grant -v` — expect FAIL.

In `novelizer/agents/editor.py`, apply the same three edits as World Architect, substituted for Editor:

```python
def build_editor_runner(settings, callbacks=None, backend=None, tools=None, subagents=None):
    from deepagents import create_deep_agent
    from novelizer.agents.llm import build_chat_model
    from novelizer.agents.middleware import ExcludeToolsMiddleware
    if backend is not None:
        model = build_chat_model(
            settings.agent_model, settings.llm_base_url, settings.llm_api_key,
            settings.agent_temperature, max_tokens=settings.llm_max_tokens,
            callbacks=None, streaming=callbacks is not None,
        )
        system_prompt = SYSTEM_PROMPT + RETRIEVAL_NOTE_BASE
        graph = create_deep_agent(
            model=model, system_prompt=system_prompt, response_format=EditorVerdict,
            backend=backend, tools=tools, subagents=subagents,
            middleware=[ExcludeToolsMiddleware(excluded=frozenset({"write_todos"}))],
        )
        config = {"recursion_limit": GRAPH_RECURSION_LIMIT}
        if callbacks:
            config["callbacks"] = callbacks
        return graph.with_config(config)
    model = build_chat_model(settings.agent_model, settings.llm_base_url, settings.llm_api_key, settings.agent_temperature, max_tokens=settings.llm_max_tokens, callbacks=callbacks)
    return create_deep_agent(model=model, system_prompt=SYSTEM_PROMPT, response_format=EditorVerdict)
```

```python
def _construct(ctx: AgentContext) -> Editor:
    enabled = ctx.settings.editor_tools_enabled
    subagent_enabled = ctx.settings.editor_subagent_enabled
    builder = ctx.tooled(build_editor_runner, enabled, subagent_enabled, "editor")
    # ...(keep the rest of the existing body as-is)
```

```python
SPEC = AgentSpec(
    name="editor",
    tool_grant=ToolGrant(enabled_setting="editor_tools_enabled"),
    subagent_grant=SubagentGrant(enabled_setting="editor_subagent_enabled"),
    construct=_construct,
)
```

Update the `registry_types` import line to add `SubagentGrant`.

Run: `pytest tests/agents/test_editor.py -v` — expect PASS.

Commit:

```bash
git add novelizer/agents/editor.py tests/agents/test_editor.py
git commit -m "feat(agents): wire subagent dispatch into Editor"
```

- [ ] **Step 3: Retconner, Structure Analyst, Plotter, Continuity Checker, Author, Triage**

Repeat the exact same three-edit pattern from Steps 1-2 for each remaining agent, one at a time, each as its own commit. Use this table for the per-agent substitutions (everything else — the `if backend is not None:` structure, the bare-branch fallback, the middleware list, the rest of each function body — stays exactly as it already is in each file; only add `subagents=None` to the signature, `subagents=subagents` to the `create_deep_agent(...)` call inside the `if backend is not None:` branch, the two new lines in `_construct`, the `subagent_grant=` line in `SPEC`, and `SubagentGrant` to the registry_types import):

| Agent | File | Setting name | `response_format=` | Test file | Existing test to add |
|---|---|---|---|---|---|
| Retconner | `novelizer/agents/retconner.py` | `retconner_subagent_enabled` | `RetconAmendments` | `tests/agents/test_retconner.py` | `assert SPEC.subagent_grant.enabled_setting == "retconner_subagent_enabled"` |
| Structure Analyst | `novelizer/agents/structure_analyst.py` | `structure_analyst_subagent_enabled` | `StructureAnalystOutput` | `tests/agents/test_structure_analyst.py` | `assert SPEC.subagent_grant.enabled_setting == "structure_analyst_subagent_enabled"` |
| Plotter | `novelizer/agents/plotter.py` | `plotter_subagent_enabled` | `PlotterOutput` | `tests/agents/test_plotter.py` | `assert SPEC.subagent_grant.enabled_setting == "plotter_subagent_enabled"` |
| Continuity Checker | `novelizer/agents/continuity_checker.py` | `checker_subagent_enabled` | `ContinuityOutput` | `tests/agents/test_continuity_checker.py` | `assert SPEC.subagent_grant.enabled_setting == "checker_subagent_enabled"` |
| Author | `novelizer/agents/author.py` | `author_subagent_enabled` | `ChapterDraft` | `tests/agents/test_author.py` | `assert SPEC.subagent_grant.enabled_setting == "author_subagent_enabled"` |
| Triage | `novelizer/agents/triage.py` | `triage_subagent_enabled` | `TriageVerdict` | `tests/agents/test_triage.py` | `assert SPEC.subagent_grant.enabled_setting == "triage_subagent_enabled"` |

For **Plotter** specifically: its `build_plotter_runner` uses `skills=PLOTTER_SKILLS, middleware=[TodoContextMiddleware()]` (no `ExcludeToolsMiddleware`) — keep that as-is, just add `subagents=subagents` alongside the existing `backend=backend, tools=tools, skills=PLOTTER_SKILLS` kwargs on the `create_deep_agent(...)` call. Also: `tests/agents/test_plotter.py` has a `fake_create_deep_agent` with a strict keyword-only signature (like Task 7's Character Keeper one) — add `subagents=None` to that fake's signature in the same edit pass, or its existing tests will start raising `TypeError`.

For **Continuity Checker** specifically: `build_continuity_checker_runner` builds `model` *before* the `if backend is not None:` check (not inside it, unlike the others) — leave that ordering untouched; only the `create_deep_agent(...)` call inside the `if backend is not None:` branch gets `subagents=subagents` added, and the function signature gets `subagents=None` added. Its `_construct` reads `ctx.settings.checker_tools_enabled` (not `continuity_checker_tools_enabled`) — the new line is `subagent_enabled = ctx.settings.checker_subagent_enabled`, and the `ctx.tooled(...)` call's fourth argument (the subagent's `agent_name` passed to `build_researcher_subagent`) should be `"continuity_checker"` (matching the researcher's `SYSTEM_PROMPT.format(agent_name=...)` text, not the settings-flag prefix `checker`).

For **Author** specifically: `build_author_runner` also builds `model` before the `if backend is not None:` check, same as Continuity Checker — same handling. Its `_construct` reads `ctx.settings.author_tools_enabled`; add `subagent_enabled = ctx.settings.author_subagent_enabled` and pass `"author"` as the fourth `ctx.tooled(...)` argument. `tests/agents/test_author.py` has a strict `fake_create_deep_agent` (same as Character Keeper/Plotter) — add `subagents=None` to its signature too.

For **Triage**: confirm `SPEC.tool_grant=ToolGrant(enabled_setting="triage_tools_enabled")` and follow the same pattern; its settings flag is `triage_subagent_enabled`, which Task 5 added to `models.py` only (not `layers.py`/`loader.py`, matching `triage_tools_enabled`'s existing precedent) — no extra step needed here, `ctx.settings.triage_subagent_enabled` still resolves fine from `EffectiveSettings`' default.

After each agent's three edits: run `pytest tests/agents/test_<agent>.py -v`, confirm PASS, then commit with message `feat(agents): wire subagent dispatch into <Agent Name>`.

- [ ] **Step 4: Full-suite regression check**

Once all 8 agents in this task (plus Character Keeper from Task 7) are wired, run the complete test suite to confirm nothing else broke — **per this repo's standing rule, do NOT run the test suite in the main checkout; run it inside this worktree only** (see `docs/TESTING-TUI.md` if the pytest run shows load-flaky TUI pilot failures — compare against a from-scratch run of the same scope before concluding a failure is real):

Run: `pytest tests/ -v`
Expected: PASS across the board — `tests/test_runtime.py`'s `_tooled` tests (Task 6), every agent's `SPEC.subagent_grant` test, and every existing test that was passing before this plan started.

- [ ] **Step 5: Update `AGENT_REGISTRY` doc comment (optional but cheap — do it)**

`novelizer/agents/registry.py`'s module comment currently only explains scheduling order. No code change needed here — `AGENT_REGISTRY` already references each module's `SPEC`, and `SPEC.subagent_grant` is now populated automatically for 9 of the 10 registry entries (`muse` and any future entries with `tool_grant=None` correctly have `subagent_grant=None` too, the dataclass default). Confirm this with a quick manual check rather than a code edit:

Run: `python3 -c "from novelizer.agents.registry import AGENT_REGISTRY; [print(s.name, s.subagent_grant) for s in AGENT_REGISTRY]"`

Expected output: every agent except `muse` prints a `SubagentGrant(enabled_setting='...')`; `muse` prints `None`.

- [ ] **Step 6: Final commit (only if Step 5's manual check required any fix)**

If Step 5 revealed nothing needing a code change (the expected outcome), there is nothing to commit here — Step 3's per-agent commits already cover this task's changes. If it revealed a gap, fix it, run the affected test file, and commit:

```bash
git add novelizer/agents/registry.py
git commit -m "fix(agents): <describe the specific gap found>"
```

---

## Self-Review Notes

- **Spec coverage:** Decision 1 (general infra) → Tasks 1, 6, 7, 8. Decision 2 (shared researcher role) → Task 2. Decision 3 (no tool subsetting) → Task 2 (no `tools` key). Decision 4 (same model) → Task 2 (no `model` key). Decision 5 (separate settings flag) → Tasks 1, 5. Decision 6 (silent no-op when tools off) → Task 6 (`if not enabled: return builder` short-circuits before subagent logic ever runs). Decision 7 (indented Engine Room lines) → Task 4. Telemetry tagging mechanism → Task 3. All Architecture subsections (factory, registry wiring, telemetry, rendering) have a corresponding task.
- **Out-of-scope items from the spec** (per-agent tool subsetting, separate/cheaper subagent model, multiple subagent roles, settings validation errors, chat/research persona rollout) are correctly *not* attempted anywhere in this plan.
- **Type/name consistency check:** `build_researcher_subagent(agent_name, extra_instructions="")` (Task 2) is called identically in Task 6 (`build_researcher_subagent(subagent_agent_name)`) — same positional-first-arg shape throughout. `SubagentGrant(enabled_setting=...)` (Task 1) is instantiated identically in every agent's `SPEC` (Tasks 7-8). `ctx.tooled(builder, enabled, subagent_enabled, name)` argument order is identical across Task 6's `_tooled` definition and every `_construct` call site in Tasks 7-8.
