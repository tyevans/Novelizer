# Wire a New Domain onto tui_kit

`tui_kit` (`tui_kit/contracts.py`, `tui_kit/run_model.py`, `tui_kit/widgets/`)
is a domain-agnostic "watch N agents run" console: a generic run-state
machine plus Textual widgets (`LiveStreamPanel`, `EngineRoom`,
`ActivityStrip`) that render it. It knows nothing about novelizer, chapters,
or canon events. This guide walks through wiring a new domain onto it, using
novelizer's own wiring (`novelizer/tui/identity.py`,
`novelizer/tui/telemetry_adapter.py`, `novelizer/tui/chat_screen.py`,
`novelizer/tui/app.py`) as the worked example throughout.

## Prerequisites (what tui_kit assumes about your domain)

Before wiring anything, your domain needs:

- **A telemetry vocabulary with agent runs, LLM calls, and tool calls** —
  something that already looks like starts/finishes/failures for those three
  concepts. `tui_kit`'s run model (`LiveRunState` in `tui_kit/run_model.py`)
  is built entirely around those three kinds of spans.
- **A stable per-agent identifier** (a string) that's consistent across your
  event stream — novelizer uses `agent_name` (e.g. `"character_keeper"`).
- **A live bus and/or a durable event log** you can iterate/subscribe to.
  novelizer has both: `runtime.telemetry_bus` (an async queue of live items)
  and `runtime.telemetry_store` (a durable log queried via `events_tail`).
  Only the live bus is required; the durable log is what lets you seed
  history on restart (Step 3).

`tui_kit` never imports anything from `novelizer` — the dependency only
flows one way. All translation lives in your domain's own adapter module.

## Step 1: Implement AgentTheme for your domain

### The four methods: glyph, label, style, verb

`tui_kit.contracts.AgentTheme` is a `Protocol` with four methods, each taking
an `agent_name: str`:

```python
class AgentTheme(Protocol):
    def glyph(self, agent_name: str) -> str: ...
    def label(self, agent_name: str) -> str: ...
    def style(self, agent_name: str) -> str: ...
    def verb(self, agent_name: str) -> str: ...
```

`LiveStreamPanel`, `EngineRoom`, and `ActivityStrip` all take a `theme=`
constructor argument and call these four methods to render a speaker glyph,
a short label, a Rich style string for color, and a present-participle verb
("drafting", "reviewing") for idle/working states. There's no base class to
subclass — any object with these four methods satisfies the protocol
structurally.

### Worked example: novelizer's NovelizerAgentTheme in novelizer/tui/identity.py

novelizer keeps a single `IDENTITIES` registry (`dict[str, AgentIdentity]`)
as the source of truth for glyph/label/style, keyed by the same
`agent_name` strings that appear in telemetry payloads:

```python
IDENTITIES: dict[str, AgentIdentity] = {
    "author": AgentIdentity("author", "Author", "✎", "A", "#d7af00"),
    "editor": AgentIdentity("editor", "Editor", "§", "E", "#8787d7"),
    ...
}
```

