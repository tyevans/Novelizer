# Engine Room Unified Stream Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the Engine Room's single-`Static` string stream with one unified, filterable, chronologically-interleaved stream of per-block widgets that have collapsible tool calls, rich markdown output, and a scroll position that stays where you put it.

**Architecture:** The pure fold (`tui_kit/run_model.py`) gains a `Block` sum type, filter/follow/window state, and markdown-detection predicates — all Textual-free and property-testable. A `StreamSource` protocol supplies blocks (in-memory for tests, `EventStore`-backed in production) so the widget never imports canon. An `OutputRenderer` protocol picks `Markdown` vs. plain rendering. The `StreamView` widget owns only mounting, folding, and scrolling.

**Tech Stack:** Python 3.12+, Textual, Rich, pydantic, aiosqlite, pytest, pytest-asyncio, hypothesis.

## Global Constraints

- Spec of record: `docs/superpowers/specs/2026-07-25-engine-room-unified-stream-design.md`. Do not relitigate its decisions table.
- `tui_kit/` MUST NOT import from `novelizer/`. It is the domain-agnostic layer; the dependency runs one way only.
- `tui_kit/run_model.py` MUST NOT import Textual. Rich is permitted (it already imports `rich.text`).
- Untrusted content (tool output, prompts, canon text) MUST reach Textual as `Text`/`Markdown` objects or via `markup=False` widgets — never as a markup-parsed `str`. See the reasoning at `tui_kit/widgets/engine_room.py:33-36`.
- The `[:1000]` at `novelizer/tui/app.py:417` bounds the cheap-LLM summarizer prompt, NOT the display. Leave it exactly as it is.
- Telemetry persists to its own `EventStore` (`telemetry.db`), separate from the domain log. Adding a telemetry event type does not touch canon.
- TDD is non-negotiable in this repo: write the failing test, watch it fail, then implement.
- NEVER run the test suite in the main checkout — only in this worktree. Run one pytest at a time; do not overlap runs.
- Commit after every task.

---

### Task 1: `events_before` for backward paging

**Files:**
- Modify: `novelizer/canon/event_store.py` (add method after `events_since`, which ends at line 121)
- Test: `tests/canon/test_event_store.py`

**Interfaces:**
- Consumes: nothing from earlier tasks.
- Produces: `EventStore.events_before(sequence: int, limit: int, event_types: Optional[list[str]] = None) -> list[StoredEvent]` — the `limit` events with `sequence < sequence`, returned in **ascending** sequence order. Task 6 consumes this.

- [ ] **Step 1: Write the failing test**

Append to `tests/canon/test_event_store.py`. Match the existing async fixture style already in that file.

```python
async def test_events_before_returns_the_window_just_below_a_sequence(tmp_path):
    store = EventStore(str(tmp_path / "t.db"))
    await store.init()
    try:
        for i in range(10):
            await store.append_raw("x.happened", "agg", {"i": i})
        window = await store.events_before(sequence=8, limit=3)
        assert [e.payload["i"] for e in window] == [4, 5, 6]
    finally:
        await store.close()


async def test_events_before_is_ascending_and_clamps_at_the_beginning(tmp_path):
    store = EventStore(str(tmp_path / "t.db"))
    await store.init()
    try:
        for i in range(3):
            await store.append_raw("x.happened", "agg", {"i": i})
        window = await store.events_before(sequence=2, limit=50)
        assert [e.payload["i"] for e in window] == [0]
        assert await store.events_before(sequence=1, limit=50) == []
    finally:
        await store.close()


async def test_events_before_filters_by_type(tmp_path):
    store = EventStore(str(tmp_path / "t.db"))
    await store.init()
    try:
        await store.append_raw("a.happened", "agg", {"i": 0})
        await store.append_raw("b.happened", "agg", {"i": 1})
        await store.append_raw("a.happened", "agg", {"i": 2})
        window = await store.events_before(sequence=99, limit=10, event_types=["a.happened"])
        assert [e.payload["i"] for e in window] == [0, 2]
    finally:
        await store.close()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/canon/test_event_store.py -k events_before -v`
Expected: FAIL — `AttributeError: 'EventStore' object has no attribute 'events_before'`

- [ ] **Step 3: Write minimal implementation**

Insert into `novelizer/canon/event_store.py` immediately after `events_since`:

```python
    async def events_before(self, sequence: int, limit: int,
                            event_types: Optional[list[str]] = None) -> list[StoredEvent]:
        """The `limit` events immediately below `sequence`, ascending.

        Backward paging for the Engine Room's windowed stream: SELECT
        DESC to take the *nearest* `limit` rows, then reverse so callers
        always see ascending sequence like every other reader here.
        `event_types` is read with the same truthiness as events_since --
        None and [] both mean "every type".
        """
        if event_types:
            placeholders = ",".join("?" for _ in event_types)
            cur = await self._conn.execute(
                f"SELECT {_COLS} FROM events WHERE sequence < ? AND event_type IN ({placeholders}) "
                "ORDER BY sequence DESC LIMIT ?",
                (sequence, *event_types, limit),
            )
        else:
            cur = await self._conn.execute(
                f"SELECT {_COLS} FROM events WHERE sequence < ? ORDER BY sequence DESC LIMIT ?",
                (sequence, limit),
            )
        rows = await cur.fetchall()
        return [_row_to_event(r) for r in reversed(rows)]
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/canon/test_event_store.py -k events_before -v`
Expected: 3 passed

- [ ] **Step 5: Commit**

```bash
git add novelizer/canon/event_store.py tests/canon/test_event_store.py
git commit -m "feat(canon): events_before for backward-paging the telemetry window"
```

---

### Task 2: `Block` becomes a sum type

**Files:**
- Modify: `tui_kit/run_model.py:27-41` (replace `Block`), and every construction/read site in that file
- Test: `tests/tui_kit/test_run_model.py`

**Interfaces:**
- Consumes: nothing from earlier tasks.
- Produces:
  - `ProseBlock(text: str)`, `ThinkingBlock(text: str)`
  - `CallBlock(call_index: int, model: str, status: str = "running", duration_s: float = 0.0)`
  - `ToolBlock(tool_name: str, input_summary: str, status: str = "running", duration_s: float = 0.0, error: str = "", summary: str | None = None, repeat_count: int = 1, delegate: str = "", preview: str = "", sequence: int = 0)`
  - `StreamBlock = ProseBlock | ThinkingBlock | CallBlock | ToolBlock`
  - `block_agent(b: StreamBlock) -> str` and `block_key(b: StreamBlock, index: int) -> str`
  - All four carry `agent_name: str = ""`. Tasks 4, 5, 8, 9 consume these.

