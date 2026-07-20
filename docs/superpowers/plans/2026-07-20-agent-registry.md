# Agent Registry Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the nine hand-written agent-construction blocks in `Runtime.start()` with a declarative registry, so adding/removing/reordering a fiction-domain agent is a one-file change instead of edits across `runtime.py`, `settings.py`, and `_tooling_pinned`.

**Architecture:** Each agent module gains a module-level `SPEC = AgentSpec(...)` next to its class. `AgentSpec.construct(ctx)` is a small factory function, owned by that module, that builds the runner (via `ctx.tooled`/`ctx.runner_for`, which are `Runtime._tooled`/`Runtime._runner_for` passed through unchanged) and returns the constructed agent. `novelizer/agents/registry.py` imports all nine specs into one ordered `AGENT_REGISTRY` list — list order is scheduling order, same as today. `Runtime.start()` shrinks to a loop over the registry; `apply_settings()` is untouched.

**Deviation from the approved spec:** the spec's `AgentSpec` sketch used a uniform `extra_kwargs(ctx)` dict merged into one generic constructor call. Two agents don't fit that: `Muse.__init__` doesn't take a `runner` positional at all, and `ContinuityChecker.__init__` takes `(runner, mining_runner, read_store, committer, event_store, ...)` — a second runner and a different positional order entirely. Forcing both into one call shape would have meant special-casing them in `Runtime.start()` anyway, defeating the point. Giving each spec its own `construct(ctx) -> BaseAgent` factory (still declared in the registry, still one-file-to-add-an-agent) keeps the uniform *declaration* while letting real construction stay honest about each agent's actual signature. `ToolGrant` is unchanged from the spec.

**Tech Stack:** Python 3, pytest, existing `novelizer` package (no new dependencies).

## Global Constraints

- Event sourcing, DDD, SOLID, red/green + property-based TDD are non-negotiable house rules for this codebase.
- NEVER run the test suite in the main checkout — always in this worktree (`.claude/worktrees/agent-registry-design`). A prior DB-lock incident came from running tests against the shared checkout.
- No behavior change: `self.agents`, `self.author`, `self.editor`, etc., and the six-key `self._tooling_pinned` dict must end up identical in shape and values to what `Runtime.start()` produces today.
- Fiction domain only — no new agent, no external config file, no subagent spawning. Out of scope per the approved spec.

---

### Task 1: Registry primitives (`ToolGrant`, `AgentContext`, `AgentSpec`)

**Files:**
- Create: `novelizer/agents/registry_types.py`
- Test: `tests/agents/test_registry_types.py`

**Interfaces:**
- Produces: `ToolGrant(enabled_setting: str)` with `.is_enabled(settings) -> bool`.
- Produces: `AgentContext` dataclass with fields `read`, `committer`, `events`, `settings`, `casting_note: str`, `personalities: dict`, `provenance: dict`, `tooled: Callable`, `runner_for: Callable`.
- Produces: `AgentSpec(name: str, tool_grant: ToolGrant | None, construct: Callable[[AgentContext], BaseAgent])`, frozen dataclass.
- Consumes: nothing from other tasks — this is the leaf module every agent module and `registry.py` will import from.

- [ ] **Step 1: Write the failing test**

Create `tests/agents/test_registry_types.py`:

