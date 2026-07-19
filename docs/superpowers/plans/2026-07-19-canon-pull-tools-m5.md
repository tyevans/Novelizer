# CPT-M5: Chat Personas Pull Tools — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Chat personas get canon pull tools (CanonBackend + search_canon), a chapter-map push diet, and telemetry — phase c of the canon pull tools spec.

**Architecture:** Mirror the delivered phase-a pattern exactly: builder gains optional `callbacks`/`backend`/`tools` params; when a backend is present, the system prompt gains a retrieval note, the graph is configured with a recursion bound and graph-scope callbacks, and a new `ExcludeToolsMiddleware` strips `write_todos` (todos are Author-only per spec). `ChatService._story_context` swaps prose excerpts for the chapter map in pull mode. `Runtime` wires it all behind a `chat_tools_enabled` settings flag.

**Tech Stack:** Python 3.13, deepagents 0.6.12, langchain 1.x middleware API, pydantic v2, pytest + pytest-asyncio (asyncio_mode auto).

## Global Constraints

- Spec: `docs/superpowers/specs/2026-07-19-canon-pull-tools-design.md` (§ "Phase c"); ladder: `docs/superpowers/plans/2026-07-19-canon-pull-tools-milestones.md`.
- Writes to canon stay on the event-sourced intent path — nothing here touches the write path. `CanonBackend` write/edit already refuse.
- Legacy behavior must be byte-identical when `chat_tools_enabled` is off (prompt-text equality is tested in phase-a style).
- **Run all tests in this worktree, NEVER the main checkout** (standing DB-lock rule).
- Run tests with `uv run pytest <path> -v` from the worktree root.
- All test files follow the existing style: module-level `async def test_*` (asyncio_mode auto), tmpfile-backed EventStore/Projector/ReadStore fixture named `stack` where DB is needed.

---

### Task 1: `ExcludeToolsMiddleware`

`write_todos` must be scoped to the Author only (spec §4). deepagents has a private `_ToolExclusionMiddleware`; we write our own small public equivalent so we don't depend on a private API.

**Files:**
- Create: `novelizer/agents/middleware.py`
- Test: `tests/agents/test_middleware.py`

**Interfaces:**
- Produces: `ExcludeToolsMiddleware(excluded: frozenset[str])` — a `langchain.agents.middleware.AgentMiddleware` subclass usable in `create_deep_agent(middleware=[...])`. Later tasks pass `ExcludeToolsMiddleware(excluded=frozenset({"write_todos"}))`.

- [ ] **Step 1: Write the failing tests**

```python
# tests/agents/test_middleware.py
from novelizer.agents.middleware import ExcludeToolsMiddleware


class FakeTool:
    def __init__(self, name): self.name = name


class FakeRequest:
    def __init__(self, tools): self.tools = tools
    def override(self, tools): return FakeRequest(tools)


def test_sync_filters_named_tools_and_calls_handler_with_rest():
    mw = ExcludeToolsMiddleware(excluded=frozenset({"write_todos"}))
    seen = {}
    def handler(request):
        seen["tools"] = [t.name for t in request.tools]
        return "response"
    request = FakeRequest([FakeTool("write_todos"), FakeTool("read_file")])
    assert mw.wrap_model_call(request, handler) == "response"
    assert seen["tools"] == ["read_file"]


def test_sync_handles_dict_tools():
    mw = ExcludeToolsMiddleware(excluded=frozenset({"write_todos"}))
    seen = {}
    def handler(request):
        seen["tools"] = request.tools
        return "response"
    request = FakeRequest([{"name": "write_todos"}, {"name": "search_canon"}])
    mw.wrap_model_call(request, handler)
    assert seen["tools"] == [{"name": "search_canon"}]


def test_empty_exclusion_passes_request_through_unchanged():
    mw = ExcludeToolsMiddleware(excluded=frozenset())
    request = FakeRequest([FakeTool("write_todos")])
    def handler(req):
        assert req is request
        return "ok"
    assert mw.wrap_model_call(request, handler) == "ok"


async def test_async_filters_named_tools():
    mw = ExcludeToolsMiddleware(excluded=frozenset({"write_todos"}))
    seen = {}
    async def handler(request):
        seen["tools"] = [t.name for t in request.tools]
        return "response"
    request = FakeRequest([FakeTool("write_todos"), FakeTool("ls")])
    assert await mw.awrap_model_call(request, handler) == "response"
    assert seen["tools"] == ["ls"]
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/agents/test_middleware.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'novelizer.agents.middleware'`

