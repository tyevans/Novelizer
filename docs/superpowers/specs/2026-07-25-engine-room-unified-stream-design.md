# Engine Room: unified stream, collapsible tool calls, detachable scroll

Date: 2026-07-25
Status: approved design, not yet planned

## Problem

The Engine Room's live pane has four distinct defects, all rooted in one
rendering decision.

1. **Tool output is unreadable.** `read_file` output is appended as raw
   indented lines with no demarcation (`tui_kit/run_model.py:266-268`), so
   a chapter's text runs into the surrounding stream with no visible
   boundary and no formatting, even when it is markdown.

2. **Nothing collapses.** Every tool call renders its full output inline,
   forever. A run that reads six files buries its own prose.

3. **Scrolling up is impossible during a run.** `render_live` calls
   `scroll_end(animate=False)` unconditionally on every body change
   (`tui_kit/widgets/engine_room.py:78, 94`), so any new token yanks the
   view back to the bottom.

4. **Tab-per-agent has outgrown itself.** `EngineRoom(agent_names=[...])`
   builds one `TabPane` per agent. The fleet is now 13 agents; the tab bar
   is unusable, and — worse — per-agent tabs structurally hide concurrency.
   You cannot see that the Author and the Editor are running at the same
   time, which is precisely what the Engine Room exists to show.

The common root: the stream body is a single string rendered into a single
`Static` (`live_body()` -> `styled_body()`). A string cannot hold a fold, a
`Markdown` widget, or a per-agent gutter, and re-rendering the whole string
on every token is what makes unconditional scroll-to-bottom feel mandatory.

A fifth defect surfaced while scoping: `Block.output` holds each tool
call's **full, uncapped** output in memory for the life of the run.
`novelizer/telemetry/callbacks.py:162` emits `output_summary=str(output)`
with no truncation, and `run_model.py:150` copies it straight into the
block. Reading six chapters means six chapters resident per agent, times
13 agents.

Note for implementers: the `[:1000]` at `novelizer/tui/app.py:417` is
**not** a display cap and must be left alone. It bounds only the text fed
to the cheap-LLM tool-call summarizer prompt; the display path never sees
it.

## Decisions

Settled during brainstorming, recorded here so the plan does not relitigate
them:

| Question | Decision |
|---|---|
| Render model | Per-block widgets: `VerticalScroll` of `Collapsible` / `Markdown` / `Static` |
| Markdown detection | Extension first, content sniff as fallback |
| Scroll behaviour | Auto-detach on scroll-up, reattach at bottom, with a "N new" jump bar |
| Fold policy | Tool calls collapsed by default; failures auto-expand |
| Stream layout | One unified chronological stream with an agent filter; tabs removed |
| Output retention | Do not hold full output in memory; read it from the store on expand. Leave `app.py:417`'s summarizer-prompt cap alone |
| Prose durability | Persist `TokenDelta` as coalesced segments |

## Architecture

### Persistence: use the store that already exists

Telemetry already persists to its **own** `EventStore` (`telemetry.db`,
separate from the domain log — see `novelizer/telemetry/events.py:16-20`).
Every `tool.call_finished` event already carries `output_summary` and a
monotonic `sequence`. That is the disk layer; we do not add another.

Full tool output is already persisted uncapped
(`callbacks.py:162`), so nothing needs to change to make it durable. Two
changes:

- **`Block` stops carrying output.** It keeps a short preview for the
  collapsed summary line plus the originating event `sequence`. Expanding a
  fold reads the full payload from the store on demand. Per-call memory
  becomes constant regardless of output size.
- **Persist prose.** `TokenDelta` is currently bus-only and explicitly
  documented as never persisted; the bus is a bounded drop-oldest queue and
  the in-memory text cap is 8000 chars, so prose scrollback is already
  lossy. Scrolling up into unpersisted prose would scroll into a void. We
  add a `llm.output_segment` event written at **segment boundaries**
  (kind switch, tool call, call end) — never per token, which would hammer
  SQLite at streaming rates.

### Windowing

The stream holds a bounded window of blocks (target: a few hundred).
Scrolling above the top pages backward by `sequence`; scrolling down
releases the tail. This needs one new store method:

```python
async def events_before(self, sequence: int, limit: int) -> list[StoredEvent]
```

mirroring the existing `events_since`, returning ascending order.

### Units and boundaries

One reason to change each:

| Unit | Responsibility | Depends on |
|---|---|---|
| `run_model` (pure) | fold events into blocks; keying, filtering, follow-state, window bounds | nothing |
| `StreamSource` (protocol) | supply a live window; page backward by sequence | — |
| `EventStoreStreamSource` | implement `StreamSource` over `EventStore` | canon |
| `InMemoryStreamSource` | test double | — |
| `OutputRenderer` (protocol) | output + optional path -> renderable | — |
| `MarkdownRenderer`, `PlainRenderer` | the two implementations | rich / textual |
| `StreamView` (widget) | mount, fold, scroll | tui_kit only |

