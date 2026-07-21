# Live Agent Stream for Chat and Research Screens — Design

**Date:** 2026-07-21
**Status:** Approved (brainstorm complete)

## Summary

Give the persona chat screens (`ChatScreen`, `@author` etc.) and the
research screen (`ResearchScreen`, "Talk to the Project") a live view of
the agent's stream of thought and tool calls while a turn is in flight —
the same token/reasoning/tool-call stream Engine Room already shows for
the seven autonomous agents, reused rather than rebuilt.

## Background

Engine Room already has the full machinery: `TelemetryCallbackHandler`
bridges LangChain model/tool callbacks to a `TelemetryBus`; `LiveRunState`
+ `apply_bus_item` (pure, Textual-free, in
`novelizer/tui/widgets/engine_room_model.py`) fold bus items into a
per-agent render state; `EngineRoom` renders one tab per agent from
`AGENT_NAMES` (the seven autonomous agents, fixed at compose time).

Two gaps stop chat and research from getting this for free:

1. **Not tagged.** `TelemetryCallbackHandler` reads `current_agent_name`/
   `current_run_id` (ambient `ContextVar`s) to stamp every LLM/tool-call
   event. Only `BaseAgent.run_once` sets them. `ChatService.generate_reply`
   and `ResearchService.ask` invoke their runners without setting either,
   so their telemetry events carry an empty agent name and are silently
   dropped by `route_agent` in the app's bus loop today — not broken,
   just invisible.
2. **No render target.** Even if tagged, `EngineRoom`'s tabs are built
   once at compose time from the fixed `AGENT_NAMES` tuple; a bus item for
   an unknown key (`"chat:author"`, `"research"`) would make
   `render_agent_live` call `query_one` for a tab that was never built.

## Decisions (locked during brainstorm)

1. **Scope: both chat and research screens.** They share the same gap;
   fixing it once, reused by both, is cheaper than fixing it twice.
2. **Separate identity, not shared with the autonomous agent.** A chat
   conversation with `@author` is tagged `"chat:author"`; research is
   tagged `"research"` — never the bare `"author"` key the autonomous
   Author agent uses. A live autonomous chapter draft and a live chat
   reply from the same persona must never collide in one render state or
   one Engine Room tab.
3. **Always-visible panel**, not a toggle. Part of each screen's layout at
   all times; idle/empty when no turn is in flight, live during one.
4. **No replay/seeding on mount.** Unlike Engine Room (which seeds from
   the durable telemetry log so a restart shows continuity), these panels
   start idle every time the screen mounts and only show activity that
   happens during the screen's own lifetime. Chat and research turns are
   short-lived; there's no requirement to reconstruct a finished turn's
   stream after the fact, and skipping it keeps this addition small.

## Architecture

### `run_with_identity` — shared run-identity helper

New function in `novelizer/telemetry/recorder.py`, extracted from the
pattern `BaseAgent.run_once` already implements inline:

```python
@asynccontextmanager
async def run_with_identity(telemetry, name: str):
    """Bracket a block of work with ambient run identity + AGENT_RUN_*
    telemetry, the same contract BaseAgent.run_once gives autonomous
    agents. Yields the run_id. telemetry may be None (tests / no-op)."""
```

Sets `current_run_id`/`current_agent_name`, emits `AGENT_RUN_STARTED`
before the block, `AGENT_RUN_FINISHED` after a clean exit,
`AGENT_RUN_FAILED` (re-raising) on exception, resets both context vars in
a `finally`. `BaseAgent.run_once` is *not* refactored to use this in this
pass — same behavior, different call site; touching the autonomous-agent
path is unnecessary risk for this feature. A follow-up unifying them is
reasonable but out of scope here.

### Service changes

`ChatService.__init__` and `ResearchService.__init__` each gain a
`telemetry` parameter (the same `TelemetryRecorder` instance every
autonomous agent already receives, from `Runtime.telemetry`).

- `ChatService.generate_reply(agent_name, ...)` wraps its
  `runner.ainvoke(...)` call in `run_with_identity(self._telemetry,
  f"chat:{agent_name}")`.
- `ResearchService.ask(question, history)` wraps its `runner.ainvoke(...)`
  call in `run_with_identity(self._telemetry, "research")`.

Both are constructed with `telemetry=self.telemetry` in `Runtime.start()`,
alongside their existing construction sites.

### `LiveStreamPanel` — new shared widget

