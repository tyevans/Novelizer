# CPT-M4: Phase-a Wiring Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** The Author and Continuity Checker become pull agents: `CanonBackend` + `search_canon` wired into their runners (settings-gated), chapter-prose push replaced by an id/title map with a retrieval instruction, and tool calls streaming into telemetry + the Engine Room.

**Architecture:** Everything is flag-gated and additive: with `author_tools_enabled=False` / `checker_tools_enabled=False`, prompts and runner construction stay byte-identical to today (the codebase's standing byte-identical discipline). With flags on (the default — phase a ships enabled), runner builders receive `backend=`/`tools=` kwargs, `poll()`-built prompts swap prose excerpts for map lines, and system prompts gain a retrieval note. deepagents auto-filters the `execute` tool for non-sandbox backends (verified in middleware source), so no middleware surgery is needed; `write_todos` scoping is CPT-M6's job, not this one.

**Tech Stack:** existing deepagents/create_deep_agent surface (`backend=`, `tools=`); `AsyncCallbackHandler.on_tool_start/end/error`; the settings-layer recipe from `sag_spike_delta` (models.py field-list + default, layers.py two classes, loader.py one line).

## Global Constraints

- Red/green TDD; tests ONLY in this worktree; `uv run pytest` prefix.
- Flags OFF ⇒ byte-identical prompts and unchanged runner construction. Every prompt-diet change must be provably gated (tests assert both modes).
- Retrieval note text (exact, used by both agents when tools are on):
  `"\n\nYou have file tools over the story canon (ls, read_file, grep, glob) and semantic search (search_canon). The chapter list below is an index — read any chapter or canon file you need in full before writing. Cite ids exactly as shown in frontmatter or search results."`
- Chapter map line format (exact): `- [{id}] '{title}' ({editorial_status}) cast: {comma-joined character_ids}` with `cast: none` when empty.
- New telemetry event names: `tool.call_started`, `tool.call_finished`, `tool.call_failed`.
- Settings flag names: `author_tools_enabled: bool = True`, `checker_tools_enabled: bool = True`.
- No changes to the write/intent path, the mining runner, or other agents.

---

### Task 1: Tool-call telemetry

**Files:**
- Modify: `novelizer/telemetry/events.py`
- Modify: `novelizer/telemetry/callbacks.py`
- Modify: `novelizer/tui/widgets/engine_room_model.py`
- Test: `tests/telemetry/test_callbacks.py` (append; mirror its existing recorder-double pattern), `tests/tui/` engine-room model test file (append — find the file that tests `engine_room_model` line rendering and follow it)

**Interfaces:**
- Produces in `events.py`:

```python
    TOOL_CALL_STARTED = "tool.call_started"
    TOOL_CALL_FINISHED = "tool.call_finished"
    TOOL_CALL_FAILED = "tool.call_failed"


class ToolCallStarted(BaseModel):
    run_id: str
    agent_name: str
    tool_name: str
    input_summary: str  # str(tool input), truncated to 300 chars


class ToolCallFinished(BaseModel):
    run_id: str
    agent_name: str
    tool_name: str
    duration_s: float
    output_chars: int


class ToolCallFailed(BaseModel):
    run_id: str
    agent_name: str
    tool_name: str
    duration_s: float
    error_type: str
    error_message: str
```

- Produces in `callbacks.py`: `on_tool_start(self, serialized, input_str, *, run_id, **kwargs)`, `on_tool_end(self, output, *, run_id, **kwargs)`, `on_tool_error(self, error, *, run_id, **kwargs)` on `TelemetryCallbackHandler`, keyed by LangChain `run_id` in a separate `self._tool_calls: dict[UUID, _ToolCallState]` (name from `(serialized or {}).get("name", "")`; novelizer run identity from `run_context` at start, same as `_start`). Emit via `self._recorder.emit(...)` like the LLM events.
- Produces in `engine_room_model.py`: one rendered line per tool event in the flat dispatcher, following the file's existing style — started: `⚒ {agent} → {tool_name}({input_summary})`; finished: `⚒ {agent} ← {tool_name} ({duration_s:.1f}s)`; failed: `⚒ {agent} ✗ {tool_name}: {error_type}`. Match the surrounding lines' exact formatting conventions (read the neighboring branches first).

- [ ] **Step 1: Write failing tests** — in `tests/telemetry/test_callbacks.py`, mirror the existing fake-recorder pattern to assert: `on_tool_start` emits `tool.call_started` with tool name + truncated input; `on_tool_end` emits finished with duration and output size; `on_tool_error` emits failed; unknown run_id in end/error is a no-op. In the engine-room test file, assert the three event types render lines containing the tool name.
- [ ] **Step 2: Run, see them fail** (`uv run pytest tests/telemetry -v`, then the engine-room test file)
- [ ] **Step 3: Implement** (events, handler, model branches)
- [ ] **Step 4: Run again — all green, plus the full `tests/telemetry` and touched TUI test file**
- [ ] **Step 5: Commit** — `feat(telemetry): tool-call events through recorder and Engine Room`

---

### Task 2: Settings flags

**Files:**
- Modify: `novelizer/settings/models.py`, `novelizer/settings/layers.py`, `novelizer/settings/loader.py`
- Test: `tests/settings/` (append to the file that tests the `sag_spike_delta`-style field plumbing; follow the same cases: default, story-layer override, env override if the pattern has one)

**Interfaces:**
- Produces: `author_tools_enabled: bool = True` and `checker_tools_enabled: bool = True` on the effective settings model, overridable through the same layers as `sag_spike_delta` (find every place that field name appears in the settings package — including the field-name list near the top of models.py and `view_model.py` if it enumerates fields — and add the two new names in each).

- [ ] **Step 1: Write failing tests** (defaults True; story-layer override to False wins)
- [ ] **Step 2: Run, see them fail**
- [ ] **Step 3: Implement across the settings layers**
- [ ] **Step 4: Run `uv run pytest tests/settings -v` — all green**
- [ ] **Step 5: Commit** — `feat(settings): author/checker tools_enabled flags`

---

### Task 3: Author pull mode

**Files:**
- Modify: `novelizer/agents/author.py`
- Test: `tests/agents/test_author.py` (append; use the file's existing fake-runner pattern that captures the prompt sent to `ainvoke`)

**Interfaces:**
- Consumes: the retrieval-note and map-line formats from Global Constraints.
- Produces: `Author.__init__` gains `pull_mode: bool = False`. `_summarize` gains `pull_mode: bool = False`: when False, output is byte-identical to today; when True, the previous-chapters block is replaced by a map over ALL chapters (`Chapter index:` header + one map line each, `None yet.` when empty) and the prior 3-chapter excerpt block is dropped. `build_author_runner(settings, callbacks=None, backend=None, tools=None)` — when `backend` is not None, append the retrieval note to `AUTHOR_SYSTEM_PROMPT` and pass `backend=`/`tools=` through to `create_deep_agent`; when None, construction is unchanged.

Map builder (module-level, reused by the checker in Task 4 — put it in `novelizer/brain/context.py` beside the other prompt-block builders):

```python
def chapter_map_note(chapters: list[Chapter]) -> str:
    """Pull-mode chapter index: one line per chapter, never prose."""
    if not chapters:
        return "None yet."
    return "\n".join(
        f"- [{c.id}] '{c.title}' ({c.editorial_status.value}) "
        f"cast: {', '.join(c.character_ids) if c.character_ids else 'none'}"
        for c in chapters
    )
```

- [ ] **Step 1: Write failing tests** — (a) `pull_mode=False` ⇒ `_summarize` output byte-identical to a captured pre-change snapshot (assert the prose excerpt marker `Previous chapters:` present and no `Chapter index:`); (b) `pull_mode=True` ⇒ `Chapter index:` present with the exact map line for a seeded chapter, and NO chapter prose in the prompt; (c) `build_author_runner` with a `CanonBackend` instance builds (smoke — no LLM call) and without backend stays constructible.
- [ ] **Step 2: Run, see them fail**
- [ ] **Step 3: Implement** (`chapter_map_note` in brain/context.py + author changes)
- [ ] **Step 4: Run `uv run pytest tests/agents/test_author.py tests/brain -v` — green**
- [ ] **Step 5: Commit** — `feat(author): pull-mode chapter map + backend-wired runner`

---

### Task 4: Continuity Checker pull mode

**Files:**
- Modify: `novelizer/agents/continuity_checker.py`
- Test: `tests/agents/test_continuity_checker.py` (append, same fake-runner prompt-capture pattern)

**Interfaces:**
- Produces: checker `__init__` gains `pull_mode: bool = False`; its prompt-builder swaps the all-chapters 300-char listing for `chapter_map_note(...)` + the retrieval note context when `pull_mode=True` (byte-identical when False). `build_continuity_checker_runner(settings, callbacks=None, backend=None, tools=None)` (existing name at continuity_checker.py:362) mirrors the author builder (retrieval note appended to its SYSTEM_PROMPT when backend given; kwargs passed through). The MINING runner/builder is untouched.

- [ ] **Step 1: Write failing tests** (both modes, same shape as Task 3; plus an explicit test that the mining builder's construction is unchanged)
- [ ] **Step 2: Run, see them fail**
- [ ] **Step 3: Implement**
- [ ] **Step 4: Run `uv run pytest tests/agents/test_continuity_checker.py -v` — green**
- [ ] **Step 5: Commit** — `feat(checker): pull-mode chapter map + backend-wired runner`

---

### Task 5: Runtime wiring

**Files:**
- Modify: `novelizer/runtime.py`
- Test: `tests/test_runtime.py` (append)

**Interfaces:**
- Produces: a small pure seam plus wiring:

```python
    def _phase_a_toolkit(self):
        """(backend, tools) for pull-mode agents, or (None, None) when the
        embeddings store isn't up yet. Built once per start()."""
        from novelizer.canon_fs.backend import CanonBackend
        from novelizer.canon_fs.search import build_search_canon_tool
        backend = CanonBackend(self.read)
        tools = [build_search_canon_tool(self.embeddings, self.read)]
        return backend, tools
```

In `start()`, after embeddings/indexer setup and before agent construction: build the toolkit once; where the Author is constructed, if `settings.author_tools_enabled`: pass `pull_mode=True` and wrap the builder so `_runner_for("author", ...)` receives a builder closure `lambda s, callbacks=None: build_author_runner(s, callbacks=callbacks, backend=backend, tools=tools)`; likewise the checker with `checker_tools_enabled`. Injected fake runners (the `runners=` dict) win exactly as before — the closure only affects the real-builder path.

- [ ] **Step 1: Write failing tests** — construct Runtime with fake runners and flags on: assert `runtime.author.pull_mode is True` (add the attribute in Tasks 3/4 as a public `self.pull_mode`) and checker likewise; flags off ⇒ False. (Runner-closure wiring is exercised implicitly: a second test constructs Runtime WITHOUT a fake author runner but with flags on and asserts `start()` completes and the author was built — builders never touch the network before ainvoke.)
- [ ] **Step 2: Run, see them fail**
- [ ] **Step 3: Implement**
- [ ] **Step 4: Run `uv run pytest tests/test_runtime.py -v` — green**
- [ ] **Step 5: Commit** — `feat(runtime): phase-a toolkit wiring for author and checker`

---

### Task 6: Milestone doc + full-suite gate

**Files:**
- Modify: `docs/superpowers/plans/2026-07-19-canon-pull-tools-milestones.md`

- [ ] **Step 1: Mark CPT-M4 delivered** (status note beside the table, style as M3's), and annotate the spec's "Prompt changes" section: the delivered map carries `character_ids` (not names) — ids feed the cite-ids discipline and names live one `read_file` away
- [ ] **Step 2: Run the whole suite**: `uv run pytest -q` — zero failures (known flake: `test_story_brain_secrets_matrix_and_causeway_tabs_populate` under load; rerun in isolation before treating as real)
- [ ] **Step 3: Commit** — `docs: CPT-M4 delivered — phase-a pull agents live`
