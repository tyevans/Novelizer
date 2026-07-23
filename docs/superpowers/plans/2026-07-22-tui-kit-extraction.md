# tui_kit Extraction Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Extract the generic "watch N agents run" console (Engine Room, live token-stream panel, activity strip, roster glyph strip) out of `novelizer/tui/` into a new domain-agnostic top-level package `tui_kit/`, so future non-novel domains can reuse it without importing novelizer.

**Architecture:** `tui_kit/contracts.py` defines a minimal event vocabulary (RunStarted, ToolCallStarted, TokenDelta, etc.) and an `AgentTheme` protocol. `tui_kit/run_model.py` is the pure (no Textual) state machine and formatters, taking an `AgentTheme` instead of hardcoding novelizer's agent list/verbs/glyphs. `tui_kit/widgets/` holds the Textual widgets built on top. `novelizer/tui/identity.py` implements `AgentTheme`; a new `novelizer/tui/telemetry_adapter.py` translates novelizer's real telemetry vocabulary (`StoredEvent`/`TelemetryEventType`/`TokenDelta`/`ToolSummaryReady`) into `tui_kit.contracts` events, and keeps `trace_line`/`trace_detail` (which stay novelizer-specific because they render domain events, not just telemetry).

**Tech Stack:** Python 3.13, Textual, Rich, pytest + pytest-asyncio + hypothesis, import-linter.

## Global Constraints

- `tui_kit` must never import `novelizer`, `substrate`, or `research_domain` — enforced by an import-linter contract (spec: "Import boundary").
- Every step that changes behavior gets a failing-then-passing test cycle (TDD, per project engineering principles).
- Commit after each task.
- Files under `tests/` are exempt from any import-boundary rules — same convention as `substrate`.
- Do not touch `brain_model.py`, `feed_model.py`, `browser_model.py`, `story_picker.py`, `chat_screen.py`'s non-engine-room logic, `research_screen.py`, or any other screen — out of scope per spec.

---

### Task 1: `tui_kit` package skeleton + `contracts.py`

**Files:**
- Create: `tui_kit/__init__.py`
- Create: `tui_kit/contracts.py`
- Test: `tests/tui_kit/__init__.py`
- Test: `tests/tui_kit/test_contracts.py`

**Interfaces:**
- Produces: `tui_kit.contracts.{RunStarted, RunFinished, RunFailed, LLMCallStarted, LLMCallFinished, ToolCallStarted, ToolCallFinished, ToolCallFailed, TokenDelta, ToolSummaryReady}` — frozen dataclasses. `tui_kit.contracts.AgentTheme` — a `typing.Protocol` with `glyph(name) -> str`, `label(name) -> str`, `style(name) -> str`, `verb(name) -> str`.

- [ ] **Step 1: Write the failing test**

```python
# tests/tui_kit/__init__.py
```
(empty file, makes the directory a package)

```python
# tests/tui_kit/test_contracts.py
from tui_kit.contracts import AgentTheme, RunStarted, TokenDelta, ToolCallStarted


class _FakeTheme:
    def glyph(self, agent_name: str) -> str:
        return "@"

    def label(self, agent_name: str) -> str:
        return agent_name.title()

    def style(self, agent_name: str) -> str:
        return "bold"

    def verb(self, agent_name: str) -> str:
        return "working"


def test_fake_theme_satisfies_the_agent_theme_protocol():
    theme: AgentTheme = _FakeTheme()
    assert theme.glyph("author") == "@"
    assert theme.label("author") == "Author"
    assert theme.style("author") == "bold"
    assert theme.verb("author") == "working"


def test_contract_events_are_frozen_dataclasses_with_expected_fields():
    started = RunStarted(run_id="r1", agent_name="author")
    assert started.run_id == "r1" and started.agent_name == "author"
    delta = TokenDelta(run_id="r1", agent_name="author", text="hi")
    assert delta.kind == "text"
    tool = ToolCallStarted(run_id="r1", agent_name="author", tool_name="search",
                           input_summary="q")
    assert tool.delegate == ""
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/tui_kit/test_contracts.py -v`
Expected: FAIL with "No module named 'tui_kit'"

- [ ] **Step 3: Write minimal implementation**

```python
# tui_kit/__init__.py
```
(empty)

```python
# tui_kit/contracts.py
"""Domain-agnostic event vocabulary and theming contract for tui_kit.

A consuming domain (novelizer, a future research or coding domain) adapts
its own telemetry into these dataclasses and supplies an AgentTheme -- this
module has no knowledge of any concrete domain's event or agent shapes.
"""
from __future__ import annotations
from dataclasses import dataclass
from typing import Protocol


class AgentTheme(Protocol):
    """How a domain presents its agents: glyph/color/verb per agent name."""

    def glyph(self, agent_name: str) -> str: ...
    def label(self, agent_name: str) -> str: ...
    def style(self, agent_name: str) -> str: ...
    def verb(self, agent_name: str) -> str: ...


@dataclass(frozen=True)
class RunStarted:
    run_id: str
    agent_name: str


@dataclass(frozen=True)
class RunFinished:
    run_id: str
    agent_name: str
    duration_s: float = 0.0


@dataclass(frozen=True)
class RunFailed:
    run_id: str
    agent_name: str
    error_type: str
    error_message: str


@dataclass(frozen=True)
class LLMCallStarted:
    run_id: str
    agent_name: str
    call_index: int
    model: str
    prompt: str


@dataclass(frozen=True)
class LLMCallFinished:
    run_id: str
    agent_name: str
    call_index: int
    duration_s: float
    output_tokens: int


@dataclass(frozen=True)
class ToolCallStarted:
    run_id: str
    agent_name: str
    tool_name: str
    input_summary: str
    delegate: str = ""


@dataclass(frozen=True)
class ToolCallFinished:
    run_id: str
    agent_name: str
    tool_name: str
    duration_s: float
    output_summary: str = ""


@dataclass(frozen=True)
class ToolCallFailed:
    run_id: str
    agent_name: str
    tool_name: str
    duration_s: float
    error_type: str


@dataclass(frozen=True)
class TokenDelta:
    run_id: str
    agent_name: str
    text: str
    kind: str = "text"  # "text" | "thinking"


@dataclass(frozen=True)
class ToolSummaryReady:
    run_id: str
    agent_name: str
    tool_name: str
    input_summary: str
    summary: str
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/tui_kit/test_contracts.py -v`
Expected: PASS (4 tests)

- [ ] **Step 5: Commit**

```bash
git add tui_kit/__init__.py tui_kit/contracts.py tests/tui_kit/__init__.py tests/tui_kit/test_contracts.py
git commit -m "feat(tui_kit): add domain-agnostic event contracts and AgentTheme protocol"
```

---

### Task 2: `tui_kit/run_model.py` — pure state machine and formatters

**Files:**
- Create: `tui_kit/run_model.py`
- Test: `tests/tui_kit/test_run_model.py`

**Interfaces:**
- Consumes: `tui_kit.contracts.{RunStarted, RunFinished, RunFailed, LLMCallStarted, LLMCallFinished, ToolCallStarted, ToolCallFinished, ToolCallFailed, TokenDelta, ToolSummaryReady, AgentTheme}` (Task 1)
- Produces: `tui_kit.run_model.{TEXT_CAP, Block, LiveRunState, normalize_input_summary, apply_bus_item, seed_state, seed_states, route_agent, strip_line, vitals_line, live_body, stream_line_kind, styled_vitals, styled_body}`. Signatures:
  - `apply_bus_item(state: LiveRunState, item, now: float) -> LiveRunState` — `item` is one of the `tui_kit.contracts` event types.
  - `seed_state(recent: list, now: float) -> LiveRunState`
  - `seed_states(recent: list, now: float) -> dict[str, LiveRunState]`
  - `route_agent(item) -> str | None`
  - `strip_line(state: LiveRunState, now: float, theme: AgentTheme, next_hint: str = "") -> str`
  - `vitals_line(state: LiveRunState, now: float, theme: AgentTheme) -> str`
  - `live_body(state: LiveRunState) -> str`
  - `stream_line_kind(line: str) -> str`
  - `styled_vitals(state: LiveRunState, now: float, theme: AgentTheme) -> rich.text.Text`
  - `styled_body(body: str) -> rich.text.Text`

- [ ] **Step 1: Write the failing test**

