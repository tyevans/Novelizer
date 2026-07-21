# Engine Room tool-call blocks Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Turn the Engine Room / chat live stream's tool-call rendering from two loose plain-text lines into structured, groupable "blocks" that (a) box a call with its result, (b) collapse consecutive identical calls with a counter, and (c) backfill a cheap-LLM one-line summary once the call finishes.

**Architecture:** `engine_room_model.py` (pure, no I/O) gains a `Block` dataclass and replaces its append-only `LiveRunState.text` string with `LiveRunState.blocks: tuple[Block, ...]`. `apply_bus_item` builds/updates/collapses blocks instead of concatenating strings. A new bus item type, `ToolSummaryReady`, carries a summary back into `apply_bus_item` once it's ready, matched by `(run_id, tool_name, input_summary)` rather than a synthetic id — this lets the same summary correctly patch the two independently-folded `LiveRunState` copies the app already keeps (the merged "All" view and the per-agent view) without needing to keep their block-numbering in sync. The actual LLM summarization call lives outside the pure model, in the Textual `App`'s telemetry loop (`novelizer/tui/app.py`), which already owns the bus subscription and `settings`; it publishes the result back onto the same shared `telemetry_bus`, so `ChatScreen`'s own subscriber picks it up for free — no per-screen wiring needed.

**Tech Stack:** Python, Textual, `rich.text.Text`, `langchain_openai` (via `novelizer.agents.llm.build_chat_model`), pydantic (telemetry payloads), pytest + hypothesis.

## Global Constraints