- [ ] **Step 3: Write the implementation**

```python
# novelizer/agents/middleware.py
from __future__ import annotations

from typing import Any

from langchain.agents.middleware import AgentMiddleware


def _tool_name(tool: Any) -> str:
    name = getattr(tool, "name", None)
    if name is not None:
        return name
    return tool.get("name", "")


class ExcludeToolsMiddleware(AgentMiddleware[Any, Any, Any]):
    """Strip named tools from the model request before the model sees them.

    Placed after deepagents' tool-injecting middleware so it can remove
    built-ins like `write_todos` (Author-only per the pull-tools spec)."""

    def __init__(self, *, excluded: frozenset[str]) -> None:
        self._excluded = excluded

    def wrap_model_call(self, request, handler):
        if self._excluded:
            filtered = [t for t in request.tools if _tool_name(t) not in self._excluded]
            request = request.override(tools=filtered)
        return handler(request)

    async def awrap_model_call(self, request, handler):
        if self._excluded:
            filtered = [t for t in request.tools if _tool_name(t) not in self._excluded]
            request = request.override(tools=filtered)
        return await handler(request)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/agents/test_middleware.py -v`
Expected: 4 PASS

- [ ] **Step 5: Commit**

```bash
git add novelizer/agents/middleware.py tests/agents/test_middleware.py
git commit -m "feat(agents): ExcludeToolsMiddleware for scoping deepagents built-in tools"
```

---

### Task 2: `chat_tools_enabled` settings flag

**Files:**
- Modify: `novelizer/settings/models.py` (STORY_OVERRIDABLE_KEYS ~line 18; EffectiveSettings fields ~line 83)
- Modify: `novelizer/settings/loader.py` (~line 54, beside `checker_tools_enabled: bool | None = None`)
- Modify: `novelizer/settings/layers.py` (~lines 50 and 78 — the flag appears in BOTH override layer classes; mirror `checker_tools_enabled` in each)
- Test: `tests/settings/test_models.py`, `tests/settings/test_layers.py`

**Interfaces:**
- Produces: `EffectiveSettings.chat_tools_enabled: bool = True`, story-overridable, layerable. Task 4 reads it in `Runtime`.

- [ ] **Step 1: Write the failing tests**

Append to `tests/settings/test_models.py`:

```python
def test_chat_tools_enabled_defaults_true_and_is_story_overridable():
    from novelizer.settings.models import EffectiveSettings, STORY_OVERRIDABLE_KEYS
    s = EffectiveSettings()
    assert s.chat_tools_enabled is True
    assert "chat_tools_enabled" in STORY_OVERRIDABLE_KEYS
```

Append to `tests/settings/test_layers.py` (mirror the existing `*_tools_enabled` layer test added in phase a — open the file, find the test containing `author_tools_enabled`, and add the same assertions for `chat_tools_enabled`; if that test parametrizes a key list, extend the list instead of adding a new test):

```python
def test_chat_tools_enabled_flows_through_layers():
    from novelizer.settings.layers import GlobalFileSettings, StoryFileSettings
    assert GlobalFileSettings(chat_tools_enabled=False).chat_tools_enabled is False
    assert StoryFileSettings(chat_tools_enabled=False).chat_tools_enabled is False
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/settings/test_models.py tests/settings/test_layers.py -v`
Expected: new tests FAIL (`AttributeError` / validation error — unknown field); pre-existing tests PASS.

Note: if the layer classes are not named `GlobalFileSettings`/`StoryFileSettings`, read `novelizer/settings/layers.py:40-80` and use the two classes that already declare `checker_tools_enabled: bool | None = None` (lines ~50 and ~78).