New widget in `novelizer/tui/widgets/live_stream_panel.py`, built from the
same rendering functions Engine Room already uses
(`vitals_line`, `live_body`, `stream_line_kind` from
`engine_room_model.py`) — those are pure and Textual-free today; the
`_styled_vitals`/`_styled_body` Rich-`Text` formatting currently private
to `engine_room.py` is lifted into `engine_room_model.py` as two more pure
functions (`styled_vitals`, `styled_body`) so both `EngineRoom` and
`LiveStreamPanel` call the identical styling, not two copies of it.
`engine_room.py`'s own `_styled_vitals`/`_styled_body` become thin
wrappers (or are replaced by direct calls) — a small, low-risk
de-duplication, not a rewrite.

`LiveStreamPanel(key: str)`:

- Composes a `Static` (vitals line) over a `VerticalScroll` containing a
  `Static` (stream body) — the same two-part layout Engine Room's per-agent
  tab pane already uses, without the `TabbedContent` wrapper.
- `render(state: LiveRunState, now: float)` — public method the owning
  screen calls; idle state renders as empty/blank (vitals line only shows
  once `status != "idle"`).
- Holds no bus subscription itself — the owning screen's existing worker
  loop drives it (see below), keeping the widget a pure render target,
  consistent with how `EngineRoom.render_agent_live` already works.

### Screen wiring

**`ChatScreen`**: gains a `LiveStreamPanel` above the transcript `RichLog`.
The screen's existing `_poll_loop` (0.5s interval) is joined by a bus
subscription — a new `_telemetry_loop` worker, started in `on_mount`
alongside the existing poll loop, subscribing to
`self.runtime.telemetry_bus`, filtering with the same `route_agent(item)`
helper `engine_room_model` already exports, keeping only items whose
routed key equals `f"chat:{self.agent_name}"` (updated when the active
tab switches, mirroring how `_refresh` already re-keys on
`self.agent_name`). Local `LiveRunState` held on the screen instance,
reset to a fresh idle state whenever the active tab switches (a panel
never shows another conversation's stale stream).

**`ResearchScreen`**: gains a `LiveStreamPanel` above the transcript,
identical wiring but a fixed key `"research"` (single conversation, no
tab-switch case) and a `LiveRunState` reset to idle at the start of each
`_ask` call (a fresh turn never shows the previous turn's finished/failed
tail state).

## Error handling

A runner failure already triggers `run_with_identity`'s `AGENT_RUN_FAILED`
emission before re-raising; `apply_bus_item` already renders `status ==
"failed"` with the error line (this is exactly what Engine Room does
today for autonomous agents) — no new error-rendering path needed. The
screen's own existing `⚠ ... failed` transcript line (chat) / `⚠ research
failed` line (research) is unaffected and still fires from the
service-level exception, independent of the telemetry panel.

## Testing

Red/green TDD, no property-based coverage needed (bus routing is already
covered by Engine Room's existing property tests; this reuses that path,
it doesn't add a new one):

- **Unit — `run_with_identity`**: emits `AGENT_RUN_STARTED` before the
  block and exactly one of `AGENT_RUN_FINISHED`/`AGENT_RUN_FAILED` after;
  context vars are set during the block and reset after (success and
  exception paths); the yielded `run_id` matches what's stamped on the
  emitted events; a `None` telemetry is a no-op (doesn't raise).
- **Unit — `ChatService`/`ResearchService` telemetry tagging**: with a
  fake `TelemetryRecorder`, assert the emitted `AGENT_RUN_STARTED`
  carries `agent_name="chat:<name>"` / `"research"` respectively; existing
  reply/answer/intent behavior is unchanged (regression check on the
  existing test suites for both services).
- **Unit — `engine_room_model.styled_vitals`/`styled_body`**: same
  assertions the existing (now-removed) private `engine_room.py` versions
  would have had — extraction, not new behavior.
- **TUI pilot — `ChatScreen`**: opening a conversation and sending a
  message shows the panel transition idle → running → finished as fake
  telemetry events land on the bus; switching tabs resets the panel to
  idle even mid-run on the previous tab; events for a different chat key
  or an autonomous-agent key are ignored.
- **TUI pilot — `ResearchScreen`**: submitting a question shows the same
  idle → running → finished/failed transition; a second submit while
  pending still behaves per the existing one-turn-at-a-time contract
  (unaffected by this change, verified as a regression check).

## Out of scope

- Refactoring `BaseAgent.run_once` to use `run_with_identity` (behavior
  parity is enough for this pass; unifying them is a separate, low-risk
  follow-up).
- Replay/seeding chat or research panels from the durable telemetry log.
- Making chat/research activity visible in Engine Room's own seven tabs —
  the separate-identity decision explicitly keeps them out of that view.
- Toggling the panel's visibility — it's always present, per the
  Decisions section.
