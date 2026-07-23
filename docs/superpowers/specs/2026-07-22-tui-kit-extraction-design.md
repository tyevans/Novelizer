# tui_kit extraction design

## Problem

`novelizer/tui/` is the presentation layer for the entire app, importing
directly from `brain`, `canon`, `chat`, `director`, `export`, `research`,
`settings`, `store`, `telemetry`, and `voices`. Most of that coupling is
legitimate — the TUI genuinely renders novel-specific concepts (brain
findings, canon events, story browsing). But a subset of the TUI —
the "watch N agents run" console (Engine Room, the live token-stream
panel, the ambient activity strip, the roster glyph strip) — is
almost entirely generic agent-run visualization with only a thin layer
of novelizer-specific naming (agent list, verbs, glyph/color theme).

As the project takes on non-novel domains (research, coding), that
generic machinery needs to be reusable without dragging novelizer's
domain types along. This spec extracts it into its own package.

## Scope

In scope: `EngineRoom`, `LiveStreamPanel`, `ActivityStrip`,
`engine_room_model.py`, and the roster glyph-strip renderer
(`roster_glyphs` in `roster.py`).

Out of scope: `brain_model.py`, `feed_model.py`, `browser_model.py`,
`story_picker.py`, `chat_screen.py`, `research_screen.py`, and other
screens — these are genuinely novel-specific and stay in
`novelizer/tui/` unchanged.

## Architecture

A new top-level package, `tui_kit/`, sibling to `substrate/`,
`novelizer/`, and `research_domain/`. It renders a "watch N agents run"
console — pure state model, formatters, and Textual widgets — with
**zero imports from novelizer, substrate, or research_domain**.

```
tui_kit/
  contracts.py     # event dataclasses + AgentTheme protocol
  run_model.py      # pure state machine + formatters (no Textual)
  widgets/
    engine_room.py
    live_stream_panel.py
    activity_strip.py
    roster.py
```

### `contracts.py`

Domain-agnostic event dataclasses describing one agent run's
lifecycle, independent of any concrete telemetry system:

- `RunStarted`, `RunFinished`, `RunFailed`
- `LLMCallStarted`, `LLMCallFinished`, `LLMCallFailed`
- `ToolCallStarted`, `ToolCallFinished`, `ToolCallFailed`
- `TokenDelta`, `ToolSummaryReady`
- `SchedulerPicked`, `SchedulerEligibilityChanged`

Plus an `AgentTheme` protocol a consuming domain implements:

```python
class AgentTheme(Protocol):
    def glyph(self, agent_name: str) -> str: ...
    def style(self, agent_name: str) -> str: ...
    def verb(self, agent_name: str) -> str: ...
```

### `run_model.py`

Today's `engine_room_model.py`, minus `AGENT_NAMES`, `_VERBS`, and the
direct `identity_for` import. Same public functions — `Block`,
`LiveRunState`, `apply_bus_item`, `seed_state`, `seed_states`,
`route_agent`, `strip_line`, `vitals_line`, `live_body`,
`styled_vitals`, `styled_body`, `trace_line`, `trace_detail` — but
`apply_bus_item` consumes `tui_kit.contracts` events instead of
novelizer's `StoredEvent`/`TelemetryEventType`, and the formatters that
need agent theming (`strip_line`, `vitals_line`, `styled_vitals`) take
an `AgentTheme` parameter instead of importing `identity_for` and a
hardcoded verb table.

### `widgets/`

`EngineRoom`, `LiveStreamPanel`, `ActivityStrip`, and `roster_glyphs`,
unchanged in behavior, built against `contracts`/`run_model` only. They
accept an `AgentTheme` (or pre-rendered `Text`, where the current API
already renders text upstream) from the mounting screen rather than
reaching for `identity_for` themselves.

## novelizer-side adapter

`novelizer/tui/` keeps:

- `identity.py` — implements `tui_kit.contracts.AgentTheme` using
  novelizer's real agent glyphs/colors/verbs. `AGENT_NAMES` and
  `_VERBS` move here from `engine_room_model.py`.
- A new `telemetry_adapter.py` — the only module that knows both
  vocabularies. Translates `StoredEvent`/`TelemetryEventType` values
  and novelizer's `TokenDelta`/`ToolSummaryReady` into
  `tui_kit.contracts` events.
- Screens (`app.py`, `chat_screen.py`, etc.) construct `tui_kit`
  widgets/model objects, feeding them adapter output and novelizer's
  `AgentTheme`.

Nothing else in novelizer's TUI changes shape.

## Import boundary

Add `tui_kit` to `root_packages` in `pyproject.toml`'s
`[tool.importlinter]` section, plus a new `forbidden` contract:
`tui_kit` may not import `novelizer`, `substrate`, or `research_domain`
— mirroring the independence contract that already protects
`substrate`. No contract is added forcing novelizer to route through a
narrow `tui_kit` public surface (unlike substrate's submodule-import
ban) — YAGNI unless that proves necessary later.

## Testing

- Pure-model tests (`tests/tui/test_engine_room_model.py`, the
  `roster_glyphs` cases in `test_roster.py`) move to `tests/tui_kit/`
  and drop all novelizer imports — they already have no Textual or
  novelizer dependency beyond the model itself, so this is close to a
  straight move plus fixture rewrites (fake `AgentTheme`, `contracts`
  events instead of `StoredEvent`).
- Widget tests (`test_engine_room.py`, `test_live_stream_panel.py`,
  the `ActivityStrip` cases) move the same way, using Textual's test
  harness with fake `AgentTheme`/event fixtures.
- novelizer keeps thin tests for `telemetry_adapter.py` (translation
  correctness) and for `identity.py`'s `AgentTheme` conformance.

## Migration plan

1. Create `tui_kit/` (`contracts.py`, `run_model.py`, `widgets/`) by
   moving and trimming the existing files.
2. Write `novelizer/tui/identity.py` as an `AgentTheme` implementation
   and `novelizer/tui/telemetry_adapter.py` for event translation.
3. Update `novelizer/tui/app.py`, `chat_screen.py`, and other call
   sites to import from `tui_kit` plus the adapter.
4. Move and rewrite tests as described above.
5. Add the import-linter contract; run `uv run lint-imports`.
6. Delete the old `novelizer/tui/widgets/engine_room_model.py`,
   `engine_room.py`, `live_stream_panel.py`, `activity_strip.py`, and
   the `roster_glyphs` function from `roster.py` once nothing
   references them.

## Non-goals

- Generalizing `identity.py`'s glyph/color *scheme* beyond making it
  conform to `AgentTheme` — a future domain can supply its own
  `AgentTheme` implementation without any further `tui_kit` change.
- Touching `ChatScreen`, `ApprovalScreen`, `EscalationsScreen`, or any
  other screen — flagged as a candidate for a later extraction pass,
  not this one.
- Enforcing a narrow novelizer-side import surface onto `tui_kit`
  (substrate-style submodule ban) — revisit only if novelizer code
  starts reaching into `tui_kit` internals in ways that hurt.