```python
from __future__ import annotations
import pytest
from novelizer.agents.registry_types import AgentContext, AgentSpec, ToolGrant


class _Settings:
    editor_tools_enabled = True
    checker_tools_enabled = False


def test_tool_grant_reads_named_setting_true():
    grant = ToolGrant(enabled_setting="editor_tools_enabled")
    assert grant.is_enabled(_Settings()) is True


def test_tool_grant_reads_named_setting_false():
    grant = ToolGrant(enabled_setting="checker_tools_enabled")
    assert grant.is_enabled(_Settings()) is False


def test_agent_context_holds_shared_construction_state():
    ctx = AgentContext(
        read="read_store", committer="committer", events="event_store",
        settings=_Settings(), casting_note="terse", personalities={"author": "wry"},
        provenance={"model": "x"}, tooled=lambda b, e: b, runner_for=lambda n, b, fallback_name=None: b,
    )
    assert ctx.casting_note == "terse"
    assert ctx.personalities["author"] == "wry"
    assert ctx.tooled(lambda: None, True)() is None


def test_agent_spec_is_frozen_and_holds_construct_callable():
    called = {}

    def construct(ctx):
        called["ctx"] = ctx
        return "agent-instance"

    spec = AgentSpec(name="author", tool_grant=None, construct=construct)
    assert spec.name == "author"
    result = spec.construct("fake-ctx")
    assert result == "agent-instance"
    assert called["ctx"] == "fake-ctx"
    with pytest.raises(Exception):
        spec.name = "editor"  # frozen dataclass rejects mutation
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/agents/test_registry_types.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'novelizer.agents.registry_types'`

- [ ] **Step 3: Write minimal implementation**

Create `novelizer/agents/registry_types.py`:

```python
from __future__ import annotations
from dataclasses import dataclass
from typing import Any, Callable


@dataclass(frozen=True)
class ToolGrant:
    """Declares which Settings field gates an agent's canon-fs tooling."""
    enabled_setting: str

    def is_enabled(self, settings: Any) -> bool:
        return bool(getattr(settings, self.enabled_setting))


@dataclass
class AgentContext:
    """Shared construction state passed to every AgentSpec.construct(ctx).

    `tooled` and `runner_for` are Runtime._tooled / Runtime._runner_for bound
    methods, passed through unchanged so each agent's construct() builds its
    runner(s) exactly the way runtime.py did before this registry existed.
    """
    read: Any
    committer: Any
    events: Any
    settings: Any
    casting_note: str
    personalities: dict
    provenance: dict
    tooled: Callable
    runner_for: Callable


@dataclass(frozen=True)
class AgentSpec:
    """One fiction-domain agent's declaration: its name, whether it can be
    tooled, and how to build it. `construct` owns full responsibility for
    that agent's actual (possibly non-uniform) constructor signature."""
    name: str
    tool_grant: ToolGrant | None
    construct: Callable[[AgentContext], Any]
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/agents/test_registry_types.py -v`
Expected: PASS (4 passed)

- [ ] **Step 5: Commit**

```bash
git add novelizer/agents/registry_types.py tests/agents/test_registry_types.py
git commit -m "feat(agents): add registry primitives (ToolGrant, AgentContext, AgentSpec)"
```

---

### Task 2: Per-agent `SPEC` + assembled `AGENT_REGISTRY`

**Files:**
- Modify: `novelizer/agents/world_architect.py`, `novelizer/agents/character_keeper.py`, `novelizer/agents/editor.py`, `novelizer/agents/continuity_checker.py`, `novelizer/agents/retconner.py`, `novelizer/agents/structure_analyst.py`, `novelizer/agents/plotter.py`, `novelizer/agents/muse.py`, `novelizer/agents/author.py` — append a `SPEC` constant to each, using each module's existing `build_*_runner` function and class (already imported in that file).
- Create: `novelizer/agents/registry.py`
- Test: `tests/agents/test_registry.py`

**Interfaces:**
- Consumes: `ToolGrant`, `AgentContext`, `AgentSpec` from `novelizer.agents.registry_types` (Task 1).
- Produces: `novelizer.agents.registry.AGENT_REGISTRY: list[AgentSpec]`, in this exact order: `["world_architect", "character_keeper", "muse", "plotter", "author", "editor", "continuity_checker", "retconner", "structure_analyst"]` — the same order `Runtime.start()` uses today for `self.agents`.
- Produces: each agent module exports `SPEC: AgentSpec` at module scope.

- [ ] **Step 1: Write the failing test**

Create `tests/agents/test_registry.py`:

```python
from __future__ import annotations
from novelizer.agents.registry import AGENT_REGISTRY
from novelizer.agents.registry_types import AgentSpec

EXPECTED_ORDER = [
    "world_architect", "character_keeper", "muse",
    "plotter", "author",
    "editor", "continuity_checker", "retconner", "structure_analyst",
]


def test_registry_has_nine_specs_in_scheduling_order():
    assert [spec.name for spec in AGENT_REGISTRY] == EXPECTED_ORDER


def test_registry_names_are_unique():
    names = [spec.name for spec in AGENT_REGISTRY]
    assert len(names) == len(set(names))


def test_every_entry_is_an_agent_spec_with_callable_construct():
    for spec in AGENT_REGISTRY:
        assert isinstance(spec, AgentSpec)
        assert callable(spec.construct)


def test_muse_has_no_tool_grant():
    muse_spec = next(spec for spec in AGENT_REGISTRY if spec.name == "muse")
    assert muse_spec.tool_grant is None


def test_tooled_agents_declare_the_correct_settings_field():
    expected = {
        "world_architect": "world_architect_tools_enabled",
        "character_keeper": "character_keeper_tools_enabled",
        "editor": "editor_tools_enabled",
        "continuity_checker": "checker_tools_enabled",
        "retconner": "retconner_tools_enabled",
        "structure_analyst": "structure_analyst_tools_enabled",
        "plotter": "plotter_tools_enabled",
        "author": "author_tools_enabled",
    }
    by_name = {spec.name: spec for spec in AGENT_REGISTRY}
    for name, setting in expected.items():
        assert by_name[name].tool_grant.enabled_setting == setting
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/agents/test_registry.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'novelizer.agents.registry'`

- [ ] **Step 3: Write minimal implementation**

Append to `novelizer/agents/world_architect.py` (after the `WorldArchitect` class):

```python
from novelizer.agents.registry_types import AgentContext, AgentSpec, ToolGrant


def _construct(ctx: AgentContext) -> WorldArchitect:
    enabled = ctx.settings.world_architect_tools_enabled
    builder = ctx.tooled(build_world_architect_runner, enabled)
    runner = ctx.runner_for("world_architect", builder)
    return WorldArchitect(
        runner, ctx.read, ctx.committer,
        interval=ctx.settings.default_agent_interval,
        personality=ctx.personalities.get("world_architect", ""),
    )


SPEC = AgentSpec(
    name="world_architect",
    tool_grant=ToolGrant(enabled_setting="world_architect_tools_enabled"),
    construct=_construct,
)
```

Append to `novelizer/agents/character_keeper.py`:

```python
from novelizer.agents.registry_types import AgentContext, AgentSpec, ToolGrant


def _construct(ctx: AgentContext) -> CharacterKeeper:
    enabled = ctx.settings.character_keeper_tools_enabled
    builder = ctx.tooled(build_character_keeper_runner, enabled)
    runner = ctx.runner_for("character_keeper", builder)
    return CharacterKeeper(
        runner, ctx.read, ctx.committer,
        interval=ctx.settings.default_agent_interval,
        personality=ctx.personalities.get("character_keeper", ""),
        prose_chars=ctx.settings.keeper_prose_chars,
        pull_mode=enabled,
    )


SPEC = AgentSpec(
    name="character_keeper",
    tool_grant=ToolGrant(enabled_setting="character_keeper_tools_enabled"),
    construct=_construct,
)
```

Append to `novelizer/agents/editor.py`:

```python
from novelizer.agents.registry_types import AgentContext, AgentSpec, ToolGrant


def _construct(ctx: AgentContext) -> Editor:
    enabled = ctx.settings.editor_tools_enabled
    builder = ctx.tooled(build_editor_runner, enabled)
    runner = ctx.runner_for("editor", builder)
    return Editor(
        runner, ctx.read, ctx.committer,
        interval=ctx.settings.default_agent_interval,
        casting_note=ctx.casting_note,
        personality=ctx.personalities.get("editor", ""),
        sag_spike_delta=ctx.settings.sag_spike_delta,
    )


SPEC = AgentSpec(
    name="editor",
    tool_grant=ToolGrant(enabled_setting="editor_tools_enabled"),
    construct=_construct,
)
```