The widget depends on the `StreamSource` protocol, never on `EventStore`,
so widget tests drive an in-memory list and never touch SQLite. This
preserves the repo's existing pure/impure split, which is why these tests
are fast.

Adding a future renderer (JSON pretty-print, diff view) becomes a new
`OutputRenderer` rather than another arm of `_render_block`, which is
already a five-way `if/elif` and would otherwise have grown to eight.

### Block becomes a sum type

`Block` currently carries `tool_name`, `input_summary`, `output`,
`duration_s`, and `error` on every block regardless of kind — meaningless
on three of its four kinds. It becomes:

```
ProseBlock | ThinkingBlock | CallBlock | ToolBlock
```

so renderer dispatch is total rather than defensive. The ubiquitous
language is unchanged: run, block, call, tool, agent.

### Widget structure

```
Vertical#er_stream
 |- Static#er_vitals          fleet-wide vitals
 |- Horizontal#er_filter      agent filter chips: [/]all + one per agent
 |- VerticalScroll#er_window  the windowed block list
 |    |- Static      <glyph> plotter  prose...
 |    |- Collapsible <glyph> plotter  > read_file(outline.md) . 0.3s . 340 lines
 |    |    `- Markdown | Static       (lazy: mounted on first expand)
 |    `- Static      <glyph> author   thinking...
 `- Static#er_follow          "detached . 12 new . End to follow"
```

Each block carries the agent's glyph and accent gutter from the existing
`AgentTheme`, so interleaved concurrency stays legible. Filter state is a
set of agent names; filtered-out widgets get `display = False` rather than
being unmounted, so toggling is instant and does not disturb the window.

### Reconciliation

The current code re-renders the entire body on every change, which cannot
survive per-block widgets. Each block gets a stable key (`run_id` + block
index). Render diffs the incoming block list against mounted widgets,
mounting only new blocks and updating only the trailing one. Streaming
prose touches exactly one widget per token batch.

Fold state lives in the widget keyed by block key, so it survives
re-render and window paging.

### Markdown selection

On expand, fetch the full output, then:

```python
md = ext_is_markdown(path) or looks_markdown(text)
```

`looks_markdown` is a pure heuristic — headings, fenced code, list markers,
tables above a threshold; negative on mostly-JSON or mostly-tabular output.
Either renderer is wrapped in a bordered container so the output's extent
is unambiguous.

Tool output is untrusted model and canon content. It reaches Textual as
`Text`/`Markdown` objects, never as markup-parsed `str` — preserving the
existing `markup=False` safety property documented at
`tui_kit/widgets/engine_room.py:33-36`.

### Scroll

`render()` no longer calls `scroll_end()` unconditionally. A `follow` flag
defaults true; a scroll landing more than a threshold above the bottom
clears it; returning to the bottom or pressing `End` restores it. While
detached, the footer counts new blocks.

## Testing

Red/green throughout, per the repo's standing engineering rules.
Property-based tests on the pure layer, where the natural targets are:

- folding an arbitrary event sequence never crashes and never loses a
  terminal state
- filtering then folding equals folding then filtering
- paging backward then forward is identity on the window
- `looks_markdown` is stable under trailing whitespace and line-ending
  changes

Widget tests drive `InMemoryStreamSource` and assert on mounted widget
structure, fold state, and follow-state transitions.

## Scope

**In:** the live stream pane, `run_model`'s block model and formatters,
output retention, prose segment persistence, `events_before`, and the
`EngineRoom` call sites in `novelizer/tui/app.py` (three collapse to one).

**Out, deliberately:** the trace `DataTable`, the roster, vitals-bar
internals, `tool_summarizer`, and the telemetry event schemas beyond the
one added segment event. None need to move for this; touching them would
make the diff unreviewable.

## Consequences

This replaces `live_body`, `styled_body`, and `_render_block`, and most of
`EngineRoom`. The following test modules assert against the old string
rendering and are rewritten alongside the implementation, not after:

- `tests/tui_kit/test_run_model.py`
- `tests/tui_kit/test_widgets.py`
- `tests/tui/test_engine_room.py`

`tests/tui/test_app_layout.py` and `tests/tui/test_identity_registry_parity.py`
reference the per-agent render path and need review; the latter exists
specifically to guard the `agent in AGENT_NAMES` gating that this design
removes, so its premise should be re-examined rather than mechanically
fixed.