```python
# tests/tui_kit/test_run_model.py
from tui_kit.contracts import (
    RunStarted, RunFinished, RunFailed, LLMCallStarted, LLMCallFinished,
    ToolCallStarted, ToolCallFinished, ToolCallFailed, TokenDelta, ToolSummaryReady,
)
from tui_kit.run_model import (
    Block, LiveRunState, TEXT_CAP, apply_bus_item, route_agent, seed_state,
    seed_states, strip_line, stream_line_kind, vitals_line, live_body,
    normalize_input_summary, styled_vitals, styled_body,
)


class _FakeTheme:
    _GLYPHS = {"author": "@", "editor": "#"}
    _VERBS = {"author": "drafting"}

    def glyph(self, agent_name):
        return self._GLYPHS.get(agent_name, "?")

    def label(self, agent_name):
        return agent_name.title()

    def style(self, agent_name):
        return "gold3" if agent_name == "author" else "dim"

    def verb(self, agent_name):
        return self._VERBS.get(agent_name, "working")


THEME = _FakeTheme()


def test_run_started_resets_state_to_a_fresh_running_run():
    s = apply_bus_item(LiveRunState(blocks=(Block(kind="prose", text="stale"),), tokens=9),
                        RunStarted(run_id="r1", agent_name="author"), now=100.0)
    assert s.status == "running" and s.agent_name == "author" and s.run_id == "r1"
    assert s.tokens == 0 and s.blocks == () and s.started_at == 100.0


def test_call_started_carries_prompt_model_and_index():
    s = apply_bus_item(LiveRunState(status="running", run_id="r1"),
                        LLMCallStarted(run_id="r1", agent_name="author", call_index=1,
                                       model="qwen", prompt="[system]\nWrite."), now=101.0)
    assert s.prompt == "[system]\nWrite." and s.model == "qwen" and s.call_index == 1


def test_token_deltas_accumulate_into_a_trailing_prose_block():
    s = LiveRunState(status="running", run_id="r1", agent_name="author")
    s = apply_bus_item(s, TokenDelta(run_id="r1", agent_name="author", text="The "), now=1.0)
    s = apply_bus_item(s, TokenDelta(run_id="r1", agent_name="author", text="sea"), now=1.1)
    assert len(s.blocks) == 1
    assert s.blocks[0].kind == "prose" and s.blocks[0].text == "The sea"
    assert s.tokens == 2


def test_thinking_and_text_deltas_open_separate_blocks():
    s = LiveRunState(status="running", run_id="r1", agent_name="author")
    s = apply_bus_item(s, TokenDelta(run_id="r1", agent_name="author",
                                     text="let me consider", kind="thinking"), now=1.0)
    assert s.blocks[0].kind == "thinking"
    s = apply_bus_item(s, TokenDelta(run_id="r1", agent_name="author",
                                     text="The lighthouse", kind="text"), now=1.2)
    assert len(s.blocks) == 2 and s.blocks[1].kind == "prose"


def test_run_failed_marks_failed_with_error():
    s = LiveRunState(status="running", run_id="r1", agent_name="author")
    s = apply_bus_item(s, RunFailed(run_id="r1", agent_name="author", error_type="TimeoutError",
                                    error_message="proxy"), now=104.0)
    assert s.status == "failed" and "TimeoutError" in s.error and s.ended_at == 104.0


def test_run_finished_marks_finished():
    s = LiveRunState(status="running", run_id="r1", agent_name="author")
    s = apply_bus_item(s, RunFinished(run_id="r1", agent_name="author", duration_s=52.0), now=200.0)
    assert s.status == "finished" and s.ended_at == 200.0


def test_llm_call_finished_closes_the_call_block():
    s = LiveRunState(status="running", run_id="r1", agent_name="author")
    s = apply_bus_item(s, LLMCallStarted(run_id="r1", agent_name="author", call_index=1,
                                         model="qwen", prompt="p"), now=1.0)
    s = apply_bus_item(s, LLMCallFinished(run_id="r1", agent_name="author", call_index=1,
                                          duration_s=2.5, output_tokens=40), now=3.0)
    call = s.blocks[0]
    assert call.status == "done" and call.duration_s == 2.5


def test_tool_call_opens_and_closes_a_tool_block():
    s = LiveRunState(status="running", run_id="r1", agent_name="author")
    s = apply_bus_item(s, ToolCallStarted(run_id="r1", agent_name="author",
                                          tool_name="search_web", input_summary="dragons"), now=1.0)
    tool = s.blocks[0]
    assert tool.kind == "tool" and tool.tool_name == "search_web" and tool.status == "running"
    s = apply_bus_item(s, ToolCallFinished(run_id="r1", agent_name="author",
                                           tool_name="search_web", duration_s=1.2), now=2.0)
    assert s.blocks[0].status == "done" and s.blocks[0].duration_s == 1.2
    assert len(s.blocks) == 1


def test_tool_call_failed_marks_the_block_failed():
    s = LiveRunState(status="running", run_id="r1", agent_name="author")
    s = apply_bus_item(s, ToolCallStarted(run_id="r1", agent_name="author",
                                          tool_name="search_web", input_summary="dragons"), now=1.0)
    s = apply_bus_item(s, ToolCallFailed(run_id="r1", agent_name="author", tool_name="search_web",
                                         duration_s=0.3, error_type="ValueError"), now=1.0)
    assert s.blocks[0].status == "failed" and s.blocks[0].error == "ValueError"


def test_repeated_identical_tool_calls_collapse_with_a_counter():
    s = LiveRunState(status="running", run_id="r1", agent_name="author")
    for _ in range(3):
        s = apply_bus_item(s, ToolCallStarted(run_id="r1", agent_name="author",
                                              tool_name="read_file", input_summary="ch3.md"), now=1.0)
        s = apply_bus_item(s, ToolCallFinished(run_id="r1", agent_name="author",
                                               tool_name="read_file", duration_s=0.1), now=1.1)
    assert len(s.blocks) == 1
    assert s.blocks[0].repeat_count == 3 and s.blocks[0].status == "done"


def test_different_delegates_do_not_collapse():
    s = LiveRunState(status="running", run_id="r1", agent_name="character_keeper")
    for delegate in ("", "researcher"):
        s = apply_bus_item(s, ToolCallStarted(run_id="r1", agent_name="character_keeper",
                                              tool_name="read_file", input_summary="ch3.md",
                                              delegate=delegate), now=1.0)
        s = apply_bus_item(s, ToolCallFinished(run_id="r1", agent_name="character_keeper",
                                               tool_name="read_file", duration_s=0.1), now=1.1)
    assert len(s.blocks) == 2
    assert all(b.repeat_count == 1 for b in s.blocks)


def test_tool_summary_ready_patches_the_matching_finished_block():
    s = LiveRunState(status="running", run_id="r1", agent_name="author",
                     blocks=(Block(kind="tool", tool_name="search_web",
                                   input_summary="dragons", status="done", duration_s=1.0),))
    s = apply_bus_item(s, ToolSummaryReady(run_id="r1", agent_name="author",
                                           tool_name="search_web", input_summary="dragons",
                                           summary="found three articles"), now=5.0)
    assert s.blocks[0].summary == "found three articles"


def test_tool_summary_ready_is_a_no_op_when_the_run_has_moved_on():
    s = LiveRunState(status="running", run_id="r2", agent_name="author", blocks=())
    s2 = apply_bus_item(s, ToolSummaryReady(run_id="r1", agent_name="author", tool_name="search_web",
                                            input_summary="dragons", summary="stale"), now=5.0)
    assert s2 == s


def test_seed_state_of_a_finished_run_is_not_stuck_running():
    s = seed_state([RunStarted(run_id="r1", agent_name="author"),
                    RunFinished(run_id="r1", agent_name="author", duration_s=52.0)], now=10.0)
    assert s.status == "finished"


def test_seed_state_marks_stream_not_attached_when_still_running():
    s = seed_state([RunStarted(run_id="r1", agent_name="author")], now=10.0)
    assert s.status == "running" and s.stream_attached is False
    assert "stream not attached" in live_body(s)


def test_seed_states_keeps_concurrent_agents_isolated():
    events = [
        RunStarted(run_id="r1", agent_name="author"),
        RunStarted(run_id="r2", agent_name="editor"),
        ToolCallStarted(run_id="r1", agent_name="author", tool_name="search_web",
                        input_summary="dragons"),
        ToolCallStarted(run_id="r2", agent_name="editor", tool_name="read", input_summary="ch1.md"),
    ]
    states = seed_states(events, now=10.0)
    assert set(states) == {"author", "editor"}
    assert states["author"].blocks[0].tool_name == "search_web"
    assert states["editor"].blocks[0].tool_name == "read"


def test_route_agent_reads_agent_name_from_any_contract_event():
    assert route_agent(TokenDelta(run_id="r1", agent_name="author", text="x")) == "author"
    assert route_agent(RunStarted(run_id="r1", agent_name="editor")) == "editor"
    assert route_agent(TokenDelta(run_id="r1", agent_name="", text="x")) is None
    assert route_agent("not a bus item") is None


def test_strip_line_running_idle_and_failed_forms():
    running = LiveRunState(status="running", agent_name="author", started_at=100.0,
                           tokens=3400, call_index=1)
    line = strip_line(running, now=152.0, theme=THEME)
    assert "▶" in line and "author" in line and "drafting" in line
    assert "3.4k tok" in line and "52s" in line
    idle = strip_line(LiveRunState(), now=0.0, theme=THEME, next_hint="next: editor in 12s")
    assert idle.startswith("idle") and "next: editor in 12s" in idle
    failed = LiveRunState(status="failed", agent_name="author", ended_at=100.0)
    fline = strip_line(failed, now=220.0, theme=THEME)
    assert "✗" in fline and "author" in fline and "Engine Room" in fline and "2m" in fline


def test_vitals_line_running_and_finished_forms():
    running = LiveRunState(status="running", agent_name="author", model="qwen",
                           call_index=2, tokens=1500, started_at=100.0)
    line = vitals_line(running, now=110.0, theme=THEME)
    assert "author" in line and "qwen" in line and "call 2" in line and "1.5k tok" in line and "10s" in line
    finished = LiveRunState(status="finished", agent_name="author", tokens=2500,
                            started_at=100.0, ended_at=142.0)
    fline = vitals_line(finished, now=999.0, theme=THEME)
    assert "author" in fline and "finished" in fline and "42s" in fline and "2.5k tok" in fline


def test_live_body_renders_a_tool_block_as_a_grouped_multiline_unit():
    s = LiveRunState(status="running", run_id="r1", agent_name="author")
    s = apply_bus_item(s, ToolCallStarted(run_id="r1", agent_name="author",
                                          tool_name="search_web", input_summary="dragons"), now=1.0)
    s = apply_bus_item(s, ToolCallFinished(run_id="r1", agent_name="author",
                                           tool_name="search_web", duration_s=1.2), now=2.0)
    body = live_body(s)
    assert "⚒ search_web(dragons)" in body and "done in 1.2s" in body


def test_live_body_indents_delegated_tool_calls():
    s = LiveRunState(status="running", run_id="r1", agent_name="character_keeper",
                     blocks=(Block(kind="tool", tool_name="read_file",
                                   input_summary="/chapters/ch-0012.md",
                                   status="running", delegate="researcher"),))
    body = live_body(s)
    assert "    ⚒ ↳ researcher: read_file(/chapters/ch-0012.md)" in body


def test_text_is_still_tail_capped_via_prose_blocks():
    long_prose = "x" * TEXT_CAP
    s = LiveRunState(status="running", run_id="r1", blocks=(Block(kind="prose", text=long_prose),))
    s = apply_bus_item(s, TokenDelta(run_id="r1", agent_name="author", text="END"), now=1.0)
    assert s.blocks[-1].text.endswith("END")
    assert len(s.blocks[-1].text) <= TEXT_CAP + 3


def test_stream_line_kind_classifies_marker_lines():
    assert stream_line_kind("⚒ search_web(dragons)") == "tool"
    assert stream_line_kind("   ↳ done in 1.2s") == "call"
    assert stream_line_kind("▸ call 1 (qwen)") == "call"
    assert stream_line_kind("💭 thinking about it") == "thinking"
    assert stream_line_kind("Once upon a time") == "prose"


def test_normalize_input_summary_replaces_newlines_and_caps_length():
    raw = "line one\nline two\n" + "x" * 200
    normalized = normalize_input_summary(raw)
    assert "\n" not in normalized and len(normalized) <= 120


def test_tool_summary_ready_matches_a_multiline_over_120_char_input_summary():
    raw = "line one\nline two\nline three " + "x" * 200
    s = apply_bus_item(LiveRunState(status="running", run_id="r1", agent_name="author"),
                       ToolCallStarted(run_id="r1", agent_name="author", tool_name="search_web",
                                       input_summary=raw), now=1.0)
    s = apply_bus_item(s, ToolCallFinished(run_id="r1", agent_name="author",
                                           tool_name="search_web", duration_s=1.0), now=2.0)
    s = apply_bus_item(s, ToolSummaryReady(run_id="r1", agent_name="author", tool_name="search_web",
                                           input_summary=normalize_input_summary(raw),
                                           summary="found three articles"), now=5.0)
    assert s.blocks[0].summary == "found three articles"


def test_styled_vitals_includes_glyph_from_theme():
    state = LiveRunState(status="running", agent_name="author", started_at=0.0,
                         model="m", call_index=1, tokens=5)
    text = styled_vitals(state, now=2.0, theme=THEME)
    assert "author" in text.plain and "@" in text.plain


def test_styled_body_applies_tool_style_to_tool_lines():
    text = styled_body("\n⚒ search_canon(query)\n")
    styles = [span.style for span in text.spans]
    assert "bold cyan" in styles


def test_styled_body_leaves_prose_unstyled():
    text = styled_body("plain prose line")
    assert text.spans == []
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/tui_kit/test_run_model.py -v`
Expected: FAIL with "No module named 'tui_kit.run_model'"

- [ ] **Step 3: Write minimal implementation**