Append to `novelizer/agents/continuity_checker.py`:

```python
from novelizer.agents.registry_types import AgentContext, AgentSpec, ToolGrant


def _construct(ctx: AgentContext) -> ContinuityChecker:
    enabled = ctx.settings.checker_tools_enabled
    builder = ctx.tooled(build_continuity_checker_runner, enabled)
    runner = ctx.runner_for("continuity_checker", builder)
    mining_runner = ctx.runner_for(
        "continuity_checker_mining", build_continuity_mining_runner,
        fallback_name="continuity_checker",
    )
    return ContinuityChecker(
        runner, mining_runner, ctx.read, ctx.committer, ctx.events,
        interval=ctx.settings.continuity_interval,
        personality=ctx.personalities.get("continuity_checker", ""),
        pull_mode=enabled,
    )


SPEC = AgentSpec(
    name="continuity_checker",
    tool_grant=ToolGrant(enabled_setting="checker_tools_enabled"),
    construct=_construct,
)
```

Append to `novelizer/agents/retconner.py`:

```python
from novelizer.agents.registry_types import AgentContext, AgentSpec, ToolGrant


def _construct(ctx: AgentContext) -> Retconner:
    enabled = ctx.settings.retconner_tools_enabled
    builder = ctx.tooled(build_retconner_runner, enabled)
    runner = ctx.runner_for("retconner", builder)
    return Retconner(
        runner, ctx.read, ctx.committer,
        interval=ctx.settings.default_agent_interval,
        personality=ctx.personalities.get("retconner", ""),
    )


SPEC = AgentSpec(
    name="retconner",
    tool_grant=ToolGrant(enabled_setting="retconner_tools_enabled"),
    construct=_construct,
)
```

Append to `novelizer/agents/structure_analyst.py`:

```python
from novelizer.agents.registry_types import AgentContext, AgentSpec, ToolGrant


def _construct(ctx: AgentContext) -> StructureAnalyst:
    enabled = ctx.settings.structure_analyst_tools_enabled
    builder = ctx.tooled(build_structure_analyst_runner, enabled)
    runner = ctx.runner_for("structure_analyst", builder)
    return StructureAnalyst(
        runner, ctx.read, ctx.committer,
        interval=ctx.settings.structure_analyst_interval,
        personality=ctx.personalities.get("structure_analyst", ""),
        pull_mode=enabled,
    )


SPEC = AgentSpec(
    name="structure_analyst",
    tool_grant=ToolGrant(enabled_setting="structure_analyst_tools_enabled"),
    construct=_construct,
)
```

Append to `novelizer/agents/plotter.py`:

```python
from novelizer.agents.registry_types import AgentContext, AgentSpec, ToolGrant


def _construct(ctx: AgentContext) -> Plotter:
    enabled = ctx.settings.plotter_tools_enabled
    builder = ctx.tooled(build_plotter_runner, enabled)
    runner = ctx.runner_for("plotter", builder)
    return Plotter(
        runner, ctx.read, ctx.committer,
        interval=ctx.settings.plotter_interval,
        personality=ctx.personalities.get("plotter", ""),
    )


SPEC = AgentSpec(
    name="plotter",
    tool_grant=ToolGrant(enabled_setting="plotter_tools_enabled"),
    construct=_construct,
)
```

Append to `novelizer/agents/muse.py`:

```python
from novelizer.agents.registry_types import AgentContext, AgentSpec


def _construct(ctx: AgentContext) -> Muse:
    return Muse(
        ctx.read, ctx.committer,
        interval=ctx.settings.muse_interval,
        era=ctx.settings.muse_era,
        exclusion_hands=ctx.settings.muse_exclusion_hands,
        personality=ctx.personalities.get("muse", ""),
    )


SPEC = AgentSpec(name="muse", tool_grant=None, construct=_construct)
```

Append to `novelizer/agents/author.py`:

```python
from novelizer.agents.registry_types import AgentContext, AgentSpec, ToolGrant


def _construct(ctx: AgentContext) -> Author:
    enabled = ctx.settings.author_tools_enabled
    builder = ctx.tooled(build_author_runner, enabled)
    runner = ctx.runner_for("author", builder)
    return Author(
        runner, ctx.read, ctx.committer,
        interval=ctx.settings.author_interval,
        casting_note=ctx.casting_note,
        personality=ctx.personalities.get("author", ""),
        provenance=ctx.provenance,
        prior_chapter_summary_chars=ctx.settings.prior_chapter_summary_chars,
        staleness_threshold_chapters=ctx.settings.staleness_threshold_chapters,
        pull_mode=enabled,
    )


SPEC = AgentSpec(
    name="author",
    tool_grant=ToolGrant(enabled_setting="author_tools_enabled"),
    construct=_construct,
)
```

Create `novelizer/agents/registry.py`:

```python
from __future__ import annotations
from novelizer.agents import (
    author, world_architect, character_keeper, editor,
    continuity_checker, retconner, structure_analyst, plotter, muse,
)
from novelizer.agents.registry_types import AgentSpec

# List order is scheduling order -- the same order Runtime.start() built
# self.agents in before this registry existed. The planner (plotter) ticks
# before the writer (author) in a fresh room; keep that ordering intact.
AGENT_REGISTRY: list[AgentSpec] = [
    world_architect.SPEC, character_keeper.SPEC, muse.SPEC,
    plotter.SPEC, author.SPEC,
    editor.SPEC, continuity_checker.SPEC, retconner.SPEC, structure_analyst.SPEC,
]
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/agents/test_registry.py tests/agents/test_registry_types.py -v`
Expected: PASS (9 passed)

- [ ] **Step 5: Commit**

```bash
git add novelizer/agents/registry.py novelizer/agents/world_architect.py \
  novelizer/agents/character_keeper.py novelizer/agents/editor.py \
  novelizer/agents/continuity_checker.py novelizer/agents/retconner.py \
  novelizer/agents/structure_analyst.py novelizer/agents/plotter.py \
  novelizer/agents/muse.py novelizer/agents/author.py tests/agents/test_registry.py
git commit -m "feat(agents): give each agent a SPEC, assemble AGENT_REGISTRY"
```

---

### Task 3: Refactor `Runtime.start()` to build agents from `AGENT_REGISTRY`

