# Engine Room tool-call blocks

## Problem

The live token/tool-call stream (`engine_room_model.py`) renders tool calls
as two loose plain-text lines appended to one running string:

```
⚒ search_web(dragons)
   ↳ done in 1.2s
```

They happen to land adjacent today because no token deltas normally arrive
between a tool call's start and finish, but there's no structural grouping —
just string concatenation — so there's no way to (a) visually box a call with
its result, (b) collapse repeated identical calls, or (c) attach a
generated summary after the fact. This affects every surface built on
`live_body`/`styled_body`: `EngineRoom` and `LiveStreamPanel`.
`ActivityStrip` shows only a one-line ambient status and is unaffected.

## Design

### 1. Structured blocks

`LiveRunState` gains a `blocks: tuple[Block, ...]` field. `Block` is a frozen
dataclass:

```python
@dataclass(frozen=True)
class Block:
    kind: str  # "prose" | "thinking" | "call" | "tool"
    block_id: int = 0
    text: str = ""              # prose/thinking content
    tool_name: str = ""
    input_summary: str = ""
    status: str = "running"     # running | done | failed
    duration_s: float = 0.0
    error: str = ""
    summary: str | None = None  # cheap-LLM one-liner, filled in later
    repeat_count: int = 1
```

`apply_bus_item` builds/updates blocks instead of concatenating strings:

- `TOKEN_DELTA` (kind `text`/`thinking`): appends to the trailing block if
  it's the same kind (`prose`/`thinking`), else opens a new one.
- `LLM_CALL_STARTED`/`FINISHED`: unchanged in spirit — a `"call"` block,
  opened on start, closed on finish (this already reads fine as one line
  today and doesn't need boxing).
- `TOOL_CALL_STARTED`: opens a `"tool"` block with a fresh `block_id`
  (monotonic counter on `LiveRunState`), unless the trailing block is a
  **finished** tool block with the same `tool_name` + `input_summary` — in
  that case, reopen it in place (`status="running"`, `repeat_count += 1`,
  clear `summary`) rather than creating a new block. This is the repeat
  collapsing.
- `TOOL_CALL_FINISHED`/`FAILED`: closes the last open tool block matching
  `tool_name`, filling `status`, `duration_s`, `error`.

`state.text` (the flat string) is removed; all existing consumers
(`live_body`, `styled_body`, `stream_line_kind`, tests) are rewritten
against `blocks`.

### 2. Cheap-LLM summaries

`engine_room_model.py` stays pure — no LLM calls inside it. The owning
screen (wherever the bus subscription lives, e.g. `chat_screen.py`) reacts
to `TOOL_CALL_FINISHED`/`FAILED`:

- Fires an async call to the run's configured agent model, thinking
  disabled, with a short prompt built from `tool_name`, `input_summary`,
  and a new `output_summary` field (truncated `str(output)[:300]`, added to
  the `ToolCallFinished` telemetry payload in `callbacks.py`, mirroring the
  existing `input_summary` truncation).
- On completion, publishes a new bus item `ToolSummaryReady(run_id,
  block_id, summary)` (new dataclass alongside `TokenDelta` in
  `telemetry/events.py`) through the same bus the token stream already
  flows through.
- `apply_bus_item` handles `ToolSummaryReady` by patching `summary` onto the
  block with matching `block_id` (no-op if the run has since moved on /
  block no longer exists).
- Summarization failure or slowness never blocks the live stream — the
  block just renders without a summary line until/unless one arrives.

### 3. Rendering

`styled_body` renders each `"tool"` block as a small unit within the single
`Static` (no new Textual widgets — the existing scroll/perf model stays
simple):

```
⚒ search_web(dragons)                    <- bold cyan
   ↳ done in 1.2s                        <- dim
   ↳ found three matching entries        <- italic dim, once summary lands
```

Repeated block: `⚒ read_file(ch3.md) ×3` with the same done/summary lines
underneath (summary reflects the most recent call in the repeat run).

Prose/thinking blocks render as before (plain / italic-dim-magenta).

## Out of scope

- Turning tool-call blocks into real per-block Textual widgets (rejected in
  brainstorming — bigger change, no material benefit over styled `Text`
  spans).
- Any change to `ActivityStrip` (no tool-call detail shown there).
- Batching/on-demand summary triggering (using immediate backfill instead).

## Testing

- Pure-function unit tests in `test_engine_room_model.py` rewritten against
  `blocks` (block creation, repeat collapsing, summary patching, rendering
  of a finished/running/failed tool block).
- `test_engine_room.py` / `test_live_stream_panel.py`: assert the rendered
  `Text` contains the boxed multi-line tool-call form and repeat counters.
- New test for `ToolSummaryReady` folding into `apply_bus_item` including
  the case where the run has since ended (patch is a no-op, doesn't crash).
