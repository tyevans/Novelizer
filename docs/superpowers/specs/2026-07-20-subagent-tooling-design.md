# Subagent Tooling for Tooled Agents — Design

**Date:** 2026-07-20
**Status:** Approved (brainstorm complete)

## Summary

Give every tooled/pull-mode agent (Character Keeper, Continuity Checker,
Structure Analyst, Author, Editor, Retconner, World Architect, plus chat
personas) the ability to dispatch a **researcher** subagent — a
deepagents `SubAgent` that reads/greps/searches canon on the parent's
behalf and returns a targeted answer, instead of the parent doing all its
own reading inline. This is infrastructure: a generic delegation
capability every tooled agent can opt into, not a one-off feature for a
single agent.

## Background

Today, tooled agents read canon directly via their own `read_file`/`grep`/
`glob`/`search_canon` tools (`Runtime._phase_a_toolkit()`,
`novelizer/runtime.py:107-130`). For work that's really "go find out X"
rather than "read everything and reason over it," a dispatched subagent
is a better fit than either (a) reading everything inline, burning the
parent's own context, or (b) a bespoke single-shot summarization tool,
which can't do multi-step lookup (cross-referencing a name against
aliases, following a search hit to its file, etc.).

deepagents' `create_deep_agent(..., subagents=[...])` already supports
this (`deepagents/middleware/subagents.py`): a `SubAgent` TypedDict
(`name`, `description`, `system_prompt`, optional `tools`/`model`) is
compiled into a `task` tool exposed to the parent. Critically:

- **Model inheritance is free.** Omitting `model` on a `SubAgent` spec
  inherits the parent's model — matches the "same model as parent"
  decision with zero extra config.
- **Telemetry propagates automatically.** The parent's `TelemetryCallbackHandler`
  reaches the subagent's own LLM/tool calls because langgraph's
  `ensure_config` seeds each subagent run from the ambient parent config
  (`subagents.py:684-690`).
- **Subagent identity is already stamped for us.** Each compiled subagent
  runnable is bound with `metadata={"lc_agent_name": spec["name"]}`
  (`subagents.py:563-568`) — LangChain callback methods receive that
  `metadata` dict on every call, giving us a free, built-in way to tell
  "this call came from the researcher subagent" apart from the parent's
  own calls, with no new plumbing on our side.

## Decisions (locked during brainstorm)

1. **Scope: general infrastructure**, not a single-agent feature. Any
   tooled agent can be granted subagent access.
2. **One shared "researcher" role**, not per-agent bespoke subagents. A
   shared factory (`build_researcher_subagent(agent_name, extra_instructions="")`)
   produces the `SubAgent` spec; every agent's researcher carries the same
   `name="researcher"` identity (consistent in telemetry/TUI regardless of
   which parent dispatched it) and the same base system prompt, with only
   a short per-agent instruction suffix for domain framing (e.g. Character
   Keeper's researcher is nudged toward alias/dedup checks; Continuity
   Checker's toward date/quantity cross-referencing).
3. **No per-agent tool subsetting in v1.** The researcher inherits the
   same canon-read toolkit its parent has (`ls`/`read_file`/`grep`/`glob`/
   `search_canon`) — no separate backend or narrower tool list.
4. **Same model as parent** — `model` omitted from the `SubAgent` spec,
   relying on deepagents' inheritance.
5. **Independently gated per agent**, via a *separate* settings flag from
   the existing tools flag (e.g. `character_keeper_subagent_enabled`
   alongside `character_keeper_tools_enabled`) — configurable
   independently, mirroring the existing `ToolGrant` pattern.
6. **Invalid combination (subagent on, tools off) is silently a no-op.**
   No settings validation error; the subagent grant simply has no effect
   without a backend to read from.
7. **TUI: indented delegate lines in the same Engine Room stream**
   (Option A) — not a collapsed single line, not a separate pane.
   Subagent tool calls appear inline, indented with a `↳` prefix, using
   the existing tool-call line style.

## Architecture

### Subagent factory — `novelizer/agents/subagents.py`

```python
def build_researcher_subagent(agent_name: str, extra_instructions: str = "") -> SubAgent:
    return {
        "name": "researcher",
        "description": "...",  # when/why the parent should dispatch it
        "system_prompt": RESEARCHER_SYSTEM_PROMPT + extra_instructions,
        # tools omitted -> inherits parent's backend-bound read tools
        # model omitted -> inherits parent's model
    }
```

### Registry wiring — `SubagentGrant`, mirrors `ToolGrant`

`novelizer/agents/registry_types.py` gains:

```python
@dataclass(frozen=True)
class SubagentGrant:
    """Declares which Settings field gates an agent's subagent access."""
    enabled_setting: str

    def is_enabled(self, settings: Any) -> bool:
        return bool(getattr(settings, self.enabled_setting))
```

`AgentSpec` gains `subagent_grant: SubagentGrant | None = None`. Each
tooled agent's module declares its own setting name in `SPEC`, the same
place `tool_grant` lives today. New settings fields
(`character_keeper_subagent_enabled`, etc.) follow the exact pattern of
`character_keeper_tools_enabled` in `novelizer/settings/models.py`,
`layers.py`, `loader.py` — default `False`.

`Runtime._tooled()`/each agent's `build_X_runner` extends: when the
agent's `SubagentGrant` is enabled (and its `ToolGrant` is also enabled —
otherwise the subagent flag is a no-op per Decision 6),
`create_deep_agent(...)` is called with
`subagents=[build_researcher_subagent(name, EXTRA_INSTRUCTIONS)]`
alongside the existing `backend`/`tools`/`skills` args.