**Files:**
- Modify: `novelizer/runtime.py:172-248` (the `_canon_backend`/`_tooling_pinned`/provenance/nine-agent-construction/`self.agents` block inside `start()`)
- Test: existing `tests/test_runtime.py`, `tests/test_apply_settings.py` (no new test file — this task's job is to prove zero behavior change against the suite that already pins this behavior)

**Interfaces:**
- Consumes: `AGENT_REGISTRY` from `novelizer.agents.registry` (Task 2), `AgentContext` from `novelizer.agents.registry_types` (Task 1).
- Produces: no new interface — `self.agents`, `self.agents_by_name` (new, internal), `self.author`/`self.world_architect`/.../`self.muse`, and `self._tooling_pinned` must remain byte-for-byte equivalent to today's output for every existing caller (`apply_settings`, `Scheduler`, `ChatService`, TUI, tests).

- [ ] **Step 1: Run the existing regression suite to confirm the baseline passes before touching runtime.py**

Run: `pytest tests/test_runtime.py tests/test_apply_settings.py tests/agents/ -v`
Expected: PASS (all green) — this is the safety net Task 3 must not break.

- [ ] **Step 2: Replace the construction block in `novelizer/runtime.py`**

In `novelizer/runtime.py`, add the import near the top (with the other `novelizer.agents.*` imports):

```python
from novelizer.agents.registry import AGENT_REGISTRY
from novelizer.agents.registry_types import AgentContext
```

Replace the entire block from `self._canon_backend, self._canon_tools = self._phase_a_toolkit()` through the `self.agents = [...]` assignment (currently `novelizer/runtime.py:172-248`) with:

```python
        self._canon_backend, self._canon_tools = self._phase_a_toolkit()
        provenance = {
            "model": s.author_model,
            "temperature": s.author_temperature,
            "voice_pack": self.voice_pack.name,
            "prose_profile": s.prose_profile,
        }
        ctx = AgentContext(
            read=self.read, committer=self.committer, events=self.events, settings=s,
            casting_note=casting_note, personalities=personalities, provenance=provenance,
            tooled=self._tooled, runner_for=self._runner_for,
        )
        self._tooling_pinned = {
            spec.name: spec.tool_grant.is_enabled(s)
            for spec in AGENT_REGISTRY if spec.tool_grant is not None
        }
        self.agents_by_name = {spec.name: spec.construct(ctx) for spec in AGENT_REGISTRY}
        self.world_architect = self.agents_by_name["world_architect"]
        self.character_keeper = self.agents_by_name["character_keeper"]
        self.muse = self.agents_by_name["muse"]
        self.plotter = self.agents_by_name["plotter"]
        self.author = self.agents_by_name["author"]
        self.editor = self.agents_by_name["editor"]
        self.continuity_checker = self.agents_by_name["continuity_checker"]
        self.retconner = self.agents_by_name["retconner"]
        self.structure_analyst = self.agents_by_name["structure_analyst"]
        # the planner ticks before the writer in a fresh room -- AGENT_REGISTRY
        # order encodes scheduling order, same as this list did before.
        self.agents = [self.agents_by_name[spec.name] for spec in AGENT_REGISTRY]
```

Leave everything else in `start()` unchanged (telemetry wiring, `Scheduler(...)`, `ChatService(...)` construction stay exactly as they are today, right after this block).

Do **not** touch `apply_settings()` — it still imports `build_author_runner`, `build_world_architect_runner`, etc. directly at the top of `runtime.py` and reads `self._tooling_pinned["world_architect"]` etc. Both keep working unchanged: the six-key shape of `self._tooling_pinned` (`world_architect`, `character_keeper`, `editor`, `retconner`, `structure_analyst`, `plotter`) is preserved exactly, since only those six specs carry a `tool_grant` in `AGENT_REGISTRY` — `author` and `continuity_checker` track their own tooling via `.pull_mode` (read directly off the agent instance in `apply_settings`, e.g. `self.author.pull_mode`), and `muse` has no `tool_grant` at all. This matches today's dict exactly.

- [ ] **Step 3: Run the regression suite again to confirm no behavior changed**

Run: `pytest tests/test_runtime.py tests/test_apply_settings.py tests/agents/ tests/chat/ tests/director/ tests/tui/ tests/canon_fs/test_skills_seam.py -v`
Expected: PASS (all green, identical pass count to Step 1 plus the new `tests/agents/` tests from Tasks 1–2)

- [ ] **Step 4: Run the full test suite as a final check**

Run: `pytest -q`
Expected: PASS, no regressions anywhere in the suite.

- [ ] **Step 5: Commit**

```bash
git add novelizer/runtime.py
git commit -m "refactor(runtime): build the agent fleet from AGENT_REGISTRY"
```

---

## Self-Review Notes

- **Spec coverage:** `AgentSpec`/`ToolGrant` (Task 1) ✓, self-registered `SPEC` per module + assembled ordered registry (Task 2) ✓, `Runtime.start()` loop replacing the nine hand-written blocks while `apply_settings()` stays untouched (Task 3) ✓. The `AgentContext`/`construct(ctx)` approach documented under "Deviation from the approved spec" above supersedes the spec's single generic `extra_kwargs` dict — required once `Muse` (no runner) and `ContinuityChecker` (second runner, different positional order) were checked against their real `__init__` signatures.
- **Placeholder scan:** none — every step has complete, real code copied from or matching the current `runtime.py`/agent module signatures.
- **Type consistency:** `AgentContext` field names (`tooled`, `runner_for`, `provenance`, `casting_note`, `personalities`, `events`) are used identically across Task 1's definition, every Task 2 `_construct` function, and Task 3's `Runtime.start()` wiring.