```python
# tui_kit/run_model.py
"""Pure live-view state machine and formatters for a generic "watch N
agents run" console. No Textual imports: everything here is black-box
testable. Consumes tui_kit.contracts events; a domain adapts its own
telemetry into those before calling apply_bus_item.
"""
from __future__ import annotations
from dataclasses import dataclass, replace
from rich.text import Text
from tui_kit.contracts import (
    AgentTheme, LLMCallFinished, LLMCallStarted, RunFailed, RunFinished,
    RunStarted, ToolCallFailed, ToolCallFinished, ToolCallStarted, TokenDelta,
    ToolSummaryReady,
)

TEXT_CAP = 8000


def normalize_input_summary(raw) -> str:
    """Canonical normalization for a tool call's input_summary: newlines
    swapped for a visible marker so single-line rendering stays intact, and
    truncated to 120 chars. The ToolCallStarted branch of apply_bus_item and
    a ToolSummaryReady producer MUST agree exactly on this, since
    apply_bus_item's ToolSummaryReady handler matches on this string."""
    return str(raw).replace("\n", "␤")[:120]


@dataclass(frozen=True)
class Block:
    kind: str  # "prose" | "thinking" | "call" | "tool"
    text: str = ""
    tool_name: str = ""
    input_summary: str = ""
    status: str = "running"  # running | done | failed
    duration_s: float = 0.0
    error: str = ""
    summary: str | None = None
    repeat_count: int = 1
    delegate: str = ""  # subagent name when this tool call was dispatched by
    # a subagent rather than the parent agent itself
    output: str = ""  # full untruncated tool output (ToolCallFinished only)


@dataclass(frozen=True)
class LiveRunState:
    status: str = "idle"  # idle | running | finished | failed
    run_id: str = ""
    agent_name: str = ""
    started_at: float = 0.0  # monotonic
    ended_at: float = 0.0
    tokens: int = 0
    blocks: tuple[Block, ...] = ()
    prompt: str = ""
    model: str = ""
    call_index: int = 0
    error: str = ""
    stream_attached: bool = True
    last_kind: str = ""  # "" | "text" | "thinking" — tracks stream-segment
    # boundaries so a switch between reasoning and answer content gets a
    # visible marker instead of running together unlabeled.


def _append_text_block(state: LiveRunState, kind: str, text: str) -> LiveRunState:
    """Append to the trailing block if it's the same kind, else open a new one."""
    if state.blocks and state.blocks[-1].kind == kind:
        last = state.blocks[-1]
        merged = (last.text + text)[-TEXT_CAP:]
        blocks = state.blocks[:-1] + (replace(last, text=merged),)
    else:
        blocks = state.blocks + (Block(kind=kind, text=text[-TEXT_CAP:]),)
    return replace(state, blocks=blocks)


def apply_bus_item(state: LiveRunState, item, now: float) -> LiveRunState:
    if isinstance(item, TokenDelta):
        if state.status != "running" or item.run_id != state.run_id:
            return state
        kind = item.kind or "text"
        block_kind = "thinking" if kind == "thinking" else "prose"
        state = _append_text_block(state, block_kind, item.text)
        return replace(state, tokens=state.tokens + 1, last_kind=kind)
    if isinstance(item, ToolSummaryReady):
        if item.run_id != state.run_id or not state.blocks:
            return state
        for i in range(len(state.blocks) - 1, -1, -1):
            b = state.blocks[i]
            if (b.kind == "tool" and b.tool_name == item.tool_name
                    and b.input_summary == item.input_summary
                    and b.status != "running" and b.summary is None):
                blocks = state.blocks[:i] + (replace(b, summary=item.summary),) + state.blocks[i + 1:]
                return replace(state, blocks=blocks)
        return state
    if isinstance(item, RunStarted):
        return LiveRunState(status="running", run_id=item.run_id,
                            agent_name=item.agent_name, started_at=now)
    if not isinstance(item, (RunFinished, RunFailed, LLMCallStarted, LLMCallFinished,
                             ToolCallStarted, ToolCallFinished, ToolCallFailed)):
        return state
    if item.run_id != state.run_id:
        return state
    if isinstance(item, LLMCallStarted):
        state = replace(state, prompt=item.prompt, model=item.model, call_index=item.call_index)
        blocks = state.blocks + (Block(kind="call", status="running",
                                       text=f"call {item.call_index} ({item.model})"),)
        return replace(state, blocks=blocks)
    if isinstance(item, LLMCallFinished):
        state = replace(state, tokens=item.output_tokens)
        if state.blocks and state.blocks[-1].kind == "call":
            last = state.blocks[-1]
            blocks = state.blocks[:-1] + (replace(last, status="done",
                                                  duration_s=item.duration_s),)
            state = replace(state, blocks=blocks)
        return state
    if isinstance(item, RunFinished):
        return replace(state, status="finished", ended_at=now)
    if isinstance(item, RunFailed):
        error = f"{item.error_type}: {item.error_message}"
        return replace(state, status="failed", ended_at=now, error=error)
    if isinstance(item, ToolCallStarted):
        input_summary = normalize_input_summary(item.input_summary)
        if (state.blocks and state.blocks[-1].kind == "tool"
                and state.blocks[-1].tool_name == item.tool_name
                and state.blocks[-1].input_summary == input_summary
                and state.blocks[-1].delegate == item.delegate
                and state.blocks[-1].status != "running"):
            last = state.blocks[-1]
            blocks = state.blocks[:-1] + (replace(last, status="running",
                                                  repeat_count=last.repeat_count + 1,
                                                  summary=None),)
        else:
            blocks = state.blocks + (Block(kind="tool", tool_name=item.tool_name,
                                           input_summary=input_summary, status="running",
                                           delegate=item.delegate),)
        return replace(state, blocks=blocks)
    if isinstance(item, (ToolCallFinished, ToolCallFailed)):
        for i in range(len(state.blocks) - 1, -1, -1):
            b = state.blocks[i]
            if b.kind == "tool" and b.tool_name == item.tool_name and b.status == "running":
                if isinstance(item, ToolCallFinished):
                    updated = replace(b, status="done", duration_s=item.duration_s,
                                      output=item.output_summary)
                else:
                    updated = replace(b, status="failed", duration_s=item.duration_s,
                                      error=item.error_type)
                blocks = state.blocks[:i] + (updated,) + state.blocks[i + 1:]
                return replace(state, blocks=blocks)
        return state
    return state


def seed_state(recent: list, now: float) -> LiveRunState:
    state = LiveRunState()
    for ev in recent:
        state = apply_bus_item(state, ev, now)
    if state.status == "running":
        # We rebooted mid-run: the ephemeral token stream from before the
        # restart is gone — say so instead of pretending to stream.
        state = replace(state, stream_attached=False)
    return state


def route_agent(item) -> str | None:
    """Which agent a contract event belongs to, for fanning a single stream
    out into per-agent buckets."""
    return getattr(item, "agent_name", None) or None


def seed_states(recent: list, now: float) -> dict[str, LiveRunState]:
    """Per-agent counterpart to seed_state: groups replayed events by agent
    and folds each group independently, so one agent's run doesn't clobber
    another's on replay."""
    by_agent: dict[str, list] = {}
    for ev in recent:
        agent = route_agent(ev)
        if agent:
            by_agent.setdefault(agent, []).append(ev)
    return {agent: seed_state(events, now) for agent, events in by_agent.items()}


def _fmt_tokens(n: int) -> str:
    return f"{n / 1000:.1f}k tok" if n >= 1000 else f"{n} tok"


def _fmt_ago(seconds: float) -> str:
    s = int(seconds)
    return f"{s // 60}m" if s >= 60 else f"{s}s"


def strip_line(state: LiveRunState, now: float, theme: AgentTheme, next_hint: str = "") -> str:
    if state.status == "running":
        verb = theme.verb(state.agent_name)
        elapsed = int(now - state.started_at)
        call = state.call_index or 1
        return (f"▶ {state.agent_name} · {verb} · {_fmt_tokens(state.tokens)} · "
                f"{elapsed}s · call {call}")
    if state.status == "failed":
        return (f"✗ {state.agent_name} crashed {_fmt_ago(now - state.ended_at)} ago "
                f"(see Engine Room)")
    return f"idle · {next_hint}" if next_hint else "idle"


def vitals_line(state: LiveRunState, now: float, theme: AgentTheme) -> str:
    if state.status == "running":
        verb = theme.verb(state.agent_name)
        model = state.model or "?"
        return (f"{state.agent_name} · {verb} · {model} · call {state.call_index or 1} · "
                f"{_fmt_tokens(state.tokens)} · {int(now - state.started_at)}s")
    if state.status == "failed":
        return f"{state.agent_name} · crashed · {state.error}"
    if state.status == "finished":
        return (f"{state.agent_name} · finished · "
                f"{int(state.ended_at - state.started_at)}s · {_fmt_tokens(state.tokens)}")
    return "idle — waiting for the scheduler"


def live_body(state: LiveRunState) -> str:
    if state.status == "running" and not state.stream_attached:
        return "run in progress (stream not attached — restarted mid-run)"
    if state.status == "idle":
        return "no run yet"
    lines: list[str] = []
    for b in state.blocks:
        lines.append(_render_block(b))
    body = "\n".join(lines).strip("\n")
    if state.status == "failed" and body:
        return body + "\n\n✗ crashed"
    return body or "(waiting for first token…)"


def _render_block(b: Block) -> str:
    if b.kind == "prose":
        return b.text
    if b.kind == "thinking":
        return f"💭 {b.text}"
    if b.kind == "call":
        header = f"▸ {b.text}"
        if b.status == "done":
            return header + f"\n   ↳ {b.duration_s:.1f}s"
        return header
    # kind == "tool"
    suffix = f" ×{b.repeat_count}" if b.repeat_count > 1 else ""
    indent = "       " if b.delegate else "   "
    if b.delegate:
        lines = [f"    ⚒ ↳ {b.delegate}: {b.tool_name}({b.input_summary}){suffix}"]
    else:
        lines = [f"⚒ {b.tool_name}({b.input_summary}){suffix}"]
    if b.status == "done":
        lines.append(f"{indent}↳ done in {b.duration_s:.1f}s")
    elif b.status == "failed":
        lines.append(f"{indent}↳ ✗ {b.error}")
    if b.summary:
        lines.append(f"{indent}↳ {b.summary}")
    if b.output:
        for out_line in b.output.split("\n"):
            lines.append(f"{indent}  {out_line}")
    return "\n".join(lines)


def stream_line_kind(line: str) -> str:
    """Classify a live_body() line for widget-level styling. Pure/text-only
    so it stays testable without Rich or Textual."""
    s = line.strip()
    if s.startswith("⚒"):
        return "tool"
    if s.startswith("▸") or s.startswith("↳"):
        return "call"
    if s.startswith("💭"):
        return "thinking"
    return "prose"


# Rich styles per stream_line_kind(); "" leaves prose in the theme default so
# the agent's own accent color (applied to the vitals bar) stays the visual
# anchor rather than competing with a wall of colored prose.
_LINE_STYLES = {"tool": "bold cyan", "call": "dim", "thinking": "italic dim magenta"}


def styled_vitals(state: LiveRunState, now: float, theme: AgentTheme) -> Text:
    glyph = theme.glyph(state.agent_name)
    style = theme.style(state.agent_name)
    return Text(f"{glyph} {vitals_line(state, now, theme)}", style=style)


def styled_body(body: str) -> Text:
    # Text objects are never markup-parsed regardless of a Static's
    # markup=False setting, so untrusted stream content (tool summaries,
    # prompts) stays safe here the same way it does as a plain str.
    text = Text()
    lines = body.split("\n")
    for i, line in enumerate(lines):
        style = _LINE_STYLES.get(stream_line_kind(line), "")
        text.append(line, style=style)
        if i != len(lines) - 1:
            text.append("\n")
    return text
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/tui_kit/test_run_model.py -v`
Expected: PASS (all tests)

- [ ] **Step 5: Commit**

```bash
git add tui_kit/run_model.py tests/tui_kit/test_run_model.py
git commit -m "feat(tui_kit): add pure agent-run state machine and formatters"
```

---

### Task 3: `tui_kit/widgets/` — EngineRoom, LiveStreamPanel, ActivityStrip

**Files:**
- Create: `tui_kit/widgets/__init__.py`
- Create: `tui_kit/widgets/engine_room.py`
- Create: `tui_kit/widgets/live_stream_panel.py`
- Create: `tui_kit/widgets/activity_strip.py`
- Test: `tests/tui_kit/test_widgets.py`

**Interfaces:**
- Consumes: `tui_kit.run_model.{LiveRunState, live_body, styled_body, styled_vitals, strip_line}` (Task 2), `tui_kit.contracts.AgentTheme` (Task 1)
- Produces:
  - `EngineRoom(agent_names: list[str], theme: AgentTheme, *args, **kwargs)` — Textual `Vertical`. Methods: `render_live(state, now=None)`, `render_agent_live(agent_name, state, now=None)`, `stream_text() -> str`, `toggle_prompt() -> bool`, `set_trace_rows(rows: list[tuple[str, str]])`, `show_detail(text: str)`.
  - `LiveStreamPanel(theme: AgentTheme, *args, **kwargs)` — Textual `Vertical`. Method: `render(state, now=None)`.
  - `ActivityStrip(*args, theme: AgentTheme, **kwargs)` — Textual `Static`. Method: `render_state(state, now, next_hint="")`.

- [ ] **Step 1: Write the failing test**