- [ ] **Step 3: Implement**

In `novelizer/settings/models.py`: change the STORY_OVERRIDABLE_KEYS line

```python
    "author_tools_enabled", "checker_tools_enabled", "chat_tools_enabled",
```

and beside the existing flags in `EffectiveSettings`:

```python
    chat_tools_enabled: bool = True
```

In `novelizer/settings/loader.py` and BOTH classes in `novelizer/settings/layers.py`, beside each existing `checker_tools_enabled: bool | None = None`:

```python
    chat_tools_enabled: bool | None = None
```

- [ ] **Step 4: Run the settings suite**

Run: `uv run pytest tests/settings/ tests/test_apply_settings.py -v`
Expected: all PASS

- [ ] **Step 5: Commit**

```bash
git add novelizer/settings/ tests/settings/
git commit -m "feat(settings): chat_tools_enabled flag"
```

---

### Task 3: `build_chat_runner` backend/tools/callbacks wiring

**Files:**
- Modify: `novelizer/chat/runners.py`
- Test: `tests/chat/test_runners.py` (new file)

**Interfaces:**
- Consumes: `ExcludeToolsMiddleware` from Task 1; `CanonBackend` (`novelizer/canon_fs/backend.py`), `build_search_canon_tool` (`novelizer/canon_fs/search.py`) — both exist.
- Produces: `build_chat_runner(settings, agent_name, callbacks=None, backend=None, tools=None)`. Task 5's `Runtime._chat_runner_for` calls it with all five args.

- [ ] **Step 1: Write the failing tests**

```python
# tests/chat/test_runners.py
from novelizer.chat.runners import CHAT_RETRIEVAL_NOTE, build_chat_runner


class FakeSettings:
    author_model = "gpt-4o-mini"
    agent_model = "gpt-4o-mini"
    llm_base_url = None
    llm_api_key = "test-key"
    agent_temperature = 0.7
    llm_max_tokens = None


class FakeBackend:
    """Stands in for CanonBackend; create_deep_agent only stores it."""


def test_build_chat_runner_without_backend_stays_constructible():
    runner = build_chat_runner(FakeSettings(), "author")
    assert runner is not None


def test_build_chat_runner_with_backend_builds():
    runner = build_chat_runner(FakeSettings(), "editor", backend=FakeBackend(), tools=[])
    assert runner is not None


def test_build_chat_runner_with_backend_adds_retrieval_note_and_config(monkeypatch):
    captured = {}
    import novelizer.chat.runners as runners_mod

    class FakeGraph:
        def with_config(self, config):
            captured["config"] = config
            return self

    def fake_create_deep_agent(**kwargs):
        captured["kwargs"] = kwargs
        return FakeGraph()

    monkeypatch.setattr("deepagents.create_deep_agent", fake_create_deep_agent)
    sentinel_cb = object()
    build_chat_runner(FakeSettings(), "muse", callbacks=[sentinel_cb], backend=FakeBackend(), tools=[])
    assert CHAT_RETRIEVAL_NOTE.strip() in captured["kwargs"]["system_prompt"]
    assert captured["kwargs"]["backend"] is not None
    assert captured["config"]["recursion_limit"] == 50
    assert captured["config"]["callbacks"] == [sentinel_cb]


def test_build_chat_runner_with_backend_excludes_write_todos(monkeypatch):
    from novelizer.agents.middleware import ExcludeToolsMiddleware
    captured = {}

    class FakeGraph:
        def with_config(self, config): return self

    def fake_create_deep_agent(**kwargs):
        captured["kwargs"] = kwargs
        return FakeGraph()

    monkeypatch.setattr("deepagents.create_deep_agent", fake_create_deep_agent)
    build_chat_runner(FakeSettings(), "editor", backend=FakeBackend(), tools=[])
    mws = captured["kwargs"]["middleware"]
    assert any(isinstance(m, ExcludeToolsMiddleware) for m in mws)


def test_build_chat_runner_without_backend_prompt_unchanged(monkeypatch):
    captured = {}

    def fake_create_deep_agent(**kwargs):
        captured["kwargs"] = kwargs
        class G:
            def with_config(self, c): return self
        return G()

    monkeypatch.setattr("deepagents.create_deep_agent", fake_create_deep_agent)
    build_chat_runner(FakeSettings(), "author")
    assert "file tools" not in captured["kwargs"]["system_prompt"]
    assert "middleware" not in captured["kwargs"]
```

