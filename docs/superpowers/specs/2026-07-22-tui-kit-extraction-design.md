# tui_kit extraction design
Status: implemented (2026-07-22).

## Problem

`novelizer/tui/widgets/` had accreted a set of Textual widgets — the roster
glyph strip, the Engine Room live panes, the live-stream panel, and the
activity strip — whose rendering logic was entirely generic (agent runs,
tokens, tool calls, timelines) but which imported novelizer's domain types
directly (`StoredEvent`, `TelemetryEventType`, canon models). That coupling
made the widgets impossible to unit-test without novelizer's telemetry bus
and canon store running, and impossible to reuse in any other Textual
project.

## Scope

Extract the domain-agnostic core — event contracts, the pure agent-run state
machine, and the four widgets — into a standalone package, `tui_kit`, with no
import of anything under `novelizer/`. Leave a thin adapter on the
novelizer side that translates real telemetry-bus items and `StoredEvent`s
into `tui_kit.contracts` events. Enforce the boundary mechanically so it
can't regress silently.

## Architecture

```
tui_kit/
  __init__.py
  contracts.py      # event/protocol vocabulary
  run_model.py       # pure state machine + formatters
  widgets/
    __init__.py
    roster.py
    engine_room.py
    live_stream_panel.py
    activity_strip.py
```

This matches the directory tree on disk as of 2026-07-22 — confirmed via
`ls tui_kit/ tui_kit/widgets/`.

### `contracts.py`

Defines the domain-agnostic event types (`TokenDelta`, `ToolSummaryReady`,
and the rest of the agent-run vocabulary) and the `AgentTheme` protocol that
callers implement to supply glyph/label/color per agent. No novelizer
imports.

### `run_model.py`

A pure state machine (`normalize_input_summary` and friends) that folds a
stream of `contracts` events into renderable state, plus formatting helpers.
No Textual, no I/O, no novelizer imports — this is what makes the widgets
unit-testable in isolation.

### `widgets/`

Four Textual widgets, each consuming only `tui_kit.contracts` /
`tui_kit.run_model` types:

- `roster.py` — glyph-strip renderer
- `engine_room.py` — live agent-run panes
- `live_stream_panel.py` — streaming token/tool display
- `activity_strip.py` — compact activity ticker

## novelizer-side adapter

Two modules on the novelizer side bridge real telemetry into the generic
contract vocabulary, confirmed present and matching this description as of
6b3a2e5:

- `novelizer/tui/identity.py` — single source of truth for agent identity
  (glyph, label, Rich color style) keyed by canonical `agent_name`, sourced
  from the mission-control design-pass spec's identity table.
  `identity_for` falls back to a dim, title-cased label for any
  `agent_name` not in the registry (including the empty string). This
  module has no dependency on `telemetry_adapter.py` and vice versa —
  the split holds cleanly: identity answers "how do I render agent X",
  the adapter answers "what `tui_kit.contracts` event is this bus item".
- `novelizer/tui/telemetry_adapter.py` — houses `to_contract_event`, the
  adapter's actual entry point (see the subsection below), plus
  `trace_line`/`trace_detail`. Those two stay here rather than moving to
  `tui_kit` because they render domain-specific `StoredEvent` payloads
  ("produced: chapter.created ch-12"), not the generic run vocabulary that
  `tui_kit.contracts` events carry.

`novelizer/tui/app.py` and `novelizer/tui/chat_screen.py` were wired onto
`tui_kit` in c87fee5, consuming the adapted contract events instead of the
deleted novelizer-local widget modules.

### Adapter entry point: `to_contract_event`

`to_contract_event(item)` is the single dispatch function every caller uses
to cross from novelizer's telemetry vocabulary into `tui_kit.contracts`. It
takes one bus item — a `StoredEvent`, a bus-only `NovelizerTokenDelta`, or a
bus-only `NovelizerToolSummaryReady` (the latter two imported from
`novelizer.telemetry.events` under aliases, since `tui_kit.contracts`
defines its own `TokenDelta`/`ToolSummaryReady`) — and returns the matching
`tui_kit.contracts` event, or `None` if the item carries nothing the generic
run model renders.

Dispatch, confirmed against the current source:

1. `NovelizerTokenDelta` -> `contracts.TokenDelta`.
2. `NovelizerToolSummaryReady` -> `contracts.ToolSummaryReady`.
3. Anything else that isn't a `StoredEvent` -> `None`.
4. `StoredEvent`, matched on `event_type` against `TelemetryEventType`:
   `AGENT_RUN_STARTED`/`FINISHED`/`FAILED`, `LLM_CALL_STARTED`/`FINISHED`,
   and `TOOL_CALL_STARTED`/`FINISHED`/`FAILED` each map to their `contracts`
   counterpart, reading fields out of `StoredEvent.payload` with `.get(...)`
   defaults.
5. Everything else (scheduler events — `SCHEDULER_PICKED`,
   `SCHEDULER_ELIGIBILITY_CHANGED` — `LLM_CALL_FAILED`, or any other
   `event_type`) -> `None`.