- `engine_room_model.py` stays pure: no LLM calls, no Textual imports, no I/O. (from spec's "Design")
- `ActivityStrip` is out of scope — no changes. (spec: "Out of scope")
- No new Textual widgets per tool-call block; rendering stays inside the existing single `Static` via `rich.text.Text` styling. (spec: "Out of scope")
- Summarization failure or slowness must never block or break the live stream — a block simply renders without a summary if none arrives. (spec: "Design §2")
- `TEXT_CAP` behavior (tail-capping very long streams) must be preserved in the new blocks model — the spec's testing section expects the running/finished/failed rendering forms to still work under the same constraints as today.

---

## File Structure

- `novelizer/tui/widgets/engine_room_model.py` — **modify**. Add `Block`; rewrite `LiveRunState`, `apply_bus_item`, `live_body`, `styled_body`, `stream_line_kind`, `route_agent`.
- `novelizer/telemetry/events.py` — **modify**. Add `ToolSummaryReady`; add `input_summary`/`output_summary` fields to `ToolCallFinished`/`ToolCallFailed`.
- `novelizer/telemetry/callbacks.py` — **modify**. Carry `input_summary` from `on_tool_start` through to `on_tool_end`/`on_tool_error`; add `output_summary` on tool-end.
- `novelizer/tui/tool_summarizer.py` — **create**. `summarize_tool_call(settings, tool_name, input_summary, output_summary, error) -> str`, the only place that makes the cheap LLM call.
- `novelizer/tui/app.py` — **modify**. Trigger `tool_summarizer.summarize_tool_call` as a background worker on `TOOL_CALL_FINISHED`/`TOOL_CALL_FAILED`, publish `ToolSummaryReady` on completion.
- `tests/tui/test_engine_room_model.py` — **modify**. Rewrite string-based assertions against `blocks`; add repeat-collapsing and summary-patch tests.
- `tests/tui/test_engine_room.py`, `tests/tui/test_live_stream_panel.py` — **modify** only if they assert on the removed `.text` field or exact rendered line shapes that change (check during Task 1).
- `tests/telemetry/test_events.py`, `tests/telemetry/test_callbacks.py` — **modify**. Cover new payload fields and `ToolSummaryReady`.
- `tests/tui/test_tool_summarizer.py` — **create**.
- `tests/tui/test_app_layout.py` — **modify**. Add one integration test for the summarizer wiring.

---

### Task 1: `Block` dataclass and blocks-based `LiveRunState`

**Files:**
- Modify: `novelizer/tui/widgets/engine_room_model.py`
- Test: `tests/tui/test_engine_room_model.py`

**Interfaces:**
- Produces: `Block` dataclass (frozen), fields `kind: str`, `text: str = ""`, `tool_name: str = ""`, `input_summary: str = ""`, `status: str = "running"`, `duration_s: float = 0.0`, `error: str = ""`, `summary: str | None = None`, `repeat_count: int = 1`.
- Produces: `LiveRunState.blocks: tuple[Block, ...] = ()` (replaces `text: str`).
- Produces: `apply_bus_item(state, item, now) -> LiveRunState` — same signature, now folds into `blocks`.
- Produces: `live_body(state) -> str`, `styled_body(body: str) -> Text` — same signatures; `live_body` renders `blocks` into the same kind of string surface used today (so `stream_line_kind`/`styled_body` keep working on it), just with tool blocks rendered as a 2-3 line boxed group.
- Consumes: nothing new from outside this file.

- [ ] **Step 1: Write failing tests for the `Block`-based fold**

Replace the string-based tool/call tests in `tests/tui/test_engine_room_model.py` with block-based ones. Edit the file:

```python
def test_run_started_resets_state_to_a_fresh_running_run():
    s = apply_bus_item(LiveRunState(blocks=(Block(kind="prose", text="stale"),), tokens=9),
                        _run_started(), now=100.0)
    assert s.status == "running" and s.agent_name == "author" and s.run_id == "r1"
    assert s.tokens == 0 and s.blocks == () and s.started_at == 100.0


def test_token_deltas_accumulate_into_a_trailing_prose_block():
    s = LiveRunState(status="running", run_id="r1", agent_name="author")
    s = apply_bus_item(s, TokenDelta(run_id="r1", agent_name="author", text="The "), now=1.0)
    s = apply_bus_item(s, TokenDelta(run_id="r1", agent_name="author", text="sea"), now=1.1)
    assert len(s.blocks) == 1
    assert s.blocks[0].kind == "prose" and s.blocks[0].text == "The sea"
    assert s.tokens == 2


def test_apply_bus_item_marks_the_boundary_between_thinking_and_answer_text():
    s = LiveRunState(status="running", run_id="r1", agent_name="author")
    s = apply_bus_item(s, TokenDelta(run_id="r1", agent_name="author",
                                     text="let me consider", kind="thinking"), now=1.0)
    assert len(s.blocks) == 1 and s.blocks[0].kind == "thinking"
    assert s.blocks[0].text == "let me consider"
    s = apply_bus_item(s, TokenDelta(run_id="r1", agent_name="author",
                                     text=" the tide.", kind="thinking"), now=1.1)
    assert len(s.blocks) == 1 and s.blocks[0].text == "let me consider the tide."
    s = apply_bus_item(s, TokenDelta(run_id="r1", agent_name="author",
                                     text="The lighthouse", kind="text"), now=1.2)
    assert len(s.blocks) == 2
    assert s.blocks[1].kind == "prose" and s.blocks[1].text == "The lighthouse"


def test_apply_bus_item_folds_llm_call_boundaries_into_a_call_block():
    s = LiveRunState(status="running", run_id="r1", agent_name="author")
    s = apply_bus_item(s, _call_started(), now=1.0)
    assert len(s.blocks) == 1
    call = s.blocks[0]
    assert call.kind == "call" and call.status == "running"

    fin = _ev(3, TelemetryEventType.LLM_CALL_FINISHED,
              {"run_id": "r1", "agent_name": "author", "call_index": 1,
               "duration_s": 2.5, "output_tokens": 40})
    s = apply_bus_item(s, fin, now=3.0)
    call = s.blocks[0]
    assert call.status == "done" and call.duration_s == 2.5


def test_apply_bus_item_opens_and_closes_a_tool_block():
    s = LiveRunState(status="running", run_id="r1", agent_name="author",
                     blocks=(Block(kind="prose", text="Once upon a time"),))
    started = _ev(6, TelemetryEventType.TOOL_CALL_STARTED,
                  {"run_id": "r1", "agent_name": "author", "tool_name": "search_web",
                   "input_summary": "dragons"})
    s = apply_bus_item(s, started, now=1.0)
    assert len(s.blocks) == 2
    tool = s.blocks[1]
    assert tool.kind == "tool" and tool.tool_name == "search_web"
    assert tool.input_summary == "dragons" and tool.status == "running"

    finished = _ev(7, TelemetryEventType.TOOL_CALL_FINISHED,
                   {"run_id": "r1", "agent_name": "author", "tool_name": "search_web",
                    "duration_s": 1.2})
    s = apply_bus_item(s, finished, now=2.0)
    tool = s.blocks[1]
    assert tool.status == "done" and tool.duration_s == 1.2
    assert len(s.blocks) == 2  # no new block created on finish


def test_apply_bus_item_marks_a_tool_block_failed():
    s = LiveRunState(status="running", run_id="r1", agent_name="author")
    started = _ev(6, TelemetryEventType.TOOL_CALL_STARTED,
                  {"run_id": "r1", "agent_name": "author", "tool_name": "search_web",
                   "input_summary": "dragons"})
    s = apply_bus_item(s, started, now=1.0)
    failed = _ev(8, TelemetryEventType.TOOL_CALL_FAILED,
                {"run_id": "r1", "agent_name": "author", "tool_name": "search_web",
                 "error_type": "ValueError", "duration_s": 0.3})
    s = apply_bus_item(s, failed, now=1.0)
    tool = s.blocks[0]
    assert tool.status == "failed" and tool.error == "ValueError"


def test_repeated_identical_tool_calls_collapse_with_a_counter():
    s = LiveRunState(status="running", run_id="r1", agent_name="author")
    for i in range(3):
        started = _ev(10 + i * 2, TelemetryEventType.TOOL_CALL_STARTED,
                      {"run_id": "r1", "agent_name": "author", "tool_name": "read_file",
                       "input_summary": "ch3.md"})
        s = apply_bus_item(s, started, now=float(i))
        finished = _ev(11 + i * 2, TelemetryEventType.TOOL_CALL_FINISHED,
                       {"run_id": "r1", "agent_name": "author", "tool_name": "read_file",
                        "duration_s": 0.1})
        s = apply_bus_item(s, finished, now=float(i) + 0.1)
    assert len(s.blocks) == 1
    assert s.blocks[0].repeat_count == 3
    assert s.blocks[0].status == "done"


def test_different_args_do_not_collapse():
    s = LiveRunState(status="running", run_id="r1", agent_name="author")
    for arg in ("ch3.md", "ch4.md"):
        started = _ev(1, TelemetryEventType.TOOL_CALL_STARTED,
                      {"run_id": "r1", "agent_name": "author", "tool_name": "read_file",
                       "input_summary": arg})
        s = apply_bus_item(s, started, now=1.0)
        finished = _ev(2, TelemetryEventType.TOOL_CALL_FINISHED,
                       {"run_id": "r1", "agent_name": "author", "tool_name": "read_file",
                        "duration_s": 0.1})
        s = apply_bus_item(s, finished, now=1.1)
    assert len(s.blocks) == 2
    assert all(b.repeat_count == 1 for b in s.blocks)


def test_live_body_renders_a_tool_block_as_a_grouped_multiline_unit():
    s = LiveRunState(status="running", run_id="r1", agent_name="author")
    started = _ev(1, TelemetryEventType.TOOL_CALL_STARTED,
                  {"run_id": "r1", "agent_name": "author", "tool_name": "search_web",
                   "input_summary": "dragons"})
    s = apply_bus_item(s, started, now=1.0)
    finished = _ev(2, TelemetryEventType.TOOL_CALL_FINISHED,
                   {"run_id": "r1", "agent_name": "author", "tool_name": "search_web",
                    "duration_s": 1.2})
    s = apply_bus_item(s, finished, now=2.0)
    body = live_body(s)
    assert "⚒ search_web(dragons)" in body
    assert "done in 1.2s" in body


def test_live_body_shows_repeat_counter_and_summary_once_attached():
    s = LiveRunState(status="running", run_id="r1", agent_name="author",
                     blocks=(Block(kind="tool", tool_name="read_file", input_summary="ch3.md",
                                   status="done", duration_s=0.3, repeat_count=3,
                                   summary="skimmed three chapter drafts"),))
    body = live_body(s)
    assert "⚒ read_file(ch3.md) ×3" in body
    assert "skimmed three chapter drafts" in body


def test_text_is_still_tail_capped_via_prose_blocks():
    from novelizer.tui.widgets.engine_room_model import TEXT_CAP
    long_prose = "x" * TEXT_CAP
    s = LiveRunState(status="running", run_id="r1",
                     blocks=(Block(kind="prose", text=long_prose),))
    s = apply_bus_item(s, TokenDelta(run_id="r1", agent_name="author", text="END"), now=1.0)
    assert s.blocks[-1].text.endswith("END")
    assert len(s.blocks[-1].text) <= TEXT_CAP + 3  # capped roughly at TEXT_CAP


def test_seed_state_of_a_finished_run_is_not_stuck_running():
    fin = _ev(3, TelemetryEventType.AGENT_RUN_FINISHED,
              {"run_id": "r1", "agent_name": "author", "duration_s": 52.0})
    s = seed_state([_run_started(), _call_started(), fin], now=10.0)
    assert s.status == "finished"


def test_seed_states_keeps_concurrent_agents_isolated():
    events = [
        _ev(1, TelemetryEventType.AGENT_RUN_STARTED, {"run_id": "r1", "agent_name": "author"}),
        _ev(2, TelemetryEventType.AGENT_RUN_STARTED, {"run_id": "r2", "agent_name": "editor"}),
        _ev(3, TelemetryEventType.TOOL_CALL_STARTED,
           {"run_id": "r1", "agent_name": "author", "tool_name": "search_web",
            "input_summary": "dragons"}),
        _ev(4, TelemetryEventType.TOOL_CALL_STARTED,
           {"run_id": "r2", "agent_name": "editor", "tool_name": "read",
            "input_summary": "ch1.md"}),
    ]
    states = seed_states(events, now=10.0)
    assert set(states) == {"author", "editor"}
    assert states["author"].blocks[0].tool_name == "search_web"
    assert states["editor"].blocks[0].tool_name == "read"
    assert states["author"].run_id == "r1" and states["editor"].run_id == "r2"


def test_styled_body_applies_tool_style_to_tool_lines():
    from novelizer.tui.widgets.engine_room_model import styled_body
    text = styled_body("\n⚒ search_canon(query)\n")
    styles = [span.style for span in text.spans]
    assert "bold cyan" in styles


def test_styled_body_leaves_prose_unstyled():
    from novelizer.tui.widgets.engine_room_model import styled_body
    text = styled_body("plain prose line")
    assert text.spans == []
```

Also update the imports at the top of the test file to add `Block`:

```python
from novelizer.tui.widgets.engine_room_model import (
    Block, LiveRunState, apply_bus_item, route_agent, seed_state, seed_states,
    strip_line, stream_line_kind, vitals_line, live_body, trace_line, trace_detail,
)
```

Remove these now-obsolete tests (they assert on the removed `.text` field or on exact behavior superseded above): `test_token_deltas_accumulate_text_and_count`, `test_text_is_tail_capped`, `test_apply_bus_item_folds_tool_calls_into_the_live_text_stream`, `test_apply_bus_item_folds_tool_call_failure_into_the_live_text_stream`, `test_apply_bus_item_folds_llm_call_boundaries_into_the_live_text_stream` (superseded by the block versions above — keep only the new ones). Leave `test_live_body_stream_not_attached_notice_after_restart_mid_run`, `test_strip_line_running_idle_and_failed_forms`, `test_vitals_line_running_and_finished_forms`, `test_trace_*`, `test_route_agent_reads_agent_name_from_token_deltas_and_events`, `test_styled_vitals_includes_glyph_and_vitals_line`, and the hypothesis replay test as-is — they don't touch `.text`/blocks.

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/tui/test_engine_room_model.py -v`
Expected: FAIL — `Block` doesn't exist yet, `LiveRunState` has no `blocks` field.

- [ ] **Step 3: Implement `Block` and rewrite the fold/render functions**

In `novelizer/tui/widgets/engine_room_model.py`:

Replace the `LiveRunState` dataclass and the `_append` helper, and add `Block`:

```python
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
```

Replace `_append` with block helpers:

```python
def _append_text_block(state: LiveRunState, kind: str, text: str) -> LiveRunState:
    """Append to the trailing block if it's the same kind, else open a new one."""
    if state.blocks and state.blocks[-1].kind == kind:
        last = state.blocks[-1]
        merged = (last.text + text)[-TEXT_CAP:]
        blocks = state.blocks[:-1] + (replace(last, text=merged),)
    else:
        blocks = state.blocks + (Block(kind=kind, text=text[-TEXT_CAP:]),)
    return replace(state, blocks=blocks)
```

Rewrite the body of `apply_bus_item` (keep the `TokenDelta`/`StoredEvent` dispatch shape, change the bodies):

```python
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
    if not isinstance(item, StoredEvent):
        return state
    p = item.payload
    et = item.event_type
    if et == TelemetryEventType.AGENT_RUN_STARTED:
        return LiveRunState(status="running", run_id=p.get("run_id", ""),
                            agent_name=p.get("agent_name", ""), started_at=now)
    if p.get("run_id") != state.run_id:
        return state
    if et == TelemetryEventType.LLM_CALL_STARTED:
        state = replace(state, prompt=p.get("prompt", ""), model=p.get("model", ""),
                        call_index=p.get("call_index", 0))
        blocks = state.blocks + (Block(kind="call", status="running",
                                       text=f"call {p.get('call_index', '?')} ({p.get('model', '?')})"),)
        return replace(state, blocks=blocks)
    if et == TelemetryEventType.LLM_CALL_FINISHED:
        state = replace(state, tokens=p.get("output_tokens", state.tokens))
        if state.blocks and state.blocks[-1].kind == "call":
            last = state.blocks[-1]
            blocks = state.blocks[:-1] + (replace(last, status="done",
                                                  duration_s=p.get("duration_s", 0.0)),)
            state = replace(state, blocks=blocks)
        return state
    if et == TelemetryEventType.AGENT_RUN_FINISHED:
        return replace(state, status="finished", ended_at=now)
    if et == TelemetryEventType.AGENT_RUN_FAILED:
        error = f"{p.get('error_type', '?')}: {p.get('error_message', '')}"
        return replace(state, status="failed", ended_at=now, error=error)
    if et == TelemetryEventType.TOOL_CALL_STARTED:
        tool_name = p.get("tool_name", "?")
        input_summary = str(p.get("input_summary", "")).replace("\n", "␤")[:120]
        if (state.blocks and state.blocks[-1].kind == "tool"
                and state.blocks[-1].tool_name == tool_name
                and state.blocks[-1].input_summary == input_summary
                and state.blocks[-1].status != "running"):
            last = state.blocks[-1]
            blocks = state.blocks[:-1] + (replace(last, status="running",
                                                  repeat_count=last.repeat_count + 1,
                                                  summary=None),)
        else:
            blocks = state.blocks + (Block(kind="tool", tool_name=tool_name,
                                           input_summary=input_summary, status="running"),)
        return replace(state, blocks=blocks)
    if et in (TelemetryEventType.TOOL_CALL_FINISHED, TelemetryEventType.TOOL_CALL_FAILED):
        tool_name = p.get("tool_name", "?")
        for i in range(len(state.blocks) - 1, -1, -1):
            b = state.blocks[i]
            if b.kind == "tool" and b.tool_name == tool_name and b.status == "running":
                if et == TelemetryEventType.TOOL_CALL_FINISHED:
                    updated = replace(b, status="done", duration_s=p.get("duration_s", 0.0))
                else:
                    updated = replace(b, status="failed", duration_s=p.get("duration_s", 0.0),
                                      error=p.get("error_type", "?"))
                blocks = state.blocks[:i] + (updated,) + state.blocks[i + 1:]
                return replace(state, blocks=blocks)
        return state
    return state
```

Add the `ToolSummaryReady` import at the top (it's defined in Task 2 — for now, forward-declare it locally in this file as a lightweight dataclass so Task 1 can land independently, then Task 2 moves it into `telemetry/events.py` and updates the import):

```python
from dataclasses import dataclass as _dc


@dataclass(frozen=True)
class ToolSummaryReady:
    run_id: str
    agent_name: str
    tool_name: str
    input_summary: str
    summary: str
```

(This placeholder lives in `engine_room_model.py` only for this step; Task 2 deletes it here and imports the real one from `telemetry/events.py`.)

Now rewrite `live_body`, `stream_line_kind`, and `styled_body` to render from `blocks`:

```python
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
    lines = [f"⚒ {b.tool_name}({b.input_summary}){suffix}"]
    if b.status == "done":
        lines.append(f"   ↳ done in {b.duration_s:.1f}s")
    elif b.status == "failed":
        lines.append(f"   ↳ ✗ {b.error}")
    if b.summary:
        lines.append(f"   ↳ {b.summary}")
    return "\n".join(lines)


def stream_line_kind(line: str) -> str:
    s = line.strip()
    if s.startswith("⚒"):
        return "tool"
    if s.startswith("▸") or s.startswith("↳"):
        return "call"
    if s.startswith("💭"):
        return "thinking"
    return "prose"
```

`styled_body` is unchanged (it already operates line-by-line on whatever `live_body` returns via `stream_line_kind`), but move it below the new functions if needed for definition order — no logic change required.

Remove the now-dead `_append` function.

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/tui/test_engine_room_model.py -v`
Expected: PASS

- [ ] **Step 5: Check downstream consumers of `.text` still work**

Run: `grep -rn "\.text\b" novelizer/tui/widgets/engine_room.py novelizer/tui/widgets/live_stream_panel.py novelizer/tui/chat_screen.py novelizer/tui/app.py`
Expected: no hits referencing `LiveRunState.text` (they only call `live_body`/`styled_body`, confirmed during design research — this step just double-checks nothing was missed).

Run: `pytest tests/tui/test_engine_room.py tests/tui/test_live_stream_panel.py tests/tui/test_brain_panel.py tests/tui/test_app_layout.py -v`
Expected: PASS (these consume `live_body`/`styled_body` output, not `.text`, so they should be unaffected; if any fail on exact string assertions, fix the assertion to match the new grouped rendering, not the production code).

- [ ] **Step 6: Commit**

```bash
git add novelizer/tui/widgets/engine_room_model.py tests/tui/test_engine_room_model.py
git commit -m "feat(tui): render tool calls as grouped blocks with repeat collapsing"
```

---

### Task 2: `ToolSummaryReady` bus item, real home in `telemetry/events.py`

**Files:**
- Modify: `novelizer/telemetry/events.py`
- Modify: `novelizer/tui/widgets/engine_room_model.py` (delete the Task 1 placeholder, import the real one; wire into `route_agent`)
- Test: `tests/telemetry/test_events.py`, `tests/tui/test_engine_room_model.py`

**Interfaces:**
- Produces: `novelizer.telemetry.events.ToolSummaryReady(run_id: str, agent_name: str, tool_name: str, input_summary: str, summary: str)` — a plain pydantic `BaseModel` like `TokenDelta`, bus-only (never persisted).
- Consumes: nothing new.

- [ ] **Step 1: Write failing test for the event shape**

Add to `tests/telemetry/test_events.py` (create the file with this content if it doesn't already cover `TokenDelta`; check first with `grep -n "TokenDelta" tests/telemetry/test_events.py`):

```python
from novelizer.telemetry.events import ToolSummaryReady


def test_tool_summary_ready_is_bus_only_shape():
    item = ToolSummaryReady(run_id="r1", agent_name="author", tool_name="search_web",
                            input_summary="dragons", summary="found three articles")
    assert item.run_id == "r1" and item.tool_name == "search_web"
    assert item.summary == "found three articles"
```

Add to `tests/tui/test_engine_room_model.py`:

```python
def test_route_agent_reads_agent_name_from_tool_summary_ready():
    from novelizer.telemetry.events import ToolSummaryReady
    item = ToolSummaryReady(run_id="r1", agent_name="editor", tool_name="t",
                            input_summary="x", summary="s")
    assert route_agent(item) == "editor"


def test_tool_summary_ready_patches_the_matching_finished_block():
    s = LiveRunState(status="running", run_id="r1", agent_name="author",
                     blocks=(Block(kind="tool", tool_name="search_web",
                                   input_summary="dragons", status="done", duration_s=1.0),))
    from novelizer.telemetry.events import ToolSummaryReady
    ready = ToolSummaryReady(run_id="r1", agent_name="author", tool_name="search_web",
                             input_summary="dragons", summary="found three articles")
    s = apply_bus_item(s, ready, now=5.0)
    assert s.blocks[0].summary == "found three articles"


def test_tool_summary_ready_is_a_no_op_when_the_run_has_moved_on():
    s = LiveRunState(status="running", run_id="r2", agent_name="author", blocks=())
    from novelizer.telemetry.events import ToolSummaryReady
    ready = ToolSummaryReady(run_id="r1", agent_name="author", tool_name="search_web",
                             input_summary="dragons", summary="stale")
    s2 = apply_bus_item(s, ready, now=5.0)
    assert s2 == s


def test_tool_summary_ready_skips_a_block_thats_running_again_via_a_repeat():
    s = LiveRunState(status="running", run_id="r1", agent_name="author",
                     blocks=(Block(kind="tool", tool_name="search_web",
                                   input_summary="dragons", status="running",
                                   repeat_count=2),))
    from novelizer.telemetry.events import ToolSummaryReady
    ready = ToolSummaryReady(run_id="r1", agent_name="author", tool_name="search_web",
                             input_summary="dragons", summary="stale summary for call 1")
    s2 = apply_bus_item(s, ready, now=5.0)
    assert s2.blocks[0].summary is None
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/telemetry/test_events.py tests/tui/test_engine_room_model.py -v`
Expected: FAIL — `ToolSummaryReady` doesn't exist in `telemetry.events` yet.

- [ ] **Step 3: Add `ToolSummaryReady` to `telemetry/events.py`**

Add near `TokenDelta`:

```python
class ToolSummaryReady(BaseModel):
    """A cheap-LLM one-line summary of a finished/failed tool call, matched
    back to its block by (run_id, tool_name, input_summary) rather than a
    synthetic id -- the same event is folded independently into more than
    one LiveRunState (the merged "All" view and each per-agent view), and
    those don't share block numbering. Bus-only: NEVER persisted."""

    run_id: str
    agent_name: str
    tool_name: str
    input_summary: str
    summary: str
```

In `novelizer/tui/widgets/engine_room_model.py`: delete the placeholder `ToolSummaryReady` dataclass added in Task 1, and instead import the real one:

```python
from novelizer.telemetry.events import TelemetryEventType, ToolSummaryReady, TokenDelta
```

In `route_agent`, add:

```python
def route_agent(item) -> str | None:
    if isinstance(item, TokenDelta):
        return item.agent_name or None
    if isinstance(item, ToolSummaryReady):
        return item.agent_name or None
    if isinstance(item, StoredEvent):
        return item.payload.get("agent_name") or None
    return None
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/telemetry/test_events.py tests/tui/test_engine_room_model.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add novelizer/telemetry/events.py novelizer/tui/widgets/engine_room_model.py \
        tests/telemetry/test_events.py tests/tui/test_engine_room_model.py
git commit -m "feat(telemetry): add ToolSummaryReady bus item and route/patch it into blocks"
```

---

### Task 3: Carry `input_summary`/`output_summary` through tool-call telemetry payloads

**Files:**
- Modify: `novelizer/telemetry/events.py`
- Modify: `novelizer/telemetry/callbacks.py`
- Test: `tests/telemetry/test_callbacks.py`

**Interfaces:**
- Produces: `ToolCallFinished.input_summary: str`, `ToolCallFinished.output_summary: str`, `ToolCallFailed.input_summary: str` (all pydantic fields on existing models).
- Consumes: `ToolCallStarted.input_summary` (already exists) — reused as the value carried forward.

- [ ] **Step 1: Write failing tests**

Add to `tests/telemetry/test_callbacks.py`, next to `test_tool_end_emits_call_finished_with_duration_and_output_size`:

```python
def test_tool_end_carries_input_summary_and_a_truncated_output_summary():
    rec = FakeRecorder()
    h = TelemetryCallbackHandler(rec)
    lc_run = uuid.uuid4()

    async def run():
        await h.on_tool_start({"name": "search_web"}, "dragons", run_id=lc_run)
        await h.on_tool_end("x" * 500, run_id=lc_run)

    _in_run(run)
    et, payload = rec.emitted[-1]
    assert et == TelemetryEventType.TOOL_CALL_FINISHED
    assert payload.input_summary == "dragons"
    assert len(payload.output_summary) <= 300


def test_tool_error_carries_input_summary():
    rec = FakeRecorder()
    h = TelemetryCallbackHandler(rec)
    lc_run = uuid.uuid4()

    async def run():
        await h.on_tool_start({"name": "search_web"}, "dragons", run_id=lc_run)
        await h.on_tool_error(ValueError("bad"), run_id=lc_run)

    _in_run(run)
    et, payload = rec.emitted[-1]
    assert et == TelemetryEventType.TOOL_CALL_FAILED
    assert payload.input_summary == "dragons"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/telemetry/test_callbacks.py -v`
Expected: FAIL — `payload.input_summary`/`output_summary` don't exist on `ToolCallFinished`/`ToolCallFailed` yet (`AttributeError`).

- [ ] **Step 3: Implement**

In `novelizer/telemetry/events.py`:

```python
class ToolCallFinished(BaseModel):
    run_id: str
    agent_name: str
    tool_name: str
    duration_s: float
    output_chars: int
    input_summary: str = ""
    output_summary: str = ""


class ToolCallFailed(BaseModel):
    run_id: str
    agent_name: str
    tool_name: str
    duration_s: float
    error_type: str
    error_message: str
    input_summary: str = ""
```

In `novelizer/telemetry/callbacks.py`, find `_ToolCallState` (used by `on_tool_start`/`on_tool_end`/`on_tool_error`) and add an `input_summary` field, populated at start:

```python
state = _ToolCallState(nrun, agent_name, tool_name)
```
becomes (check the actual `_ToolCallState` definition — it's likely a small dataclass/namedtuple near the top of the file; add the field there, e.g. `input_summary: str = ""`), then in `on_tool_start`:

```python
async def on_tool_start(self, serialized: dict, input_str: str, *, run_id: UUID, **kwargs: Any) -> None:
    nrun = current_run_id.get() or ""
    tool_name = (serialized or {}).get("name", "")
    agent_name = current_agent_name.get()
    input_summary = str(input_str)[:300]
    state = _ToolCallState(nrun, agent_name, tool_name)
    state.input_summary = input_summary
    self._tool_calls[run_id] = state
    await self._recorder.emit(
        TelemetryEventType.TOOL_CALL_STARTED, nrun,
        ToolCallStarted(run_id=nrun, agent_name=agent_name, tool_name=tool_name,
                        input_summary=input_summary),
    )
```

(If `_ToolCallState` is a frozen dataclass, construct it with the field directly instead of assigning after the fact — check its definition first and match its actual construction style.)

Update `on_tool_end` and `on_tool_error`:

```python
async def on_tool_end(self, output: Any, *, run_id: UUID, **kwargs: Any) -> None:
    state = self._tool_calls.pop(run_id, None)
    if state is None:
        return
    await self._recorder.emit(
        TelemetryEventType.TOOL_CALL_FINISHED, state.novelizer_run_id,
        ToolCallFinished(run_id=state.novelizer_run_id, agent_name=state.agent_name,
                         tool_name=state.tool_name,
                         duration_s=time.monotonic() - state.started,
                         output_chars=len(str(output)),
                         input_summary=state.input_summary,
                         output_summary=str(output)[:300]),
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
                       input_summary=state.input_summary),
    )
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/telemetry/test_callbacks.py tests/telemetry/test_events.py -v`
Expected: PASS

- [ ] **Step 5: Also feed `input_summary` into `apply_bus_item`'s tool-finish matching (Task 1's finish handler currently only matches on `tool_name` + `status == "running"`, which is already sufficient since only one tool call per name is `running` at a time — no change needed there, but confirm)**

Run: `pytest tests/tui/test_engine_room_model.py -v`
Expected: PASS (no change required; this step is a verification checkpoint, not new code).

- [ ] **Step 6: Commit**

```bash
git add novelizer/telemetry/events.py novelizer/telemetry/callbacks.py tests/telemetry/test_callbacks.py
git commit -m "feat(telemetry): carry input/output summaries through tool-call events"
```

---

### Task 4: `tool_summarizer.summarize_tool_call`

**Files:**
- Create: `novelizer/tui/tool_summarizer.py`
- Test: `tests/tui/test_tool_summarizer.py`

**Interfaces:**
- Produces: `async def summarize_tool_call(settings, tool_name: str, input_summary: str, output_summary: str, error: str) -> str`.
- Consumes: `novelizer.agents.llm.build_chat_model(model, base_url, api_key, temperature=..., max_tokens=...)` (existing, returns a LangChain chat model with an async `.ainvoke(messages)`); `settings.agent_model`, `settings.llm_base_url`, `settings.llm_api_key` (existing `Settings` attributes, already used the same way in every `novelizer/agents/*.py` builder).

- [ ] **Step 1: Write the failing test**

Create `tests/tui/test_tool_summarizer.py`:

```python
import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch
from novelizer.tui.tool_summarizer import summarize_tool_call


def _settings():
    return SimpleNamespace(agent_model="m", llm_base_url="http://x", llm_api_key="k")


def test_summarize_tool_call_builds_a_result_prompt_and_returns_stripped_content():
    fake_model = SimpleNamespace(ainvoke=AsyncMock(
        return_value=SimpleNamespace(content=" found three matching entries \n")))
    with patch("novelizer.tui.tool_summarizer.build_chat_model", return_value=fake_model) as build:
        result = asyncio.run(summarize_tool_call(
            _settings(), "search_web", "dragons", "3 results found", ""))
    assert result == "found three matching entries"
    build.assert_called_once()
    _, kwargs = build.call_args
    assert kwargs.get("max_tokens", None) is not None  # capped, stays cheap


def test_summarize_tool_call_uses_the_error_line_when_the_call_failed():
    captured = {}

    async def fake_ainvoke(messages):
        captured["prompt"] = messages[0].content
        return SimpleNamespace(content="failed to reach the search API")

    fake_model = SimpleNamespace(ainvoke=fake_ainvoke)
    with patch("novelizer.tui.tool_summarizer.build_chat_model", return_value=fake_model):
        result = asyncio.run(summarize_tool_call(
            _settings(), "search_web", "dragons", "", "TimeoutError: proxy"))
    assert result == "failed to reach the search API"
    assert "TimeoutError: proxy" in captured["prompt"]
    assert "dragons" in captured["prompt"]
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/tui/test_tool_summarizer.py -v`
Expected: FAIL — `novelizer.tui.tool_summarizer` doesn't exist.

- [ ] **Step 3: Implement**

Create `novelizer/tui/tool_summarizer.py`:

```python
from __future__ import annotations
from langchain_core.messages import HumanMessage
from novelizer.agents.llm import build_chat_model

_PROMPT = (
    "Summarize this tool call in one short plain sentence (under 15 words), "
    "no markdown, no quotes.\n\nTool: {tool_name}\nInput: {input_summary}\n{result_line}"
)


async def summarize_tool_call(
    settings, tool_name: str, input_summary: str, output_summary: str, error: str,
) -> str:
    """Cheap one-line summary of a finished/failed tool call, for backfilling
    into the Engine Room's live stream. Runs with no telemetry callbacks (so
    it never appears in the machinery view itself) and a small max_tokens
    cap to keep it cheap; the caller is expected to treat failures here as
    non-fatal (see novelizer/tui/app.py)."""
    result_line = f"Error: {error}" if error else f"Result: {output_summary}"
    prompt = _PROMPT.format(tool_name=tool_name, input_summary=input_summary,
                            result_line=result_line)
    model = build_chat_model(
        settings.agent_model, settings.llm_base_url, settings.llm_api_key,
        temperature=0.0, max_tokens=40,
    )
    response = await model.ainvoke([HumanMessage(content=prompt)])
    text = str(response.content).strip().replace("\n", " ")
    return text[:200]
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/tui/test_tool_summarizer.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add novelizer/tui/tool_summarizer.py tests/tui/test_tool_summarizer.py
git commit -m "feat(tui): add cheap-LLM tool-call summarizer"
```

---

### Task 5: Wire the summarizer into the App's telemetry loop

**Files:**
- Modify: `novelizer/tui/app.py`
- Test: `tests/tui/test_app_layout.py`

**Interfaces:**
- Consumes: `novelizer.tui.tool_summarizer.summarize_tool_call` (Task 4); `novelizer.telemetry.events.ToolSummaryReady` (Task 2); `self.runtime.telemetry_bus.publish(item)` (existing, `TelemetryBus.publish`); `self.runtime.settings` (existing).
- Produces: nothing new consumed elsewhere — this is the terminal wiring task.

- [ ] **Step 1: Write the failing integration test**

Add to `tests/tui/test_app_layout.py` (check the top of the file for the existing app-construction fixture pattern — likely a `_make_app()`/`_settings()` helper used by the other tests; reuse it). Add:

```python
async def test_tool_call_finish_triggers_a_summary_that_lands_in_the_live_state():
    from unittest.mock import AsyncMock, patch
    from novelizer.canon.events import StoredEvent
    from novelizer.telemetry.events import TelemetryEventType

    app = _make_app()  # reuse whatever fixture the file already uses for a bare app
    async with app.run_test() as pilot:
        with patch("novelizer.tui.app.summarize_tool_call",
                   new=AsyncMock(return_value="skimmed the outline")):
            started = StoredEvent(
                sequence=1, id="e1", event_type=TelemetryEventType.TOOL_CALL_STARTED,
                aggregate_id="r1", created_at="2026-07-21T00:00:00+00:00",
                payload={"run_id": "r1", "agent_name": "author", "tool_name": "read_file",
                        "input_summary": "ch3.md"})
            finished = StoredEvent(
                sequence=2, id="e2", event_type=TelemetryEventType.TOOL_CALL_FINISHED,
                aggregate_id="r1", created_at="2026-07-21T00:00:01+00:00",
                payload={"run_id": "r1", "agent_name": "author", "tool_name": "read_file",
                        "input_summary": "ch3.md", "duration_s": 0.5,
                        "output_summary": "chapter text..."})
            app.runtime.telemetry_bus.publish(started)
            app.runtime.telemetry_bus.publish(finished)
            await pilot.pause()
            await pilot.pause()  # let the background summarizer worker complete
            block = app._live_state.blocks[-1]
            assert block.tool_name == "read_file"
            assert block.summary == "skimmed the outline"
```

If `_make_app()` doesn't exist as a shared helper, check how the other tests in this file construct `app` (look at `test_mission_control_panes_present_and_populate`) and copy that construction inline instead — do not invent a new fixture name that collides with an existing one.

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/tui/test_app_layout.py -v -k tool_call_finish_triggers`
Expected: FAIL — `novelizer.tui.app.summarize_tool_call` doesn't exist as an importable name yet (nothing triggers a summary).

- [ ] **Step 3: Implement the wiring**

In `novelizer/tui/app.py`, add the import:

```python
from novelizer.tui.tool_summarizer import summarize_tool_call
from novelizer.telemetry.events import ToolSummaryReady
```

In `_telemetry_bus_loop`, right after the existing `self._live_state = apply_bus_item(...)` / `self._agent_live_states[...] = apply_bus_item(...)` block (inside the `while True: item = await q.get(); ...` loop), add the trigger:

```python
if isinstance(item, StoredEvent) and item.event_type in (
        TelemetryEventType.TOOL_CALL_FINISHED, TelemetryEventType.TOOL_CALL_FAILED):
    self.run_worker(self._summarize_tool_call(item), exclusive=False, group="tool-summary")
```

Add the worker method to the `App` class (near the other `_telemetry_*` methods):

```python
async def _summarize_tool_call(self, ev: StoredEvent) -> None:
    p = ev.payload
    tool_name = p.get("tool_name", "?")
    input_summary = p.get("input_summary", "")
    if ev.event_type == TelemetryEventType.TOOL_CALL_FINISHED:
        output_summary, error = p.get("output_summary", ""), ""
    else:
        output_summary = ""
        error = f"{p.get('error_type', '?')}: {p.get('error_message', '')}"
    try:
        summary = await summarize_tool_call(
            self.runtime.settings, tool_name, input_summary, output_summary, error)
    except Exception as e:
        logger.warning("tool-call summarization failed: %s", e)
        return
    self.runtime.telemetry_bus.publish(ToolSummaryReady(
        run_id=p.get("run_id", ""), agent_name=p.get("agent_name", ""),
        tool_name=tool_name, input_summary=input_summary, summary=summary))
```

(Check `app.py` for an existing `logger` — it likely already imports `logging` and defines a module `logger`; reuse it. If it doesn't, add `import logging` and `logger = logging.getLogger(__name__)` near the top, matching the pattern in `chat_screen.py`.)

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/tui/test_app_layout.py -v -k tool_call_finish_triggers`
Expected: PASS

- [ ] **Step 5: Run the full TUI + telemetry test suites**

Per project convention, never run test suites in the main checkout — run from this worktree only.

Run: `pytest tests/tui tests/telemetry -v`
Expected: PASS, no regressions.

- [ ] **Step 6: Commit**

```bash
git add novelizer/tui/app.py tests/tui/test_app_layout.py
git commit -m "feat(tui): fire cheap-LLM tool-call summaries from the telemetry loop"
```

---

### Task 6: Full-suite verification and manual smoke check

**Files:** none (verification only).

- [ ] **Step 1: Run the whole test suite in this worktree**

Run: `pytest -q`
Expected: PASS, no regressions anywhere (in particular `tests/tui/test_brain_panel.py`, which also touches `engine_room_model` per the earlier grep).

- [ ] **Step 2: Manual smoke check per project convention for TUI changes**

Use the `run` skill (or the project's documented TUI launch steps) to start the app against a test story, trigger an agent run that calls a tool at least twice in a row (e.g. two `read_file` calls on the same file), and visually confirm in the Engine Room: (a) the tool call and its result render as one grouped block, (b) the repeat renders as `×2`, (c) a summary line appears under the block within a few seconds of it finishing. Note in your final report whether this was actually run and observed, or whether it could only be verified via the automated tests.

- [ ] **Step 3: Commit if the smoke check surfaced any fixes; otherwise this task is verification-only and produces no commit.**
