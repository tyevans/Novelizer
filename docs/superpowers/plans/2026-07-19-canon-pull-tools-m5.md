# CPT-M5: Chat Personas Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Chat personas become pull agents: consultations with the Director can grep/read/search the whole canon before answering, with tool calls in telemetry — same machinery, gates, and byte-identical-off discipline as CPT-M4's Author/Checker.

**Architecture:** Mirror CPT-M4 exactly — it is the reviewed, production-fixed reference. `build_chat_runner` gains `callbacks/backend/tools` and binds callbacks + `recursion_limit: 50` at GRAPH scope via `with_config` (constructor callbacks stay off the chat model — that's the M4 lesson: constructor callbacks never see tool events). `ChatService` gains `pull_mode`; `_story_context`'s chapter-excerpt block swaps for the `chapter_map_note` index. One new settings flag `chat_tools_enabled: bool = True`. Runtime wires the stored `_canon_backend`/`_canon_tools` (M4 keeps them on self) into `_chat_runner_for` and passes `pull_mode` to `ChatService`.

## Global Constraints

- Red/green TDD; tests ONLY in this worktree; `uv run pytest` prefix.
- Flag OFF ⇒ byte-identical chat prompts and unchanged `build_chat_runner(settings, agent_name)` construction; existing chat tests pass unmodified.
- Reuse `RETRIEVAL_NOTE` (import from `novelizer.agents.author`) and `chapter_map_note` (from `novelizer.brain.context`) — no string duplication.
- Chat runners get graph-scope `{"callbacks": ..., "recursion_limit": 50}` exactly like `build_author_runner` (read it first: novelizer/agents/author.py).
- Settings flag name: `chat_tools_enabled: bool = True`, plumbed everywhere `author_tools_enabled` is.

---

### Task 1: `chat_tools_enabled` settings flag

**Files:** the same five settings sites as `author_tools_enabled` (grep it); tests mirror its default + story-override tests.

- [ ] Failing tests → implement → `uv run pytest tests/settings -v` green
- [ ] Commit: `feat(settings): chat_tools_enabled flag`

### Task 2: Tooled chat runner builder

**Files:**
- Modify: `novelizer/chat/runners.py`
- Test: `tests/chat/` (find the runner-builder test file; append)

**Interfaces:** `build_chat_runner(settings, agent_name, callbacks=None, backend=None, tools=None)`. When `backend` given: append `RETRIEVAL_NOTE` to the formatted `CHAT_SYSTEM_PROMPT` and pass `backend=`/`tools=` to `create_deep_agent`. When `callbacks` or `backend` given: chain `.with_config(...)` with callbacks and/or `recursion_limit: 50` (mirror `build_author_runner`'s exact composition). Bare call `build_chat_runner(settings, "author")` byte-identical construction to today.

- [ ] Failing tests (bare unchanged; tooled has note + config; smoke with `CanonBackend(read_store=None)`) → implement → green
- [ ] Commit: `feat(chat): tooled chat runner builder with graph-scope callbacks`

### Task 3: ChatService pull mode

**Files:**
- Modify: `novelizer/chat/service.py`
- Test: `tests/chat/` service tests (append; the file has prompt-assertion patterns)

**Interfaces:** `ChatService.__init__(..., pull_mode: bool = False)` (public attr). In `_story_context`, pull mode replaces the `Recent chapters:` 200-char excerpt block with `Chapter index:` + `chapter_map_note(chapters)` over ALL chapters (import from brain.context); every other block unchanged; byte-identical when False.

- [ ] Failing tests (both modes: excerpt marker vs `Chapter index:`, no prose leak in pull mode) → implement → green, existing chat tests unmodified
- [ ] Commit: `feat(chat): pull-mode story context with chapter index`

### Task 4: Runtime wiring + docs + gate

**Files:**
- Modify: `novelizer/runtime.py` (`_chat_runner_for` at :111-119, `ChatService(...)` at :207, cache-clear path at :292 — read them)
- Modify: `docs/superpowers/plans/2026-07-19-canon-pull-tools-milestones.md`
- Test: `tests/test_runtime.py` (append)

**Interfaces:** `_chat_runner_for`'s real-builder path becomes `build_chat_runner(self.settings, agent_name, callbacks=self._llm_callbacks, backend=self._canon_backend, tools=self._canon_tools)` when `self.settings.chat_tools_enabled` else the bare legacy call (verify `_canon_backend`/`_canon_tools` attribute names from M4's `_tooled` wiring — read runtime.py; if M4 stored them under different names or only locally, hoist to `self` in this task). Note `_chat_runner_for` can be called before/without `start()` in some tests — guard: fall back to bare when the toolkit attrs are None/absent. `ChatService` constructed with `pull_mode=self.settings.chat_tools_enabled`. Injected `chat_<name>` fakes keep winning. The `_chat_runner_cache.clear()` rebuild path needs no change beyond the flag-aware `_chat_runner_for` (verify).

- [ ] Failing tests (flag on ⇒ `runtime.chat.pull_mode is True` and real-built chat runner is tooled — spy the builder like M4's rebuild tests; flag off ⇒ both false/bare) → implement → green
- [ ] Mark CPT-M5 delivered in the milestone doc (style as M4's)
- [ ] Full-suite gate: `uv run pytest -q` — zero failures (known load-flakes: test_story_brain_secrets..., test_author_loop_survives... — rerun isolated before treating as real)
- [ ] Commit: `feat(runtime): chat toolkit wiring — CPT-M5 delivered`