Note `ToolBlock` replaces the old `output: str` field with `preview: str` (first 200 chars, newlines collapsed) plus `sequence: int` (the originating event's store sequence). This is the memory fix from the spec.

- [ ] **Step 1: Write the failing test**

Replace the `Block(kind=...)` usages in `tests/tui_kit/test_run_model.py` and add:

```python
from tui_kit.run_model import (
    ProseBlock, ThinkingBlock, CallBlock, ToolBlock, block_key, block_agent,
)


def test_tool_block_keeps_a_preview_and_a_sequence_not_the_whole_output():
    s = apply_bus_item(LiveRunState(status="running", run_id="r1"),
                       ToolCallStarted(run_id="r1", agent_name="author",
                                       tool_name="read_file", input_summary="ch1.md"),
                       now=1.0)
    s = apply_bus_item(s, ToolCallFinished(run_id="r1", agent_name="author",
                                           tool_name="read_file", duration_s=1.2,
                                           input_summary="ch1.md",
                                           output_summary="x" * 5000, sequence=42),
                       now=2.2)
    b = s.blocks[-1]
    assert isinstance(b, ToolBlock)
    assert b.sequence == 42
    assert len(b.preview) <= 200
    assert not hasattr(b, "output")


def test_prose_block_has_no_tool_fields():
    b = ProseBlock(text="hello", agent_name="author")
    assert not hasattr(b, "tool_name")
    assert not hasattr(b, "input_summary")


def test_block_key_is_stable_across_reconstruction():
    b = ProseBlock(text="hi", agent_name="author")
    assert block_key(b, 3) == block_key(replace(b, text="hi there"), 3)


def test_block_agent_reads_the_agent_off_any_kind():
    assert block_agent(ProseBlock(text="", agent_name="author")) == "author"
    assert block_agent(ToolBlock(tool_name="t", input_summary="", agent_name="editor")) == "editor"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/tui_kit/test_run_model.py -v`
Expected: FAIL — `ImportError: cannot import name 'ProseBlock'`

- [ ] **Step 3: Write minimal implementation**

In `tui_kit/run_model.py`, replace the `Block` dataclass with:

```python
PREVIEW_CAP = 200


@dataclass(frozen=True)
class _BaseBlock:
    agent_name: str = ""


@dataclass(frozen=True)
class ProseBlock(_BaseBlock):
    text: str = ""


@dataclass(frozen=True)
class ThinkingBlock(_BaseBlock):
    text: str = ""


@dataclass(frozen=True)
class CallBlock(_BaseBlock):
    call_index: int = 0
    model: str = ""
    status: str = "running"  # running | done
    duration_s: float = 0.0


@dataclass(frozen=True)
class ToolBlock(_BaseBlock):
    tool_name: str = ""
    input_summary: str = ""
    status: str = "running"  # running | done | failed
    duration_s: float = 0.0
    error: str = ""
    summary: str | None = None
    repeat_count: int = 1
    delegate: str = ""
    preview: str = ""   # first PREVIEW_CAP chars, for the collapsed line
    sequence: int = 0   # store sequence; full output is read from there


StreamBlock = ProseBlock | ThinkingBlock | CallBlock | ToolBlock


def make_preview(raw: str) -> str:
    return str(raw).replace("\n", " ")[:PREVIEW_CAP]


def block_agent(b: StreamBlock) -> str:
    return b.agent_name


def block_key(b: StreamBlock, index: int) -> str:
    """Stable identity for widget reconciliation. Index within the run is
    enough: blocks are append-only and never reordered, and a block's kind
    never changes once opened."""
    return f"{type(b).__name__}:{index}"
```

Then update `run_model.py`'s internals:
- `_append_text_block(state, kind, text)` — take a class instead of a kind string: `cls` is `ProseBlock` or `ThinkingBlock`; the "same kind" check becomes `isinstance(state.blocks[-1], cls)`.
- `LLMCallStarted` branch constructs `CallBlock(call_index=item.call_index, model=item.model, agent_name=item.agent_name)`.
- `LLMCallFinished` branch checks `isinstance(state.blocks[-1], CallBlock)`.
- `ToolCallStarted`/`Finished`/`Failed` branches construct and match `ToolBlock`, with `isinstance(b, ToolBlock)` replacing `b.kind == "tool"`.
- `ToolCallFinished` sets `preview=make_preview(item.output_summary), sequence=item.sequence` instead of `output=...`.
- `ToolSummaryReady` branch matches `isinstance(b, ToolBlock)`.
- Every `TokenDelta` construction passes `agent_name=item.agent_name`.

Add `sequence: int = 0` to `ToolCallFinished` and `ToolCallFailed` in `tui_kit/contracts.py`.

Leave `_render_block` and `live_body` in place for now — Task 13 deletes them. Update them mechanically to the new types so the suite stays green: `b.output` becomes `b.preview`.

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/tui_kit/test_run_model.py -v`
Expected: all pass

- [ ] **Step 5: Commit**

```bash
git add tui_kit/run_model.py tui_kit/contracts.py tests/tui_kit/test_run_model.py
git commit -m "refactor(tui_kit): Block becomes a sum type; tool blocks hold a preview + sequence"
```

---

### Task 3: markdown detection predicates

**Files:**
- Create: `tui_kit/markdown_detect.py`
- Test: `tests/tui_kit/test_markdown_detect.py`

**Interfaces:**
- Consumes: nothing.
- Produces: `ext_is_markdown(path: str) -> bool` and `looks_markdown(text: str) -> bool`. Task 7 consumes both.

- [ ] **Step 1: Write the failing test**

Create `tests/tui_kit/test_markdown_detect.py`:

```python
import pytest
from tui_kit.markdown_detect import ext_is_markdown, looks_markdown


@pytest.mark.parametrize("path", ["ch1.md", "canon/ch1.markdown", "A.MD", "  ch1.md  "])
def test_ext_is_markdown_accepts_markdown_paths(path):
    assert ext_is_markdown(path)


@pytest.mark.parametrize("path", ["", "ch1.txt", "data.json", "notes.md.bak", "ch1md"])
def test_ext_is_markdown_rejects_everything_else(path):
    assert not ext_is_markdown(path)


def test_looks_markdown_needs_two_distinct_signals():
    assert not looks_markdown("# just a heading and nothing else")
    assert looks_markdown("# Chapter One\n\n- a bullet\n- another")


def test_looks_markdown_accepts_fenced_code_plus_a_heading():
    assert looks_markdown("## Usage\n\n```python\nx = 1\n```")


def test_looks_markdown_rejects_json():
    assert not looks_markdown('{"a": 1, "b": [2, 3], "c": {"d": "# not a heading"}}')


def test_looks_markdown_rejects_plain_prose():
    assert not looks_markdown("The rain had not stopped for three days.\nShe waited.")


def test_looks_markdown_is_stable_under_line_endings_and_trailing_space():
    doc = "# Chapter One\n\n- a bullet\n- another"
    assert looks_markdown(doc) == looks_markdown(doc.replace("\n", "\r\n"))
    assert looks_markdown(doc) == looks_markdown(doc + "   \n\n")


def test_looks_markdown_is_false_on_empty():
    assert not looks_markdown("")
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/tui_kit/test_markdown_detect.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'tui_kit.markdown_detect'`

- [ ] **Step 3: Write minimal implementation**

Create `tui_kit/markdown_detect.py`:

```python
"""Is this tool output worth handing to a Markdown renderer?

Two predicates, deliberately separate: a path is authoritative when we
have one, and the content sniff is the fallback for tools that return
markdown without naming a file. Both are pure -- no Rich, no Textual --
so the decision is unit-testable apart from any rendering.
"""
from __future__ import annotations
import re

_MD_EXTENSIONS = (".md", ".markdown")

_SIGNALS = (
    re.compile(r"^#{1,6} \S", re.MULTILINE),      # ATX heading
    re.compile(r"^```", re.MULTILINE),            # fenced code
    re.compile(r"^\s*[-*+] \S", re.MULTILINE),    # bullet list
    re.compile(r"^\s*\d+\. \S", re.MULTILINE),    # ordered list
    re.compile(r"^\s*\|.*\|\s*$", re.MULTILINE),  # table row
    re.compile(r"^\s*> \S", re.MULTILINE),        # blockquote
)

# Two signals, not one: a lone "# comment" line in a log or a lone "- " in
# prose is not a markdown document, and rendering it as one reflows text
# the reader wanted verbatim.
_MIN_SIGNALS = 2


def ext_is_markdown(path: str) -> bool:
    return str(path).strip().lower().endswith(_MD_EXTENSIONS)


def _looks_like_json(text: str) -> bool:
    s = text.strip()
    return len(s) >= 2 and s[0] in "{[" and s[-1] in "}]"


def looks_markdown(text: str) -> bool:
    if not text or not text.strip():
        return False
    # JSON is full of braces, colons and quoted "#" strings that trip the
    # heading and table patterns; it is never markdown.
    if _looks_like_json(text):
        return False
    normalized = text.replace("\r\n", "\n")
    return sum(1 for p in _SIGNALS if p.search(normalized)) >= _MIN_SIGNALS
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/tui_kit/test_markdown_detect.py -v`
Expected: 12 passed

- [ ] **Step 5: Commit**

```bash
git add tui_kit/markdown_detect.py tests/tui_kit/test_markdown_detect.py
git commit -m "feat(tui_kit): markdown detection by extension with a content-sniff fallback"
```

---

### Task 4: unified stream state — merge, filter, follow

**Files:**
- Create: `tui_kit/stream_model.py`
- Test: `tests/tui_kit/test_stream_model.py`

**Interfaces:**
- Consumes: `StreamBlock`, `block_agent` (Task 2).
- Produces:
  - `StreamState(blocks: tuple[StreamBlock, ...] = (), agent_filter: frozenset[str] = frozenset(), follow: bool = True, unseen: int = 0)`
  - `visible_blocks(state) -> tuple[StreamBlock, ...]`
  - `toggle_agent(state, agent) -> StreamState`
  - `clear_filter(state) -> StreamState`
  - `on_scroll(state, at_bottom: bool) -> StreamState`
  - `on_new_blocks(state, blocks) -> StreamState`
  - Tasks 8, 10, 11 consume these.

An empty `agent_filter` means "show everything" — that is the "✓all" chip.

- [ ] **Step 1: Write the failing test**

Create `tests/tui_kit/test_stream_model.py`:

```python
from hypothesis import given, strategies as st
from tui_kit.run_model import ProseBlock, ToolBlock
from tui_kit.stream_model import (
    StreamState, visible_blocks, toggle_agent, clear_filter, on_scroll, on_new_blocks,
)


def _blocks():
    return (ProseBlock(text="a", agent_name="author"),
            ToolBlock(tool_name="read_file", input_summary="x", agent_name="editor"),
            ProseBlock(text="b", agent_name="author"))


def test_empty_filter_shows_every_agent():
    s = StreamState(blocks=_blocks())
    assert len(visible_blocks(s)) == 3


def test_toggling_an_agent_narrows_to_it():
    s = toggle_agent(StreamState(blocks=_blocks()), "author")
    assert [b.agent_name for b in visible_blocks(s)] == ["author", "author"]


def test_toggling_the_same_agent_twice_returns_to_everything():
    s = StreamState(blocks=_blocks())
    assert visible_blocks(toggle_agent(toggle_agent(s, "author"), "author")) == visible_blocks(s)


def test_clear_filter_restores_everything():
    s = toggle_agent(StreamState(blocks=_blocks()), "author")
    assert len(visible_blocks(clear_filter(s))) == 3


def test_scrolling_up_detaches_and_returning_to_bottom_reattaches():
    s = on_scroll(StreamState(), at_bottom=False)
    assert s.follow is False
    assert on_scroll(s, at_bottom=True).follow is True


def test_detached_stream_counts_unseen_blocks():
    s = on_scroll(StreamState(), at_bottom=False)
    s = on_new_blocks(s, (ProseBlock(text="x", agent_name="author"),))
    s = on_new_blocks(s, (ProseBlock(text="y", agent_name="author"),))
    assert s.unseen == 2


def test_following_stream_never_accumulates_unseen():
    s = on_new_blocks(StreamState(), (ProseBlock(text="x", agent_name="author"),))
    assert s.unseen == 0


def test_reattaching_clears_the_unseen_count():
    s = on_scroll(StreamState(), at_bottom=False)
    s = on_new_blocks(s, (ProseBlock(text="x", agent_name="author"),))
    assert on_scroll(s, at_bottom=True).unseen == 0


_AGENTS = st.sampled_from(["author", "editor", "plotter"])


@given(st.lists(_AGENTS.map(lambda a: ProseBlock(text="t", agent_name=a))),
       st.sets(_AGENTS))
def test_filtering_then_appending_equals_appending_then_filtering(blocks, agents):
    """The filter is a pure view over the block list, so it must not matter
    whether a block arrives before or after the filter is set."""
    state = StreamState(agent_filter=frozenset(agents))
    appended_then_filtered = visible_blocks(on_new_blocks(state, tuple(blocks)))
    filtered = [b for b in blocks if not agents or b.agent_name in agents]
    assert list(appended_then_filtered) == filtered
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/tui_kit/test_stream_model.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'tui_kit.stream_model'`

- [ ] **Step 3: Write minimal implementation**

Create `tui_kit/stream_model.py`:

```python
"""Unified-stream view state: which blocks are shown, and whether the
view is following the tail.

Split from run_model because the concerns differ: run_model folds events
into blocks (one reason to change: the event vocabulary), this folds user
intent over that list (one reason to change: the interaction design).
No Textual, no Rich -- the widget layer applies these decisions.
"""
from __future__ import annotations
from dataclasses import dataclass, replace
from tui_kit.run_model import StreamBlock, block_agent


@dataclass(frozen=True)
class StreamState:
    blocks: tuple[StreamBlock, ...] = ()
    # Empty means "every agent" -- the "all" chip. Storing the empty set
    # rather than the full roster keeps this layer ignorant of the roster.
    agent_filter: frozenset[str] = frozenset()
    follow: bool = True
    unseen: int = 0


def visible_blocks(state: StreamState) -> tuple[StreamBlock, ...]:
    if not state.agent_filter:
        return state.blocks
    return tuple(b for b in state.blocks if block_agent(b) in state.agent_filter)


def toggle_agent(state: StreamState, agent: str) -> StreamState:
    f = state.agent_filter
    updated = f - {agent} if agent in f else f | {agent}
    return replace(state, agent_filter=frozenset(updated))


def clear_filter(state: StreamState) -> StreamState:
    return replace(state, agent_filter=frozenset())


def on_scroll(state: StreamState, at_bottom: bool) -> StreamState:
    """The only place follow-mode changes. Reaching the bottom reattaches
    and clears the backlog counter; leaving it detaches."""
    if at_bottom:
        return replace(state, follow=True, unseen=0)
    return replace(state, follow=False)


def on_new_blocks(state: StreamState, blocks: tuple[StreamBlock, ...]) -> StreamState:
    """Append. While detached, count what the reader has not seen; while
    following, there is by definition no backlog."""
    merged = state.blocks + tuple(blocks)
    if state.follow:
        return replace(state, blocks=merged)
    return replace(state, blocks=merged, unseen=state.unseen + len(blocks))
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/tui_kit/test_stream_model.py -v`
Expected: 9 passed

- [ ] **Step 5: Commit**

```bash
git add tui_kit/stream_model.py tests/tui_kit/test_stream_model.py
git commit -m "feat(tui_kit): unified stream state - agent filter and follow/detach"
```

---

### Task 5: window bounds and backward paging

**Files:**
- Modify: `tui_kit/stream_model.py`
- Test: `tests/tui_kit/test_stream_model.py`

**Interfaces:**
- Consumes: `StreamState` (Task 4).
- Produces: `WINDOW_CAP: int`, `trim_window(state) -> StreamState`, `prepend_blocks(state, blocks) -> StreamState`, `oldest_sequence(state) -> int`. Tasks 6 and 8 consume these.

- [ ] **Step 1: Write the failing test**

Append to `tests/tui_kit/test_stream_model.py`:

```python
from tui_kit.stream_model import WINDOW_CAP, trim_window, prepend_blocks, oldest_sequence


def test_trim_window_drops_the_head_when_over_cap():
    blocks = tuple(ProseBlock(text=str(i), agent_name="author") for i in range(WINDOW_CAP + 25))
    trimmed = trim_window(StreamState(blocks=blocks))
    assert len(trimmed.blocks) == WINDOW_CAP
    assert trimmed.blocks[-1].text == str(WINDOW_CAP + 24)


def test_trim_window_is_a_noop_under_cap():
    s = StreamState(blocks=(ProseBlock(text="a", agent_name="author"),))
    assert trim_window(s) == s


def test_prepend_puts_paged_history_at_the_front_and_never_counts_as_unseen():
    s = StreamState(blocks=(ProseBlock(text="new", agent_name="author"),), follow=False)
    s = prepend_blocks(s, (ProseBlock(text="old", agent_name="author"),))
    assert [b.text for b in s.blocks] == ["old", "new"]
    assert s.unseen == 0


def test_oldest_sequence_reports_the_paging_cursor():
    s = StreamState(blocks=(ToolBlock(tool_name="t", input_summary="", sequence=7),
                            ToolBlock(tool_name="t", input_summary="", sequence=9)))
    assert oldest_sequence(s) == 7


def test_oldest_sequence_ignores_blocks_that_have_no_sequence():
    s = StreamState(blocks=(ProseBlock(text="a", agent_name="author"),
                            ToolBlock(tool_name="t", input_summary="", sequence=5)))
    assert oldest_sequence(s) == 5


def test_oldest_sequence_of_an_empty_window_is_zero():
    assert oldest_sequence(StreamState()) == 0


@given(st.lists(st.integers(min_value=1, max_value=50), min_size=1, max_size=20))
def test_paging_back_then_trimming_never_exceeds_the_cap(seqs):
    """However much history is paged in, the window stays bounded --
    this is the whole point of windowing."""
    s = StreamState()
    for i in seqs:
        s = trim_window(prepend_blocks(
            s, tuple(ProseBlock(text="h", agent_name="author") for _ in range(i))))
    assert len(s.blocks) <= WINDOW_CAP
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/tui_kit/test_stream_model.py -k "window or prepend or oldest or paging" -v`
Expected: FAIL — `ImportError: cannot import name 'WINDOW_CAP'`

- [ ] **Step 3: Write minimal implementation**

Append to `tui_kit/stream_model.py`:

```python
# How many blocks stay mounted. Textual mounts a widget per block, so this
# bounds widget count and memory both; history beyond it is paged back in
# from the event store on demand.
WINDOW_CAP = 400


def trim_window(state: StreamState) -> StreamState:
    """Drop from the head -- the tail is what a following reader is
    watching, and paged-in history can always be re-fetched."""
    if len(state.blocks) <= WINDOW_CAP:
        return state
    return replace(state, blocks=state.blocks[-WINDOW_CAP:])


def prepend_blocks(state: StreamState, blocks: tuple[StreamBlock, ...]) -> StreamState:
    """Paged-in history. Never touches `unseen`: the reader scrolled here
    deliberately, so this is not a backlog."""
    return replace(state, blocks=tuple(blocks) + state.blocks)


def oldest_sequence(state: StreamState) -> int:
    """Cursor for the next backward page. Only tool blocks carry a store
    sequence; prose is reconstructed from the segments around them."""
    for b in state.blocks:
        seq = getattr(b, "sequence", 0)
        if seq:
            return seq
    return 0
```

Note `trim_window` after `prepend_blocks` would defeat paging (it drops the head we just added). The widget in Task 8 calls `trim_window` only after `on_new_blocks`, never after `prepend_blocks`; the property test above pins that `trim_window` keeps the window bounded regardless.

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/tui_kit/test_stream_model.py -v`
Expected: all pass

- [ ] **Step 5: Commit**

```bash
git add tui_kit/stream_model.py tests/tui_kit/test_stream_model.py
git commit -m "feat(tui_kit): bounded stream window with backward paging cursor"
```

---

### Task 6: `StreamSource` protocol and its two implementations

**Files:**
- Create: `tui_kit/stream_source.py` (protocol + in-memory implementation)
- Create: `novelizer/tui/event_store_stream_source.py` (production implementation)
- Test: `tests/tui_kit/test_stream_source.py`, `tests/tui/test_event_store_stream_source.py`

**Interfaces:**
- Consumes: `EventStore.events_before` (Task 1), `StreamBlock` (Task 2).
- Produces:
  - `class StreamSource(Protocol)` with `async def page_before(self, sequence: int, limit: int) -> list[StreamBlock]` and `async def fetch_output(self, sequence: int) -> str`
  - `InMemoryStreamSource(blocks: list[StreamBlock], outputs: dict[int, str])`
  - `EventStoreStreamSource(store, to_contract_event)`
  - Tasks 8 and 9 consume the protocol only.

The widget depends on this protocol and never on `EventStore` — that is the DIP boundary that keeps widget tests off SQLite.

- [ ] **Step 1: Write the failing test**

Create `tests/tui_kit/test_stream_source.py`:

```python
import pytest
from tui_kit.run_model import ProseBlock, ToolBlock
from tui_kit.stream_source import InMemoryStreamSource, StreamSource


def test_in_memory_source_satisfies_the_protocol():
    assert isinstance(InMemoryStreamSource([], {}), StreamSource)


@pytest.mark.asyncio
async def test_page_before_returns_blocks_below_the_cursor():
    blocks = [ToolBlock(tool_name="t", input_summary="", sequence=s) for s in (1, 2, 3, 4)]
    src = InMemoryStreamSource(blocks, {})
    assert [b.sequence for b in await src.page_before(4, limit=2)] == [2, 3]


@pytest.mark.asyncio
async def test_page_before_at_the_beginning_is_empty():
    src = InMemoryStreamSource([ToolBlock(tool_name="t", input_summary="", sequence=1)], {})
    assert await src.page_before(1, limit=10) == []


@pytest.mark.asyncio
async def test_fetch_output_returns_the_full_untruncated_payload():
    src = InMemoryStreamSource([], {7: "x" * 9000})
    assert await src.fetch_output(7) == "x" * 9000


@pytest.mark.asyncio
async def test_fetch_output_of_an_unknown_sequence_is_empty():
    assert await InMemoryStreamSource([], {}).fetch_output(99) == ""
```

Create `tests/tui/test_event_store_stream_source.py`:

```python
import pytest
from novelizer.canon.event_store import EventStore
from novelizer.telemetry.events import TelemetryEventType
from novelizer.tui.event_store_stream_source import EventStoreStreamSource
from novelizer.tui.telemetry_adapter import to_contract_event


@pytest.mark.asyncio
async def test_fetch_output_reads_the_full_output_back_off_disk(tmp_path):
    store = EventStore(str(tmp_path / "telemetry.db"))
    await store.init()
    try:
        stored = await store.append_raw(
            TelemetryEventType.TOOL_CALL_FINISHED, "r1",
            {"run_id": "r1", "agent_name": "author", "tool_name": "read_file",
             "duration_s": 1.0, "input_summary": "ch1.md", "output_summary": "y" * 9000})
        src = EventStoreStreamSource(store, to_contract_event)
        assert await src.fetch_output(stored.sequence) == "y" * 9000
    finally:
        await store.close()


@pytest.mark.asyncio
async def test_page_before_folds_stored_events_into_blocks(tmp_path):
    store = EventStore(str(tmp_path / "telemetry.db"))
    await store.init()
    try:
        await store.append_raw(
            TelemetryEventType.TOOL_CALL_STARTED, "r1",
            {"run_id": "r1", "agent_name": "author", "tool_name": "read_file",
             "input_summary": "ch1.md"})
        last = await store.append_raw(
            TelemetryEventType.TOOL_CALL_FINISHED, "r1",
            {"run_id": "r1", "agent_name": "author", "tool_name": "read_file",
             "duration_s": 1.0, "input_summary": "ch1.md", "output_summary": "done"})
        src = EventStoreStreamSource(store, to_contract_event)
        blocks = await src.page_before(last.sequence + 1, limit=50)
        assert any(getattr(b, "tool_name", "") == "read_file" for b in blocks)
    finally:
        await store.close()
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/tui_kit/test_stream_source.py tests/tui/test_event_store_stream_source.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'tui_kit.stream_source'`

- [ ] **Step 3: Write minimal implementation**

Create `tui_kit/stream_source.py`:

```python
"""Where the stream's blocks come from.

The widget depends on this protocol, never on a concrete store: that is
what lets the widget tests run against a list and stay off SQLite, and
what will let a future domain back the same view with something else.
"""
from __future__ import annotations
from typing import Protocol, runtime_checkable
from tui_kit.run_model import StreamBlock


@runtime_checkable
class StreamSource(Protocol):
    async def page_before(self, sequence: int, limit: int) -> list[StreamBlock]:
        """The `limit` blocks immediately older than `sequence`, ascending."""
        ...

    async def fetch_output(self, sequence: int) -> str:
        """Full, untruncated output for the tool call at `sequence`.
        Empty string when it cannot be found -- a missing payload is a
        display gap, never an exception into the render path."""
        ...


class InMemoryStreamSource:
    """Test double, and the seed source before any store is wired."""

    def __init__(self, blocks: list[StreamBlock], outputs: dict[int, str]) -> None:
        self._blocks = list(blocks)
        self._outputs = dict(outputs)

    async def page_before(self, sequence: int, limit: int) -> list[StreamBlock]:
        older = [b for b in self._blocks if getattr(b, "sequence", 0) < sequence]
        return older[-limit:]

    async def fetch_output(self, sequence: int) -> str:
        return self._outputs.get(sequence, "")
```

Create `novelizer/tui/event_store_stream_source.py`:

```python
"""EventStore-backed StreamSource: the production wiring.

Lives in novelizer/, not tui_kit/, because it knows about canon's
EventStore and novelizer's telemetry vocabulary. tui_kit only ever sees
the protocol.
"""
from __future__ import annotations
import logging
from novelizer.canon.event_store import EventStore
from novelizer.telemetry.events import TelemetryEventType
from tui_kit.run_model import LiveRunState, StreamBlock, apply_bus_item

logger = logging.getLogger(__name__)

_STREAM_TYPES = [
    TelemetryEventType.LLM_CALL_STARTED,
    TelemetryEventType.LLM_CALL_FINISHED,
    TelemetryEventType.TOOL_CALL_STARTED,
    TelemetryEventType.TOOL_CALL_FINISHED,
    TelemetryEventType.TOOL_CALL_FAILED,
]


class EventStoreStreamSource:
    def __init__(self, store: EventStore, to_contract_event) -> None:
        self._store = store
        self._to_contract = to_contract_event

    async def page_before(self, sequence: int, limit: int) -> list[StreamBlock]:
        events = await self._store.events_before(sequence, limit, _STREAM_TYPES)
        # Fold per run: apply_bus_item ignores events whose run_id does not
        # match the state it is folding, so one shared state would silently
        # drop every run but the first in the page.
        by_run: dict[str, list] = {}
        for ev in events:
            contract = self._to_contract(ev)
            if contract is not None:
                by_run.setdefault(getattr(contract, "run_id", ""), []).append(contract)
        blocks: list[StreamBlock] = []
        for run_id, items in by_run.items():
            state = LiveRunState(status="running", run_id=run_id)
            for item in items:
                state = apply_bus_item(state, item, now=0.0)
            blocks.extend(state.blocks)
        return blocks

    async def fetch_output(self, sequence: int) -> str:
        try:
            page = await self._store.events_before(sequence + 1, 1)
        except Exception:
            logger.warning("stream: output fetch failed at seq %s", sequence, exc_info=True)
            return ""
        if not page or page[-1].sequence != sequence:
            return ""
        return str(page[-1].payload.get("output_summary", ""))
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/tui_kit/test_stream_source.py tests/tui/test_event_store_stream_source.py -v`
Expected: 7 passed

- [ ] **Step 5: Commit**

```bash
git add tui_kit/stream_source.py novelizer/tui/event_store_stream_source.py tests/tui_kit/test_stream_source.py tests/tui/test_event_store_stream_source.py
git commit -m "feat: StreamSource protocol with in-memory and EventStore implementations"
```

---

### Task 7: `OutputRenderer` protocol and its implementations

**Files:**
- Create: `tui_kit/output_renderer.py`
- Test: `tests/tui_kit/test_output_renderer.py`

**Interfaces:**
- Consumes: `ext_is_markdown`, `looks_markdown` (Task 3).
- Produces: `OutputRenderer(Protocol)` with `def matches(self, text: str, path: str) -> bool` and `def render(self, text: str) -> Widget`; `MarkdownRenderer`, `PlainRenderer`, `pick_renderer(text, path, renderers=None) -> OutputRenderer`, `DEFAULT_RENDERERS`. Task 9 consumes `pick_renderer`.

- [ ] **Step 1: Write the failing test**

Create `tests/tui_kit/test_output_renderer.py`:

```python
from textual.widgets import Markdown, Static
from tui_kit.output_renderer import (
    MarkdownRenderer, PlainRenderer, pick_renderer, DEFAULT_RENDERERS,
)

MD = "# Chapter One\n\n- a bullet\n- another"


def test_markdown_wins_on_extension_even_without_content_signals():
    assert isinstance(pick_renderer("just plain prose", "ch1.md"), MarkdownRenderer)


def test_markdown_wins_on_content_when_there_is_no_path():
    assert isinstance(pick_renderer(MD, ""), MarkdownRenderer)


def test_plain_is_the_fallback():
    assert isinstance(pick_renderer("2026-07-25 INFO started", "app.log"), PlainRenderer)


def test_markdown_renderer_produces_a_markdown_widget():
    assert isinstance(MarkdownRenderer().render(MD), Markdown)


def test_plain_renderer_produces_a_static_with_markup_disabled():
    """Tool output is untrusted: a markup-parsing Static would raise
    MarkupError on any '[...]' sequence in canon text."""
    w = PlainRenderer().render("a [not a tag] b")
    assert isinstance(w, Static)
    assert w._render_markup is False


def test_pick_renderer_never_returns_none_for_empty_output():
    assert pick_renderer("", "") is not None


def test_default_renderers_are_ordered_most_specific_first():
    assert isinstance(DEFAULT_RENDERERS[0], MarkdownRenderer)
    assert isinstance(DEFAULT_RENDERERS[-1], PlainRenderer)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/tui_kit/test_output_renderer.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'tui_kit.output_renderer'`

- [ ] **Step 3: Write minimal implementation**

Create `tui_kit/output_renderer.py`:

```python
"""How a tool call's output becomes a widget.

Open for extension: a JSON pretty-printer or a diff view is a new
OutputRenderer appended to DEFAULT_RENDERERS, not another branch in a
rendering function. PlainRenderer is the total fallback, so pick_renderer
never fails to return one.
"""
from __future__ import annotations
from typing import Protocol
from textual.widget import Widget
from textual.widgets import Markdown, Static
from tui_kit.markdown_detect import ext_is_markdown, looks_markdown


class OutputRenderer(Protocol):
    def matches(self, text: str, path: str) -> bool: ...
    def render(self, text: str) -> Widget: ...


class MarkdownRenderer:
    def matches(self, text: str, path: str) -> bool:
        return ext_is_markdown(path) or looks_markdown(text)

    def render(self, text: str) -> Widget:
        # Markdown parses its source as markdown, never as Textual markup,
        # so untrusted "[...]" sequences are safe here.
        return Markdown(text, classes="er-output-md")


class PlainRenderer:
    def matches(self, text: str, path: str) -> bool:
        return True  # total fallback

    def render(self, text: str) -> Widget:
        return Static(text, markup=False, classes="er-output-plain")


DEFAULT_RENDERERS: tuple[OutputRenderer, ...] = (MarkdownRenderer(), PlainRenderer())


def pick_renderer(text: str, path: str,
                  renderers: tuple[OutputRenderer, ...] | None = None) -> OutputRenderer:
    for r in renderers or DEFAULT_RENDERERS:
        if r.matches(text, path):
            return r
    return PlainRenderer()
```

If `w._render_markup` is not the attribute Textual exposes in this version, assert on the constructor path instead: keep a `markup=False` kwarg and assert `w.render_str("[x]")` does not raise. Verify against the installed Textual before settling the assertion.

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/tui_kit/test_output_renderer.py -v`
Expected: 7 passed

- [ ] **Step 5: Commit**

```bash
git add tui_kit/output_renderer.py tests/tui_kit/test_output_renderer.py
git commit -m "feat(tui_kit): OutputRenderer protocol - markdown and plain implementations"
```

---

### Task 8: `StreamView` widget — mount and reconcile

**Files:**
- Create: `tui_kit/widgets/stream_view.py`
- Test: `tests/tui_kit/test_stream_view.py`

**Interfaces:**
- Consumes: `StreamState`, `visible_blocks`, `on_new_blocks`, `trim_window` (Tasks 4-5); `block_key`, block types (Task 2); `StreamSource` (Task 6); `AgentTheme` (contracts).
- Produces: `StreamView(theme: AgentTheme, source: StreamSource)` with `append_blocks(blocks) -> None`, `mounted_keys() -> list[str]`. Tasks 9-11 extend this class.

- [ ] **Step 1: Write the failing test**

Create `tests/tui_kit/test_stream_view.py`:

```python
import pytest
from textual.app import App, ComposeResult
from tui_kit.run_model import ProseBlock, ThinkingBlock, CallBlock, ToolBlock
from tui_kit.stream_source import InMemoryStreamSource
from tui_kit.widgets.stream_view import StreamView


class _Theme:
    def glyph(self, n): return {"author": "@", "editor": "#"}.get(n, "?")
    def label(self, n): return n.title()
    def style(self, n): return "gold3"
    def verb(self, n): return "working"


class _App(App):
    def compose(self) -> ComposeResult:
        yield StreamView(theme=_Theme(), source=InMemoryStreamSource([], {}), id="stream")


@pytest.mark.asyncio
async def test_appending_blocks_mounts_one_widget_each():
    async with _App().run_test() as pilot:
        view = pilot.app.query_one("#stream", StreamView)
        view.append_blocks((ProseBlock(text="a", agent_name="author"),
                            ToolBlock(tool_name="read_file", input_summary="x",
                                      agent_name="editor")))
        await pilot.pause()
        assert len(view.mounted_keys()) == 2


@pytest.mark.asyncio
async def test_reappending_the_same_blocks_does_not_remount_them():
    """Streaming prose updates the trailing block on every token; remounting
    the world each time would make the pane unusable."""
    async with _App().run_test() as pilot:
        view = pilot.app.query_one("#stream", StreamView)
        view.append_blocks((ProseBlock(text="a", agent_name="author"),))
        await pilot.pause()
        first = view.mounted_keys()
        view.append_blocks(())
        await pilot.pause()
        assert view.mounted_keys() == first


@pytest.mark.asyncio
async def test_every_block_kind_mounts_without_error():
    async with _App().run_test() as pilot:
        view = pilot.app.query_one("#stream", StreamView)
        view.append_blocks((ProseBlock(text="p", agent_name="author"),
                            ThinkingBlock(text="t", agent_name="author"),
                            CallBlock(call_index=1, model="m", agent_name="author"),
                            ToolBlock(tool_name="t", input_summary="i", agent_name="author")))
        await pilot.pause()
        assert len(view.mounted_keys()) == 4


@pytest.mark.asyncio
async def test_blocks_from_different_agents_interleave_in_arrival_order():
    async with _App().run_test() as pilot:
        view = pilot.app.query_one("#stream", StreamView)
        view.append_blocks((ProseBlock(text="a", agent_name="author"),
                            ProseBlock(text="b", agent_name="editor"),
                            ProseBlock(text="c", agent_name="author")))
        await pilot.pause()
        assert len(view.mounted_keys()) == 3
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/tui_kit/test_stream_view.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'tui_kit.widgets.stream_view'`

- [ ] **Step 3: Write minimal implementation**

Create `tui_kit/widgets/stream_view.py`:

```python
"""The unified stream: one widget per block, all agents interleaved.

Replaces the tab-per-agent panes. Tabs scaled with fleet size and hid the
one thing this view exists to show -- who is running at the same time as
whom.
"""
from __future__ import annotations
from rich.text import Text
from textual.app import ComposeResult
from textual.containers import Vertical, VerticalScroll
from textual.widgets import Static
from tui_kit.contracts import AgentTheme
from tui_kit.run_model import (
    CallBlock, ProseBlock, StreamBlock, ThinkingBlock, ToolBlock, block_key,
)
from tui_kit.stream_model import StreamState, on_new_blocks, trim_window, visible_blocks
from tui_kit.stream_source import StreamSource


class StreamView(Vertical):
    def __init__(self, theme: AgentTheme, source: StreamSource, *args, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        self._theme = theme
        self._source = source
        self._state = StreamState()
        self._mounted: dict[str, Static] = {}

    def compose(self) -> ComposeResult:
        yield VerticalScroll(id="sv_window", classes="sv-window")

    # -- public API ----------------------------------------------------------

    def append_blocks(self, blocks: tuple[StreamBlock, ...]) -> None:
        self._state = trim_window(on_new_blocks(self._state, blocks))
        self._reconcile()

    def mounted_keys(self) -> list[str]:
        return list(self._mounted)

    # -- reconciliation ------------------------------------------------------

    def _reconcile(self) -> None:
        """Mount what is new, update what changed, leave the rest alone.
        Rebuilding the pane per token is what made the old string renderer
        force a scroll-to-bottom on every update."""
        window = self.query_one("#sv_window", VerticalScroll)
        for i, block in enumerate(visible_blocks(self._state)):
            key = block_key(block, i)
            widget = self._mounted.get(key)
            if widget is None:
                widget = Static(markup=False, classes=self._classes_for(block))
                self._mounted[key] = widget
                window.mount(widget)
            widget.update(self._line_for(block))

    def _classes_for(self, block: StreamBlock) -> str:
        return {ProseBlock: "sv-prose", ThinkingBlock: "sv-thinking",
                CallBlock: "sv-call", ToolBlock: "sv-tool"}[type(block)]

    def _line_for(self, block: StreamBlock) -> Text:
        """A Text is never markup-parsed, so untrusted block content is safe
        regardless of the Static's markup setting."""
        gutter = Text(f"{self._theme.glyph(block.agent_name)} ",
                      style=self._theme.style(block.agent_name))
        if isinstance(block, ProseBlock):
            return gutter + Text(block.text)
        if isinstance(block, ThinkingBlock):
            return gutter + Text(block.text, style="italic dim magenta")
        if isinstance(block, CallBlock):
            tail = f" · {block.duration_s:.1f}s" if block.status == "done" else ""
            return gutter + Text(f"▸ call {block.call_index} ({block.model}){tail}", style="dim")
        return gutter + Text(self._tool_summary_line(block), style="bold cyan")

    def _tool_summary_line(self, b: ToolBlock) -> str:
        parts = [f"⚒ {b.tool_name}({b.input_summary})"]
        if b.repeat_count > 1:
            parts.append(f"×{b.repeat_count}")
        if b.status == "done":
            parts.append(f"· {b.duration_s:.1f}s")
        elif b.status == "failed":
            parts.append(f"· ✗ {b.error}")
        if b.summary:
            parts.append(f"· {b.summary}")
        return " ".join(parts)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/tui_kit/test_stream_view.py -v`
Expected: 4 passed

- [ ] **Step 5: Commit**

```bash
git add tui_kit/widgets/stream_view.py tests/tui_kit/test_stream_view.py
git commit -m "feat(tui_kit): StreamView mounts one widget per block with keyed reconciliation"
```

---

### Task 9: collapsible tool calls with lazy output

**Files:**
- Modify: `tui_kit/widgets/stream_view.py`
- Test: `tests/tui_kit/test_stream_view.py`

**Interfaces:**
- Consumes: `pick_renderer` (Task 7), `StreamSource.fetch_output` (Task 6), `StreamView` (Task 8).
- Produces: tool blocks mount as `Collapsible`; `StreamView.expanded_keys() -> list[str]`.

Fold policy from the spec: tool calls collapsed by default, **failures auto-expand**.

- [ ] **Step 1: Write the failing test**

Append to `tests/tui_kit/test_stream_view.py`:

```python
from textual.widgets import Collapsible, Markdown


class _MDApp(App):
    def compose(self) -> ComposeResult:
        source = InMemoryStreamSource([], {42: "# Chapter One\n\n- a\n- b"})
        yield StreamView(theme=_Theme(), source=source, id="stream")


@pytest.mark.asyncio
async def test_tool_blocks_mount_collapsed_by_default():
    async with _App().run_test() as pilot:
        view = pilot.app.query_one("#stream", StreamView)
        view.append_blocks((ToolBlock(tool_name="read_file", input_summary="ch1.md",
                                      status="done", agent_name="author", sequence=42),))
        await pilot.pause()
        assert pilot.app.query_one(Collapsible).collapsed is True


@pytest.mark.asyncio
async def test_failed_tool_calls_auto_expand():
    async with _App().run_test() as pilot:
        view = pilot.app.query_one("#stream", StreamView)
        view.append_blocks((ToolBlock(tool_name="write_scene", input_summary="ch4",
                                      status="failed", error="ValidationError",
                                      agent_name="author", sequence=7),))
        await pilot.pause()
        assert pilot.app.query_one(Collapsible).collapsed is False


@pytest.mark.asyncio
async def test_expanding_fetches_the_full_output_and_renders_markdown():
    async with _MDApp().run_test() as pilot:
        view = pilot.app.query_one("#stream", StreamView)
        view.append_blocks((ToolBlock(tool_name="read_file", input_summary="ch1.md",
                                      status="done", agent_name="author", sequence=42),))
        await pilot.pause()
        pilot.app.query_one(Collapsible).collapsed = False
        await pilot.pause()
        assert pilot.app.query(Markdown)


@pytest.mark.asyncio
async def test_output_is_fetched_once_not_on_every_toggle():
    class _CountingSource(InMemoryStreamSource):
        calls = 0

        async def fetch_output(self, sequence):
            _CountingSource.calls += 1
            return await super().fetch_output(sequence)

    class _CountApp(App):
        def compose(self):
            yield StreamView(theme=_Theme(),
                             source=_CountingSource([], {42: "plain output"}), id="stream")

    async with _CountApp().run_test() as pilot:
        view = pilot.app.query_one("#stream", StreamView)
        view.append_blocks((ToolBlock(tool_name="t", input_summary="i", status="done",
                                      agent_name="author", sequence=42),))
        await pilot.pause()
        c = pilot.app.query_one(Collapsible)
        for collapsed in (False, True, False):
            c.collapsed = collapsed
            await pilot.pause()
        assert _CountingSource.calls == 1
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/tui_kit/test_stream_view.py -k "collaps or expand or fetch" -v`
Expected: FAIL — no `Collapsible` is mounted

- [ ] **Step 3: Write minimal implementation**

In `tui_kit/widgets/stream_view.py`, add imports:

```python
from textual.widgets import Collapsible, Static
from tui_kit.output_renderer import pick_renderer
```

Track fold state and change `_reconcile` so a `ToolBlock` mounts a `Collapsible`:

```python
    def expanded_keys(self) -> list[str]:
        return [k for k, w in self._mounted.items()
                if isinstance(w, Collapsible) and not w.collapsed]

    def _mount_block(self, window, key: str, block: StreamBlock):
        if not isinstance(block, ToolBlock):
            widget = Static(markup=False, classes=self._classes_for(block))
            window.mount(widget)
            return widget
        # Failures open on arrival: an error the reader has to click for is
        # an error they will miss.
        collapsible = Collapsible(title=self._tool_summary_line(block),
                                  collapsed=block.status != "failed",
                                  classes="sv-tool")
        collapsible._sv_key = key
        collapsible._sv_sequence = block.sequence
        collapsible._sv_path = block.input_summary
        collapsible._sv_loaded = False
        window.mount(collapsible)
        if block.status == "failed":
            self._load_output(collapsible)
        return collapsible

    def on_collapsible_expanded(self, event) -> None:
        self._load_output(event.collapsible)

    def _load_output(self, collapsible) -> None:
        if getattr(collapsible, "_sv_loaded", False):
            return
        collapsible._sv_loaded = True   # set before the await: a second
        # expand while the fetch is in flight must not fetch again
        self.run_worker(self._fetch_and_mount(collapsible), exclusive=False,
                        group="sv-output")

    async def _fetch_and_mount(self, collapsible) -> None:
        seq = getattr(collapsible, "_sv_sequence", 0)
        text = await self._source.fetch_output(seq) if seq else ""
        if not text:
            await collapsible.mount(Static("(no output recorded)", markup=False,
                                           classes="sv-output-empty"))
            return
        renderer = pick_renderer(text, getattr(collapsible, "_sv_path", ""))
        await collapsible.mount(renderer.render(text))
```

`_reconcile` calls `_mount_block` for new keys, and for existing `Collapsible`s updates `widget.title` instead of `widget.update(...)`.

Add to the widget's CSS (or the app's stylesheet): `.sv-tool { border: round $panel; }` so an expanded output's extent is unambiguous — the demarcation the spec calls for.

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/tui_kit/test_stream_view.py -v`
Expected: 8 passed

- [ ] **Step 5: Commit**

```bash
git add tui_kit/widgets/stream_view.py tests/tui_kit/test_stream_view.py
git commit -m "feat(tui_kit): collapsible tool calls with lazily-fetched rich output"
```

---

### Task 10: scroll detach, reattach, and the jump bar

**Files:**
- Modify: `tui_kit/widgets/stream_view.py`
- Test: `tests/tui_kit/test_stream_view.py`

**Interfaces:**
- Consumes: `on_scroll` (Task 4), `StreamView` (Tasks 8-9).
- Produces: `StreamView.is_following() -> bool`, `action_follow_end() -> None`, a `#sv_follow` footer, and a `BINDINGS` entry for `End`.

- [ ] **Step 1: Write the failing test**

Append to `tests/tui_kit/test_stream_view.py`:

```python
@pytest.mark.asyncio
async def test_a_new_view_follows_the_tail():
    async with _App().run_test() as pilot:
        assert pilot.app.query_one("#stream", StreamView).is_following() is True


@pytest.mark.asyncio
async def test_scrolling_away_from_the_bottom_detaches():
    async with _App().run_test() as pilot:
        view = pilot.app.query_one("#stream", StreamView)
        view.append_blocks(tuple(ProseBlock(text=f"line {i}", agent_name="author")
                                 for i in range(200)))
        await pilot.pause()
        view.notify_scroll(at_bottom=False)
        assert view.is_following() is False


@pytest.mark.asyncio
async def test_returning_to_the_bottom_reattaches():
    async with _App().run_test() as pilot:
        view = pilot.app.query_one("#stream", StreamView)
        view.notify_scroll(at_bottom=False)
        view.notify_scroll(at_bottom=True)
        assert view.is_following() is True


@pytest.mark.asyncio
async def test_detached_view_shows_the_backlog_count_and_hides_it_when_following():
    async with _App().run_test() as pilot:
        view = pilot.app.query_one("#stream", StreamView)
        view.notify_scroll(at_bottom=False)
        view.append_blocks((ProseBlock(text="x", agent_name="author"),
                            ProseBlock(text="y", agent_name="author")))
        await pilot.pause()
        bar = pilot.app.query_one("#sv_follow", Static)
        assert bar.display is True
        assert "2" in str(bar.renderable)
        view.action_follow_end()
        await pilot.pause()
        assert bar.display is False


@pytest.mark.asyncio
async def test_end_reattaches_and_clears_the_backlog():
    async with _App().run_test() as pilot:
        view = pilot.app.query_one("#stream", StreamView)
        view.notify_scroll(at_bottom=False)
        view.append_blocks((ProseBlock(text="x", agent_name="author"),))
        view.action_follow_end()
        assert view.is_following() is True


@pytest.mark.asyncio
async def test_appending_while_detached_does_not_scroll_the_window():
    """The whole bug: a new token must not yank the reader back down."""
    async with _App().run_test() as pilot:
        view = pilot.app.query_one("#stream", StreamView)
        view.append_blocks(tuple(ProseBlock(text=f"l{i}", agent_name="author")
                                 for i in range(200)))
        await pilot.pause()
        window = pilot.app.query_one("#sv_window")
        window.scroll_to(y=0, animate=False)
        await pilot.pause()
        view.notify_scroll(at_bottom=False)
        view.append_blocks((ProseBlock(text="new", agent_name="author"),))
        await pilot.pause()
        assert window.scroll_offset.y == 0
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/tui_kit/test_stream_view.py -k "follow or detach or scroll or reattach or backlog" -v`
Expected: FAIL — `AttributeError: 'StreamView' object has no attribute 'is_following'`

- [ ] **Step 3: Write minimal implementation**

In `tui_kit/widgets/stream_view.py`:

```python
from textual.binding import Binding
from tui_kit.stream_model import on_scroll

# How close to the bottom still counts as "at the bottom". A couple of
# lines of slack: demanding an exact match makes reattaching feel broken.
SCROLL_EPSILON = 2
```

Add to the class:

```python
    BINDINGS = [Binding("end", "follow_end", "Follow", show=True)]

    def is_following(self) -> bool:
        return self._state.follow

    def notify_scroll(self, at_bottom: bool) -> None:
        self._state = on_scroll(self._state, at_bottom)
        self._refresh_follow_bar()

    def on_scroll_up(self) -> None:
        self.notify_scroll(self._at_bottom())

    def on_scroll_down(self) -> None:
        self.notify_scroll(self._at_bottom())

    def _at_bottom(self) -> bool:
        window = self.query_one("#sv_window", VerticalScroll)
        return window.scroll_offset.y >= window.max_scroll_y - SCROLL_EPSILON

    def action_follow_end(self) -> None:
        self._state = on_scroll(self._state, at_bottom=True)
        self.query_one("#sv_window", VerticalScroll).scroll_end(animate=False)
        self._refresh_follow_bar()

    def _refresh_follow_bar(self) -> None:
        bar = self.query_one("#sv_follow", Static)
        if self._state.follow:
            bar.display = False
            return
        n = self._state.unseen
        bar.update(f"↓ detached · {n} new · End to follow")
        bar.display = True
```

Add the footer to `compose`:

```python
    def compose(self) -> ComposeResult:
        yield VerticalScroll(id="sv_window", classes="sv-window")
        yield Static("", id="sv_follow", classes="sv-follow", markup=False)
```

and in `on_mount`: `self.query_one("#sv_follow", Static).display = False`.

Finally, make `append_blocks` scroll **only when following** — this is the fix for the reported bug:

```python
    def append_blocks(self, blocks: tuple[StreamBlock, ...]) -> None:
        self._state = trim_window(on_new_blocks(self._state, blocks))
        self._reconcile()
        if self._state.follow:
            self.query_one("#sv_window", VerticalScroll).scroll_end(animate=False)
        self._refresh_follow_bar()
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/tui_kit/test_stream_view.py -v`
Expected: 14 passed

- [ ] **Step 5: Commit**

```bash
git add tui_kit/widgets/stream_view.py tests/tui_kit/test_stream_view.py
git commit -m "feat(tui_kit): detach from the tail on scroll-up, reattach at bottom"
```

---

### Task 11: agent filter chips

**Files:**
- Modify: `tui_kit/widgets/stream_view.py`
- Test: `tests/tui_kit/test_stream_view.py`

**Interfaces:**
- Consumes: `toggle_agent`, `clear_filter`, `visible_blocks` (Task 4).
- Produces: `StreamView.set_agents(names: list[str]) -> None`, `toggle_agent_filter(name) -> None`, `active_filter() -> frozenset[str]`.

`set_agents` is how the roster reaches the widget **as data**, not as constructor-time structure — this is the fix for the coupling that made tabs scale badly.

- [ ] **Step 1: Write the failing test**

Append to `tests/tui_kit/test_stream_view.py`:

```python
from textual.widgets import Button


@pytest.mark.asyncio
async def test_set_agents_renders_an_all_chip_plus_one_per_agent():
    async with _App().run_test() as pilot:
        view = pilot.app.query_one("#stream", StreamView)
        view.set_agents(["author", "editor", "plotter"])
        await pilot.pause()
        assert len(pilot.app.query(Button)) == 4


@pytest.mark.asyncio
async def test_set_agents_is_idempotent_and_does_not_duplicate_chips():
    async with _App().run_test() as pilot:
        view = pilot.app.query_one("#stream", StreamView)
        view.set_agents(["author", "editor"])
        await pilot.pause()
        view.set_agents(["author", "editor"])
        await pilot.pause()
        assert len(pilot.app.query(Button)) == 3


@pytest.mark.asyncio
async def test_a_growing_fleet_only_adds_chips_never_restructures_the_view():
    """The tab design broke at 13 agents. Chips must not."""
    async with _App().run_test() as pilot:
        view = pilot.app.query_one("#stream", StreamView)
        view.set_agents([f"agent{i}" for i in range(13)])
        await pilot.pause()
        assert len(pilot.app.query(Button)) == 14


@pytest.mark.asyncio
async def test_toggling_a_chip_hides_other_agents_blocks():
    async with _App().run_test() as pilot:
        view = pilot.app.query_one("#stream", StreamView)
        view.set_agents(["author", "editor"])
        view.append_blocks((ProseBlock(text="a", agent_name="author"),
                            ProseBlock(text="b", agent_name="editor")))
        await pilot.pause()
        view.toggle_agent_filter("author")
        await pilot.pause()
        assert view.active_filter() == frozenset({"author"})
        assert len(view.visible_keys()) == 1


@pytest.mark.asyncio
async def test_filtered_out_widgets_are_hidden_not_unmounted():
    """Toggling must be instant and must not disturb the window or fold state."""
    async with _App().run_test() as pilot:
        view = pilot.app.query_one("#stream", StreamView)
        view.set_agents(["author", "editor"])
        view.append_blocks((ProseBlock(text="a", agent_name="author"),
                            ProseBlock(text="b", agent_name="editor")))
        await pilot.pause()
        view.toggle_agent_filter("author")
        await pilot.pause()
        assert len(view.mounted_keys()) == 2
        assert len(view.visible_keys()) == 1
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/tui_kit/test_stream_view.py -k "chip or filter or fleet" -v`
Expected: FAIL — `AttributeError: 'StreamView' object has no attribute 'set_agents'`

- [ ] **Step 3: Write minimal implementation**

Add to `compose`, before the window:

```python
        yield Horizontal(id="sv_filter", classes="sv-filter")
```

(import `Horizontal` from `textual.containers`, `Button` from `textual.widgets`.)

Add to the class:

```python
    _ALL_CHIP = "sv_chip__all"

    def set_agents(self, names: list[str]) -> None:
        """The roster arrives as data. The widget's structure does not
        depend on how many agents there are -- which is exactly what the
        tab-per-agent design got wrong."""
        wanted = list(dict.fromkeys(names))
        if wanted == self._agents:
            return
        self._agents = wanted
        bar = self.query_one("#sv_filter", Horizontal)
        bar.remove_children()
        bar.mount(Button("all", id=self._ALL_CHIP, classes="sv-chip sv-chip-on"))
        for name in wanted:
            bar.mount(Button(f"{self._theme.glyph(name)} {self._theme.label(name)}",
                             id=f"sv_chip_{name}", classes="sv-chip"))

    def active_filter(self) -> frozenset[str]:
        return self._state.agent_filter

    def visible_keys(self) -> list[str]:
        return [block_key(b, i) for i, b in enumerate(visible_blocks(self._state))]

    def toggle_agent_filter(self, name: str) -> None:
        self._state = toggle_agent(self._state, name)
        self._apply_filter()

    def on_button_pressed(self, event) -> None:
        bid = event.button.id or ""
        if bid == self._ALL_CHIP:
            self._state = clear_filter(self._state)
            self._apply_filter()
        elif bid.startswith("sv_chip_"):
            self.toggle_agent_filter(bid[len("sv_chip_"):])

    def _apply_filter(self) -> None:
        """Hide rather than unmount: toggling stays instant and fold state
        and scroll position survive it."""
        shown = set(self.visible_keys())
        for key, widget in self._mounted.items():
            widget.display = key in shown
        active = self._state.agent_filter
        for chip in self.query(".sv-chip"):
            bid = chip.id or ""
            on = (bid == self._ALL_CHIP and not active) or bid[len("sv_chip_"):] in active
            chip.set_class(on, "sv-chip-on")
```

Initialize `self._agents: list[str] = []` in `__init__`.

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/tui_kit/test_stream_view.py -v`
Expected: 19 passed

- [ ] **Step 5: Commit**

```bash
git add tui_kit/widgets/stream_view.py tests/tui_kit/test_stream_view.py
git commit -m "feat(tui_kit): agent filter chips replace tab-per-agent"
```

---

### Task 12: persist prose as coalesced segments

**Files:**
- Modify: `novelizer/telemetry/events.py`, `novelizer/telemetry/recorder.py`, `novelizer/tui/telemetry_adapter.py`
- Test: `tests/telemetry/test_recorder.py`, `tests/tui/test_telemetry_adapter.py`

**Interfaces:**
- Consumes: nothing from earlier tasks.
- Produces: `TelemetryEventType.LLM_OUTPUT_SEGMENT = "llm.output_segment"`; `LlmOutputSegment(run_id, agent_name, kind, text)`; `TelemetryRecorder.publish_token` coalescing plus `async def flush_segment(run_id) -> None`.

Per-token persistence would hammer SQLite at streaming rates. Segments flush on kind change, on tool call, on call end, and at a size threshold.

- [ ] **Step 1: Write the failing test**

Append to `tests/telemetry/test_recorder.py`:

```python
SEGMENT = TelemetryEventType.LLM_OUTPUT_SEGMENT


def test_tokens_are_buffered_not_persisted_one_by_one():
    rec, store = _recorder()
    for ch in "hello":
        rec.publish_token(TokenDelta(run_id="r1", agent_name="author", text=ch))
    assert [e for e in store.appended if e[0] == SEGMENT] == []


def test_flush_persists_one_segment_carrying_the_whole_buffer():
    rec, store = _recorder()
    for ch in "hello":
        rec.publish_token(TokenDelta(run_id="r1", agent_name="author", text=ch))
    _run(rec.flush_segment("r1"))
    segments = [p for et, p in store.appended if et == SEGMENT]
    assert len(segments) == 1
    assert segments[0].text == "hello"
    assert segments[0].kind == "text"


def test_switching_between_thinking_and_answer_flushes_a_segment_boundary():
    rec, store = _recorder()
    rec.publish_token(TokenDelta(run_id="r1", agent_name="author", text="mulling", kind="thinking"))
    rec.publish_token(TokenDelta(run_id="r1", agent_name="author", text="answer", kind="text"))
    _run(rec.flush_segment("r1"))
    segments = [p for et, p in store.appended if et == SEGMENT]
    assert [(s.kind, s.text) for s in segments] == [("thinking", "mulling"), ("text", "answer")]


def test_flushing_an_empty_buffer_persists_nothing():
    rec, store = _recorder()
    _run(rec.flush_segment("r1"))
    assert [e for e in store.appended if e[0] == SEGMENT] == []


def test_a_run_ending_flushes_its_pending_segment():
    rec, store = _recorder()
    rec.publish_token(TokenDelta(run_id="r1", agent_name="author", text="tail"))
    _run(rec.emit(TelemetryEventType.AGENT_RUN_FINISHED, "r1",
                  AgentRunFinished(run_id="r1", agent_name="author", duration_s=1.0)))
    assert [p.text for et, p in store.appended if et == SEGMENT] == ["tail"]


def test_buffers_for_different_runs_do_not_bleed_into_each_other():
    rec, store = _recorder()
    rec.publish_token(TokenDelta(run_id="r1", agent_name="author", text="A"))
    rec.publish_token(TokenDelta(run_id="r2", agent_name="editor", text="B"))
    _run(rec.flush_segment("r1"))
    segments = [p for et, p in store.appended if et == SEGMENT]
    assert [(s.run_id, s.text) for s in segments] == [("r1", "A")]
```

Add helpers `_recorder()` (returning a recorder over a fake store recording `appended`) and `_run(coro)` if the module lacks them — follow the existing fakes in that file.

Append to `tests/tui/test_telemetry_adapter.py`:

```python
def test_output_segment_becomes_a_token_delta_contract_event():
    ev = _stored(TelemetryEventType.LLM_OUTPUT_SEGMENT,
                 {"run_id": "r1", "agent_name": "author", "text": "hello", "kind": "text"})
    assert to_contract_event(ev) == TokenDelta(run_id="r1", agent_name="author",
                                               text="hello", kind="text")
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/telemetry/test_recorder.py tests/tui/test_telemetry_adapter.py -k "segment" -v`
Expected: FAIL — `AttributeError: LLM_OUTPUT_SEGMENT`

- [ ] **Step 3: Write minimal implementation**

In `novelizer/telemetry/events.py`, add to `TelemetryEventType`:

```python
    LLM_OUTPUT_SEGMENT = "llm.output_segment"
```

and the payload, replacing `TokenDelta`'s "NEVER persisted" docstring claim (it is now persisted in coalesced form, not per token):

```python
class LlmOutputSegment(BaseModel):
    """A coalesced run of streamed output, persisted at segment boundaries.

    TokenDelta stays bus-only -- persisting per token would write thousands
    of rows per call. This is the durable form, flushed when the kind
    changes, when a tool call interrupts, when the call ends, or when the
    buffer passes SEGMENT_FLUSH_CHARS.
    """

    run_id: str
    agent_name: str
    text: str
    kind: str = "text"
```

In `novelizer/telemetry/recorder.py`:

```python
SEGMENT_FLUSH_CHARS = 2000


class _Segment:
    __slots__ = ("agent_name", "kind", "parts", "size")

    def __init__(self, agent_name: str, kind: str) -> None:
        self.agent_name, self.kind, self.parts, self.size = agent_name, kind, [], 0
```

Add `self._segments: dict[str, _Segment] = {}` to `__init__`, then:

```python
    def publish_token(self, delta: TokenDelta) -> None:
        self._bus.publish(delta)          # live view is unchanged
        self._buffer_token(delta)

    def _buffer_token(self, delta: TokenDelta) -> None:
        seg = self._segments.get(delta.run_id)
        if seg is not None and seg.kind != delta.kind:
            self._pending_flushes.append((delta.run_id, seg))
            seg = None
        if seg is None:
            seg = _Segment(delta.agent_name, delta.kind)
            self._segments[delta.run_id] = seg
        seg.parts.append(delta.text)
        seg.size += len(delta.text)
        if seg.size >= SEGMENT_FLUSH_CHARS:
            self._pending_flushes.append((delta.run_id, seg))
            self._segments.pop(delta.run_id, None)

    async def flush_segment(self, run_id: str) -> None:
        """Persist this run's pending segments. Called on call end, on tool
        call, and on run end; safe to call when there is nothing buffered."""
        pending = [(rid, seg) for rid, seg in self._pending_flushes if rid == run_id]
        self._pending_flushes = [x for x in self._pending_flushes if x[0] != run_id]
        seg = self._segments.pop(run_id, None)
        if seg is not None:
            pending.append((run_id, seg))
        for rid, s in pending:
            text = "".join(s.parts)
            if not text:
                continue
            await self.emit(TelemetryEventType.LLM_OUTPUT_SEGMENT, rid,
                            LlmOutputSegment(run_id=rid, agent_name=s.agent_name,
                                             kind=s.kind, text=text))
```

Initialize `self._pending_flushes: list[tuple[str, _Segment]] = []` in `__init__`.

`publish_token` is sync and `flush_segment` is async, so `_buffer_token` must never await — it only queues. In `emit`, before `_track`, flush when the event is a boundary:

```python
        run_id = getattr(payload, "run_id", None)
        if run_id and event_type in _SEGMENT_BOUNDARIES:
            await self.flush_segment(run_id)
```

with

```python
_SEGMENT_BOUNDARIES = {
    TelemetryEventType.LLM_CALL_FINISHED,
    TelemetryEventType.LLM_CALL_FAILED,
    TelemetryEventType.TOOL_CALL_STARTED,
    *_RUN_END_TYPES,
}
```

Guard against recursion: `flush_segment` calls `emit`, so `emit` must not re-enter the flush for `LLM_OUTPUT_SEGMENT` itself — it is not in `_SEGMENT_BOUNDARIES`, which is sufficient.

In `novelizer/tui/telemetry_adapter.py`, map the new type to a `TokenDelta` contract event so replay and paging reconstruct prose identically to live streaming.

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/telemetry/ tests/tui/test_telemetry_adapter.py -v`
Expected: all pass

- [ ] **Step 5: Commit**

```bash
git add novelizer/telemetry/events.py novelizer/telemetry/recorder.py novelizer/tui/telemetry_adapter.py tests/telemetry/test_recorder.py tests/tui/test_telemetry_adapter.py
git commit -m "feat(telemetry): persist streamed output as coalesced segments"
```

---

### Task 13: cut `EngineRoom` over and delete the tabs

**Files:**
- Modify: `tui_kit/widgets/engine_room.py`, `tui_kit/run_model.py` (delete dead formatters), `novelizer/tui/app.py:352-402, 428-440`
- Modify: `tests/tui_kit/test_widgets.py`, `tests/tui/test_engine_room.py`, `tests/tui_kit/test_run_model.py`, `tests/tui/test_app_layout.py`, `tests/tui/test_identity_registry_parity.py`

**Interfaces:**
- Consumes: everything from Tasks 1-12.
- Produces: `EngineRoom(theme, source)` — note `agent_names` is **gone** from the constructor; the roster now arrives via `set_agents`.

- [ ] **Step 1: Write the failing test**

Rewrite `tests/tui/test_engine_room.py`'s construction to the new signature and add:

```python
@pytest.mark.asyncio
async def test_engine_room_has_no_tabs():
    from textual.widgets import TabbedContent
    async with _EngineRoomApp().run_test() as pilot:
        assert not pilot.app.query(TabbedContent)


@pytest.mark.asyncio
async def test_engine_room_exposes_one_unified_stream():
    async with _EngineRoomApp().run_test() as pilot:
        assert len(pilot.app.query(StreamView)) == 1


@pytest.mark.asyncio
async def test_render_live_appends_only_blocks_not_yet_shown():
    """app.py calls render_live on every bus item; re-sending the whole
    block list must not duplicate widgets."""
    async with _EngineRoomApp().run_test() as pilot:
        room = pilot.app.query_one(EngineRoom)
        state = LiveRunState(status="running", run_id="r1",
                             blocks=(ProseBlock(text="a", agent_name="author"),))
        room.render_live(state)
        room.render_live(state)
        await pilot.pause()
        assert len(room.query_one(StreamView).mounted_keys()) == 1
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/tui/test_engine_room.py -v`
Expected: FAIL — `TypeError: __init__() got an unexpected keyword argument 'agent_names'`

- [ ] **Step 3: Write minimal implementation**

In `tui_kit/widgets/engine_room.py`:
- Delete `TabbedContent`/`TabPane` and the per-agent panes entirely.
- `__init__(self, theme, source, *args, **kwargs)` — no `agent_names`.
- `compose` yields `Static#er_vitals`, `StreamView#er_stream`, `DataTable#er_trace`, `Static#er_detail`.
- `render_live(state, now=None)` updates vitals and forwards **only blocks not already appended** to `StreamView.append_blocks`, tracking a per-run count of forwarded blocks. Delete `render_agent_live` and `_rendered_body`.
- Add `set_agents(names)` delegating to the `StreamView`.
- Keep `set_trace_rows`, `show_detail`, `toggle_prompt` untouched — out of scope.

In `tui_kit/run_model.py`, delete `live_body`, `styled_body`, `_render_block`, `stream_line_kind`, and `_LINE_STYLES` — all superseded. Keep `strip_line`, `vitals_line`, `styled_vitals` (the activity strip and vitals bar still use them).

In `novelizer/tui/app.py`:
- Construct `EngineRoom(theme=NOVELIZER_AGENT_THEME, source=EventStoreStreamSource(self.runtime.telemetry_store, to_contract_event), id="engine_room")` at line 114.
- Call `engine_room.set_agents(list(AGENT_NAMES))` in `on_mount`.
- Delete `_render_agent_panes` and `self._agent_live_states` along with the `seed_states` call.
- The three `render_live`/`render_agent_live` call sites collapse to one `render_live(self._live_state)` each in `_telemetry_bus_loop` and `_telemetry_refresh_loop`.
- Leave `app.py:417`'s `[:1000]` exactly as it is.

Delete the tests in `tests/tui_kit/test_widgets.py` and `tests/tui_kit/test_run_model.py` that assert on `live_body`/`styled_body`/`stream_line_kind`; their behaviour is covered by Tasks 8-11.

`tests/tui/test_identity_registry_parity.py` exists specifically to guard the `agent in AGENT_NAMES` gating this task deletes. Its premise is gone. Re-premise it to assert that every name in `AGENT_NAMES` has a theme glyph, label, style, and verb — which is what the filter chips now need — rather than deleting the file outright.

- [ ] **Step 4: Run the full suite**

Run: `pytest -x -q`
Expected: all pass. Investigate any failure before proceeding — do not accept a red suite. Per the repo's testing notes, TUI pilot tests are load-flaky; if something fails, re-run that module alone before concluding it is a real regression, and compare against `main` for parity.

- [ ] **Step 5: Commit**

```bash
git add -A
git commit -m "feat(tui): unified Engine Room stream replaces tab-per-agent"
```

---

### Task 14: documentation

**Files:**
- Modify: `docs/` — the Engine Room reference and any how-to mentioning agent tabs
- Test: the repo's docs tests, if the touched pages have them

- [ ] **Step 1: Find every doc that describes the tabs**

Run: `grep -rln "TabPane\|agent tab\|per-agent tab\|Engine Room" docs/`

- [ ] **Step 2: Update them**

Replace tab descriptions with the unified stream: the filter chips, `End` to reattach, click-to-expand tool calls, failures auto-expanded. Add `events_before` and `llm.output_segment` to the reference pages that enumerate store methods and telemetry event types.

- [ ] **Step 3: Run the docs tests**

Run: `pytest tests/docs -q` (skip if no such directory)
Expected: pass

- [ ] **Step 4: Commit**

```bash
git add docs/
git commit -m "docs: unified Engine Room stream replaces per-agent tabs"
```

---

## Self-Review Notes

**Spec coverage:** demarcation + rich markdown → Tasks 3, 7, 9. Collapsible, collapsed by default, failures expanded → Task 9. Scroll detach/reattach + jump bar → Task 10. Unified stream + filter → Tasks 4, 8, 11. Memory/windowing → Tasks 2, 5, 6. Prose durability → Task 12. `events_before` → Task 1. Block sum type → Task 2. Cutover and test rewrites → Task 13. Docs → Task 14.

**Known risks the implementer should watch:**
- Task 7's `w._render_markup` assertion depends on the installed Textual version; the task says to verify and gives a fallback.
- Task 9 attaches `_sv_*` attributes to `Collapsible` instances. If Textual's `Collapsible` uses `__slots__` in this version, subclass it instead of monkey-patching.
- Task 13 is the only task that touches many files at once; it is deliberately last so everything it depends on is already green.