### Telemetry — tag by `lc_agent_name` metadata, no new event types

`TelemetryCallbackHandler` (`novelizer/telemetry/callbacks.py`) already
receives `metadata: dict | None` as a kwarg on `on_tool_start`/
`on_llm_start`/etc. per LangChain's callback contract — currently
unread. Add one optional field to the emitted payloads:
`delegate = metadata.get("lc_agent_name")` (`"researcher"` when the call
originated inside a dispatched subagent, absent for the parent's own
calls). This is carried through `TelemetryRecorder.emit`/`publish`
unchanged — no new `EventType`; just an extra optional field on the
existing `ToolCallStarted`/`ToolCallFinished`/`ToolCallFailed` (and LLM
call) payloads. `agent_name` on these events stays the parent's identity
throughout (e.g. `"character_keeper"`) — `delegate` marks "this happened
inside a dispatched researcher call," it does not replace the parent's
identity.

### Engine Room rendering — indented delegate lines

In `engine_room_model.py`'s bus-item folding (`apply_bus_item`, near the
existing `TOOL_CALL_STARTED`/`FINISHED`/`FAILED` branches around line 97),
when a payload carries `delegate`, render with an indent + `↳` prefix
instead of the parent's own `⚒` line, reusing the existing tool-call
style (`bold cyan`, from `_LINE_STYLES`):

```
⚒ character_keeper: task(researcher: "check ch.12 for Mateo's debt mentions")
    ↳ researcher: read_file(/chapters/ch-0012.md)
    ↳ researcher: grep("Mateo")
⚒ character_keeper ← task ✓ (2.1s)
```

The `task` tool call itself needs no special-case — it's already
rendered today as an ordinary tool call on the parent. Only calls
carrying `delegate` get the indented treatment.

## Error handling

A subagent failure surfaces the same way any tool failure does today:
`on_tool_error` → `ToolCallFailed` (now optionally carrying `delegate`)
→ rendered as the existing failed-tool-call line, indented per the above
when `delegate` is present. No new failure path.

## Testing

Red/green TDD:

- **Unit — `SubagentGrant`**: `is_enabled` reads the declared settings
  field, mirrors existing `ToolGrant` tests.
- **Unit — `build_researcher_subagent`**: returns a `SubAgent` dict with
  expected `name`, `description`, and `system_prompt` (base + suffix);
  omits `tools`/`model` (inheritance contract, not something we assert
  values for).
- **Unit — `TelemetryCallbackHandler`**: `on_tool_start`/`on_tool_end`/
  `on_tool_error` with a `metadata={"lc_agent_name": "researcher"}` kwarg
  produce events carrying `delegate="researcher"`; without that metadata,
  `delegate` is absent/`None` — regression check that parent-only calls
  are unaffected.
- **Unit — agent runner builders** (one representative agent, e.g.
  Character Keeper): with the subagent grant enabled, `create_deep_agent`
  is called with a `subagents=[...]` kwarg containing the researcher spec;
  with the grant disabled (or tools disabled), no `subagents` kwarg.
- **Unit — settings guard**: subagent flag on + tools flag off resolves
  to no `subagents` kwarg (Decision 6), no error raised.
- **Unit — `engine_room_model.apply_bus_item`**: a `TOOL_CALL_STARTED`
  bus item with `delegate="researcher"` renders the indented `↳` line;
  the same event without `delegate` renders the existing unindented line
  — regression check against current Engine Room behavior for all seven
  autonomous agents.

## Out of scope

- Per-agent tool subsetting for the researcher subagent (Decision 3) —
  it always gets the parent's full canon-read toolkit.
- A separate/cheaper model for subagent dispatch (Decision 4) — always
  inherits the parent's model.
- Multiple distinct subagent roles per agent (e.g. a summarizer vs. a
  cross-checker) — one shared "researcher" role for all agents in this
  pass; a follow-up could add more roles later if a concrete need
  emerges.
- Settings-layer validation errors for the invalid subagent-on/tools-off
  combination (Decision 6) — silently a no-op instead.
- Rollout to chat/research personas' runners specifically — the registry
  mechanism (`SubagentGrant`) is generic and applies to the seven
  autonomous `AGENT_REGISTRY` agents; extending it to `chat/runners.py`/
  `research/runner.py` (which build runners outside the registry) is
  straightforward with the same `build_researcher_subagent` factory but
  is not enumerated task-by-task here — left to the implementation plan
  to decide the pilot scope (a single agent first, or all at once).