Note on monkeypatching: `build_chat_runner` does `from deepagents import create_deep_agent` inside the function body, so patch the *source* attribute `deepagents.create_deep_agent` (as shown), not `novelizer.chat.runners.create_deep_agent`. This mirrors `tests/agents/test_author.py::test_build_author_runner_with_backend_bounds_recursion`.

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/chat/test_runners.py -v`
Expected: FAIL — `ImportError: cannot import name 'CHAT_RETRIEVAL_NOTE'`

- [ ] **Step 3: Implement**

Replace `novelizer/chat/runners.py` content below the `CHAT_SYSTEM_PROMPT` definition (keep the module docstring/imports/prompt as-is):

```python
CHAT_RETRIEVAL_NOTE = (
    "\n\nYou have file tools over the story canon (ls, read_file, grep, glob) and "
    "semantic search (search_canon). The story context you receive is an index — "
    "read any chapter or canon file you need in full before answering. Cite ids "
    "exactly as shown in frontmatter or search results."
)


def build_chat_runner(settings, agent_name: str, callbacks=None, backend=None, tools=None):
    from deepagents import create_deep_agent
    from novelizer.agents.llm import build_chat_model
    from novelizer.agents.middleware import ExcludeToolsMiddleware
    model_name = settings.author_model if agent_name == "author" else settings.agent_model
    # Telemetry callbacks bind graph-scope (with_config), not on the model:
    # tool executions run in the graph's ToolNode under invoke-time config.
    model = build_chat_model(
        model_name, settings.llm_base_url, settings.llm_api_key,
        settings.agent_temperature, max_tokens=settings.llm_max_tokens,
        streaming=callbacks is not None,
    )
    persona = CHAT_PERSONAS[agent_name]
    if backend is not None:
        graph = create_deep_agent(
            model=model,
            system_prompt=CHAT_SYSTEM_PROMPT.format(role_prompt=persona.role_prompt) + CHAT_RETRIEVAL_NOTE,
            response_format=ChatReply,
            backend=backend, tools=tools,
            middleware=[ExcludeToolsMiddleware(excluded=frozenset({"write_todos"}))],
        )
        config = {"recursion_limit": 50}
        if callbacks:
            config["callbacks"] = callbacks
        return graph.with_config(config)
    graph = create_deep_agent(
        model=model,
        system_prompt=CHAT_SYSTEM_PROMPT.format(role_prompt=persona.role_prompt),
        response_format=ChatReply,
    )
    if callbacks:
        return graph.with_config({"callbacks": callbacks})
    return graph
```

Check first whether `build_chat_model` accepts a `streaming` kwarg (read `novelizer/agents/llm.py`); phase-a builders pass `callbacks=None, streaming=callbacks is not None` — copy exactly what `build_author_runner` (novelizer/agents/author.py:178-182) passes.

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/chat/ -v`
Expected: all PASS (including pre-existing chat tests)

- [ ] **Step 5: Commit**

```bash
git add novelizer/chat/runners.py tests/chat/test_runners.py
git commit -m "feat(chat): backend-wired chat runner with retrieval note and todos exclusion"
```

---

### Task 4: `ChatService` chapter-map push diet

**Files:**
- Modify: `novelizer/chat/service.py` (constructor ~line 42; `_story_context` ~lines 97-116)
- Test: `tests/chat/test_service.py`

**Interfaces:**
- Consumes: `chapter_map_note(chapters)` from `novelizer/brain/context.py:85`.
- Produces: `ChatService(..., pull_mode: bool = False)` — Task 5 passes `pull_mode=settings.chat_tools_enabled`.

- [ ] **Step 1: Write the failing tests**