`trace_line`/`trace_detail` are deliberately outside this dispatch: they
take a `StoredEvent` directly and render its domain-specific payload for the
trace table (e.g. `"⚒ author → write_scene(...)"`,
`"produced: chapter.created ch-12"`), which has no equivalent in the
generic `tui_kit.contracts` vocabulary. Callers that populate the trace
table read raw `StoredEvent`s and call `trace_line`/`trace_detail`
directly; callers that feed `tui_kit` widgets (Engine Room, live stream
panel, activity strip, live-pane seeding) route every bus item through
`to_contract_event` first and drop the `None`s.

## Restart-seeding correctness

Fixed post-merge in 6b3a2e5 ("fix(novelizer): seed Engine Room live panes
from adapted contract events on restart").

`app.py`'s `_telemetry_bus_loop` seeds two views from the recent-events
buffer on a mid-run restart: the trace table, which consumes raw
`StoredEvent`s directly via `trace_line`/`trace_detail` and so seeded
correctly all along, and the Engine Room's live panes, which are seeded via
`seed_state`/`seed_states` — functions that only recognize
`tui_kit.contracts` events. Before the fix, the restart-seed block passed
the same raw `StoredEvent`s straight to `seed_state`/`seed_states`. Their
internal `isinstance` checks against `contracts` types silently no-op'd on
a `StoredEvent` — no exception, no log line, just an empty Engine Room — so
live panes stayed unseeded after a mid-run restart even though the trace
table sitting right next to them looked fully populated.

The fix maps `recent[-50:]` through `to_contract_event` and filters out
`None` before handing the result to `seed_state`/`seed_states`:

```python
contract_recent = [c for c in (to_contract_event(e) for e in recent[-50:]) if c is not None]
self._live_state = seed_state(contract_recent, now)
self._agent_live_states = seed_states(contract_recent, now)
```

This mirrors the adaptation the same method's live bus-item loop already
performed — that path was never affected by the bug, since it always ran
incoming items through the adapter before dispatch.

## Import boundary

Enforced by import-linter, added in b40df29 ("chore(tui_kit): enforce
independence from novelizer via import-linter") — `tui_kit` may not import
`novelizer`. This runs as part of the standard lint/CI path, so a future
patch that reaches back into novelizer from `tui_kit` fails mechanically
rather than depending on review discipline.

## Testing

Pure-logic tests for the extracted package live under `tests/tui_kit/`:
`test_contracts.py`, `test_roster.py`, `test_run_model.py`, `test_widgets.py`
— confirmed present on disk.

`tests/tui/test_telemetry_adapter.py` covers `to_contract_event` translation
correctness — confirmed present on disk. `tests/tui/test_engine_room.py::
test_seeded_live_pane_survives_restart` regression-tests the restart-seed
adaptation path added in 6b3a2e5, confirmed present at line 388 of that
file.

The novelizer-side test suite was trimmed to match, rather than moved
wholesale: 4ede08d ("refactor(novelizer): trim roster.py to autonomy dial,
delete superseded tui_kit-migrated modules") cut `tests/tui/test_roster.py`
from 100+ lines down to the handful of assertions that still exercise
novelizer-local behavior (the autonomy dial), deleting the coverage that had
already migrated to `tests/tui_kit/test_roster.py`. The same commit deletes
the superseded `novelizer/tui/widgets/engine_room.py`,
`engine_room_model.py`, `live_stream_panel.py`, and trims
`activity_strip.py`/`roster.py` to what still differs from the extracted
package. This is a trim-in-place of the old suite, not a straight `git mv`
— call this out explicitly since the plan below originally assumed a move.

## Migration plan

Executed in six steps, each tied to its merge commit:

1. **Extract contracts, state machine, and widgets into `tui_kit/`** —
   fb79318 (domain-agnostic event contracts + `AgentTheme` protocol),
   b66f188 (pure agent-run state machine and formatters), e5f0295
   (EngineRoom, LiveStreamPanel, ActivityStrip widgets), 358de1c (roster
   glyph-strip renderer).
2. **Build the novelizer-side adapter** (`telemetry_adapter.py`,
   `identity.py`) translating bus/`StoredEvent` items into `tui_kit.contracts`
   events — c5e3589.
3. **Wire `app.py` and `chat_screen.py` onto `tui_kit`** — c87fee5.
4. **Move/trim tests** to match the new split (`tests/tui_kit/` added for
   the extracted package; `tests/tui/test_roster.py` trimmed in place rather
   than moved) — 4ede08d.
5. **Enforce the import boundary** with import-linter — b40df29.
6. **Delete superseded novelizer-local widget modules** (`engine_room.py`,
   `engine_room_model.py`, `live_stream_panel.py`, trimmed `activity_strip.py`
   /`roster.py`) — 4ede08d.

## Non-goals

- No attempt to publish `tui_kit` as an installable package (no
  `pyproject.toml`/version boundary) — it stays an in-repo package for now.
- No migration of other Textual screens (research, chat shell chrome) beyond
  what already consumes the four extracted widgets.
- No change to the telemetry bus's own event vocabulary — the adapter
  absorbs the translation so the bus stays novelizer-native.