```python
# tests/tui_kit/test_widgets.py
import pytest
from textual.app import App, ComposeResult
from textual.widgets import DataTable, Static
from tui_kit.run_model import Block, LiveRunState
from tui_kit.widgets.activity_strip import ActivityStrip
from tui_kit.widgets.engine_room import EngineRoom
from tui_kit.widgets.live_stream_panel import LiveStreamPanel


class _FakeTheme:
    _GLYPHS = {"author": "@", "editor": "#"}

    def glyph(self, agent_name):
        return self._GLYPHS.get(agent_name, "?")

    def label(self, agent_name):
        return agent_name.title()

    def style(self, agent_name):
        return "gold3"

    def verb(self, agent_name):
        return "drafting"


THEME = _FakeTheme()
AGENTS = ["author", "editor"]


class _LSPHarness(App):
    def compose(self) -> ComposeResult:
        yield LiveStreamPanel(theme=THEME, id="panel")


@pytest.mark.asyncio
async def test_live_stream_panel_idle_state_renders_idle_body():
    app = _LSPHarness()
    async with app.run_test() as pilot:
        panel = app.query_one("#panel", LiveStreamPanel)
        panel.render(LiveRunState(), now=0.0)
        await pilot.pause()
        body = panel.query_one(LiveStreamPanel._STREAM_ID, Static)
        assert "no run yet" in str(body.renderable)


@pytest.mark.asyncio
async def test_live_stream_panel_running_state_shows_agent_name_in_vitals():
    app = _LSPHarness()
    async with app.run_test() as pilot:
        panel = app.query_one("#panel", LiveStreamPanel)
        state = LiveRunState(status="running", agent_name="author", started_at=0.0,
                             blocks=(Block(kind="prose", text="hello"),))
        panel.render(state, now=1.0)
        await pilot.pause()
        vitals = panel.query_one(LiveStreamPanel._VITALS_ID, Static)
        assert "author" in str(vitals.renderable)
        body = panel.query_one(LiveStreamPanel._STREAM_ID, Static)
        assert "hello" in str(body.renderable)


class _StripHarness(App):
    def compose(self) -> ComposeResult:
        yield ActivityStrip("idle", theme=THEME, id="strip")


@pytest.mark.asyncio
async def test_activity_strip_renders_running_state():
    app = _StripHarness()
    async with app.run_test() as pilot:
        strip = app.query_one("#strip", ActivityStrip)
        state = LiveRunState(status="running", agent_name="author", started_at=0.0, tokens=10)
        strip.render_state(state, now=2.0)
        await pilot.pause()
        assert "author" in str(strip.renderable) and "drafting" in str(strip.renderable)


class _EngineRoomHarness(App):
    def compose(self) -> ComposeResult:
        yield EngineRoom(agent_names=AGENTS, theme=THEME, id="engine_room")


@pytest.mark.asyncio
async def test_engine_room_has_a_tab_per_agent_with_theme_glyph():
    from textual.content import Content
    from textual.widgets import TabbedContent
    app = _EngineRoomHarness()
    async with app.run_test() as pilot:
        tabs = app.query_one("#er_tabs", TabbedContent)
        author_tab = tabs.query_one("#er_tab_author")._title
        assert isinstance(author_tab, Content)
        assert author_tab.plain == "@ Author"


@pytest.mark.asyncio
async def test_engine_room_renders_live_state_into_the_all_pane():
    app = _EngineRoomHarness()
    async with app.run_test() as pilot:
        room = app.query_one("#engine_room", EngineRoom)
        state = LiveRunState(status="running", agent_name="author", started_at=0.0,
                             blocks=(Block(kind="prose", text="The sea rose."),))
        room.render_live(state, now=1.0)
        await pilot.pause()
        assert "author" in str(app.query_one("#er_vitals").renderable)
        assert "The sea rose." in room.stream_text()


@pytest.mark.asyncio
async def test_engine_room_renders_per_agent_pane_independently():
    app = _EngineRoomHarness()
    async with app.run_test() as pilot:
        room = app.query_one("#engine_room", EngineRoom)
        state = LiveRunState(status="running", agent_name="editor", started_at=0.0,
                             blocks=(Block(kind="prose", text="looks good"),))
        room.render_agent_live("editor", state, now=1.0)
        await pilot.pause()
        body = app.query_one("#er_stream_editor", Static).renderable
        assert "looks good" in str(body)


@pytest.mark.asyncio
async def test_engine_room_prompt_pane_toggles():
    app = _EngineRoomHarness()
    async with app.run_test() as pilot:
        room = app.query_one("#engine_room", EngineRoom)
        assert app.query_one("#er_prompt", Static).display is False
        assert room.toggle_prompt() is True
        assert app.query_one("#er_prompt", Static).display is True


@pytest.mark.asyncio
async def test_engine_room_trace_rows_and_detail_pane():
    app = _EngineRoomHarness()
    async with app.run_test() as pilot:
        room = app.query_one("#engine_room", EngineRoom)
        room.set_trace_rows([("k1", "12:00:00 author run started")])
        table = app.query_one("#er_trace", DataTable)
        rows = [table.get_row_at(i)[0] for i in range(table.row_count)]
        assert any("run started" in str(r) for r in rows)
        room.show_detail("some detail text")
        detail = app.query_one("#er_detail", Static)
        assert detail.display is True
        assert "some detail text" in str(detail.renderable)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/tui_kit/test_widgets.py -v`
Expected: FAIL with "No module named 'tui_kit.widgets'"

- [ ] **Step 3: Write minimal implementation**

```python
# tui_kit/widgets/__init__.py
```
(empty)

```python
# tui_kit/widgets/live_stream_panel.py
from __future__ import annotations
import time
from textual.app import ComposeResult
from textual.containers import Vertical, VerticalScroll
from textual.widgets import Static
from tui_kit.contracts import AgentTheme
from tui_kit.run_model import LiveRunState, live_body, styled_body, styled_vitals


class LiveStreamPanel(Vertical):
    """A single-agent live token/tool-call stream, the same rendering
    EngineRoom gives each agent's tab, without the tab strip. Owns no bus
    subscription and no identity — the mounting screen computes the
    LiveRunState for its own key and calls render()."""

    _VITALS_ID = "#lsp_vitals"
    _STREAM_ID = "#lsp_stream"
    _STREAM_SCROLL_ID = "#lsp_stream_scroll"

    def __init__(self, theme: AgentTheme, *args, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        self._theme = theme
        self._rendered_body: str = ""

    def compose(self) -> ComposeResult:
        yield Static("idle — waiting for the scheduler",
                     id=self._VITALS_ID.removeprefix("#"), classes="lsp-vitals", markup=False)
        with VerticalScroll(id=self._STREAM_SCROLL_ID.removeprefix("#"), classes="lsp-stream-scroll"):
            yield Static("", id=self._STREAM_ID.removeprefix("#"), classes="lsp-stream", markup=False)

    def render(self, state: LiveRunState | None = None, now: float | None = None):
        if state is None:
            # Textual's internal Widget.render() calls this with no args to
            # get this container's own visual; Vertical paints nothing itself.
            return ""
        now = time.monotonic() if now is None else now
        self.query_one(self._VITALS_ID, Static).update(styled_vitals(state, now, self._theme))
        body = live_body(state)
        if body != self._rendered_body:
            self.query_one(self._STREAM_ID, Static).update(styled_body(body))
            self._rendered_body = body
            self.query_one(self._STREAM_SCROLL_ID, VerticalScroll).scroll_end(animate=False)
```

```python
# tui_kit/widgets/activity_strip.py
from __future__ import annotations
from textual.widgets import Static
from tui_kit.contracts import AgentTheme
from tui_kit.run_model import LiveRunState, strip_line


class ActivityStrip(Static):
    """One-line ambient machinery status."""

    def __init__(self, *args, theme: AgentTheme, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        self._theme = theme

    def render_state(self, state: LiveRunState, now: float, next_hint: str = "") -> None:
        self.update(strip_line(state, now, self._theme, next_hint))
```

```python
# tui_kit/widgets/engine_room.py
"""EngineRoom: live token stream, vitals, and durable trace for N agents.

The stream body is a Static inside a VerticalScroll, not a RichLog: a
RichLog renders one line per write() call, which would put every
streamed token on its own line.
"""
from __future__ import annotations
import time
from rich.markup import escape
from textual.app import ComposeResult
from textual.content import Content
from textual.containers import Vertical, VerticalScroll
from textual.widgets import DataTable, Static, TabbedContent, TabPane
from tui_kit.contracts import AgentTheme
from tui_kit.run_model import LiveRunState, live_body, styled_body, styled_vitals


class EngineRoom(Vertical):
    """The thick machinery view: live vitals + token stream on top (an "All"
    tab plus one tab per agent so concurrent runs don't clobber each other),
    the durable trace below (rows filled by the caller), prompt pane
    toggleable (off by default)."""

    _rendered_body: dict[str, str]

    def __init__(self, agent_names: list[str], theme: AgentTheme, *args, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        self._agent_names = tuple(agent_names)
        self._theme = theme
        self._rendered_body = {}

    def compose(self) -> ComposeResult:
        # markup=False throughout: these panes show raw prompts, token streams,
        # and payload text — untrusted content full of "[...]" sequences that
        # Textual's markup parser rejects (MarkupError crashes the caller
        # otherwise).
        with TabbedContent(id="er_tabs"):
            with TabPane("All", id="er_tab_all"):
                yield Static("idle — waiting for the scheduler", id="er_vitals",
                            classes="er-vitals", markup=False)
                with VerticalScroll(id="er_stream_scroll", classes="er-stream-scroll"):
                    yield Static("", id="er_stream", classes="er-stream", markup=False)
                yield Static("", id="er_prompt", markup=False)
            for agent_name in self._agent_names:
                glyph = self._theme.glyph(agent_name)
                label = self._theme.label(agent_name)
                style = self._theme.style(agent_name)
                # A plain str title is markup-parsed by TabPane (Widget.render_str
                # -> Content.from_markup), which silently drops any style not
                # spelled out as markup tags -- pass a pre-styled Content instead
                # so the tab title actually carries the agent's color.
                title = Content.styled(f"{glyph} {label}", style)
                with TabPane(title, id=f"er_tab_{agent_name}"):
                    yield Static("idle — waiting for the scheduler",
                                id=f"er_vitals_{agent_name}", classes="er-vitals", markup=False)
                    with VerticalScroll(id=f"er_stream_scroll_{agent_name}",
                                       classes="er-stream-scroll"):
                        yield Static("", id=f"er_stream_{agent_name}",
                                    classes="er-stream", markup=False)
        yield DataTable(id="er_trace", cursor_type="row")
        yield Static("", id="er_detail", markup=False)

    def on_mount(self) -> None:
        self.query_one("#er_prompt", Static).display = False
        self.query_one("#er_detail", Static).display = False
        table = self.query_one("#er_trace", DataTable)
        table.add_column("machinery", key="line", width=110)

    # -- live pane -----------------------------------------------------------

    def render_live(self, state: LiveRunState, now: float | None = None) -> None:
        now = time.monotonic() if now is None else now
        self.query_one("#er_vitals", Static).update(styled_vitals(state, now, self._theme))
        body = live_body(state)
        if body != self._rendered_body.get("__all__"):
            self.query_one("#er_stream", Static).update(styled_body(body))
            self._rendered_body["__all__"] = body
            self.query_one("#er_stream_scroll", VerticalScroll).scroll_end(animate=False)
        self.query_one("#er_prompt", Static).update(state.prompt or "(no call in flight)")

    def render_agent_live(self, agent_name: str, state: LiveRunState,
                          now: float | None = None) -> None:
        now = time.monotonic() if now is None else now
        self.query_one(f"#er_vitals_{agent_name}", Static).update(
            styled_vitals(state, now, self._theme))
        body = live_body(state)
        if body != self._rendered_body.get(agent_name):
            self.query_one(f"#er_stream_{agent_name}", Static).update(styled_body(body))
            self._rendered_body[agent_name] = body
            self.query_one(f"#er_stream_scroll_{agent_name}", VerticalScroll).scroll_end(animate=False)

    def stream_text(self) -> str:
        return self._rendered_body.get("__all__", "")

    def toggle_prompt(self) -> bool:
        pane = self.query_one("#er_prompt", Static)
        pane.display = not pane.display
        return pane.display

    # -- trace pane (rows managed by the caller) ------------------------------

    def set_trace_rows(self, rows: list[tuple[str, str]]) -> None:
        """rows: (row_key, rendered_line), newest first."""
        table = self.query_one("#er_trace", DataTable)
        table.clear()
        for key, line in rows:
            # DataTable runs str cells through Text.from_markup; trace lines
            # carry untrusted text (tool summaries, error messages), so escape.
            table.add_row(escape(line), key=key)

    def show_detail(self, text: str) -> None:
        detail = self.query_one("#er_detail", Static)
        detail.update(text)
        detail.display = True
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/tui_kit/test_widgets.py -v`
Expected: PASS (all tests)

