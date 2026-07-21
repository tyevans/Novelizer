# Live Agent Stream for Chat and Research Screens Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Give `ChatScreen` (`@author` etc.) and `ResearchScreen` ("Talk to the Project") a live panel showing the agent's token/reasoning stream and tool calls while a turn is in flight, by reusing Engine Room's existing telemetry machinery instead of rebuilding it.

**Architecture:** A shared `run_with_identity` helper (mirrors `BaseAgent.run_once`'s ambient-identity + `AGENT_RUN_*` bracket) is used by both `ChatService` and `ResearchService` to tag their runner calls with separate identities (`"chat:<agent>"`, `"research"`). A new `LiveStreamPanel` widget, built from Engine Room's existing pure render functions (extracted, not duplicated), is mounted on both screens and fed by a per-screen `TelemetryBus` subscription filtered to that screen's identity key.

**Tech Stack:** Python, Textual, pytest + pytest-asyncio.

## Global Constraints

- Separate identity per spec: chat uses `f"chat:{agent_name}"`, research uses `"research"` — never the bare agent name an autonomous agent already uses, and never rendered into Engine Room's own seven tabs (spec §Decisions 2).
- Panel is always present in the screen's layout, not toggled (spec §Decisions 3).
- No replay/seeding from the durable telemetry log — panels start idle on every mount (spec §Decisions 4).
- `telemetry` is an optional (`None`-defaultable) constructor parameter on both services — existing tests and call sites that don't pass it keep working unchanged.
- Every new `TelemetryBus` subscription (`bus.subscribe()`) must be paired with `bus.unsubscribe()` when the owning screen unmounts/the worker ends, via `try/finally` — screens are pushed and popped repeatedly across a session, unlike the app's own single lifetime subscription, so an un-paired subscribe is a real accumulating leak, not a theoretical one.

---

### Task 1: `run_with_identity` telemetry helper

**Files:**
- Modify: `novelizer/telemetry/recorder.py`
- Test: `tests/telemetry/test_recorder.py`

**Interfaces:**
- Consumes: `novelizer.run_context.current_run_id`/`current_agent_name`
  (existing `ContextVar`s); `novelizer.telemetry.events.TelemetryEventType`,
  `AgentRunStarted`, `AgentRunFinished`, `AgentRunFailed` (existing);
  `TelemetryRecorder.emit`/`in_llm_call` (existing, same file).
- Produces: `run_with_identity(telemetry, name: str)` — an
  `@asynccontextmanager` yielding the generated `run_id: str`. `telemetry`
  may be `None` (no-op: no events emitted, context vars still set/reset).
  Tasks 4 and 5 (`ChatService`, `ResearchService`) both call this.

- [ ] **Step 1: Write the failing tests**

Append to `tests/telemetry/test_recorder.py`:

```python
from novelizer.telemetry.recorder import run_with_identity
from novelizer.run_context import current_agent_name, current_run_id


async def test_run_with_identity_emits_started_then_finished(rig):
    store, bus, rec = rig
    q = bus.subscribe()
    async with run_with_identity(rec, "research") as run_id:
        assert current_agent_name.get() == "research"
        assert current_run_id.get() == run_id
    started = q.get_nowait()
    finished = q.get_nowait()
    assert started.event_type == TelemetryEventType.AGENT_RUN_STARTED
    assert started.payload["agent_name"] == "research"
    assert started.payload["run_id"] == run_id
    assert finished.event_type == TelemetryEventType.AGENT_RUN_FINISHED
    assert finished.payload["run_id"] == run_id


async def test_run_with_identity_emits_failed_and_reraises(rig):
    store, bus, rec = rig
    q = bus.subscribe()
    with pytest.raises(ValueError):
        async with run_with_identity(rec, "chat:author"):
            raise ValueError("boom")
    q.get_nowait()  # started
    failed = q.get_nowait()
    assert failed.event_type == TelemetryEventType.AGENT_RUN_FAILED
    assert failed.payload["error_type"] == "ValueError"
    assert failed.payload["error_message"] == "boom"


async def test_run_with_identity_resets_context_vars_after(rig):
    store, bus, rec = rig
    assert current_agent_name.get() == ""
    assert current_run_id.get() is None
    async with run_with_identity(rec, "research"):
        pass
    assert current_agent_name.get() == ""
    assert current_run_id.get() is None


async def test_run_with_identity_resets_context_vars_on_exception(rig):
    store, bus, rec = rig
    with pytest.raises(RuntimeError):
        async with run_with_identity(rec, "research"):
            raise RuntimeError("x")
    assert current_agent_name.get() == ""
    assert current_run_id.get() is None


async def test_run_with_identity_is_a_no_op_with_no_telemetry():
    async with run_with_identity(None, "research") as run_id:
        assert current_agent_name.get() == "research"
        assert run_id  # still a real generated id, just nothing emitted
    assert current_agent_name.get() == ""
```

`pytest` is already collecting `tests/telemetry/test_recorder.py` as async
(no `@pytest.mark.asyncio` markers on the existing tests in that file —
check the file's top for an `asyncio_mode` fixture/marker configuration
before assuming markers are needed; match whatever convention the existing
tests in that file already use).

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/telemetry/test_recorder.py -v`
Expected: FAIL — `ImportError: cannot import name 'run_with_identity'`

- [ ] **Step 3: Write the implementation**

In `novelizer/telemetry/recorder.py`, add near the top (after existing
imports):

```python
import time
import uuid
from contextlib import asynccontextmanager
from novelizer.run_context import current_agent_name, current_run_id
from novelizer.telemetry.events import AgentRunFailed, AgentRunFinished, AgentRunStarted
```

(`TelemetryEventType` and `TokenDelta` are already imported in this file —
don't duplicate that import line, just add the three new event model names
and the three new stdlib/local imports above.)

Add at the end of the file:

```python
@asynccontextmanager
async def run_with_identity(telemetry, name: str):
    """Bracket a block of work with ambient run identity (current_run_id /
    current_agent_name) and AGENT_RUN_* telemetry — the same contract
    BaseAgent.run_once gives autonomous agents, reusable for call sites
    that aren't a BaseAgent (chat, research). `telemetry` may be None: the
    context vars are still set/reset, nothing is emitted."""
    run_id = str(uuid.uuid4())
    started = time.monotonic()
    rid_token = current_run_id.set(run_id)
    name_token = current_agent_name.set(name)
    if telemetry is not None:
        await telemetry.emit(
            TelemetryEventType.AGENT_RUN_STARTED, run_id,
            AgentRunStarted(run_id=run_id, agent_name=name),
        )
    try:
        yield run_id
    except Exception as e:
        if telemetry is not None:
            phase = "llm_call" if telemetry.in_llm_call(run_id) else "agent"
            await telemetry.emit(
                TelemetryEventType.AGENT_RUN_FAILED, run_id,
                AgentRunFailed(
                    run_id=run_id, agent_name=name, error_type=type(e).__name__,
                    error_message=str(e), phase=phase, duration_s=time.monotonic() - started,
                ),
            )
        raise
    else:
        if telemetry is not None:
            await telemetry.emit(
                TelemetryEventType.AGENT_RUN_FINISHED, run_id,
                AgentRunFinished(run_id=run_id, agent_name=name, duration_s=time.monotonic() - started),
            )
    finally:
        current_run_id.reset(rid_token)
        current_agent_name.reset(name_token)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/telemetry/test_recorder.py -v`
Expected: PASS (all tests in the file, existing + 5 new)

- [ ] **Step 5: Commit**

```bash
git add novelizer/telemetry/recorder.py tests/telemetry/test_recorder.py
git commit -m "feat(telemetry): add run_with_identity helper for non-agent callers"
```

---

### Task 2: Extract `styled_vitals`/`styled_body` into `engine_room_model.py`

**Files:**
- Modify: `novelizer/tui/widgets/engine_room_model.py`
- Modify: `novelizer/tui/widgets/engine_room.py`
- Test: `tests/tui/test_engine_room_model.py`

**Interfaces:**
- Consumes: `novelizer.tui.identity.identity_for` (existing);
  `rich.text.Text`; the existing `vitals_line`, `live_body`,
  `stream_line_kind` (same file, unchanged).
- Produces: `styled_vitals(state: LiveRunState, now: float) -> Text`,
  `styled_body(body: str) -> Text` — pure functions in
  `engine_room_model.py`. Task 3 (`LiveStreamPanel`) imports these
  directly; `engine_room.py`'s own rendering calls them instead of its
  former private copies.

- [ ] **Step 1: Write the failing tests**

Append to `tests/tui/test_engine_room_model.py` (read the top of that file
first for its existing import style and `LiveRunState(...)` construction
pattern, and match it):

```python
def test_styled_vitals_includes_glyph_and_vitals_line():
    from novelizer.tui.widgets.engine_room_model import styled_vitals
    state = LiveRunState(status="running", agent_name="author", started_at=0.0,
                         model="m", call_index=1, tokens=5)
    text = styled_vitals(state, now=2.0)
    plain = text.plain
    assert "author" in plain
    assert "✎" in plain  # author's glyph from identity_for


def test_styled_body_applies_tool_style_to_tool_lines():
    from novelizer.tui.widgets.engine_room_model import styled_body
    text = styled_body("\n⚒ search_canon(query)\n")
    # spans carries the style runs; a tool-prefixed line gets the "bold cyan" style
    styles = [span.style for span in text.spans]
    assert "bold cyan" in styles


def test_styled_body_leaves_prose_unstyled():
    from novelizer.tui.widgets.engine_room_model import styled_body
    text = styled_body("plain prose line")
    assert text.spans == []
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/tui/test_engine_room_model.py -v`
Expected: FAIL — `ImportError: cannot import name 'styled_vitals'`

- [ ] **Step 3: Write the implementation**

In `novelizer/tui/widgets/engine_room_model.py`, add near the top:

```python
from rich.text import Text
from novelizer.tui.identity import identity_for
```

Add at the end of the file (after `trace_detail`):

```python
# Rich styles per stream_line_kind(); "" leaves prose in the theme default so
# the agent's own accent color (applied to the vitals bar) stays the visual
# anchor rather than competing with a wall of colored prose.
_LINE_STYLES = {"tool": "bold cyan", "call": "dim", "thinking": "italic dim magenta"}


def styled_vitals(state: LiveRunState, now: float) -> Text:
    ident = identity_for(state.agent_name)
    return Text(f"{ident.glyph} {vitals_line(state, now)}", style=ident.style)


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

In `novelizer/tui/widgets/engine_room.py`:

1. Remove the `_LINE_STYLES` dict and the `_styled_vitals`/`_styled_body`
   function definitions (lines 20-42 of the file as it stands before this
   task).
2. Change the import from `engine_room_model` to include the two new
   names:

```python
from novelizer.tui.widgets.engine_room_model import (
    AGENT_NAMES, LiveRunState, live_body, stream_line_kind, styled_body, styled_vitals, vitals_line,
)
```

(`stream_line_kind` is no longer used directly in this file after the
extraction — check whether anything else in `engine_room.py` still
references it before removing it from this import list; if nothing does,
drop it from the import too.)

3. Replace every call site `_styled_vitals(...)` → `styled_vitals(...)`
   and `_styled_body(...)` → `styled_body(...)` in `render_live` and
   `render_agent_live` (two call sites each, four total).

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/tui/test_engine_room_model.py tests/tui/test_engine_room.py -v`
Expected: PASS (new tests + no regressions in the existing Engine Room
suite — this is a pure extraction, behavior must be identical)

- [ ] **Step 5: Commit**

```bash
git add novelizer/tui/widgets/engine_room_model.py novelizer/tui/widgets/engine_room.py tests/tui/test_engine_room_model.py
git commit -m "refactor(tui): extract styled_vitals/styled_body into engine_room_model"
```

---

### Task 3: `LiveStreamPanel` widget

**Files:**
- Create: `novelizer/tui/widgets/live_stream_panel.py`
- Test: `tests/tui/test_live_stream_panel.py`

**Interfaces:**
- Consumes: `novelizer.tui.widgets.engine_room_model.LiveRunState`,
  `live_body`, `styled_vitals`, `styled_body` (Task 2).
- Produces: `LiveStreamPanel` (a Textual `Vertical`), with `render(state:
  LiveRunState, now: float | None = None) -> None`. Widget owns no bus
  subscription and no identity/key — the owning screen (Tasks 6, 7) does
  the routing and calls `render()` with whatever `LiveRunState` it has
  computed for its own key.

- [ ] **Step 1: Write the failing tests**

Create `tests/tui/test_live_stream_panel.py`:

```python
import pytest
from textual.app import App, ComposeResult
from textual.widgets import Static
from textual.containers import VerticalScroll
from novelizer.tui.widgets.live_stream_panel import LiveStreamPanel
from novelizer.tui.widgets.engine_room_model import LiveRunState


class _Harness(App):
    def compose(self) -> ComposeResult:
        yield LiveStreamPanel(id="panel")


@pytest.mark.asyncio
async def test_idle_state_renders_idle_body():
    app = _Harness()
    async with app.run_test() as pilot:
        panel = app.query_one("#panel", LiveStreamPanel)
        panel.render(LiveRunState(), now=0.0)
        await pilot.pause()
        body = panel.query_one(LiveStreamPanel._STREAM_ID, Static)
        assert "no run yet" in str(body.renderable)


@pytest.mark.asyncio
async def test_running_state_shows_agent_name_in_vitals():
    app = _Harness()
    async with app.run_test() as pilot:
        panel = app.query_one("#panel", LiveStreamPanel)
        state = LiveRunState(status="running", agent_name="research", started_at=0.0, text="hello")
        panel.render(state, now=1.0)
        await pilot.pause()
        vitals = panel.query_one(LiveStreamPanel._VITALS_ID, Static)
        assert "research" in str(vitals.renderable)
        body = panel.query_one(LiveStreamPanel._STREAM_ID, Static)
        assert "hello" in str(body.renderable)
```

Widget ids referenced via class attributes (`_VITALS_ID`, `_STREAM_ID`)
rather than hardcoded strings in the test, so the implementation and test
can't silently drift apart — Step 3 defines these constants.

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/tui/test_live_stream_panel.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'novelizer.tui.widgets.live_stream_panel'`

- [ ] **Step 3: Write the implementation**

Create `novelizer/tui/widgets/live_stream_panel.py`:

```python
from __future__ import annotations
import time
from textual.app import ComposeResult
from textual.containers import Vertical, VerticalScroll
from textual.widgets import Static
from novelizer.tui.widgets.engine_room_model import LiveRunState, live_body, styled_body, styled_vitals


class LiveStreamPanel(Vertical):
    """A single-agent live token/tool-call stream, the same rendering
    Engine Room gives each autonomous agent's tab, without the tab strip.
    Owns no bus subscription and no identity — the mounting screen computes
    the LiveRunState for its own key and calls render()."""

    _VITALS_ID = "#lsp_vitals"
    _STREAM_ID = "#lsp_stream"
    _STREAM_SCROLL_ID = "#lsp_stream_scroll"

    def __init__(self, *args, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        self._rendered_body: str = ""

    def compose(self) -> ComposeResult:
        yield Static("idle — waiting for the scheduler",
                     id=self._VITALS_ID.removeprefix("#"), classes="lsp-vitals", markup=False)
        with VerticalScroll(id=self._STREAM_SCROLL_ID.removeprefix("#"), classes="lsp-stream-scroll"):
            yield Static("", id=self._STREAM_ID.removeprefix("#"), classes="lsp-stream", markup=False)

    def render(self, state: LiveRunState, now: float | None = None) -> None:
        now = time.monotonic() if now is None else now
        self.query_one(self._VITALS_ID, Static).update(styled_vitals(state, now))
        body = live_body(state)
        if body != self._rendered_body:
            self.query_one(self._STREAM_ID, Static).update(styled_body(body))
            self._rendered_body = body
            self.query_one(self._STREAM_SCROLL_ID, VerticalScroll).scroll_end(animate=False)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/tui/test_live_stream_panel.py -v`
Expected: PASS (2 tests)

- [ ] **Step 5: Commit**

```bash
git add novelizer/tui/widgets/live_stream_panel.py tests/tui/test_live_stream_panel.py
git commit -m "feat(tui): add LiveStreamPanel widget for chat/research live streams"
```

---

### Task 4: Wire `run_with_identity` into `ChatService`

**Files:**
- Modify: `novelizer/chat/service.py`
- Modify: `novelizer/runtime.py`
- Test: `tests/chat/test_service.py`

**Interfaces:**
- Consumes: `novelizer.telemetry.recorder.run_with_identity` (Task 1).
- Produces: `ChatService.__init__` gains `telemetry=None` keyword param
  (stored as `self._telemetry`). `generate_reply` tags its runner call
  with identity `f"chat:{agent_name}"`. `Runtime.start()` passes
  `telemetry=self.telemetry` at its existing `ChatService(...)`
  construction site. Task 7 (`ChatScreen`) relies on this identity string
  format to filter the telemetry bus.

- [ ] **Step 1: Write the failing test**

Append to `tests/chat/test_service.py` (match the existing file's fixture
style — `_runtime(path, chat_runners)` and the `db_path` fixture are
already defined there; reuse them):

```python
from novelizer.telemetry.bus import TelemetryBus
from novelizer.telemetry.recorder import TelemetryRecorder
from novelizer.canon.event_store import EventStore
from novelizer.telemetry.events import TelemetryEventType


@pytest.mark.asyncio
async def test_generate_reply_tags_telemetry_with_chat_prefixed_identity(db_path, tmp_path):
    telemetry_store = EventStore(str(tmp_path / "telemetry.db"))
    await telemetry_store.init()
    bus = TelemetryBus()
    telemetry = TelemetryRecorder(telemetry_store, bus)
    q = bus.subscribe()

    runner = _R(ChatReply(reply_text="hi"))
    rt = await _runtime(db_path, {"chat_author": runner})
    rt.chat._telemetry = telemetry  # inject after construction; Runtime wiring is Task 4's own concern, not retested here
    try:
        mid = await rt.chat.send("author", "hello?")
        await rt.chat.generate_reply("author", replying_to=mid)
        started = q.get_nowait()
        assert started.event_type == TelemetryEventType.AGENT_RUN_STARTED
        assert started.payload["agent_name"] == "chat:author"
        finished = q.get_nowait()
        assert finished.event_type == TelemetryEventType.AGENT_RUN_FINISHED
    finally:
        await rt.close()
        await telemetry_store.close()
```

Also add a runtime-level test verifying the constructor wiring itself.
Append to `tests/chat/test_service.py`:

```python
@pytest.mark.asyncio
async def test_runtime_wires_telemetry_into_chat_service(db_path):
    rt = await _runtime(db_path, {"chat_author": _R(ChatReply(reply_text="hi"))})
    try:
        assert rt.chat._telemetry is rt.telemetry
    finally:
        await rt.close()
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/chat/test_service.py -v -k "telemetry"`
Expected: FAIL — `AttributeError` (`ChatService` has no `_telemetry`
attribute yet)

- [ ] **Step 3: Write the implementation**

In `novelizer/chat/service.py`, add to the imports:

```python
from novelizer.telemetry.recorder import run_with_identity
```

Change `ChatService.__init__`'s signature to add the new parameter, and
store it:

```python
    def __init__(
        self, events, read, committer, runner_for: Callable, personality_for: Callable[[str], str],
        pull_mode: bool = False, telemetry=None,
    ) -> None:
        self._events = events
        self._read = read
        self._committer = committer
        self._runner_for = runner_for
        self._personality_for = personality_for
        self._locks: dict[str, asyncio.Lock] = {}
        self._pending: dict[str, int] = {}
        self.pull_mode = pull_mode
        self._telemetry = telemetry
```

In `generate_reply`, wrap the runner call and the reply-None check in
`run_with_identity`:

```python
    async def generate_reply(self, agent_name: str, replying_to: str = "") -> None:
        lock = self._locks.setdefault(agent_name, asyncio.Lock())
        self._pending[agent_name] = self._pending.get(agent_name, 0) + 1
        try:
            async with lock:
                prompt = await self._build_prompt(agent_name)
                runner = self._runner_for(agent_name)
                async with run_with_identity(self._telemetry, f"chat:{agent_name}"):
                    result = await runner.ainvoke({"messages": [{"role": "user", "content": prompt}]})
                    reply: ChatReply | None = result.get("structured_response")
                    if reply is None:
                        raise ChatReplyError(f"{agent_name} returned no structured reply")
                await self._events.append(
                    EventType.CHAT_AGENT_REPLIED, agent_name,
                    ChatAgentReplied(
                        message_id=str(uuid.uuid4()), agent_name=agent_name,
                        text=reply.reply_text, replying_to=replying_to,
                    ),
                )
                await self._commit_intents(agent_name, reply)
        finally:
            self._pending[agent_name] -= 1
```

(Only the two lines inside the new `async with run_with_identity(...)`
block move — the `await self._events.append(...)` and
`await self._commit_intents(...)` calls stay exactly where they were,
outside that block, since a committed reply/intents write shouldn't be
attributed to the "run" the telemetry identity is tagging.)

In `novelizer/runtime.py`, at the existing `self.chat = ChatService(...)`
construction site in `start()`, add the new keyword argument:

```python
        self.chat = ChatService(
            self.events, self.read, self.committer, self._chat_runner_for,
            lambda name: self.voice_pack.agent_personalities.get(name, ""),
            pull_mode=s.chat_tools_enabled, telemetry=self.telemetry,
        )
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/chat -v`
Expected: PASS (all — existing tests unaffected since `telemetry` defaults
to `None`, new tests pass)

- [ ] **Step 5: Commit**

```bash
git add novelizer/chat/service.py novelizer/runtime.py tests/chat/test_service.py
git commit -m "feat(chat): tag ChatService replies with chat:<agent> telemetry identity"
```

---

### Task 5: Wire `run_with_identity` into `ResearchService`

**Files:**
- Modify: `novelizer/research/service.py`
- Modify: `novelizer/runtime.py`
- Test: `tests/research/test_service.py`

**Interfaces:**
- Consumes: `novelizer.telemetry.recorder.run_with_identity` (Task 1).
- Produces: `ResearchService.__init__` gains `telemetry=None` keyword
  param (`self._telemetry`). `ask` tags its runner call with identity
  `"research"`. `Runtime.start()` passes `telemetry=self.telemetry` at its
  existing `ResearchService(...)` construction site. Task 6
  (`ResearchScreen`) relies on this exact identity string.

- [ ] **Step 1: Write the failing tests**

Append to `tests/research/test_service.py`:

```python
from novelizer.telemetry.bus import TelemetryBus
from novelizer.telemetry.recorder import TelemetryRecorder
from novelizer.canon.event_store import EventStore
from novelizer.telemetry.events import TelemetryEventType


@pytest.mark.asyncio
async def test_ask_tags_telemetry_with_research_identity(tmp_path):
    telemetry_store = EventStore(str(tmp_path / "telemetry.db"))
    await telemetry_store.init()
    bus = TelemetryBus()
    telemetry = TelemetryRecorder(telemetry_store, bus)
    q = bus.subscribe()

    runner = _R(ResearchAnswer(answer_text="answer"))
    service = ResearchService(lambda: runner, telemetry=telemetry)

    await service.ask("q?", history=[])

    started = q.get_nowait()
    assert started.event_type == TelemetryEventType.AGENT_RUN_STARTED
    assert started.payload["agent_name"] == "research"
    finished = q.get_nowait()
    assert finished.event_type == TelemetryEventType.AGENT_RUN_FINISHED
    await telemetry_store.close()


@pytest.mark.asyncio
async def test_runtime_wires_telemetry_into_research_service(db_path):
    settings = Settings(db_path=db_path, projector_interval=0.05)
    rt = Runtime(settings, runners={"research": _R(ResearchAnswer(answer_text="ok"))})
    await rt.start()
    try:
        assert rt.research._telemetry is rt.telemetry
    finally:
        await rt.close()
```

(`Settings`, `Runtime`, and the `db_path` fixture are already imported in
this file from Task 4 of the previous plan's work on this same test file —
check the top of `tests/research/test_service.py` before re-adding
duplicate imports.)

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/research/test_service.py -v -k "telemetry"`
Expected: FAIL — `TypeError: ResearchService.__init__() got an unexpected keyword argument 'telemetry'`

- [ ] **Step 3: Write the implementation**

In `novelizer/research/service.py`, add to the imports:

```python
from novelizer.telemetry.recorder import run_with_identity
```

Change `ResearchService.__init__` and `ask`:

```python
    def __init__(self, runner_for: Callable, telemetry=None) -> None:
        self._runner_for = runner_for
        self._telemetry = telemetry

    async def ask(self, question: str, history: list[tuple[str, str]]) -> str:
        prompt = (
            f"Research conversation so far:\n{_transcript_block(history)}\n\n"
            f"New question: {question}"
        )
        runner = self._runner_for()
        async with run_with_identity(self._telemetry, "research"):
            result = await runner.ainvoke({"messages": [{"role": "user", "content": prompt}]})
            answer = result.get("structured_response")
            if answer is None:
                raise ResearchAnswerError("research runner returned no structured answer")
        return answer.answer_text
```

In `novelizer/runtime.py`, at the existing `self.research =
ResearchService(...)` construction site in `start()`:

```python
        from novelizer.research.service import ResearchService
        self.research = ResearchService(self._research_runner_for, telemetry=self.telemetry)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/research -v`
Expected: PASS (all — existing tests unaffected, new tests pass)

- [ ] **Step 5: Commit**

```bash
git add novelizer/research/service.py novelizer/runtime.py tests/research/test_service.py
git commit -m "feat(research): tag ResearchService.ask with research telemetry identity"
```

---

### Task 6: Live stream panel on `ResearchScreen`

**Files:**
- Modify: `novelizer/tui/research_screen.py`
- Test: `tests/tui/test_research_screen.py`

**Interfaces:**
- Consumes: `novelizer.tui.widgets.live_stream_panel.LiveStreamPanel`
  (Task 3); `novelizer.tui.widgets.engine_room_model.LiveRunState`,
  `apply_bus_item`, `route_agent` (existing/Task 2); the `"research"`
  telemetry identity (Task 5); `runtime.telemetry_bus` (existing
  `TelemetryBus`, `Runtime.telemetry_bus`).
- Produces: `ResearchScreen` mounts a `LiveStreamPanel` above the
  transcript; nothing new is consumed by later tasks (this is one of the
  two identical wiring tasks, alongside Task 7).

- [ ] **Step 1: Write the failing tests**

Append to `tests/tui/test_research_screen.py`:

```python
from novelizer.telemetry.events import (
    TelemetryEventType, AgentRunStarted, AgentRunFinished, TokenDelta,
)
from novelizer.tui.widgets.live_stream_panel import LiveStreamPanel


@pytest.mark.asyncio
async def test_panel_shows_running_state_during_a_turn(db_path):
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

            await rt.telemetry.emit(TelemetryEventType.AGENT_RUN_STARTED, "r1",
                                    AgentRunStarted(run_id="r1", agent_name="research"))
            rt.telemetry.publish_token(TokenDelta(run_id="r1", agent_name="research", text="thinking…"))
            await pilot.pause(0.2)

            panel = screen.query_one(LiveStreamPanel)
            body = panel.query_one(LiveStreamPanel._STREAM_ID)
            assert "thinking…" in str(body.renderable)

            gate.set()
            await pilot.pause(0.3)
    finally:
        await rt.close()


@pytest.mark.asyncio
async def test_panel_ignores_events_for_other_identities(db_path):
    rt = await _runtime(db_path, _R(ResearchAnswer(answer_text="answer")))
    try:
        screen = ResearchScreen(rt)
        from textual.app import App

        class _TestApp(App):
            def on_mount(self):
                self.push_screen(screen)

        app = _TestApp()
        async with app.run_test() as pilot:
            await pilot.pause()
            await rt.telemetry.emit(TelemetryEventType.AGENT_RUN_STARTED, "r1",
                                    AgentRunStarted(run_id="r1", agent_name="chat:author"))
            rt.telemetry.publish_token(TokenDelta(run_id="r1", agent_name="chat:author", text="not mine"))
            await pilot.pause(0.2)
            panel = screen.query_one(LiveStreamPanel)
            body = panel.query_one(LiveStreamPanel._STREAM_ID)
            assert "not mine" not in str(body.renderable)
    finally:
        await rt.close()
```

`_R`, `_runtime`, `db_path`, `ResearchAnswer`, `Input` are already defined
or imported in `tests/tui/test_research_screen.py` from earlier plan
work — reuse them, don't redefine.

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/tui/test_research_screen.py -v -k "panel"`
Expected: FAIL — `NoMatches` (no `LiveStreamPanel` mounted yet)

- [ ] **Step 3: Write the implementation**

In `novelizer/tui/research_screen.py`, update imports:

```python
from novelizer.tui.widgets.engine_room_model import LiveRunState, apply_bus_item, route_agent
from novelizer.tui.widgets.live_stream_panel import LiveStreamPanel
```

Update `compose`, `__init__`, `on_mount`, and add the telemetry loop:

```python
    def __init__(self, runtime) -> None:
        super().__init__()
        self.runtime = runtime
        self._history: list[tuple[str, str]] = []
        self._pending = False
        self._live_state = LiveRunState()

    def compose(self) -> ComposeResult:
        yield LiveStreamPanel(id="research_live")
        log = RichLog(highlight=False, markup=False, id="research_log")
        log.border_title = "TALK TO THE PROJECT"
        yield log
        yield Input(id="research_input", placeholder="ask about the project…", compact=True)
        yield Footer()

    async def on_mount(self) -> None:
        self.set_focus(self.query_one("#research_input", Input))
        self.run_worker(self._telemetry_loop(), exclusive=False)

    async def _telemetry_loop(self) -> None:
        q = self.runtime.telemetry_bus.subscribe()
        try:
            while True:
                item = await q.get()
                if route_agent(item) != "research":
                    continue
                self._live_state = apply_bus_item(self._live_state, item, time.monotonic())
                self.query_one(LiveStreamPanel).render(self._live_state)
        finally:
            self.runtime.telemetry_bus.unsubscribe(q)
```

Add `import time` to the top-level imports.

In `_ask`, reset the panel's state to idle at the start of every turn (a
fresh turn must never show the previous turn's finished/failed tail):

```python
    async def _ask(self, question: str) -> None:
        log = self.query_one("#research_log", RichLog)
        input_widget = self.query_one("#research_input", Input)
        self._live_state = LiveRunState()
        self.query_one(LiveStreamPanel).render(self._live_state)
        try:
            answer = await self.runtime.research.ask(question, self._history)
            ...
```

(Only the two new lines are added at the top of the existing `try` block —
everything else in `_ask` is unchanged.)

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/tui/test_research_screen.py -v`
Expected: PASS (all — including the pre-existing tests from earlier plan
work, unaffected by this change)

- [ ] **Step 5: Commit**

```bash
git add novelizer/tui/research_screen.py tests/tui/test_research_screen.py
git commit -m "feat(tui): show live agent stream on ResearchScreen"
```

---

### Task 7: Live stream panel on `ChatScreen`

**Files:**
- Modify: `novelizer/tui/chat_screen.py`
- Test: `tests/tui/test_chat_screen.py`

**Interfaces:**
- Consumes: same as Task 6 (`LiveStreamPanel`, `LiveRunState`,
  `apply_bus_item`, `route_agent`, `runtime.telemetry_bus`), plus the
  `f"chat:{agent_name}"` telemetry identity (Task 4). This is the final
  task in the plan — nothing downstream depends on it.

- [ ] **Step 1: Write the failing tests**

Append to `tests/tui/test_chat_screen.py`:

```python
from novelizer.telemetry.events import TelemetryEventType, AgentRunStarted, TokenDelta
from novelizer.tui.widgets.live_stream_panel import LiveStreamPanel


@pytest.mark.asyncio
async def test_panel_shows_stream_for_the_active_agent_only(db_path):
    rt = await _runtime(db_path)
    try:
        await rt.events.append(
            EventType.CHAT_USER_MESSAGED, "author",
            ChatUserMessaged(message_id="m1", agent_name="author", text="hi"),
        )
        await rt.projector.catch_up()
        app = NovelizerApp(rt)
        async with app.run_test() as pilot:
            screen = ChatScreen(rt, "author")
            await app.push_screen(screen)
            await pilot.pause()

            await rt.telemetry.emit(TelemetryEventType.AGENT_RUN_STARTED, "r1",
                                    AgentRunStarted(run_id="r1", agent_name="chat:author"))
            rt.telemetry.publish_token(TokenDelta(run_id="r1", agent_name="chat:author", text="drafting…"))
            await pilot.pause(0.2)

            panel = screen.query_one(LiveStreamPanel)
            body = panel.query_one(LiveStreamPanel._STREAM_ID)
            assert "drafting…" in str(body.renderable)
    finally:
        await rt.close()


@pytest.mark.asyncio
async def test_switching_conversation_resets_the_panel(db_path):
    rt = await _runtime(db_path)
    try:
        app = NovelizerApp(rt)
        async with app.run_test() as pilot:
            screen = ChatScreen(rt, "author")
            await app.push_screen(screen)
            await pilot.pause()

            await rt.telemetry.emit(TelemetryEventType.AGENT_RUN_STARTED, "r1",
                                    AgentRunStarted(run_id="r1", agent_name="chat:author"))
            rt.telemetry.publish_token(TokenDelta(run_id="r1", agent_name="chat:author", text="drafting…"))
            await pilot.pause(0.2)

            await screen.set_current("editor")
            await pilot.pause(0.1)

            panel = screen.query_one(LiveStreamPanel)
            body = panel.query_one(LiveStreamPanel._STREAM_ID)
            assert "drafting…" not in str(body.renderable)
    finally:
        await rt.close()
```

`_runtime`, `db_path`, `EventType`, `ChatUserMessaged`, `NovelizerApp` are
already defined/imported at the top of `tests/tui/test_chat_screen.py` —
reuse them.

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/tui/test_chat_screen.py -v -k "panel"`
Expected: FAIL — `NoMatches` (no `LiveStreamPanel` mounted yet)

- [ ] **Step 3: Write the implementation**

In `novelizer/tui/chat_screen.py`, update imports:

```python
import time
from novelizer.tui.widgets.engine_room_model import LiveRunState, apply_bus_item, route_agent
from novelizer.tui.widgets.live_stream_panel import LiveStreamPanel
```

Update `__init__` and `compose`:

```python
    def __init__(self, runtime, agent_name: str) -> None:
        super().__init__()
        self.runtime = runtime
        self.agent_name = agent_name
        self._agents: list[str] = [agent_name]
        self._seen: dict[str, int] = {}
        self._errors: dict[str, list[str]] = {}
        self._last_render_key: tuple = ()
        self._live_state = LiveRunState()

    def compose(self) -> ComposeResult:
        yield Tabs(Tab(f"@{self.agent_name}", id=f"chat-{self.agent_name}"), id="chat_tabs")
        yield LiveStreamPanel(id="chat_live")
        yield RichLog(highlight=False, markup=False, id="chat_log")
        yield Input(id="chat_input", placeholder=f"message @{self.agent_name}…", compact=True)
        yield Footer()
```

Update `on_mount` to also start the telemetry loop:

```python
    async def on_mount(self) -> None:
        self.run_worker(self._poll_loop(), exclusive=False)
        self.run_worker(self._telemetry_loop(), exclusive=False)
        self.set_focus(self.query_one("#chat_input", Input))
```

Add the loop method (near `_poll_loop`):

```python
    async def _telemetry_loop(self) -> None:
        q = self.runtime.telemetry_bus.subscribe()
        try:
            while True:
                item = await q.get()
                if route_agent(item) != f"chat:{self.agent_name}":
                    continue
                self._live_state = apply_bus_item(self._live_state, item, time.monotonic())
                self.query_one(LiveStreamPanel).render(self._live_state)
        finally:
            self.runtime.telemetry_bus.unsubscribe(q)
```

Reset the panel to idle whenever the active conversation changes — both
places `self.agent_name` is reassigned already exist in this file
(`set_current` and `on_tabs_tab_activated`); add the reset to both:

In `set_current`, after `self.agent_name = agent_name`:

```python
    async def set_current(self, agent_name: str) -> None:
        self.agent_name = agent_name
        self._live_state = LiveRunState()
        self.query_one(LiveStreamPanel).render(self._live_state)
        if agent_name not in self._agents:
            self._agents.append(agent_name)
        await self._sync_tabs()
        tabs = self.query_one("#chat_tabs", Tabs)
        tabs.active = f"chat-{agent_name}"
        self.query_one("#chat_input", Input).placeholder = f"message @{agent_name}…"
```

In `on_tabs_tab_activated`, inside the `if agent != self.agent_name:`
branch, after `self.agent_name = agent`:

```python
    def on_tabs_tab_activated(self, event) -> None:
        tab_id = event.tab.id or ""
        if tab_id.startswith("chat-"):
            agent = tab_id.removeprefix("chat-")
            if agent != self.agent_name:
                self.agent_name = agent
                self._live_state = LiveRunState()
                self.query_one(LiveStreamPanel).render(self._live_state)
                self._last_render_key = ()
                self.query_one("#chat_input", Input).placeholder = f"message @{agent}…"
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/tui/test_chat_screen.py tests/tui/test_chat_routing.py -v`
Expected: PASS (all — including pre-existing tests, unaffected)

Then run the full affected-suite regression check:

Run: `uv run pytest tests/telemetry tests/tui tests/chat tests/research -q`
Expected: PASS, no regressions anywhere in this surface

- [ ] **Step 5: Commit**

```bash
git add novelizer/tui/chat_screen.py tests/tui/test_chat_screen.py
git commit -m "feat(tui): show live agent stream on ChatScreen"
```

---

## Post-plan verification

After all seven tasks are committed, run the full affected suite once more
from a clean state (this plan already executes inside an isolated
worktree, so this is safe here — never run test suites in the main
checkout):

Run: `uv run pytest tests/telemetry tests/tui tests/chat tests/research -q`
Expected: PASS, no regressions.
