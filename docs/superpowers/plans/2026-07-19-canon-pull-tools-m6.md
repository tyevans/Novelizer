# CPT-M6: Phase-b All Agents Pull Tools — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** The remaining five scheduled agents (WorldArchitect, CharacterKeeper, Editor, Retconner, StructureAnalyst) get canon pull tools; `write_todos` becomes Author-only; the StructureAnalyst scores from full prose instead of 400-char excerpts.

**Architecture:** All five bare builders share one shape (agent_model + agent_temperature + own prompt + own response_format), so tooled construction goes through one shared factory (`novelizer/agents/toolkit.py`) instead of five copies. Bare (backend-less) paths stay byte-identical. Push diet: only the StructureAnalyst changes its prompt (its 400-char excerpts were its *scoring input*, not context — pull mode has it read full chapters). Editor/Keeper keep their object-of-work prose push (the Keeper's full-prose push is the deliberate 598e078 fix — do NOT diet it); WorldArchitect/Retconner push no chapter prose today. Runtime wires each agent through the existing `_tooled` wrapper behind per-agent flags.

**Tech Stack:** Python 3.13, deepagents 0.6.12, langchain 1.x, pydantic v2, pytest + pytest-asyncio (asyncio_mode auto).

## Global Constraints

- Spec: `docs/superpowers/specs/2026-07-19-canon-pull-tools-design.md` (§ "Phase b"); ladder: `docs/superpowers/plans/2026-07-19-canon-pull-tools-milestones.md`.
- Depends on CPT-M5 being merged (uses `ExcludeToolsMiddleware` from `novelizer/agents/middleware.py`).
- Writes stay on the intent path; nothing here touches the write path.
- Bare-path prompts and builder behavior must remain byte-identical when flags are off.
- Do not refactor the Author/chat builders to use the new factory in this milestone (phase-a/M5 code is reviewed and closed; consolidation is a future cleanup). The single sanctioned phase-a modification: adding todos-exclusion to the Continuity Checker's tooled path (Task 4) — that IS this milestone's ladder deliverable.
- **Run all tests in this worktree, NEVER the main checkout.** Use `uv run pytest <path> -v`.

---

### Task 1: Five per-agent settings flags

**Files:**
- Modify: `novelizer/settings/models.py` (STORY_OVERRIDABLE_KEYS ~line 18; fields ~line 85)
- Modify: `novelizer/settings/loader.py` (~line 55)
- Modify: `novelizer/settings/layers.py` (both layer classes, ~lines 51 and 79)
- Test: `tests/settings/test_models.py`, `tests/settings/test_layers.py`

**Interfaces:**
- Produces: `EffectiveSettings.architect_tools_enabled/keeper_tools_enabled/editor_tools_enabled/retconner_tools_enabled/analyst_tools_enabled: bool = True`. Task 5 reads them in `Runtime`.

- [ ] **Step 1: Write the failing tests**

Append to `tests/settings/test_models.py`:

```python
def test_phase_b_tools_flags_default_true_and_are_story_overridable():
    from novelizer.settings.models import EffectiveSettings, STORY_OVERRIDABLE_KEYS
    s = EffectiveSettings()
    for flag in ("architect_tools_enabled", "keeper_tools_enabled", "editor_tools_enabled",
                 "retconner_tools_enabled", "analyst_tools_enabled"):
        assert getattr(s, flag) is True, flag
        assert flag in STORY_OVERRIDABLE_KEYS, flag
```

Append to `tests/settings/test_layers.py`:

```python
def test_phase_b_tools_flags_flow_through_layers():
    from novelizer.settings.layers import GlobalFileSettings, StoryFileSettings
    for flag in ("architect_tools_enabled", "keeper_tools_enabled", "editor_tools_enabled",
                 "retconner_tools_enabled", "analyst_tools_enabled"):
        assert getattr(GlobalFileSettings(**{flag: False}), flag) is False, flag
        assert getattr(StoryFileSettings(**{flag: False}), flag) is False, flag
```

(If the layer classes have different names, use the two classes that declare `chat_tools_enabled: bool | None = None` after CPT-M5.)

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/settings/ -v -k phase_b`
Expected: FAIL (unknown fields)

- [ ] **Step 3: Implement**

`novelizer/settings/models.py` — STORY_OVERRIDABLE_KEYS gains one line beside the existing flags:

```python
    "architect_tools_enabled", "keeper_tools_enabled", "editor_tools_enabled",
    "retconner_tools_enabled", "analyst_tools_enabled",
```

`EffectiveSettings` fields, beside `chat_tools_enabled`:

```python
    architect_tools_enabled: bool = True
    keeper_tools_enabled: bool = True
    editor_tools_enabled: bool = True
    retconner_tools_enabled: bool = True
    analyst_tools_enabled: bool = True
```

`novelizer/settings/loader.py` and both classes in `novelizer/settings/layers.py`:

```python
    architect_tools_enabled: bool | None = None
    keeper_tools_enabled: bool | None = None
    editor_tools_enabled: bool | None = None
    retconner_tools_enabled: bool | None = None
    analyst_tools_enabled: bool | None = None
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/settings/ tests/test_apply_settings.py -v`
Expected: all PASS

- [ ] **Step 5: Commit**

```bash
git add novelizer/settings/ tests/settings/
git commit -m "feat(settings): phase-b per-agent tools flags"
```

---

### Task 2: Shared tooled-builder factory

**Files:**
- Create: `novelizer/agents/toolkit.py`
- Test: `tests/agents/test_toolkit.py`

**Interfaces:**
- Consumes: `ExcludeToolsMiddleware` (`novelizer/agents/middleware.py`, CPT-M5), `RETRIEVAL_NOTE` (`novelizer/agents/author.py:19`), `build_chat_model` (`novelizer/agents/llm.py`).
- Produces: `build_pull_runner(settings, system_prompt, response_format, callbacks=None, backend=None, tools=None)` — Task 3's five builders delegate their tooled path to it.

- [ ] **Step 1: Write the failing tests**

```python
# tests/agents/test_toolkit.py
from pydantic import BaseModel

from novelizer.agents.toolkit import build_pull_runner


class FakeSettings:
    agent_model = "gpt-4o-mini"
    llm_base_url = None
    llm_api_key = "test-key"
    agent_temperature = 0.7
    llm_max_tokens = None


class Out(BaseModel):
    note: str = ""


class FakeBackend:
    pass


def test_build_pull_runner_builds():
    runner = build_pull_runner(FakeSettings(), "PROMPT", Out, backend=FakeBackend(), tools=[])
    assert runner is not None


def test_build_pull_runner_adds_retrieval_note_config_and_todos_exclusion(monkeypatch):
    from novelizer.agents.author import RETRIEVAL_NOTE
    from novelizer.agents.middleware import ExcludeToolsMiddleware
    captured = {}

    class FakeGraph:
        def with_config(self, config):
            captured["config"] = config
            return self

    def fake_create_deep_agent(**kwargs):
        captured["kwargs"] = kwargs
        return FakeGraph()

    monkeypatch.setattr("deepagents.create_deep_agent", fake_create_deep_agent)
    sentinel_cb = object()
    build_pull_runner(FakeSettings(), "PROMPT", Out,
                      callbacks=[sentinel_cb], backend=FakeBackend(), tools=[])
    assert captured["kwargs"]["system_prompt"] == "PROMPT" + RETRIEVAL_NOTE
    assert captured["kwargs"]["response_format"] is Out
    assert any(isinstance(m, ExcludeToolsMiddleware) for m in captured["kwargs"]["middleware"])
    assert captured["config"]["recursion_limit"] == 50
    assert captured["config"]["callbacks"] == [sentinel_cb]


def test_build_pull_runner_without_callbacks_still_bounds_recursion(monkeypatch):
    captured = {}

    class FakeGraph:
        def with_config(self, config):
            captured["config"] = config
            return self

    def fake_create_deep_agent(**kwargs):
        return FakeGraph()

    monkeypatch.setattr("deepagents.create_deep_agent", fake_create_deep_agent)
    build_pull_runner(FakeSettings(), "PROMPT", Out, backend=FakeBackend(), tools=[])
    assert captured["config"] == {"recursion_limit": 50}
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/agents/test_toolkit.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'novelizer.agents.toolkit'`

- [ ] **Step 3: Implement**

```python
# novelizer/agents/toolkit.py
from __future__ import annotations


def build_pull_runner(settings, system_prompt: str, response_format,
                      callbacks=None, backend=None, tools=None):
    """Tooled deep-agent construction shared by the phase-b agents: canon
    backend + tools, retrieval note, Author-only todos (excluded here),
    recursion bound, telemetry callbacks bound graph-scope (tool executions
    run in the graph's ToolNode under invoke-time config, not constructor
    callbacks on the chat model)."""
    from deepagents import create_deep_agent
    from novelizer.agents.author import RETRIEVAL_NOTE
    from novelizer.agents.llm import build_chat_model
    from novelizer.agents.middleware import ExcludeToolsMiddleware
    model = build_chat_model(
        settings.agent_model, settings.llm_base_url, settings.llm_api_key,
        settings.agent_temperature, max_tokens=settings.llm_max_tokens,
        callbacks=None, streaming=callbacks is not None,
    )
    graph = create_deep_agent(
        model=model, system_prompt=system_prompt + RETRIEVAL_NOTE,
        response_format=response_format, backend=backend, tools=tools,
        middleware=[ExcludeToolsMiddleware(excluded=frozenset({"write_todos"}))],
    )
    config = {"recursion_limit": 50}
    if callbacks:
        config["callbacks"] = callbacks
    return graph.with_config(config)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/agents/test_toolkit.py -v`
Expected: 3 PASS

- [ ] **Step 5: Commit**

```bash
git add novelizer/agents/toolkit.py tests/agents/test_toolkit.py
git commit -m "feat(agents): shared tooled-runner factory for phase-b agents"
```

---

### Task 3: Five builders gain the tooled path

**Files:**
- Modify: `novelizer/agents/world_architect.py` (builder ~line 62)
- Modify: `novelizer/agents/character_keeper.py` (builder ~line 145)
- Modify: `novelizer/agents/editor.py` (builder ~line 154)
- Modify: `novelizer/agents/retconner.py` (builder ~line 83)
- Modify: `novelizer/agents/structure_analyst.py` (builder ~line 79)
- Test: `tests/agents/test_phase_b_builders.py` (new file)

**Interfaces:**
- Consumes: `build_pull_runner` from Task 2.
- Produces: each `build_X_runner(settings, callbacks=None, backend=None, tools=None)`. Task 5's `Runtime._tooled` calls them with all four.

- [ ] **Step 1: Write the failing tests**

```python
# tests/agents/test_phase_b_builders.py
import pytest

from novelizer.agents.world_architect import build_world_architect_runner
from novelizer.agents.character_keeper import build_character_keeper_runner
from novelizer.agents.editor import build_editor_runner
from novelizer.agents.retconner import build_retconner_runner
from novelizer.agents.structure_analyst import build_structure_analyst_runner

BUILDERS = [
    build_world_architect_runner, build_character_keeper_runner,
    build_editor_runner, build_retconner_runner, build_structure_analyst_runner,
]


class FakeSettings:
    agent_model = "gpt-4o-mini"
    llm_base_url = None
    llm_api_key = "test-key"
    agent_temperature = 0.7
    llm_max_tokens = None


class FakeBackend:
    pass


@pytest.mark.parametrize("builder", BUILDERS)
def test_builder_without_backend_stays_constructible(builder):
    assert builder(FakeSettings()) is not None


@pytest.mark.parametrize("builder", BUILDERS)
def test_builder_with_backend_routes_through_pull_factory(builder, monkeypatch):
    captured = {}

    def fake_build_pull_runner(settings, system_prompt, response_format,
                               callbacks=None, backend=None, tools=None):
        captured["system_prompt"] = system_prompt
        captured["backend"] = backend
        captured["tools"] = tools
        captured["callbacks"] = callbacks
        return "tooled-runner"

    monkeypatch.setattr("novelizer.agents.toolkit.build_pull_runner", fake_build_pull_runner)
    backend = FakeBackend()
    sentinel_cb = object()
    result = builder(FakeSettings(), callbacks=[sentinel_cb], backend=backend, tools=[])
    assert result == "tooled-runner"
    assert captured["backend"] is backend
    assert captured["tools"] == []
    assert captured["callbacks"] == [sentinel_cb]
    assert len(captured["system_prompt"]) > 0
```

Monkeypatch note: the builders must import the factory lazily *inside* the function via `from novelizer.agents import toolkit` + `toolkit.build_pull_runner(...)` (attribute access, shown in Step 3) so this patch of `novelizer.agents.toolkit.build_pull_runner` is seen.

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/agents/test_phase_b_builders.py -v`
Expected: `test_builder_without_backend_stays_constructible` PASS (builders exist); `..._routes_through_pull_factory` FAIL with `TypeError: ... unexpected keyword argument 'backend'`

- [ ] **Step 3: Implement**

Apply the same mechanical change to all five builders. Example for `novelizer/agents/world_architect.py` (repeat with each module's own `SYSTEM_PROMPT` and response_format — Keeper: `KeeperOutput`, Editor: `EditorVerdict`, Retconner: `RetconAmendments`, StructureAnalyst: `StructureAnalystOutput`):

```python
def build_world_architect_runner(settings, callbacks=None, backend=None, tools=None):
    from deepagents import create_deep_agent
    from novelizer.agents.llm import build_chat_model
    if backend is not None:
        from novelizer.agents import toolkit
        return toolkit.build_pull_runner(
            settings, SYSTEM_PROMPT, WorldEntriesDraft,
            callbacks=callbacks, backend=backend, tools=tools,
        )
    model = build_chat_model(settings.agent_model, settings.llm_base_url, settings.llm_api_key, settings.agent_temperature, max_tokens=settings.llm_max_tokens, callbacks=callbacks)
    return create_deep_agent(model=model, system_prompt=SYSTEM_PROMPT, response_format=WorldEntriesDraft)
```

The bare path (backend is None) is the existing body **unchanged** — do not touch the existing `model = ...` / `return create_deep_agent(...)` lines.

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/agents/test_phase_b_builders.py tests/agents/ -v`
Expected: all PASS (including each agent's pre-existing tests)

- [ ] **Step 5: Commit**

```bash
git add novelizer/agents/world_architect.py novelizer/agents/character_keeper.py novelizer/agents/editor.py novelizer/agents/retconner.py novelizer/agents/structure_analyst.py tests/agents/test_phase_b_builders.py
git commit -m "feat(agents): phase-b builders gain canon pull tooling"
```

---

### Task 4: Author-only todos — exclude `write_todos` from the Checker's tooled path

**Files:**
- Modify: `novelizer/agents/continuity_checker.py` (tooled branch of `build_continuity_checker_runner`, ~lines 368-392)
- Test: `tests/agents/test_continuity_checker.py`

**Interfaces:**
- Consumes: `ExcludeToolsMiddleware` (CPT-M5).
- Produces: nothing new — behavior change only. After this task, `write_todos` reaches the model only in the Author's runner.

- [ ] **Step 1: Write the failing test**

Append to `tests/agents/test_continuity_checker.py` (reuse that file's existing FakeSettings-style class if one exists — read the builder tests already present from phase a and mirror their monkeypatch shape):

```python
def test_build_checker_runner_with_backend_excludes_write_todos(monkeypatch):
    from novelizer.agents.continuity_checker import build_continuity_checker_runner
    from novelizer.agents.middleware import ExcludeToolsMiddleware
    captured = {}

    class FakeGraph:
        def with_config(self, config): return self

    def fake_create_deep_agent(**kwargs):
        captured["kwargs"] = kwargs
        return FakeGraph()

    monkeypatch.setattr("deepagents.create_deep_agent", fake_create_deep_agent)

    class FakeSettings:
        agent_model = "gpt-4o-mini"
        llm_base_url = None
        llm_api_key = "test-key"
        agent_temperature = 0.7
        llm_max_tokens = None

    class FakeBackend: pass

    build_continuity_checker_runner(FakeSettings(), backend=FakeBackend(), tools=[])
    mws = captured["kwargs"].get("middleware", [])
    assert any(isinstance(m, ExcludeToolsMiddleware) for m in mws)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/agents/test_continuity_checker.py -v -k excludes_write_todos`
Expected: FAIL — no `middleware` kwarg captured

- [ ] **Step 3: Implement**

In the `backend is not None` branch of `build_continuity_checker_runner`, add the middleware to the existing `create_deep_agent` call:

```python
        from novelizer.agents.middleware import ExcludeToolsMiddleware
        graph = create_deep_agent(
            model=model, system_prompt=system_prompt, response_format=ContinuityOutput,
            backend=backend, tools=tools,
            middleware=[ExcludeToolsMiddleware(excluded=frozenset({"write_todos"}))],
        )
```

(Match the actual existing call at ~line 381-385 — only the `middleware=` line is new. The Author's builder is NOT touched: todos stay enabled there by design.)

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/agents/test_continuity_checker.py -v`
Expected: all PASS

- [ ] **Step 5: Commit**

```bash
git add novelizer/agents/continuity_checker.py tests/agents/test_continuity_checker.py
git commit -m "feat(checker): write_todos scoped to Author only"
```

---

### Task 5: StructureAnalyst full-prose scoring (pull-mode diet)

**Files:**
- Modify: `novelizer/agents/structure_analyst.py` (`__init__` ~line 25-30, `work` ~lines 47-55)
- Test: `tests/agents/test_structure_analyst.py`

**Interfaces:**
- Consumes: `chapter_map_note` (`novelizer/brain/context.py:85`).
- Produces: `StructureAnalyst(..., pull_mode: bool = False)`. Task 6 passes `pull_mode=s.analyst_tools_enabled`.

- [ ] **Step 1: Write the failing tests**

Append to `tests/agents/test_structure_analyst.py` (reuse the file's existing `stack`-style fixture and FakeRunner; seed one chapter whose prose contains a sentinel, e.g. `prose="secret prose text like a grudge"`):

```python
async def test_analyst_pull_mode_false_keeps_prose_excerpts(stack):
    ...
    analyst = StructureAnalyst(runner, read, committer, pull_mode=False)
    await analyst.run_once()
    sent = runner.calls[0]["messages"][0]["content"]
    assert "secret prose text" in sent


async def test_analyst_pull_mode_true_sends_map_and_read_instruction(stack):
    ...
    analyst = StructureAnalyst(runner, read, committer, pull_mode=True)
    await analyst.run_once()
    sent = runner.calls[0]["messages"][0]["content"]
    assert "secret prose text" not in sent
    assert "read each chapter in full" in sent.lower()
    # chapter map line shape (brain.context.chapter_map_note)
    assert "cast:" in sent
```

Fill `...` from the file's existing test setup (FakeRunner returning a `StructureAnalystOutput`, chapter appended via `events.append(EventType.CHAPTER_CREATED, ...)` + `proj.catch_up()`); match the constructor signature already used in that file (it may pass `interval`/`personality`).

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/agents/test_structure_analyst.py -v -k pull_mode`
Expected: FAIL — `TypeError: unexpected keyword argument 'pull_mode'`

- [ ] **Step 3: Implement**

`__init__` gains and stores the flag (mirror the Author's `pull_mode` param at `novelizer/agents/author.py:82,89`):

```python
    def __init__(self, runner, read_store, committer, interval: int = 300,
                 personality: str = "", pull_mode: bool = False) -> None:
        super().__init__(runner, read_store, committer, interval, name="structure_analyst", personality=personality)
        self.pull_mode = pull_mode
```

(Match the real current signature — keep any existing params exactly; only `pull_mode` is new.)

In `work()`, replace the `listing`/`msg` lines:

```python
        if self.pull_mode:
            from novelizer.brain.context import chapter_map_note
            listing = chapter_map_note(chapters)
            msg = (
                f"Score these chapters:\n{listing}{cast}\n"
                "Read each chapter in full (read_file on its /chapters/ path) before scoring — "
                "the list above is an index, not the prose."
            )
        else:
            listing = "\n\n".join(f"Chapter id:{c.id} '{c.title}': {c.prose[:400]}" for c in chapters)
            msg = f"Score these chapters:\n{listing}{cast}"
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/agents/test_structure_analyst.py -v`
Expected: all PASS

- [ ] **Step 5: Commit**

```bash
git add novelizer/agents/structure_analyst.py tests/agents/test_structure_analyst.py
git commit -m "feat(analyst): full-prose scoring via chapter map in pull mode"
```

---

### Task 6: Runtime wiring for the five agents

**Files:**
- Modify: `novelizer/runtime.py` (`start()` agent constructions ~lines 163-196; `apply_settings` rebuild branch ~lines 283-291)
- Test: `tests/test_runtime.py`, `tests/test_apply_settings.py`

**Interfaces:**
- Consumes: Tasks 1, 3, 5. Existing `Runtime._tooled(builder, enabled)` (runtime.py:98) already returns a backend-carrying builder — reuse unchanged.
- Produces: all eight LLM agents run tooled when their flag is on.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_runtime.py` (mirror `test_runtime_flags_on_wire_pull_mode_for_author_and_checker` at line 465 — same fixture, same fake-runners dict):

```python
async def test_runtime_phase_b_flags_on_wire_tooled_builders(settings):
    rt = ...  # construct as the line-465 test does
    await rt.start()
    try:
        assert rt.structure_analyst.pull_mode is True
        # _tooled wraps builders only when enabled; the wrapper closes over the
        # canon backend. Absent a pull_mode attr on the other four, assert via
        # the builders Runtime used: flags on => the lambda wrapper, not the raw builder.
        assert rt._canon_backend is not None
    finally:
        await rt.close()


async def test_runtime_phase_b_flags_off_leave_agents_bare(settings):
    # settings variant with all five phase-b flags False
    ...
    assert rt.structure_analyst.pull_mode is False
```

Follow the *existing* observable style: the phase-a tests at lines 465-491 assert on `rt.author.pull_mode` / real-builder start completion. For the four agents without a `pull_mode` attribute, extend the flags-on test the same way phase a proved wiring: monkeypatch each `novelizer.runtime.build_X_runner` with a fake capturing `backend=`, construct Runtime with NO fake for that agent, `await rt.start()`, and assert `captured["backend"] is rt._canon_backend` when the flag is on and `is None` when off. One parametrizable helper in the test file keeps this compact:

```python
import novelizer.runtime as runtime_mod

@pytest.mark.parametrize("builder_name,flag", [
    ("build_world_architect_runner", "architect_tools_enabled"),
    ("build_character_keeper_runner", "keeper_tools_enabled"),
    ("build_editor_runner", "editor_tools_enabled"),
    ("build_retconner_runner", "retconner_tools_enabled"),
    ("build_structure_analyst_runner", "analyst_tools_enabled"),
])
async def test_runtime_flag_controls_backend_for_each_phase_b_agent(settings, monkeypatch, builder_name, flag):
    captured = {}
    def fake_builder(s, callbacks=None, backend=None, tools=None):
        captured["backend"] = backend
        class G:
            async def ainvoke(self, *_a, **_k): return {}
        return G()
    monkeypatch.setattr(runtime_mod, builder_name, fake_builder)
    # flags-on runtime (fixture default): construct with fakes for every OTHER agent
    rt = ...
    await rt.start()
    try:
        assert captured["backend"] is rt._canon_backend
    finally:
        await rt.close()
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_runtime.py -v -k phase_b`
Expected: FAIL — `captured["backend"] is None` (Runtime never passes a backend to these builders yet)

- [ ] **Step 3: Implement**

In `Runtime.start()` (novelizer/runtime.py:163-196), route each construction through `_tooled`, mirroring the author/checker lines exactly:

```python
        architect_builder = self._tooled(build_world_architect_runner, s.architect_tools_enabled)
        self.world_architect = WorldArchitect(
            self._runner_for("world_architect", architect_builder), self.read, self.committer,
            interval=s.default_agent_interval, personality=personalities.get("world_architect", ""),
        )
        keeper_builder = self._tooled(build_character_keeper_runner, s.keeper_tools_enabled)
        self.character_keeper = CharacterKeeper(
            self._runner_for("character_keeper", keeper_builder), self.read, self.committer,
            interval=s.default_agent_interval, personality=personalities.get("character_keeper", ""),
            prose_chars=s.keeper_prose_chars,
        )
        editor_builder = self._tooled(build_editor_runner, s.editor_tools_enabled)
        self.editor = Editor(
            self._runner_for("editor", editor_builder), self.read, self.committer,
            interval=s.default_agent_interval, casting_note=casting_note, personality=personalities.get("editor", ""),
            sag_spike_delta=s.sag_spike_delta,
        )
        retconner_builder = self._tooled(build_retconner_runner, s.retconner_tools_enabled)
        self.retconner = Retconner(
            self._runner_for("retconner", retconner_builder), self.read, self.committer,
            interval=s.default_agent_interval, personality=personalities.get("retconner", ""),
        )
        analyst_builder = self._tooled(build_structure_analyst_runner, s.analyst_tools_enabled)
        self.structure_analyst = StructureAnalyst(
            self._runner_for("structure_analyst", analyst_builder), self.read, self.committer,
            interval=s.structure_analyst_interval, personality=personalities.get("structure_analyst", ""),
            pull_mode=s.analyst_tools_enabled,
        )
```

(The mining runner line inside the ContinuityChecker construction stays untouched — mining is push-mode by design.)

In `apply_settings` (~lines 283-291), the `agent_temperature` rebuild branch must keep tooling, mirroring the author line at 281-282:

```python
        if "agent_temperature" in changed and rebuild:
            architect_builder = self._tooled(build_world_architect_runner, self.settings.architect_tools_enabled)
            self.world_architect._runner = architect_builder(stored, callbacks=self._llm_callbacks)
            keeper_builder = self._tooled(build_character_keeper_runner, self.settings.keeper_tools_enabled)
            self.character_keeper._runner = keeper_builder(stored, callbacks=self._llm_callbacks)
            editor_builder = self._tooled(build_editor_runner, self.settings.editor_tools_enabled)
            self.editor._runner = editor_builder(stored, callbacks=self._llm_callbacks)
            checker_builder = self._tooled(build_continuity_checker_runner, self.continuity_checker.pull_mode)
            self.continuity_checker._runner = checker_builder(stored, callbacks=self._llm_callbacks)
            self.continuity_checker._mining_runner = build_continuity_mining_runner(stored, callbacks=self._llm_callbacks)
            retconner_builder = self._tooled(build_retconner_runner, self.settings.retconner_tools_enabled)
            self.retconner._runner = retconner_builder(stored, callbacks=self._llm_callbacks)
            analyst_builder = self._tooled(build_structure_analyst_runner, self.settings.analyst_tools_enabled)
            self.structure_analyst._runner = analyst_builder(stored, callbacks=self._llm_callbacks)
```

- [ ] **Step 4: Write the apply_settings regression test**

Append to `tests/test_apply_settings.py`, mirroring `test_rebuild_keeps_author_tooled_when_flags_on` (line 113 — copy its structure):

```python
async def test_rebuild_keeps_phase_b_agents_tooled_when_flags_on(tmp_path, monkeypatch):
    # copy the line-113 test's runtime construction; change agent_temperature;
    # assert the analyst's rebuilt runner came from a builder that received backend=
    ...
```

- [ ] **Step 5: Run the affected suites**

Run: `uv run pytest tests/test_runtime.py tests/test_apply_settings.py tests/agents/ -v`
Expected: all PASS

- [ ] **Step 6: Commit**

```bash
git add novelizer/runtime.py tests/test_runtime.py tests/test_apply_settings.py
git commit -m "feat(runtime): phase-b agents wired with canon pull tools behind per-agent flags"
```

---

### Task 7: Docs + full-suite gate

**Files:**
- Modify: `docs/QUICKSTART.md` (mention the per-agent `*_tools_enabled` flags — find the settings/flags section and extend it; if none exists, add a short "Agent canon tools" subsection listing all eight flags and that defaults are on)
- Modify: `docs/superpowers/plans/2026-07-19-canon-pull-tools-milestones.md` (Status section)
- Modify: `docs/MILESTONES.md` (Phase 2 table: M6 Deep Read status)

- [ ] **Step 1: Update docs**

Ladder Status addition:

```markdown
- **CPT-M6: delivered** (2026-07-19). All eight LLM agents run tooled:
  the five phase-b agents (architect/keeper/editor/retconner/analyst) build
  through a shared pull factory behind per-agent flags (default on),
  `write_todos` is Author-only, and the Structure Analyst scores from full
  prose via the chapter map instead of 400-char excerpts. The Keeper and
  Editor keep their object-of-work prose push by design (598e078).
```

MILESTONES.md M6 row status: `✅ complete (phase a external; phases b+c this branch — pending review)`

- [ ] **Step 2: Full suite**

Run: `uv run pytest -q`
Expected: all pass.

- [ ] **Step 3: Commit**

```bash
git add docs/
git commit -m "docs: CPT-M6 delivered — all agents pull canon; M6 Deep Read status"
```