- [ ] **Step 5: Commit**

```bash
git add tui_kit/widgets/
git add tests/tui_kit/test_widgets.py
git commit -m "feat(tui_kit): add EngineRoom, LiveStreamPanel, ActivityStrip widgets"
```

---

### Task 4: `tui_kit/widgets/roster.py` — glyph strip renderer

**Files:**
- Create: `tui_kit/widgets/roster.py`
- Test: `tests/tui_kit/test_roster.py`

**Interfaces:**
- Consumes: `tui_kit.contracts.AgentTheme` (Task 1)
- Produces: `tui_kit.widgets.roster.{RUNNING_MARK, IDLE_MARK, PAUSED_MARK, ERROR_MARK, ALARM_STYLE, roster_glyphs(status: list, theme: AgentTheme) -> Text, roster_summary(status: list, theme: AgentTheme) -> str}`

- [ ] **Step 1: Write the failing test**

```python
# tests/tui_kit/test_roster.py
from hypothesis import given, strategies as st
from tui_kit.widgets.roster import (
    ALARM_STYLE, ERROR_MARK, IDLE_MARK, PAUSED_MARK, RUNNING_MARK,
    roster_glyphs, roster_summary,
)


class _FakeTheme:
    _STYLES = {"author": "gold3", "editor": "medium_purple"}
    _GLYPHS = {"author": "✎", "editor": "§"}

    def glyph(self, agent_name):
        return self._GLYPHS.get(agent_name, "?")

    def label(self, agent_name):
        return agent_name.title()

    def style(self, agent_name):
        return self._STYLES.get(agent_name, "dim")

    def verb(self, agent_name):
        return "working"


THEME = _FakeTheme()
CAST = ("author", "editor")


def _row(name, paused=False, running=False, last_error=None):
    return {"name": name, "paused": paused, "running": running, "last_error": last_error,
            "last_completed": False, "run_count": 0, "next_ready_in": 0.0}


def test_no_agents_renders_dim_placeholder():
    strip = roster_glyphs([], THEME)
    assert strip.plain == "no agents"
    assert str(strip.style) == "dim"
    assert roster_summary([], THEME) == "no agents"


def test_idle_cast_renders_every_glyph_with_idle_mark():
    strip = roster_glyphs([_row(n) for n in CAST], THEME)
    assert strip.plain == "✎· §·"


def test_running_agent_carries_spinner_mark():
    strip = roster_glyphs([_row("author", running=True), _row("editor")], THEME)
    assert strip.plain == f"✎{RUNNING_MARK} §{IDLE_MARK}"


def test_paused_agent_carries_pause_mark():
    strip = roster_glyphs([_row("editor", paused=True)], THEME)
    assert strip.plain == f"§{PAUSED_MARK}"


def test_errored_agent_carries_alarm_mark_without_error_text():
    strip = roster_glyphs([_row("author", last_error="RuntimeError: boom" * 10)], THEME)
    assert strip.plain == f"✎{ERROR_MARK}"
    assert "boom" not in strip.plain


def test_error_wins_over_paused_and_running():
    strip = roster_glyphs([_row("author", paused=True, running=True, last_error="x")], THEME)
    assert strip.plain == f"✎{ERROR_MARK}"


def test_paused_wins_over_running():
    strip = roster_glyphs([_row("author", paused=True, running=True)], THEME)
    assert strip.plain == f"✎{PAUSED_MARK}"


def test_glyph_takes_agent_style_and_error_mark_takes_alarm_style():
    strip = roster_glyphs([_row("author", last_error="x")], THEME)
    styles = [(strip.plain[s.start:s.end], str(s.style)) for s in strip.spans]
    assert ("✎", "gold3") in styles
    assert (ERROR_MARK, ALARM_STYLE) in styles


@given(
    st.lists(
        st.tuples(st.sampled_from(CAST), st.booleans(), st.booleans(),
                  st.one_of(st.none(), st.just("err"))),
        max_size=8,
    )
)
def test_summary_is_the_plain_strip_and_one_cell_pair_per_agent(rows):
    status = [_row(n, paused=p, running=r, last_error=e) for n, p, r, e in rows]
    strip = roster_glyphs(status, THEME)
    assert roster_summary(status, THEME) == strip.plain
    if status:
        assert len(strip.plain) == 3 * len(status) - 1
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/tui_kit/test_roster.py -v`
Expected: FAIL with "No module named 'tui_kit.widgets.roster'"

- [ ] **Step 3: Write minimal implementation**

```python
# tui_kit/widgets/roster.py
"""Pure glyph-strip rendering: the cast's status as one glyph+mark pair
per agent, in the agent's theme color. No Textual imports, no I/O,
unit-testable without a terminal."""
from __future__ import annotations

from rich.text import Text

from tui_kit.contracts import AgentTheme

DIM = "dim"
ALARM_STYLE = "bold red"

# State marks appended to each agent's glyph. Precedence: errored > paused >
# running > idle. The spinner is static per render (any spinner char is fine).
RUNNING_MARK = "⠋"
IDLE_MARK = "·"
PAUSED_MARK = "‖"
ERROR_MARK = "!"


def _mark(s: dict) -> tuple[str, str | None]:
    """(mark, style) for one status row; style None means 'agent color'."""
    if s.get("last_error"):
        return ERROR_MARK, ALARM_STYLE
    if s.get("paused"):
        return PAUSED_MARK, DIM
    if s.get("running"):
        return RUNNING_MARK, None
    return IDLE_MARK, DIM


def roster_glyphs(status: list, theme: AgentTheme) -> Text:
    """The cast as a glyph strip — '✎⠋ §· ⌂· ♥· ⚖· ↺· ∿·'. Glyph in the
    agent's theme color; mark carries state. Status fields the strip does
    not need (last_completed, run_count, next_ready_in) are accepted and
    ignored."""
    if not status:
        return Text("no agents", style=DIM)
    strip = Text()
    for i, s in enumerate(status):
        if i:
            strip.append(" ")
        style = theme.style(s["name"])
        strip.append(theme.glyph(s["name"]), style=style)
        mark, mark_style = _mark(s)
        strip.append(mark, style=style if mark_style is None else mark_style)
    return strip


def roster_summary(status: list, theme: AgentTheme) -> str:
    """Plain-string variant of the glyph strip for string-surface needs."""
    return roster_glyphs(status, theme).plain
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/tui_kit/test_roster.py -v`
Expected: PASS (all tests)

- [ ] **Step 5: Commit**

```bash
git add tui_kit/widgets/roster.py tests/tui_kit/test_roster.py
git commit -m "feat(tui_kit): add roster glyph-strip renderer"
```

---

### Task 5: Import-linter boundary for `tui_kit`

**Files:**
- Modify: `pyproject.toml`

**Interfaces:**
- Consumes: nothing new.
- Produces: an enforced guarantee that `tui_kit` never imports `novelizer`, `substrate`, or `research_domain`.

- [ ] **Step 1: Write the failing check**