Append to `tests/chat/test_service.py` (reuse that file's existing fixture/FakeRunner pattern — read the file first and copy its setup helper; the assertions below are what matters). The service must be constructed once with `pull_mode=False` and once with `pull_mode=True` against a story containing one chapter whose prose includes a sentinel string, e.g. `prose="secret prose text like a grudge"`:

```python
async def test_story_context_pull_mode_false_keeps_prose_excerpts(...):
    service = ChatService(events, read, committer, runner_for, personality_for)
    context = await service._story_context()
    assert "secret prose text" in context
    assert "Chapter index:" not in context


async def test_story_context_pull_mode_true_replaces_prose_with_chapter_map(...):
    service = ChatService(events, read, committer, runner_for, personality_for, pull_mode=True)
    context = await service._story_context()
    assert "secret prose text" not in context
    assert "Chapter index:" in context
    # map line shape pinned by brain.context.chapter_map_note
    assert "cast:" in context
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/chat/test_service.py -v`
Expected: new tests FAIL (`TypeError: unexpected keyword argument 'pull_mode'`)

- [ ] **Step 3: Implement**

In `novelizer/chat/service.py`:

Constructor — add the parameter and store it:

```python
    def __init__(self, events, read, committer, runner_for: Callable,
                 personality_for: Callable[[str], str], pull_mode: bool = False) -> None:
        ...
        self._pull_mode = pull_mode
```

Add the import at the top: `from novelizer.brain.context import chapter_map_note`

In `_story_context`, replace the single line

```python
        prev = "\n".join(f"- '{ch.title}': {ch.prose[:200]}" for ch in chapters[-3:]) or "None yet."
```

with

```python
        if self._pull_mode:
            prev = None
        else:
            prev = "\n".join(f"- '{ch.title}': {ch.prose[:200]}" for ch in chapters[-3:]) or "None yet."
```

and replace the `Recent chapters` segment of the return f-string:

```python
        chapters_block = (
            f"Chapter index:\n{chapter_map_note(chapters)}" if self._pull_mode
            else f"Recent chapters:\n{prev}"
        )
        return (
            f"Story context.\nWorld lore:\n{w}\n\nCharacters:\n{c}\n\n{chapters_block}"
            f"\n\nThreads:\n{t}\n\nSecrets:\n{s}\n\nThemes:\n{tm}"
        )
```

(Off-mode output must remain byte-identical: same labels, same ordering.)

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/chat/ -v`
Expected: all PASS

- [ ] **Step 5: Commit**

```bash
git add novelizer/chat/service.py tests/chat/test_service.py
git commit -m "feat(chat): chapter-map push diet in story context under pull mode"
```

---

### Task 5: Runtime wiring + telemetry for chat runners

**Files:**
- Modify: `novelizer/runtime.py` (`_chat_runner_for` ~line 111; `ChatService` construction ~line 208; `apply_settings` chat-cache clear ~line 292 stays as-is)
- Test: `tests/test_runtime.py`

**Interfaces:**
- Consumes: Tasks 2-4. `Runtime._canon_backend`/`_canon_tools` (built in `start()` at line 147 — before `ChatService` construction at line 208, so ordering is already correct).
- Produces: chat runners built with `callbacks=self._llm_callbacks, backend=self._canon_backend, tools=self._canon_tools` when `settings.chat_tools_enabled`; `ChatService` gets `pull_mode=settings.chat_tools_enabled`.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_runtime.py`, mirroring `test_runtime_flags_on_wire_pull_mode_for_author_and_checker` (line 465 — read it first and reuse its `settings` fixture and fake-runners construction):

```python
async def test_runtime_chat_flag_on_wires_pull_mode_chat(settings, monkeypatch):
    captured = {}

    def fake_build_chat_runner(s, agent_name, callbacks=None, backend=None, tools=None):
        captured[agent_name] = {"callbacks": callbacks, "backend": backend, "tools": tools}
        class G:
            async def ainvoke(self, *_a, **_k): return {}
        return G()

    monkeypatch.setattr("novelizer.runtime.build_chat_runner", fake_build_chat_runner)
    rt = ...  # construct exactly as the line-465 test does, with fake runners for scheduled agents
    await rt.start()
    try:
        rt._chat_runner_for("editor")
        assert captured["editor"]["backend"] is rt._canon_backend
        assert captured["editor"]["tools"] is rt._canon_tools
        assert captured["editor"]["callbacks"] == rt._llm_callbacks
        assert rt.chat._pull_mode is True
    finally:
        await rt.close()


async def test_runtime_chat_flag_off_builds_bare_chat_runner(settings, monkeypatch):
    # same shape with settings.chat_tools_enabled=False (model_copy on the fixture
    # settings or construct EffectiveSettings(chat_tools_enabled=False, ...))
    ...
    rt._chat_runner_for("editor")
    assert captured["editor"]["backend"] is None
    assert rt.chat._pull_mode is False
```

The elided `...` lines must be filled from the real fixture in the file — copy the construction used by `test_runtime_flags_on_wire_pull_mode_for_author_and_checker` verbatim, including its `runners=` fake dict, minus any `chat_*` keys (we want the real `_chat_runner_for` path to run our patched builder).

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_runtime.py -v -k chat_flag`
Expected: FAIL (`TypeError` — current `_chat_runner_for` calls `build_chat_runner(self.settings, agent_name)` with no kwargs, so `captured[...]` has `backend is None` in the flag-on test / `ChatService` has no `_pull_mode`).

- [ ] **Step 3: Implement**

In `novelizer/runtime.py` replace `_chat_runner_for` body (line ~111):

```python
    def _chat_runner_for(self, agent_name: str):
        """Lazy per-agent chat runner. Injected fakes use key 'chat_<name>' in
        the runners dict; real runners are built on first use and cached."""
        key = f"chat_{agent_name}"
        if self._runners is not None and key in self._runners:
            return self._runners[key]
        if key not in self._chat_runner_cache:
            if self.settings.chat_tools_enabled:
                self._chat_runner_cache[key] = build_chat_runner(
                    self.settings, agent_name, callbacks=self._llm_callbacks,
                    backend=self._canon_backend, tools=self._canon_tools,
                )
            else:
                self._chat_runner_cache[key] = build_chat_runner(
                    self.settings, agent_name, callbacks=self._llm_callbacks,
                )
        return self._chat_runner_cache[key]
```

and the `ChatService` construction (line ~208):

```python
        self.chat = ChatService(
            self.events, self.read, self.committer, self._chat_runner_for,
            lambda name: self.voice_pack.agent_personalities.get(name, ""),
            pull_mode=s.chat_tools_enabled,
        )
```

Guard note: `_chat_runner_for` can only be *called* after `start()` (chat exists only then), but the method is *referenced* earlier; `self._canon_backend` is set in `start()` before `ChatService` is constructed, so no ordering hazard. Do not add defensive hasattr checks.

- [ ] **Step 4: Run the runtime + chat + settings suites**

Run: `uv run pytest tests/test_runtime.py tests/chat/ tests/test_apply_settings.py -v`
Expected: all PASS

- [ ] **Step 5: Update the ladder status and commit**

In `docs/superpowers/plans/2026-07-19-canon-pull-tools-milestones.md` add to the Status section:

```markdown
- **CPT-M5: delivered** (2026-07-19). Chat personas pull canon: chat runners
  build with `CanonBackend` + `search_canon` + graph-scope telemetry callbacks
  when `chat_tools_enabled` (default on), story-context prose excerpts replaced
  by the chapter map in pull mode, `write_todos` excluded from chat via the new
  `ExcludeToolsMiddleware` (novelizer-owned; no deepagents private imports).
```

```bash
git add novelizer/runtime.py tests/test_runtime.py docs/superpowers/plans/2026-07-19-canon-pull-tools-milestones.md
git commit -m "feat(runtime): chat personas wired with canon pull tools behind chat_tools_enabled"
```

---

### Task 6: Full-suite regression gate

**Files:** none (verification only)

- [ ] **Step 1: Run the full suite in the worktree**

Run: `uv run pytest -q`
Expected: all pass, no new warnings about unawaited coroutines / unclosed DBs. If anything fails, fix forward within this plan's scope before proceeding to review.
