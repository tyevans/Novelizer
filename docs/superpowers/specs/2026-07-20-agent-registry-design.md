# Agent registry: declarative roster + tool grants

Status: approved, not yet implemented.

## Problem

`Runtime.start()` (`novelizer/runtime.py:146-260`) hand-wires all nine agents
imperatively. Each construction is a bespoke block: a `_tooled(...)` wrapper
call, a constructor with agent-specific kwargs pulled from `Settings`, and a
line appended to the final `self.agents = [...]` list that defines scheduling
order. Tool/MCP grants are tracked separately in `self._tooling_pinned`, keyed
by string against `s.<name>_tools_enabled` settings fields.

Adding, removing, or reordering an agent today means editing `runtime.py`,
`settings.py`, and (if tooled) the `_tooling_pinned` dict — three places for
one conceptual change. There's no single seam that answers "what agents
exist, what do they need, in what order do they run."

This is scoped narrowly: **fiction domain only, no new use case.** The goal
is to move the fleet definition toward a SOLID (Open/Closed) shape so that a
future domain (research, software-project canon, etc.) becomes a config
problem later, not a rewrite. No behavior changes; this is a structural
refactor.

## Design

### AgentSpec

A frozen dataclass in `novelizer/agents/registry.py`:

```python
@dataclass(frozen=True)
class AgentSpec:
    name: str                          # "author", "editor", ...
    agent_class: type[BaseAgent]
    build_runner: Callable             # build_author_runner, etc.
    tool_grant: ToolGrant | None       # None = never tooled
    interval_setting: str              # attribute name on Settings
    extra_kwargs: Callable[[AgentContext], dict]  # bespoke ctor kwargs
    fallback_name: str | None = None   # e.g. checker's mining runner -> checker
```

Each agent module self-registers by exposing a module-level `SPEC =
AgentSpec(...)` next to its class. Adding a new fiction-domain agent means:
new module + one line adding it to the explicit registry list in
`registry.py` — not touching `runtime.py`.

`extra_kwargs` takes a small `AgentContext` (read_store, committer, settings,
casting_note, personalities) and returns the same bespoke dict each agent
already receives today (`casting_note`, `sag_spike_delta`, `prose_chars`,
`prior_chapter_summary_chars`, `pull_mode`, etc.). This keeps each agent's
real heterogeneity typed and local to its own module, rather than forcing a
lowest-common-denominator shared schema.

### ToolGrant

Replaces the current `_tooling_pinned` dict + scattered
`s.<name>_tools_enabled` lookups:

```python
@dataclass(frozen=True)
class ToolGrant:
    enabled_setting: str   # e.g. "editor_tools_enabled"

    def is_enabled(self, settings) -> bool:
        return bool(getattr(settings, self.enabled_setting))
```

Today every tooled agent shares the same canon backend (`_phase_a_toolkit()`
output), so `ToolGrant` only needs to say *whether* an agent is tooled. The
shape leaves room to later name specific backends/MCP servers per agent
without forcing that generalization now — YAGNI applies to the "which tools"
axis, not the "is tooled" axis, since the latter is exactly what
`Runtime.start()` already branches on today.

### Registry list and scheduling order

```python
AGENT_REGISTRY: list[AgentSpec] = [
    world_architect.SPEC, character_keeper.SPEC, muse.SPEC,
    plotter.SPEC, author.SPEC,
    editor.SPEC, continuity_checker.SPEC, retconner.SPEC, structure_analyst.SPEC,
]
```

The registry's list order **is** the scheduling order — same as today's
`self.agents = [...]` literal, just relocated. Reordering stays a visible
one-line diff, not implied by dict insertion order.

### Runtime.start() refactor

```python
self.agents_by_name = {}
ctx = AgentContext(
    read=self.read, committer=self.committer, settings=s,
    casting_note=casting_note, personalities=personalities,
)
for spec in AGENT_REGISTRY:
    enabled = spec.tool_grant.is_enabled(s) if spec.tool_grant else False
    builder = self._tooled(spec.build_runner, enabled)
    runner = self._runner_for(spec.name, builder, fallback_name=spec.fallback_name)
    agent = spec.agent_class(
        runner, self.read, self.committer,
        interval=getattr(s, spec.interval_setting),
        personality=personalities.get(spec.name, ""),
        **spec.extra_kwargs(ctx),
    )
    self.agents_by_name[spec.name] = agent

self.agents = [self.agents_by_name[spec.name] for spec in AGENT_REGISTRY]
self.author = self.agents_by_name["author"]
self.editor = self.agents_by_name["editor"]
# ... one assignment per existing self.<name> attribute, unchanged externally
```

Muse (non-LLM, no tool grant, no `pull_mode`) and the Continuity Checker's
second "mining" runner (which falls back to the checker's own injected fake
in tests via `fallback_name`) are documented exceptions inside their own
`AgentSpec` entries — not contorted into a forced-uniform shape. Being
explicit about the two edge cases is more honest than a "clever" abstraction
that hides them.

`self.author`, `self.editor`, etc. — read elsewhere by chat, the TUI, and
tests — stay as instance attributes, just assigned from
`self.agents_by_name` after the loop. Nothing outside `runtime.py` changes.

## What doesn't change

- `Settings` schema, `BaseAgent`, individual agent classes' `__init__`
  signatures, prompts, `Scheduler`, chat personas.
- Scheduling order (still an explicit list, now living in `registry.py`
  instead of inline in `runtime.py`).
- Runtime behavior — this is a structural refactor, not a feature. Existing
  runtime/agent/TUI tests should pass unmodified; they assert on
  `runtime.author`, `runtime.editor`, etc. and on scheduling behavior, not on
  `runtime.py`'s internals.

## Risks & mitigations

- **`_runner_for`'s fallback logic.** The checker's mining runner falls back
  to the checker's own injected fake (`fallback_name="continuity_checker"`,
  `novelizer/runtime.py:73-80`) so TUI tests don't hang on live connection
  attempts. Mitigation: carry `fallback_name` explicitly on the mining
  agent's spec; run the full runtime/TUI test suite before merging, in a
  worktree — never the main checkout (standing rule after the DB-lock
  incident).
- **`extra_kwargs` becoming a dumping ground.** Mitigation: each agent module
  owns its own `extra_kwargs` function beside its class, not a shared file —
  keeps bespoke logic local to the agent that needs it.
- **Registry import cycles.** `registry.py` imports every agent module, and
  each agent module imports `AgentSpec`/`AgentContext`/`ToolGrant` from
  `registry.py`. Mitigation: `AgentSpec`, `ToolGrant`, and `AgentContext`
  live in a leaf module (`novelizer/agents/registry_types.py`) with no
  imports of agent modules; `registry.py` imports both the types and the
  agent modules and assembles `AGENT_REGISTRY`.

## Out of scope (explicitly deferred)

- Any second domain (research, software-project canon, etc.).
- Naming specific tools/MCP servers per agent in `ToolGrant` — deferred until
  a second domain or a genuinely heterogeneous tool need exists.
- Subagent spawning / agent-to-agent delegation.
- External (YAML/JSON) config for the roster — the registry stays Python,
  loaded at import time, not parsed at runtime.