First run the linter to confirm the current (pre-contract) state passes trivially (there's nothing to violate yet, but this documents the baseline):

Run: `uv run lint-imports`
Expected: PASS (no `tui_kit` contract exists yet, so nothing to check)

- [ ] **Step 2: Add `tui_kit` to root_packages and add the independence contract**

Read the current `[tool.importlinter]` section first:

Run: `sed -n '/\[tool.importlinter\]/,/^\[/p' pyproject.toml`

Edit `pyproject.toml`: change

```toml
[tool.importlinter]
root_packages = ["substrate", "novelizer", "research_domain"]
```

to

```toml
[tool.importlinter]
root_packages = ["substrate", "novelizer", "research_domain", "tui_kit"]
```

and add a new contract after the existing `[[tool.importlinter.contracts]]` block(s), at the end of the `[tool.importlinter]` section:

```toml
[[tool.importlinter.contracts]]
name = "tui_kit independence"
type = "forbidden"
source_modules = ["tui_kit"]
forbidden_modules = ["novelizer", "substrate", "research_domain"]
```

- [ ] **Step 3: Run the linter to verify the new contract holds**

Run: `uv run lint-imports`
Expected: PASS — `tui_kit` (as built in Tasks 1-4) imports only `rich`, `textual`, and its own submodules.

- [ ] **Step 4: Commit**

```bash
git add pyproject.toml
git commit -m "chore(tui_kit): enforce independence from novelizer via import-linter"
```

---

### Task 6: `novelizer/tui/identity.py` — `AgentTheme` implementation

**Files:**
- Modify: `novelizer/tui/identity.py`
- Modify: `tests/tui/test_identity.py`

**Interfaces:**
- Consumes: `tui_kit.contracts.AgentTheme` (Task 1)
- Produces: `novelizer.tui.identity.{AGENT_NAMES, NovelizerAgentTheme, NOVELIZER_AGENT_THEME}` in addition to the existing `IDENTITIES`, `AgentIdentity`, `SPEAKER_WIDTH`, `identity_for`.

- [ ] **Step 1: Write the failing test**

Read the existing test file first:

Run: `cat tests/tui/test_identity.py`

Append to `tests/tui/test_identity.py`:

```python
from tui_kit.contracts import AgentTheme
from novelizer.tui.identity import AGENT_NAMES, NOVELIZER_AGENT_THEME


def test_agent_names_matches_the_scheduler_registry_order():
    assert AGENT_NAMES == (
        "world_architect", "character_keeper", "muse", "plotter", "author",
        "editor", "continuity_checker", "retconner", "structure_analyst",
    )


def test_novelizer_agent_theme_satisfies_the_agent_theme_protocol():
    theme: AgentTheme = NOVELIZER_AGENT_THEME
    assert theme.glyph("author") == "✎"
    assert theme.label("author") == "Author"
    assert theme.style("author") == "gold3"


def test_novelizer_agent_theme_verb_uses_the_verb_table_with_a_fallback():
    assert NOVELIZER_AGENT_THEME.verb("author") == "drafting"
    assert NOVELIZER_AGENT_THEME.verb("muse") == "working"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/tui/test_identity.py -v`
Expected: FAIL with "cannot import name 'AGENT_NAMES'"

- [ ] **Step 3: Write minimal implementation**

Append to `novelizer/tui/identity.py`:

```python
# Mirrors AGENT_REGISTRY's scheduling order in novelizer/agents/registry.py --
# kept as a plain tuple (not imported) so this module stays free of the heavy
# agent-construction import chain. Keep in sync if agents are added/removed.
AGENT_NAMES = (
    "world_architect", "character_keeper", "muse", "plotter", "author",
    "editor", "continuity_checker", "retconner", "structure_analyst",
)

_VERBS = {
    "author": "drafting",
    "editor": "reviewing",
    "world_architect": "worldbuilding",
    "character_keeper": "tending characters",
    "continuity_checker": "checking continuity",
    "retconner": "retconning",
    "structure_analyst": "scoring structure",
}


class NovelizerAgentTheme:
    """novelizer's tui_kit.contracts.AgentTheme implementation, backed by
    the IDENTITIES registry and the agent-verb table above."""

    def glyph(self, agent_name: str) -> str:
        return identity_for(agent_name).glyph

    def label(self, agent_name: str) -> str:
        return identity_for(agent_name).label

    def style(self, agent_name: str) -> str:
        return identity_for(agent_name).style

    def verb(self, agent_name: str) -> str:
        return _VERBS.get(agent_name, "working")


NOVELIZER_AGENT_THEME = NovelizerAgentTheme()
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/tui/test_identity.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add novelizer/tui/identity.py tests/tui/test_identity.py
git commit -m "feat(novelizer): implement AgentTheme via NovelizerAgentTheme"
```

---

### Task 7: `novelizer/tui/telemetry_adapter.py` — event translation + trace formatting

**Files:**
- Create: `novelizer/tui/telemetry_adapter.py`
- Test: `tests/tui/test_telemetry_adapter.py`

**Interfaces:**
- Consumes: `novelizer.canon.events.{StoredEvent}`, `novelizer.telemetry.events.{TelemetryEventType, TokenDelta, ToolSummaryReady}`, `tui_kit.contracts.*` (Task 1), `tui_kit.run_model.normalize_input_summary` (Task 2)
- Produces: `novelizer.tui.telemetry_adapter.{to_contract_event(item) -> object | None, trace_line(ev: StoredEvent) -> str, trace_detail(ev: StoredEvent, produced: list[StoredEvent]) -> str}`

- [ ] **Step 1: Write the failing test**

```python
# tests/tui/test_telemetry_adapter.py
from hypothesis import given, strategies as st
from novelizer.canon.events import StoredEvent
from novelizer.telemetry.events import TelemetryEventType, TokenDelta, ToolSummaryReady
from novelizer.tui.telemetry_adapter import to_contract_event, trace_line, trace_detail
from tui_kit.contracts import (
    RunStarted, RunFinished, RunFailed, LLMCallStarted, LLMCallFinished,
    ToolCallStarted, ToolCallFinished, ToolCallFailed,
)
from tui_kit.contracts import TokenDelta as ContractTokenDelta
from tui_kit.contracts import ToolSummaryReady as ContractToolSummaryReady


def _ev(seq, etype, payload, created_at="2026-07-18T12:04:32+00:00"):
    return StoredEvent(sequence=seq, id=f"e{seq}", event_type=etype,
                       aggregate_id="r1", payload=payload, created_at=created_at)


def test_token_delta_translates_1_to_1():
    item = TokenDelta(run_id="r1", agent_name="author", text="hi", kind="thinking")
    out = to_contract_event(item)
    assert out == ContractTokenDelta(run_id="r1", agent_name="author", text="hi", kind="thinking")


def test_tool_summary_ready_translates_1_to_1():
    item = ToolSummaryReady(run_id="r1", agent_name="author", tool_name="t",
                            input_summary="x", summary="s")
    out = to_contract_event(item)
    assert out == ContractToolSummaryReady(run_id="r1", agent_name="author", tool_name="t",
                                           input_summary="x", summary="s")


def test_run_started_translates():
    ev = _ev(1, TelemetryEventType.AGENT_RUN_STARTED, {"run_id": "r1", "agent_name": "author"})
    assert to_contract_event(ev) == RunStarted(run_id="r1", agent_name="author")


def test_run_finished_translates():
    ev = _ev(2, TelemetryEventType.AGENT_RUN_FINISHED,
            {"run_id": "r1", "agent_name": "author", "duration_s": 52.0})
    assert to_contract_event(ev) == RunFinished(run_id="r1", agent_name="author", duration_s=52.0)


def test_run_failed_translates():
    ev = _ev(3, TelemetryEventType.AGENT_RUN_FAILED,
            {"run_id": "r1", "agent_name": "author", "error_type": "TimeoutError",
             "error_message": "proxy", "phase": "llm_call", "duration_s": 4.0})
    out = to_contract_event(ev)
    assert out == RunFailed(run_id="r1", agent_name="author", error_type="TimeoutError",
                            error_message="proxy")


def test_llm_call_started_translates():
    ev = _ev(4, TelemetryEventType.LLM_CALL_STARTED,
            {"run_id": "r1", "agent_name": "author", "call_index": 1, "model": "qwen",
             "prompt": "[system]\nWrite."})
    out = to_contract_event(ev)
    assert out == LLMCallStarted(run_id="r1", agent_name="author", call_index=1,
                                 model="qwen", prompt="[system]\nWrite.")


def test_llm_call_finished_translates():
    ev = _ev(5, TelemetryEventType.LLM_CALL_FINISHED,
            {"run_id": "r1", "agent_name": "author", "call_index": 1,
             "duration_s": 2.5, "output_tokens": 40})
    out = to_contract_event(ev)
    assert out == LLMCallFinished(run_id="r1", agent_name="author", call_index=1,
                                  duration_s=2.5, output_tokens=40)


def test_tool_call_started_translates_without_pre_normalizing():
    """to_contract_event passes input_summary through raw -- tui_kit.run_model's
    apply_bus_item normalizes it, matching the original single-normalization
    contract (see normalize_input_summary's docstring)."""
    ev = _ev(6, TelemetryEventType.TOOL_CALL_STARTED,
            {"run_id": "r1", "agent_name": "author", "tool_name": "search_web",
             "input_summary": "line one\nline two", "delegate": "researcher"})
    out = to_contract_event(ev)
    assert out == ToolCallStarted(run_id="r1", agent_name="author", tool_name="search_web",
                                  input_summary="line one\nline two", delegate="researcher")


def test_tool_call_finished_translates():
    ev = _ev(7, TelemetryEventType.TOOL_CALL_FINISHED,
            {"run_id": "r1", "agent_name": "author", "tool_name": "search_web",
             "duration_s": 1.2, "output_summary": "found stuff"})
    out = to_contract_event(ev)
    assert out == ToolCallFinished(run_id="r1", agent_name="author", tool_name="search_web",
                                   duration_s=1.2, output_summary="found stuff")


def test_tool_call_failed_translates():
    ev = _ev(8, TelemetryEventType.TOOL_CALL_FAILED,
            {"run_id": "r1", "agent_name": "author", "tool_name": "search_web",
             "duration_s": 0.3, "error_type": "ValueError"})
    out = to_contract_event(ev)
    assert out == ToolCallFailed(run_id="r1", agent_name="author", tool_name="search_web",
                                 duration_s=0.3, error_type="ValueError")


def test_scheduler_events_and_unknown_event_types_translate_to_none():
    picked = _ev(9, TelemetryEventType.SCHEDULER_PICKED, {"agent_name": "author"})
    assert to_contract_event(picked) is None
    elig = _ev(10, TelemetryEventType.SCHEDULER_ELIGIBILITY_CHANGED,
              {"agent_name": "author", "eligible": True, "reason": "ready"})
    assert to_contract_event(elig) is None
    assert to_contract_event("not a bus item") is None


def test_trace_line_formats_key_event_shapes():
    fin = _ev(3, TelemetryEventType.AGENT_RUN_FINISHED,
              {"run_id": "r1", "agent_name": "author", "duration_s": 52.0})
    assert "12:04:32" in trace_line(fin) and "author" in trace_line(fin) and "✓" in trace_line(fin)
    fail = _ev(4, TelemetryEventType.AGENT_RUN_FAILED,
               {"run_id": "r1", "agent_name": "editor", "error_type": "TimeoutError",
                "error_message": "x", "phase": "agent", "duration_s": 1.0})
    assert "✗" in trace_line(fail) and "TimeoutError" in trace_line(fail)
    picked = _ev(5, TelemetryEventType.SCHEDULER_PICKED, {"agent_name": "author"})
    assert "picked author" in trace_line(picked)


def test_trace_line_sanitizes_tool_call_input_summary():
    noisy = _ev(9, TelemetryEventType.TOOL_CALL_STARTED,
                {"run_id": "r1", "agent_name": "author", "tool_name": "grep",
                 "input_summary": "line one\nline two\nline three"})
    line = trace_line(noisy)
    assert "\n" not in line and "␤" in line
    long_input = _ev(10, TelemetryEventType.TOOL_CALL_STARTED,
                      {"run_id": "r1", "agent_name": "author", "tool_name": "grep",
                       "input_summary": "x" * 500})
    assert len(trace_line(long_input)) < 550


@given(st.lists(st.sampled_from([
    TelemetryEventType.AGENT_RUN_STARTED, TelemetryEventType.AGENT_RUN_FINISHED,
    TelemetryEventType.LLM_CALL_STARTED, TelemetryEventType.LLM_CALL_FINISHED,
    TelemetryEventType.SCHEDULER_PICKED, TelemetryEventType.SCHEDULER_ELIGIBILITY_CHANGED,
]), max_size=40))
def test_trace_replay_is_one_to_one_never_drops_or_duplicates(types):
    events = [_ev(i + 1, t, {"run_id": "r", "agent_name": "author", "eligible": True,
                             "reason": "ready", "call_index": 1, "model": "m", "prompt": "p",
                             "duration_s": 1.0, "output_tokens": 1, "error_type": "E",
                             "error_message": "m", "phase": "agent"})
              for i, t in enumerate(types)]
    lines = [trace_line(e) for e in events]
    assert len(lines) == len(events)
    assert all(isinstance(line, str) and line for line in lines)


def test_trace_detail_shows_prompt_and_produced_domain_events():
    call = _ev(2, TelemetryEventType.LLM_CALL_STARTED,
              {"run_id": "r1", "agent_name": "author", "call_index": 1, "model": "qwen",
               "prompt": "[system]\nWrite."})
    produced = [StoredEvent(sequence=9, id="d9", event_type="chapter.created",
                            aggregate_id="ch-12", payload={"title": "T"},
                            created_at="t", run_id="r1")]
    text = trace_detail(call, produced)
    assert "[system]\nWrite." in text
    assert "produced: chapter.created ch-12" in text
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/tui/test_telemetry_adapter.py -v`
Expected: FAIL with "No module named 'novelizer.tui.telemetry_adapter'"

- [ ] **Step 3: Write minimal implementation**

```python
# novelizer/tui/telemetry_adapter.py
"""Translates novelizer's real telemetry vocabulary (StoredEvent +
TelemetryEventType, plus the bus-only TokenDelta/ToolSummaryReady) into
tui_kit.contracts events, and formats the durable machinery trace.

trace_line/trace_detail stay here rather than in tui_kit: they render
*domain* events ("produced: chapter.created ch-12"), which is inherently
novelizer-specific, not part of the generic agent-run vocabulary.
"""
from __future__ import annotations
from novelizer.canon.events import StoredEvent
from novelizer.telemetry.events import TelemetryEventType
from novelizer.telemetry.events import TokenDelta as NovelizerTokenDelta
from novelizer.telemetry.events import ToolSummaryReady as NovelizerToolSummaryReady
from tui_kit import contracts
from tui_kit.run_model import normalize_input_summary


def to_contract_event(item):
    """Translate one bus item into a tui_kit.contracts event, or None if it
    carries nothing the generic run model renders (scheduler events, or any
    unrecognized shape)."""
    if isinstance(item, NovelizerTokenDelta):
        return contracts.TokenDelta(run_id=item.run_id, agent_name=item.agent_name,
                                    text=item.text, kind=item.kind)
    if isinstance(item, NovelizerToolSummaryReady):
        return contracts.ToolSummaryReady(run_id=item.run_id, agent_name=item.agent_name,
                                          tool_name=item.tool_name,
                                          input_summary=item.input_summary, summary=item.summary)
    if not isinstance(item, StoredEvent):
        return None
    p = item.payload
    et = item.event_type
    if et == TelemetryEventType.AGENT_RUN_STARTED:
        return contracts.RunStarted(run_id=p.get("run_id", ""), agent_name=p.get("agent_name", ""))
    if et == TelemetryEventType.AGENT_RUN_FINISHED:
        return contracts.RunFinished(run_id=p.get("run_id", ""), agent_name=p.get("agent_name", ""),
                                     duration_s=p.get("duration_s", 0.0))
    if et == TelemetryEventType.AGENT_RUN_FAILED:
        return contracts.RunFailed(run_id=p.get("run_id", ""), agent_name=p.get("agent_name", ""),
                                   error_type=p.get("error_type", "?"),
                                   error_message=p.get("error_message", ""))
    if et == TelemetryEventType.LLM_CALL_STARTED:
        return contracts.LLMCallStarted(run_id=p.get("run_id", ""), agent_name=p.get("agent_name", ""),
                                        call_index=p.get("call_index", 0), model=p.get("model", ""),
                                        prompt=p.get("prompt", ""))
    if et == TelemetryEventType.LLM_CALL_FINISHED:
        return contracts.LLMCallFinished(run_id=p.get("run_id", ""),
                                         agent_name=p.get("agent_name", ""),
                                         call_index=p.get("call_index", 0),
                                         duration_s=p.get("duration_s", 0.0),
                                         output_tokens=p.get("output_tokens", 0))
    if et == TelemetryEventType.TOOL_CALL_STARTED:
        return contracts.ToolCallStarted(run_id=p.get("run_id", ""), agent_name=p.get("agent_name", ""),
                                         tool_name=p.get("tool_name", "?"),
                                         input_summary=p.get("input_summary", ""),
                                         delegate=p.get("delegate", ""))
    if et == TelemetryEventType.TOOL_CALL_FINISHED:
        return contracts.ToolCallFinished(run_id=p.get("run_id", ""),
                                          agent_name=p.get("agent_name", ""),
                                          tool_name=p.get("tool_name", "?"),
                                          duration_s=p.get("duration_s", 0.0),
                                          output_summary=p.get("output_summary", ""))
    if et == TelemetryEventType.TOOL_CALL_FAILED:
        return contracts.ToolCallFailed(run_id=p.get("run_id", ""), agent_name=p.get("agent_name", ""),
                                        tool_name=p.get("tool_name", "?"),
                                        duration_s=p.get("duration_s", 0.0),
                                        error_type=p.get("error_type", "?"))
    return None


def _t(ev: StoredEvent) -> str:
    return ev.created_at[11:19]


def trace_line(ev: StoredEvent) -> str:
    p = ev.payload
    et = ev.event_type
    if et == TelemetryEventType.AGENT_RUN_STARTED:
        return f"{_t(ev)} {p.get('agent_name', '?')} run started"
    if et == TelemetryEventType.AGENT_RUN_FINISHED:
        return f"{_t(ev)} {p.get('agent_name', '?')} run ✓ {p.get('duration_s', 0):.0f}s"
    if et == TelemetryEventType.AGENT_RUN_FAILED:
        return (f"{_t(ev)} {p.get('agent_name', '?')} run ✗ {p.get('error_type', '?')} "
                f"({p.get('phase', '?')})")
    if et == TelemetryEventType.LLM_CALL_STARTED:
        return (f"{_t(ev)} {p.get('agent_name', '?')} llm call {p.get('call_index', '?')} "
                f"started ({p.get('model', '?')})")
    if et == TelemetryEventType.LLM_CALL_FINISHED:
        return (f"{_t(ev)} {p.get('agent_name', '?')} llm call {p.get('call_index', '?')} "
                f"✓ {p.get('duration_s', 0):.0f}s · {p.get('output_tokens', 0)} tok")
    if et == TelemetryEventType.LLM_CALL_FAILED:
        return (f"{_t(ev)} {p.get('agent_name', '?')} llm call {p.get('call_index', '?')} "
                f"✗ {p.get('error_type', '?')}")
    if et == TelemetryEventType.TOOL_CALL_STARTED:
        summary = normalize_input_summary(p.get('input_summary', ''))
        return (f"{_t(ev)} ⚒ {p.get('agent_name', '?')} → "
                f"{p.get('tool_name', '?')}({summary})")
    if et == TelemetryEventType.TOOL_CALL_FINISHED:
        return (f"{_t(ev)} ⚒ {p.get('agent_name', '?')} ← {p.get('tool_name', '?')} "
                f"({p.get('duration_s', 0):.1f}s)")
    if et == TelemetryEventType.TOOL_CALL_FAILED:
        return (f"{_t(ev)} ⚒ {p.get('agent_name', '?')} ✗ {p.get('tool_name', '?')}: "
                f"{p.get('error_type', '?')}")
    if et == TelemetryEventType.SCHEDULER_PICKED:
        return f"{_t(ev)} scheduler picked {p.get('agent_name', '?')}"
    if et == TelemetryEventType.SCHEDULER_ELIGIBILITY_CHANGED:
        flag = "eligible" if p.get("eligible") else "ineligible"
        return f"{_t(ev)} {p.get('agent_name', '?')} {flag}: {p.get('reason', '?')}"
    return f"{_t(ev)} {et}"


def trace_detail(ev: StoredEvent, produced: list[StoredEvent]) -> str:
    lines = [trace_line(ev), ""]
    p = dict(ev.payload)
    prompt = p.pop("prompt", None)
    for k, v in p.items():
        lines.append(f"{k}: {v}")
    for d in produced:
        lines.append(f"produced: {d.event_type} {d.aggregate_id}")
    if prompt is not None:
        lines += ["", "─ prompt ─", prompt]
    return "\n".join(lines)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/tui/test_telemetry_adapter.py -v`
Expected: PASS (all tests)

- [ ] **Step 5: Commit**

```bash
git add novelizer/tui/telemetry_adapter.py tests/tui/test_telemetry_adapter.py
git commit -m "feat(novelizer): translate telemetry bus items into tui_kit contract events"
```

---

### Task 8: Rewire `app.py` and `chat_screen.py` onto `tui_kit`

**Files:**
- Modify: `novelizer/tui/app.py`
- Modify: `novelizer/tui/chat_screen.py`
- Modify: `tests/tui/test_engine_room.py`
- Modify: `tests/tui/test_live_stream_panel.py`
- Delete: `tests/tui/test_engine_room_model.py` (superseded by `tests/tui_kit/test_run_model.py`, Task 2)
- Modify: `tests/tui/test_chat_screen.py`, `tests/tui/test_chat_routing.py` (import path only, if they import `engine_room_model`/`live_stream_panel` — check first)

**Interfaces:**
- Consumes: `tui_kit.run_model.{LiveRunState, apply_bus_item, route_agent, seed_state, seed_states}` (Task 2), `tui_kit.widgets.{EngineRoom, LiveStreamPanel, ActivityStrip}` (Task 3), `novelizer.tui.identity.{AGENT_NAMES, NOVELIZER_AGENT_THEME}` (Task 6), `novelizer.tui.telemetry_adapter.{to_contract_event, trace_line, trace_detail}` (Task 7)
- Produces: no new public interface — internal wiring only.

- [ ] **Step 1: Check which test files reference the old modules**

Run: `grep -rln "engine_room_model\|widgets\.live_stream_panel\|widgets\.activity_strip\|widgets\.engine_room\b" tests/tui/`

This should list `test_app.py`, `test_app_commands.py`, `test_app_resilience.py`, `test_app_smoke.py`, `test_app_layout.py`, `test_chat_screen.py`, `test_chat_routing.py`, `test_engine_room.py`, `test_live_stream_panel.py` (exact set may vary — check the actual output before editing).

- [ ] **Step 2: Update `novelizer/tui/app.py` imports and construction**

Replace the import block (`novelizer/tui/app.py` lines 35-40):

```python
from novelizer.tui.widgets.activity_strip import ActivityStrip
from novelizer.tui.widgets.engine_room import EngineRoom
from novelizer.tui.widgets.engine_room_model import (
    AGENT_NAMES, LiveRunState, apply_bus_item, route_agent, seed_state, seed_states,
    trace_line, trace_detail, normalize_input_summary,
)
```

with:

```python
from tui_kit.widgets.activity_strip import ActivityStrip
from tui_kit.widgets.engine_room import EngineRoom
from tui_kit.run_model import LiveRunState, apply_bus_item, route_agent, seed_state, seed_states
from novelizer.tui.identity import AGENT_NAMES, NOVELIZER_AGENT_THEME
from novelizer.tui.telemetry_adapter import to_contract_event, trace_line, trace_detail
from novelizer.tui.tool_summarizer import summarize_tool_call
```

(the `tool_summarizer` import already exists at line 18 — do not duplicate it; only add the four new import lines and drop `normalize_input_summary`, which is no longer used directly in `app.py`. If it is, keep `from tui_kit.run_model import normalize_input_summary` too — check `_summarize_tool_call`, Step 5 below.)

Replace `yield EngineRoom(id="engine_room")` (line 111) with:

```python
                yield EngineRoom(agent_names=list(AGENT_NAMES), theme=NOVELIZER_AGENT_THEME,
                                 id="engine_room")
```

Replace `yield ActivityStrip("idle", id="activity_strip")` (line 120) with:

```python
        yield ActivityStrip("idle", theme=NOVELIZER_AGENT_THEME, id="activity_strip")
```

- [ ] **Step 3: Update `_refresh_strip`**

Replace (around line 315-317):

```python
    def _refresh_strip(self) -> None:
        strip = self.query_one("#activity_strip", ActivityStrip)
        strip.render_state(self._live_state, time.monotonic(), self._next_hint())
```

This method is unchanged — `ActivityStrip.render_state`'s signature didn't gain a theme parameter (theme is bound at construction in Step 2). No edit needed here; confirm by re-reading the method after Step 2's changes and leave as-is.

- [ ] **Step 4: Update `_telemetry_bus_loop` to adapt bus items before folding**

Replace (around lines 339-361):

```python
        q = self.runtime.telemetry_bus.subscribe()
        while True:
            try:
                item = await q.get()
                now = time.monotonic()
                self._live_state = apply_bus_item(self._live_state, item, now)
                agent = route_agent(item)
                if agent:
                    self._agent_live_states[agent] = apply_bus_item(
                        self._agent_live_states.get(agent, LiveRunState()), item, now)
                if isinstance(item, StoredEvent):
                    self._trace_events.append(item)
                    self._refresh_trace()
                if isinstance(item, StoredEvent) and item.event_type in (
                        TelemetryEventType.TOOL_CALL_FINISHED, TelemetryEventType.TOOL_CALL_FAILED):
                    self.run_worker(self._summarize_tool_call(item), exclusive=False, group="tool-summary")
                self._refresh_strip()
                engine_room = self.query_one("#engine_room", EngineRoom)
                engine_room.render_live(self._live_state)
                if agent in AGENT_NAMES:
                    engine_room.render_agent_live(agent, self._agent_live_states[agent], now)
            except Exception as e:
                self._report_worker_error("telemetry", e)
```

with:

```python
        q = self.runtime.telemetry_bus.subscribe()
        while True:
            try:
                item = await q.get()
                now = time.monotonic()
                contract_item = to_contract_event(item)
                agent = None
                if contract_item is not None:
                    self._live_state = apply_bus_item(self._live_state, contract_item, now)
                    agent = route_agent(contract_item)
                    if agent:
                        self._agent_live_states[agent] = apply_bus_item(
                            self._agent_live_states.get(agent, LiveRunState()), contract_item, now)
                if isinstance(item, StoredEvent):
                    self._trace_events.append(item)
                    self._refresh_trace()
                if isinstance(item, StoredEvent) and item.event_type in (
                        TelemetryEventType.TOOL_CALL_FINISHED, TelemetryEventType.TOOL_CALL_FAILED):
                    self.run_worker(self._summarize_tool_call(item), exclusive=False, group="tool-summary")
                self._refresh_strip()
                engine_room = self.query_one("#engine_room", EngineRoom)
                engine_room.render_live(self._live_state)
                if agent in AGENT_NAMES:
                    engine_room.render_agent_live(agent, self._agent_live_states[agent], now)
            except Exception as e:
                self._report_worker_error("telemetry", e)
```

- [ ] **Step 5: Update `_summarize_tool_call`'s normalize_input_summary import**

Read the current method (`novelizer/tui/app.py` around line 363-386): it calls `normalize_input_summary` (imported from the old `engine_room_model`). Add this single import alongside the others from Step 2:

```python
from tui_kit.run_model import normalize_input_summary
```

(Add it to the `from tui_kit.run_model import ...` line already introduced in Step 2, i.e. that line becomes:
`from tui_kit.run_model import LiveRunState, apply_bus_item, route_agent, seed_state, seed_states, normalize_input_summary`)

No other change needed in this method's body — it already calls `normalize_input_summary(...)` by that name.

- [ ] **Step 6: Update `chat_screen.py`**

Read the current imports (`novelizer/tui/chat_screen.py` lines 9-10):

```python
from novelizer.tui.widgets.engine_room_model import LiveRunState, apply_bus_item, route_agent
from novelizer.tui.widgets.live_stream_panel import LiveStreamPanel
```

Replace with:

```python
from tui_kit.run_model import LiveRunState, apply_bus_item, route_agent
from tui_kit.widgets.live_stream_panel import LiveStreamPanel
from novelizer.tui.telemetry_adapter import to_contract_event
from novelizer.tui.identity import NOVELIZER_AGENT_THEME
```

Read the rest of `chat_screen.py` in full (`cat novelizer/tui/chat_screen.py`) and update every call site:

- `yield LiveStreamPanel(id="chat_live")` → `yield LiveStreamPanel(theme=NOVELIZER_AGENT_THEME, id="chat_live")`
- Every `apply_bus_item(self._live_state, item, ...)` call must first adapt: replace `route_agent(item)` / `apply_bus_item(self._live_state, item, time.monotonic())` with:
  ```python
  contract_item = to_contract_event(item)
  if contract_item is None or route_agent(contract_item) != f"chat:{self.agent_name}":
      return
  self._live_state = apply_bus_item(self._live_state, contract_item, time.monotonic())
  ```
  (mirror the exact original control flow — the original checked `route_agent(item) != f"chat:{self.agent_name}"` before folding; preserve that early-return, just adapt `item` to `contract_item` first and treat a `None` adaptation the same as a non-matching route).

- [ ] **Step 7: Update `tests/tui/test_engine_room.py` and `tests/tui/test_live_stream_panel.py` import paths**

In `tests/tui/test_engine_room.py`, replace:
```python
from novelizer.tui.widgets.activity_strip import ActivityStrip
```
with
```python
from tui_kit.widgets.activity_strip import ActivityStrip
```
and replace every inline `from novelizer.tui.widgets.engine_room import EngineRoom` with `from tui_kit.widgets.engine_room import EngineRoom`.

The test `test_agent_tab_titles_carry_glyph_and_color` constructs `EngineRoom(id="engine_room")` directly in a bare harness — update it to:
```python
from novelizer.tui.identity import AGENT_NAMES, NOVELIZER_AGENT_THEME
...
    def compose(self):
        yield EngineRoom(agent_names=list(AGENT_NAMES), theme=NOVELIZER_AGENT_THEME, id="engine_room")
```

All other tests in this file drive `EngineRoom` through `NovelizerApp`, which now constructs it correctly (Step 2) — no further changes needed there.

In `tests/tui/test_live_stream_panel.py`, replace:
```python
from novelizer.tui.widgets.live_stream_panel import LiveStreamPanel
from novelizer.tui.widgets.engine_room_model import Block, LiveRunState
```
with:
```python
from tui_kit.widgets.live_stream_panel import LiveStreamPanel
from tui_kit.run_model import Block, LiveRunState
from novelizer.tui.identity import NOVELIZER_AGENT_THEME
```
and update the harness:
```python
class _Harness(App):
    def compose(self) -> ComposeResult:
        yield LiveStreamPanel(theme=NOVELIZER_AGENT_THEME, id="panel")
```

- [ ] **Step 8: Delete the superseded pure-model test file**

```bash
git rm tests/tui/test_engine_room_model.py
```

(its coverage now lives in `tests/tui_kit/test_run_model.py`, Task 2)

- [ ] **Step 9: Run the full TUI test suite to verify it passes**

Run: `uv run pytest tests/tui/ tests/tui_kit/ -v`
Expected: PASS (all tests). If any test still imports `novelizer.tui.widgets.engine_room_model` / `.engine_room` / `.live_stream_panel` / `.activity_strip`, fix that import per the patterns above before proceeding.

- [ ] **Step 10: Commit**

```bash
git add novelizer/tui/app.py novelizer/tui/chat_screen.py tests/tui/
git commit -m "refactor(novelizer): wire app.py and chat_screen.py onto tui_kit"
```

---

### Task 9: Trim `roster.py`, delete superseded modules

**Files:**
- Modify: `novelizer/tui/widgets/roster.py`
- Modify: `tests/tui/test_roster.py`
- Delete: `novelizer/tui/widgets/engine_room_model.py`
- Delete: `novelizer/tui/widgets/engine_room.py`
- Delete: `novelizer/tui/widgets/live_stream_panel.py`
- Delete: `novelizer/tui/widgets/activity_strip.py`

**Interfaces:**
- Consumes: `tui_kit.widgets.roster.roster_glyphs` (Task 4), `novelizer.tui.identity.NOVELIZER_AGENT_THEME` (Task 6)
- Produces: `novelizer.tui.widgets.roster.{dial_meter, status_strip, DIAL_SEGMENTS, DIAL_FILLED, DIAL_EMPTY, DIAL_LEVELS, DIAL_STYLES}` — unchanged signatures, `status_strip(status, state)` still takes exactly two arguments (no caller-visible change).

- [ ] **Step 1: Confirm nothing outside novelizer/tui/widgets/roster.py and its tests still imports the old symbols**

Run: `grep -rln "widgets\.roster import roster_glyphs\|widgets\.roster import roster_summary\|widgets\.engine_room_model\|widgets\.engine_room import\|widgets\.live_stream_panel\|widgets\.activity_strip" --include="*.py" novelizer/ tests/`

Expected: only `tests/tui/test_roster.py` (to be edited in Step 3) and the four files staged for deletion. If anything else appears, stop and update it first (it was missed in Task 8).

- [ ] **Step 2: Rewrite `novelizer/tui/widgets/roster.py`**

Read the current file (`cat novelizer/tui/widgets/roster.py`) then replace its content with:

```python
"""Pure Zone-5 statusbar rendering: scheduler status + autonomy state ->
Rich Text. The roster glyph strip itself lives in tui_kit.widgets.roster
(domain-agnostic); this module adds novelizer's autonomy dial and composes
the two into the full statusbar, using NOVELIZER_AGENT_THEME for glyphs."""
from __future__ import annotations

from rich.text import Text

from novelizer.canon.autonomy import AutonomyLevel, AutonomyState
from novelizer.tui.identity import NOVELIZER_AGENT_THEME
from tui_kit.widgets.roster import roster_glyphs

DIM = "dim"

DIAL_SEGMENTS = 4
DIAL_FILLED = "▮"
DIAL_EMPTY = "▯"

# Trust ladder from the real AutonomyLevel enum: filled segments = trust.
# full_auto is the wide-open dial; gated_all is the floor (1 filled).
DIAL_LEVELS: dict[AutonomyLevel, int] = {
    AutonomyLevel.full_auto: 4,
    AutonomyLevel.gated_retcons: 3,
    AutonomyLevel.gated_canon: 2,
    AutonomyLevel.gated_all: 1,
}
# Color steps with trust (spec Zone 5: green → amber; red at the floor).
DIAL_STYLES: dict[AutonomyLevel, str] = {
    AutonomyLevel.full_auto: "green3",
    AutonomyLevel.gated_retcons: "gold3",
    AutonomyLevel.gated_canon: "dark_orange",
    AutonomyLevel.gated_all: "red",
}


def dial_meter(state: AutonomyState) -> Text:
    """The dial: 'AUTONOMY ▮▮▯▯ gated_canon' — filled segments = trust
    position on the ladder, per-agent overrides folded to a dim suffix."""
    filled = DIAL_LEVELS[state.global_level]
    style = DIAL_STYLES[state.global_level]
    meter = Text()  # no base style: each part styles itself via spans
    meter.append("AUTONOMY ", style=DIM)
    meter.append(DIAL_FILLED * filled, style=style)
    if filled < DIAL_SEGMENTS:
        meter.append(DIAL_EMPTY * (DIAL_SEGMENTS - filled), style=DIM)
    meter.append(f" {state.global_level.value}", style=style)
    if state.overrides:
        summary = ", ".join(f"{k}={v.value}" for k, v in state.overrides.items())
        meter.append(f" ({summary})", style=DIM)
    return meter


def status_strip(status: list, state: AutonomyState) -> Text:
    """The whole Zone-5 statusbar: roster glyph strip + autonomy dial."""
    strip = roster_glyphs(status, NOVELIZER_AGENT_THEME)
    strip.append("    ")
    strip.append_text(dial_meter(state))
    return strip
```

- [ ] **Step 3: Rewrite `tests/tui/test_roster.py`**

Read the current file (`cat tests/tui/test_roster.py`), then replace its content with just the `dial_meter`/`status_strip` tests (the `roster_glyphs`/`roster_summary` tests already moved to `tests/tui_kit/test_roster.py` in Task 4):

```python
from novelizer.canon.autonomy import AutonomyLevel, AutonomyState
from novelizer.tui.widgets.roster import dial_meter, status_strip


def _row(name, paused=False, running=False, last_error=None):
    return {"name": name, "paused": paused, "running": running, "last_error": last_error,
            "last_completed": False, "run_count": 0, "next_ready_in": 0.0}


def test_dial_meter_gated_canon_is_two_filled_segments():
    meter = dial_meter(AutonomyState(global_level=AutonomyLevel.gated_canon))
    assert meter.plain == "AUTONOMY ▮▮▯▯ gated_canon"


def test_dial_meter_full_auto_all_filled_and_gated_all_one_filled():
    full = dial_meter(AutonomyState(global_level=AutonomyLevel.full_auto))
    assert full.plain == "AUTONOMY ▮▮▮▮ full_auto"
    floor = dial_meter(AutonomyState(global_level=AutonomyLevel.gated_all))
    assert floor.plain == "AUTONOMY ▮▯▯▯ gated_all"
    mid = dial_meter(AutonomyState(global_level=AutonomyLevel.gated_retcons))
    assert mid.plain == "AUTONOMY ▮▮▮▯ gated_retcons"


def test_dial_meter_color_steps_with_trust():
    full = dial_meter(AutonomyState(global_level=AutonomyLevel.full_auto))
    styles = [(full.plain[s.start:s.end], str(s.style)) for s in full.spans]
    assert ("▮▮▮▮", "green3") in styles
    floor = dial_meter(AutonomyState(global_level=AutonomyLevel.gated_all))
    styles = [(floor.plain[s.start:s.end], str(s.style)) for s in floor.spans]
    assert ("▮", "red") in styles


def test_dial_meter_summarizes_overrides_compactly():
    meter = dial_meter(AutonomyState(
        global_level=AutonomyLevel.full_auto,
        overrides={"retconner": AutonomyLevel.gated_all},
    ))
    assert meter.plain == "AUTONOMY ▮▮▮▮ full_auto (retconner=gated_all)"


def test_status_strip_composes_roster_then_dial():
    strip = status_strip([_row("author")], AutonomyState(global_level=AutonomyLevel.gated_canon))
    assert strip.plain == "✎·    AUTONOMY ▮▮▯▯ gated_canon"
```

- [ ] **Step 4: Run the roster tests to verify they pass**

Run: `uv run pytest tests/tui/test_roster.py -v`
Expected: PASS (all 5 tests)

- [ ] **Step 5: Delete the superseded modules**

```bash
git rm novelizer/tui/widgets/engine_room_model.py
git rm novelizer/tui/widgets/engine_room.py
git rm novelizer/tui/widgets/live_stream_panel.py
git rm novelizer/tui/widgets/activity_strip.py
```

- [ ] **Step 6: Run the full test suite**

Run: `uv run pytest tests/ -v`
Expected: PASS (all tests, including `tests/tui_kit/`, `tests/tui/`, and everything else unaffected by this change)

- [ ] **Step 7: Run the import linter**

Run: `uv run lint-imports`
Expected: PASS

- [ ] **Step 8: Commit**

```bash
git add novelizer/tui/widgets/roster.py tests/tui/test_roster.py
git commit -m "refactor(novelizer): trim roster.py to autonomy dial, delete superseded tui_kit-migrated modules"
```

---

### Task 10: Final verification pass

**Files:** none (verification only)

**Interfaces:** none

- [ ] **Step 1: Grep for any remaining stale references**

Run: `grep -rln "widgets\.engine_room_model\|widgets\.engine_room\b\|widgets\.live_stream_panel\|widgets\.activity_strip" --include="*.py" .`

Expected: no output. If anything appears, fix the import and re-run.

- [ ] **Step 2: Run the full test suite one more time**

Run: `uv run pytest tests/ -v`
Expected: PASS

- [ ] **Step 3: Run the import linter one more time**

Run: `uv run lint-imports`
Expected: PASS

- [ ] **Step 4: Manually smoke-test the running app**

Run: `uv run novelizer` against a scratch story directory (or whatever this project's existing manual-verification convention is — check `docs/TESTING-TUI.md` first) and confirm: the Engine Room opens with `e`, shows per-agent tabs with the correct glyphs/colors, the activity strip updates, and the roster glyph strip in the statusbar still renders correctly.

- [ ] **Step 5: Update the spec's implementation status (optional but recommended)**

If this project's convention (see recent commits like `fbab101 docs(substrate): add README and mark package-boundary spec implemented`) is to mark specs as implemented, add a one-line note to the top of `docs/superpowers/specs/2026-07-22-tui-kit-extraction-design.md` noting it's implemented, with the merge commit once known.

- [ ] **Step 6: Hand off**

Invoke `superpowers:finishing-a-development-branch` to decide how to merge this work to `main`.