**Spell colors as hex, not as Rich color names.** `style()` is consumed by two
different renderers: Rich (`rich.Text` — vitals, roster, feed) *and* Textual
(`Content.styled` — the engine room's stream block headers and filter chips).
Textual's parser only knows CSS color names, so a Rich 256-color name like
`gold3` or `dark_cyan` raises there and the style is dropped **silently** —
the block header or chip renders colorless while everything Rich draws looks
right. Hex parses identically in both. Assert it in your tests:

```python
def test_styles_parse_under_both_renderers():
    import rich.style, textual.style
    for ident in IDENTITIES.values():
        rich.style.Style.parse(ident.style)
        textual.style.Style.parse(ident.style)
```

This constraint applies only to `AgentTheme.style()`. Styles you render
exclusively through `rich.Text` yourself (status bars, banners) can keep using
Rich color names.

`NovelizerAgentTheme` is a thin adapter over that registry plus a separate
`_VERBS` dict (verb isn't part of `AgentIdentity` because it's a run-state
concept, not an identity one):

```python
class NovelizerAgentTheme:
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

A single module-level instance (`NOVELIZER_AGENT_THEME`) is constructed once
and passed to every widget and screen that needs it — there's no per-screen
theme state.

### Handling unknown/unregistered agent names (fallback identity)

`identity_for()` is the one place that must never raise or return `None`,
since screens call it for every agent name that shows up in the telemetry
stream, including ones that predate a registry change or come from
misconfigured payloads:

```python
def identity_for(agent_name: str) -> AgentIdentity:
    ident = IDENTITIES.get(agent_name)
    if ident is not None:
        return ident
    label = agent_name.replace("_", " ").title() or "System"
    return AgentIdentity(agent_name, label, "·", "-", "dim")
```

Unknown names fall back to a dim `·` glyph and a title-cased label derived
from the name itself (`"story_brain"` → `"Story Brain"`); the empty string
falls back to `"System"`. `_VERBS.get(agent_name, "working")` gives the verb
side of the same fallback. Any new `AgentTheme` implementation should follow
this pattern — a registry lookup with a computed, never-failing default —
since it's the only thing standing between a stale/unknown agent name and a
crash mid-render.

## Step 2: Write an adapter from your domain's events to tui_kit.contracts

### Why this lives in your domain, not in tui_kit (trace_line/trace_detail stay domain-specific)

`tui_kit.run_model` only understands its own `contracts` dataclasses
(`RunStarted`, `ToolCallStarted`, `TokenDelta`, ...). It has no idea what a
`StoredEvent` or a `TelemetryEventType` is. The adapter module is where that
translation happens, and it belongs to the domain for two reasons: your
event shapes (enum values, payload dict keys) are domain-specific, and so is
any *debug/machinery* trace rendering — `trace_line`/`trace_detail` render
things like `"produced: chapter.created ch-12"`, which is inherently about
novelizer's aggregates and event types, not part of the generic
agent-run vocabulary tui_kit renders in the live panels.

### The to_contract_event(item) -> contract event | None pattern

The adapter exposes one function, `to_contract_event(item)`, that takes
whatever shape arrives off your bus or log and returns either a
`tui_kit.contracts` dataclass instance or `None`:

```python
def to_contract_event(item):
    if isinstance(item, NovelizerTokenDelta):
        return contracts.TokenDelta(run_id=item.run_id, agent_name=item.agent_name,
                                    text=item.text, kind=item.kind)
    if isinstance(item, NovelizerToolSummaryReady):
        return contracts.ToolSummaryReady(...)
    if not isinstance(item, StoredEvent):
        return None
    p = item.payload
    et = item.event_type
    if et == TelemetryEventType.AGENT_RUN_STARTED:
        return contracts.RunStarted(run_id=p.get("run_id", ""), agent_name=p.get("agent_name", ""))
    ...
    return None
```

Every field is pulled with `p.get(key, default)` rather than `p[key]` —
telemetry payloads are dicts assembled at the emission site, and a missing
key should degrade to an empty/zero default rather than raise inside the
adapter.

### Mapping your event enum/types to RunStarted/RunFinished/RunFailed/LLMCallStarted/LLMCallFinished/ToolCallStarted/ToolCallFinished/ToolCallFailed/TokenDelta/ToolSummaryReady

novelizer's `TelemetryEventType` members map onto the contract dataclasses
one-to-one:

| `TelemetryEventType` member | contract dataclass |
|---|---|
| `AGENT_RUN_STARTED` | `RunStarted(run_id, agent_name)` |
| `AGENT_RUN_FINISHED` | `RunFinished(run_id, agent_name, duration_s)` |
| `AGENT_RUN_FAILED` | `RunFailed(run_id, agent_name, error_type, error_message)` |
| `AGENT_RUN_CANCELLED` | `RunFailed(run_id, agent_name, "CancelledError", "run cancelled")` — the run model has no cancelled status, and a terminal event that maps to nothing leaves the run reading as still running |
| `AGENT_RUN_TRUNCATED` | *(none)* — the tool-call budget landed the run early. Deliberately unmapped: the run is still going and emits its own terminal event afterwards, so closing the live block here would hide the rest of it. It shows in the durable trace instead |
| `LLM_OUTPUT_SEGMENT` | `TokenDelta(run_id, agent_name, text, kind)` — a stored, coalesced segment (payload `LlmOutputSegment`) reconstructs prose the same way live streaming did, so replay/paged history renders identically to what the live view showed token-by-token |
| `LLM_CALL_STARTED` | `LLMCallStarted(run_id, agent_name, call_index, model, prompt)` |
| `LLM_CALL_FINISHED` | `LLMCallFinished(run_id, agent_name, call_index, duration_s, output_tokens)` |
| `TOOL_CALL_STARTED` | `ToolCallStarted(run_id, agent_name, tool_name, input_summary, delegate)` |
| `TOOL_CALL_FINISHED` | `ToolCallFinished(run_id, agent_name, tool_name, duration_s, output_summary, input_summary)` |
| `TOOL_CALL_FAILED` | `ToolCallFailed(run_id, agent_name, tool_name, duration_s, error_type, input_summary)` |

`input_summary` on the two result events is optional but load-bearing when
your domain runs same-named tool calls in parallel: `apply_bus_item` uses it
to attach each result to the block that made that exact call. Omit it and
results fall back to closing the *last* running block with that tool name,
which scrambles call/result pairing under parallelism.

`ToolSummaryReady`, and per-token `TokenDelta` streamed live, don't come from
`StoredEvent` at all in novelizer — they're bus-only, high-frequency items
(`NovelizerTokenDelta`, `NovelizerToolSummaryReady` from
`novelizer.telemetry.events`) that are never persisted to the durable log, so
they're checked with `isinstance` before the `StoredEvent` branch even runs.
What *is* persisted is coarser: the recorder coalesces runs of same-kind
output into `LlmOutputSegment` events (flushed on a kind change, a tool call,
call end, or a size threshold) and appends those as `LLM_OUTPUT_SEGMENT`
`StoredEvent`s, which the adapter's `StoredEvent` branch turns back into the
same `contracts.TokenDelta` shape — so scrollback and replay show the prose
that streamed live, just not at per-token granularity.

### Returning None for events tui_kit's run model doesn't render (e.g. scheduler-only events)

`TelemetryEventType.SCHEDULER_PICKED`,
`SCHEDULER_ELIGIBILITY_CHANGED`, and `LLM_CALL_FAILED` all fall through
`to_contract_event`'s `if/elif` chain to the trailing `return None` — there
is no `contracts.LLMCallFailed` at all, and scheduler bookkeeping has no
run-state representation in `tui_kit.run_model`. Callers (Step 3) always
guard on `is not None` before feeding a translated item into
`apply_bus_item`, so returning `None` for anything tui_kit can't render is
the correct, silent no-op — not an error path.

### Worked example: novelizer/tui/telemetry_adapter.py translating StoredEvent + TelemetryEventType

The full adapter module handles three input shapes in one function:
`NovelizerTokenDelta` / `NovelizerToolSummaryReady` (bus-only dataclasses,
translated unconditionally), `StoredEvent` (translated by switching on
`item.event_type`), and anything else (returns `None` immediately via the
`if not isinstance(item, StoredEvent): return None` guard). This keeps the
function total over every shape that can arrive on the bus, including
future event types nobody has taught the adapter about yet.

## Step 3: Wire the adapter and theme into your screens

### Passing theme= into LiveStreamPanel, EngineRoom, ActivityStrip

All three widgets take the module-level theme instance; `EngineRoom` also
takes a `StreamSource` (how the unified stream pages back through history
and fetches a tool call's full output on demand):

```python
yield LiveStreamPanel(theme=NOVELIZER_AGENT_THEME, id="chat_live")          # chat_screen.py
yield EngineRoom(theme=NOVELIZER_AGENT_THEME,                               # app.py
                 source=EventStoreStreamSource(self.runtime.telemetry_store,
                                               to_contract_event),
                 id="engine_room")
yield ActivityStrip("idle", theme=NOVELIZER_AGENT_THEME, id="activity_strip")  # app.py
```

The agent roster is *not* a constructor argument. It arrives as data, after
mount:

```python
self.query_one("#engine_room", EngineRoom).set_agents(list(AGENT_NAMES))    # on_mount
```

### Feeding live bus items through to_contract_event(item) into apply_bus_item/LiveRunState (chat_screen.py pattern)

`chat_screen.py`'s live loop is the minimal pattern — one `LiveRunState`,
fed straight from the bus:

```python
self._live_state = LiveRunState()
...
contract_item = to_contract_event(item)
if contract_item is not None:
    self._live_state = apply_bus_item(self._live_state, contract_item, time.monotonic())
    self.query_one(LiveStreamPanel).render(self._live_state)
```

`apply_bus_item` is pure (`tui_kit/run_model.py`: no Textual imports, a
`(state, item, now) -> state` reducer), so the screen's only job is to
translate, guard on `None`, replace its `LiveRunState`, and re-render the
widget. `app.py`'s `_telemetry_bus_loop` follows the identical
translate-guard-apply-render sequence against one global `self._live_state`,
which it hands whole to `EngineRoom.render_live` — the Engine Room works out
which of those blocks its stream already owns.

### Seeding history/replay on restart: mapping recent stored events through the adapter before applying (app.py contract_recent pattern)

Because novelizer's telemetry is durable, `app.py` seeds both live states
from the log *before* subscribing to the live bus, so a restart never shows
a blank Engine Room:

```python
recent = await self.runtime.telemetry_store.events_tail(200)
now = time.monotonic()
contract_recent = [c for c in (to_contract_event(e) for e in recent[-50:]) if c is not None]
self._live_state = seed_state(contract_recent, now)
```

`seed_state` (also in `tui_kit.run_model`) replays a list of
already-translated contract events to reconstruct a `LiveRunState` as of
"now" — the same adapter function used for the live loop is reused here,
just mapped over a batch instead of called per-item. Only a domain with a
durable log needs this step; a bus-only domain can skip straight to the
live loop with `LiveRunState()` as the initial state.

### Passing your domain's static agent roster (AGENT_NAMES) alongside the theme

`novelizer/tui/identity.py` also exports `AGENT_NAMES`, a plain tuple
mirroring `AGENT_REGISTRY`'s scheduling order in
`novelizer/agents/registry.py` — kept as a tuple rather than imported from
the registry so `identity.py` stays free of the heavy agent-construction
import chain. `EngineRoom.set_agents(...)` turns it into one filter chip per
agent, so a name missing from it means an agent whose output cannot be
isolated in the stream. Because the roster is data rather than structure,
you may call `set_agents` again whenever your domain's roster changes; an
agent name the roster does not know still streams, it just falls back to
`identity_for`'s unknown-agent identity and has no chip.

## Step 4 (optional): Domain-specific trace rendering

### When to add your own trace_line/trace_detail instead of using tui_kit's generic rendering

`tui_kit`'s widgets render the *live* run view (`LiveRunState`) generically,
but a durable, scrollable machinery/debug trace — the kind `EngineRoom`'s
trace pane shows via `set_trace_rows`/`show_detail` — has no generic
representation, because it renders raw domain events and what they
produced, not run-state. Add `trace_line`/`trace_detail` functions to your
own adapter module (not to `tui_kit`) if your domain has a durable event log
you want to expose this way; skip this step entirely if you only need the
live panels.

### Rendering payload fields and produced downstream events for a machinery/debug trace

novelizer's `trace_line(ev)` switches on `event_type` to produce a single
timestamped summary line per event (e.g.
`"14:03:21 character_keeper llm call 2 ✓ 4s · 812 tok"`), including branches
for the scheduler-only event types that `to_contract_event` deliberately
returns `None` for — the trace pane is a superset of what the live panels
show. `trace_detail(ev, produced)` expands one event into a full block:
every remaining payload key/value pair (with `prompt` popped out and
rendered last, since it can be long), a `"produced: {event_type} {aggregate_id}"`
line per downstream event the original event caused, and the prompt text at
the end if present. `app.py` calls
`engine_room.show_detail(trace_detail(ev, produced))` when a trace row is
selected, where `produced` is looked up from the same durable log the trace
rows were seeded from.

## Verification checklist

- [ ] Your `AgentTheme` implementation's four methods never raise for any
      `str` input, including the empty string and names not in your
      registry (verify the fallback path explicitly).
- [ ] `to_contract_event(item)` returns `None` (not a partially-populated
      dataclass, not an exception) for every event shape your domain emits
      that isn't in the RunStarted/.../ToolSummaryReady list.
- [ ] Every call site that uses the result of `to_contract_event` guards
      with `is not None` before passing it to `apply_bus_item`/`seed_state`.
- [ ] `LiveStreamPanel`/`EngineRoom`/`ActivityStrip` are all constructed
      with the *same* theme instance (a shared module-level singleton, not
      one instance per screen).
- [ ] If you seed from a durable log on restart, `contract_recent` is built
      with the identical `to_contract_event` function used in the live
      loop — one adapter, two call sites, not two adapters.
- [ ] `EngineRoom`'s `agent_names=` roster and any `agent in AGENT_NAMES`
      guards stay in sync with your domain's actual registry.

## Common pitfalls

### Forgetting a contract event maps 1:1 to a dataclass field set — mismatched kwargs fail silently at construction time

Every `contracts` dataclass is `frozen` with a fixed field set; passing an
unexpected keyword argument or omitting a required one raises a `TypeError`
at construction, inside `to_contract_event`, at the moment that particular
event type first fires — which can be much later than when you wrote the
adapter branch. Check each branch's kwargs against the dataclass definition
in `tui_kit/contracts.py` directly rather than against another event type's
similarly-named payload keys (e.g. `ToolCallStarted` has `delegate`,
`ToolCallFinished` does not).

### Reusing tui_kit's RunModel dataclasses directly instead of translating (couples tui_kit to your domain's types)

Don't have your domain's telemetry code construct `tui_kit.contracts`
instances directly at the emission site, and don't have `tui_kit` widgets
reach back into domain-specific payload dicts. The adapter module is the
only place the two vocabularies should meet — `tui_kit` stays reusable
across domains, and your domain's event shapes stay free to evolve without
touching `tui_kit`.

### Missing agent names not falling back gracefully in the theme

If `identity_for`-equivalent lookup uses `IDENTITIES[agent_name]` (a
`KeyError`-raising subscript) instead of `.get(...)` plus a computed
fallback, any agent name that predates a registry update — or any
misconfigured payload — crashes the render loop rather than showing a dim
placeholder row. novelizer's own `_VERBS` table is a live example of the
same fallback doing real work even for *registered* agents: it only has
entries for 7 of the 9 names in `AGENT_NAMES` (`muse` and `plotter` have no
bespoke verb), so `NovelizerAgentTheme.verb` falls back to the generic
`"working"` for them via `_VERBS.get(agent_name, "working")` — proof the
fallback path isn't just a theoretical edge case reserved for typos, it's
exercised every time one of those two agents runs. Always test the
"unknown agent name" path explicitly (including the empty string), as
novelizer's `identity_for` fallback is documented and tested to do.

## See also

- `tui_kit/contracts.py` — the full `AgentTheme` protocol and event
  dataclass definitions.
- `tui_kit/run_model.py` — `LiveRunState`, the stream block types,
  `apply_bus_item`, `seed_state`, `seed_states`, `route_agent`,
  `normalize_input_summary`.
- `tui_kit/widgets/stream_view.py` — the unified stream; `tui_kit/stream_source.py`
  for the `StreamSource` protocol `EngineRoom` requires.
- `novelizer/tui/identity.py` — `NovelizerAgentTheme`, `IDENTITIES`,
  `AGENT_NAMES`, `identity_for`.
- `novelizer/tui/telemetry_adapter.py` — `to_contract_event`, `trace_line`,
  `trace_detail`.
- `novelizer/tui/chat_screen.py` and `novelizer/tui/app.py` — the two
  worked wiring sites (bus-only live loop vs. durable-log-seeded loop).
