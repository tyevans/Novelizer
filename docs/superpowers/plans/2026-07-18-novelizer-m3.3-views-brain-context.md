# M3.3 · Story Shape & Thread Board Views + Brain Context Injection Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Close the M3 loop. Author and Editor prompts gain a live "brain context" note — stale threads (Author) and pacing flags (Editor) — computed from the exact same pure functions (`novelizer.brain.staleness`, `novelizer.brain.sag_spike`) that two new Mission Control panes, Thread Board and Story Shape, render at display time, so the two views of "what's stale/sagging" can never disagree. This lands the M3 milestone done-when: (a) a CI-verifiable mechanical chain proving the plumbing (seed a stale thread → Author's prompt names it → a scripted Author response touches it → the event lands → the Thread Board no longer calls it stale), and (b) an `live_llm`-marked live-LLM smoke test proving the actual causal claim — an unprompted real Author reacts to the injected note.

**Architecture:** (1) **Brain context is computed live, in `poll()`, not frozen at construction.** The M3 decomposition doc describes `BrainContext` "analogous to the M2 voice provider... handed to Author/Editor as an additional optional constructor param." Read literally, that would freeze the note at `Runtime.start()` — correct for `casting_note`/`personality` (static per-story voice-pack values) but wrong for stale-thread/pacing signals, which change every time a new chapter lands. This plan resolves that tension explicitly in favor of liveness: Author/Editor's existing `poll()` (which already fetches `ctx["threads"]` per M3.1) is extended to also fetch the chapter list and, for the Editor, structure scores; `work()` builds the brain-context string from `ctx` each call, via new pure functions in `novelizer/brain/context.py`, and appends it to the prompt exactly like `casting_note`/`personality` — conditional, empty when the brain has nothing to report, byte-identical output in that case. The *mechanism* (conditional string, appended once, empty-safe) is the M2 pattern verbatim; only *where the string is computed* differs from a literal reading of the doc, and that's stated here rather than silently changed. (2) **The note-builder functions are pure**, living in `novelizer/brain/context.py` alongside M3.2's `staleness.py`/`sag_spike.py` — `stale_threads_note(threads, chapters) -> str` and `pacing_flags_note(scores) -> str` take already-fetched domain objects, not a `ReadStore`, keeping every function in `novelizer/brain/` I/O-free and unit-testable without a database. Only agents' `poll()` methods touch `ReadStore`. (3) **The two new TUI widgets follow `AgentRoster`'s pattern, not `StoryBrowser`'s** — `ThreadBoard`/`StoryShape` are flat-list `Static` widgets with a pure `*_line(...)` formatter function (importing the same `is_thread_stale`/`detect_sag_spike` functions the agents use) plus an async `refresh_from(read)` method that fetches `ReadStore` data and renders it, mirroring `AgentRoster.update_from`/`roster_line` exactly. `StoryBrowser`'s `Tree`-based, section/drill-in pattern doesn't fit a flat scored/state list. (4) **Both panes land in Mission Control's persistent dashboard**, as two more `Static` panes in the left column (below `AgentRoster`/the proposals pane), each with its own refresh worker loop — matching the vision spec's "Mission Control (persistent dashboard)" framing. M3.3's stated done-when only requires the views "wired into `NovelizerApp.compose()`"; a dedicated full-screen drill-in view and its own keybinding (like `r` for the Room) are not required by the doc and are left as a natural follow-up, noted explicitly rather than silently added or dropped. (5) **The carry-over regression item is test-only.** Reading `novelizer/scheduler.py` and `novelizer/tui/app.py` confirms neither `Scheduler.tick()` nor `Scheduler._run()` catches exceptions by design (existing `tests/test_scheduler.py` already relies on `tick()` propagating `StubAgent` behavior straight through) — the two catch-alls that actually exist are `Scheduler.run()`'s loop (for headless use) and `NovelizerApp._scheduler_loop()` (used by the TUI, and already covered by `tests/tui/test_app_resilience.py`'s `BoomRunner` pattern for a plain `RuntimeError`). This plan's Task 1 adds the missing case for a `pydantic.ValidationError` surfaced from inside `StructureAnalyst.commit()`, correcting "Scheduler.tick's catch-all" to name the two real catch points.

**Tech Stack:** Python 3.13, `pydantic` v2, `textual`, `pytest`+`pytest-asyncio` (`asyncio_mode=auto`), `hypothesis>=6.156.6`; the `live_llm` pytest marker (registered in `pyproject.toml`, excluded by default via `addopts = "-m 'not ollama and not live_llm'"`) for the live-LLM smoke test, split out from the `ollama` marker used by `tests/store/test_embeddings.py`'s existing precedent.

## Global Constraints

- `novelizer/brain/context.py`'s functions are pure: no `ReadStore`, no I/O, inputs are plain lists of already-fetched domain objects.
- Brain context is never persisted and never re-implemented outside `novelizer.brain.staleness`/`novelizer.brain.sag_spike` — the Thread Board, Story Shape, and Author/Editor prompts all call the same functions, per M3.2's stated design intent.
- `_commit_thread_intents`'s signature is unchanged from M3.1 (`intents, active_thread_ids, chapter_id=""`) — brain context injection only affects what agents are *told*, never how declared thread intents are *validated or committed*.
- M2 injection mechanics apply verbatim: the brain-context string is appended in `work()`/`_summarize()` only when non-empty; when empty, the prompt is byte-identical to pre-M3.3 output. Every pre-existing `Author`/`Editor` test stays green untouched.
- TDD, black-box-first; property tests only where warranted (this milestone is mostly integration/wiring, so most tasks are example-based, matching M1–M3.2's own mix).
- The M3 done-when has two parts and both are explicit, separately-graded tasks in this plan (Tasks 9 and 10) — the CI-verifiable chain is necessary but not sufficient; the `live_llm`-marked test is the milestone's true observation, per the doc's own framing.
- Backward compatibility: the existing test suite (286 tests after M3.2) stays green throughout; `StructureAnalyst`, `Scheduler`, `Committer`/`GatingCommitter` are untouched by this plan except where Task 1 adds tests (no production code change in Task 1).

---

### Task 1: Regression — agent exceptions inside a scheduler tick propagate, are caught one level up, and leave no partial event

**Files:**
- Test: `tests/agents/test_structure_analyst.py`, `tests/test_scheduler.py`

**Interfaces:** none produced — this task is test-only, pinning existing behavior (confirmed by reading `novelizer/scheduler.py` and `novelizer/tui/app.py`, neither of which needs to change). No production code is touched.

- [ ] **Step 1: Write the tests**

Append to `tests/agents/test_structure_analyst.py`:

```python
async def test_commit_propagates_validation_error_and_commits_nothing_for_the_bad_score(stack):
    """A malformed score (out-of-range tension) reaching commit() — e.g. a future
    lenient runner that skips ChapterScore's own Field(ge=0.0, le=1.0) bound --
    still fails fast at AnnotationStructureScored construction, and the exception
    is not swallowed inside the agent: it propagates out of run_once() uncaught."""
    import pytest
    from pydantic import ValidationError
    from types import SimpleNamespace

    events, proj, read, committer = stack
    await events.append(EventType.CHAPTER_CREATED, "c1", Chapter(id="c1", title="One", prose="p"))
    await proj.catch_up()
    bad_out = SimpleNamespace(
        scores=[SimpleNamespace(chapter_id="c1", tension=1.5, pacing_label="off the charts")],
        feed_note="",
    )
    analyst = StructureAnalyst(FakeRunner(bad_out), read, committer)
    with pytest.raises(ValidationError):
        await analyst.run_once()
    log = await events.events_since(0)
    assert [e for e in log if e.event_type == EventType.ANNOTATION_STRUCTURE_SCORED] == []
```

Append to `tests/test_scheduler.py`:

```python
async def test_tick_propagates_an_agents_exception_uncaught():
    """Scheduler.tick() has no try/except by design -- a crashing agent's
    exception must reach the caller, not vanish silently mid-tick."""
    class BoomAgent(StubAgent):
        async def run_once(self):
            raise ValueError("boom")

    a = BoomAgent("a", 0.9)
    sched = Scheduler([a], StubRead(), clock=lambda: 1000.0)
    with pytest.raises(ValueError, match="boom"):
        await sched.tick()


async def test_run_survives_a_ticking_agents_exception_and_keeps_selecting_others():
    """Scheduler.run()'s loop is the one existing catch-all around tick() for
    headless use (NovelizerApp._scheduler_loop is the TUI's own, already
    covered by tests/tui/test_app_resilience.py) -- a single agent's repeated
    crash must not stop the room from continuing to run other agents."""
    import asyncio

    class BoomAgent(StubAgent):
        async def run_once(self):
            raise ValueError("boom")

    boom = BoomAgent("boom", 0.9)
    healthy = StubAgent("healthy", 0.1)
    sched = Scheduler([boom, healthy], StubRead(), tick_sleep=0.01, clock=lambda: 1000.0)
    task = asyncio.create_task(sched.run())
    try:
        # boom always outscores healthy, so only stopping boom lets healthy run;
        # instead, prove survival: run() must still be alive (not crashed) after
        # several ticks despite boom raising on every one.
        await asyncio.sleep(0.1)
        assert not task.done(), "Scheduler.run() must survive a crashing agent, not exit"
    finally:
        sched.stop()
        await asyncio.wait_for(task, timeout=1.0)
```

- [ ] **Step 2: Run the tests to verify they pass**

Run: `uv run pytest tests/agents/test_structure_analyst.py tests/test_scheduler.py -v`
Expected: PASS immediately — this task pins existing behavior; if any of these three fail, that's a real bug in `scheduler.py` or `structure_analyst.py` to fix before proceeding (not an implementation step of this plan, since the Architecture section's claim is that no production code change is needed here).

- [ ] **Step 3: Commit**

```bash
git add tests/agents/test_structure_analyst.py tests/test_scheduler.py
git commit -m "test: pin exception-isolation contract — Scheduler.tick propagates, Scheduler.run survives"
```

---

### Task 2: `novelizer/brain/context.py` — pure brain-context note builders

**Files:**
- Create: `novelizer/brain/context.py`
- Test: `tests/brain/test_context.py`

**Interfaces:**
- Consumes: `ThreadRecord`, `Chapter`, `StructureScore` (`novelizer.store.models`); `stale_threads` (`novelizer.brain.staleness`, M3.2); `detect_sag_spike` (`novelizer.brain.sag_spike`, M3.2).
- Produces: `stale_threads_note(threads: list[ThreadRecord], chapters: list[Chapter]) -> str` and `pacing_flags_note(scores: list[StructureScore]) -> str` — both return `""` when there's nothing to report, and a `\n\n`-prefixed block otherwise (matching the exact `voice`/`cast` conditional-block shape already used in `author.py`/`editor.py`).

- [ ] **Step 1: Write the failing tests**

Create `tests/brain/test_context.py`:

```python
from novelizer.brain.context import stale_threads_note, pacing_flags_note
from novelizer.store.models import Chapter, ThreadRecord, ThreadState, StructureScore


def _chapters(n: int) -> list[Chapter]:
    return [Chapter(id=f"c{i}", title=str(i), prose="p") for i in range(n)]


def test_stale_threads_note_empty_when_nothing_stale():
    chs = _chapters(2)
    fresh = ThreadRecord(id="t1", name="Fresh", state=ThreadState.touched, last_chapter_id="c1")
    assert stale_threads_note([fresh], chs) == ""


def test_stale_threads_note_lists_stale_thread_name_and_id():
    chs = _chapters(5)
    stale = ThreadRecord(id="the-locket", name="The Locket", state=ThreadState.planted, last_chapter_id="c0")
    note = stale_threads_note([stale], chs)
    assert "The Locket" in note
    assert "the-locket" in note
    assert note.startswith("\n\n")


def test_stale_threads_note_omits_terminal_threads():
    chs = _chapters(10)
    closed = ThreadRecord(id="t1", name="Closed", state=ThreadState.paid_off, last_chapter_id="c0")
    assert stale_threads_note([closed], chs) == ""


def test_pacing_flags_note_empty_when_no_flags():
    scores = [StructureScore(chapter_id=f"c{i}", tension=0.5, pacing_label="steady") for i in range(3)]
    assert pacing_flags_note(scores) == ""


def test_pacing_flags_note_lists_flagged_chapter_and_direction():
    scores = [
        StructureScore(chapter_id="c1", tension=0.9, pacing_label="climax"),
        StructureScore(chapter_id="c2", tension=0.1, pacing_label="flat"),
        StructureScore(chapter_id="c3", tension=0.85, pacing_label="climax"),
    ]
    note = pacing_flags_note(scores)
    assert "c2" in note and "sag" in note
    assert note.startswith("\n\n")
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/brain/test_context.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'novelizer.brain.context'`.

- [ ] **Step 3: Implement**

Create `novelizer/brain/context.py`:

```python
from __future__ import annotations
from novelizer.brain.sag_spike import detect_sag_spike
from novelizer.brain.staleness import stale_threads
from novelizer.store.models import Chapter, StructureScore, ThreadRecord


def stale_threads_note(threads: list[ThreadRecord], chapters: list[Chapter]) -> str:
    """Build the Author-facing prompt block naming every currently-stale
    thread and the id the Author must cite to touch it back (per M3.1's
    thread identity rule -- ids are never invented, only cited). Empty
    string when nothing is stale, so Author.work()'s prompt stays
    byte-identical to pre-M3.3 output whenever the brain has nothing to say.
    """
    stale = stale_threads(threads, chapters)
    if not stale:
        return ""
    lines = "\n".join(f"- {t.name} (id:{t.id})" for t in stale)
    return f"\n\nStale threads (consider touching one, citing its id exactly):\n{lines}"


def pacing_flags_note(scores: list[StructureScore]) -> str:
    """Build the Editor-facing prompt block naming every chapter the pure
    sag/spike detector has flagged. Empty string when nothing is flagged.
    """
    flags = detect_sag_spike(scores)
    if not flags:
        return ""
    lines = "\n".join(f"- chapter {chapter_id}: {flag}" for chapter_id, flag in flags.items())
    return f"\n\nPacing flags:\n{lines}"
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/brain/test_context.py -v`
Expected: PASS (6 passed). Then `uv run pytest tests/ -v` for the full suite green.

- [ ] **Step 5: Commit**

```bash
git add novelizer/brain/context.py tests/brain/test_context.py
git commit -m "feat: novelizer.brain.context — pure stale-thread/pacing-flag prompt note builders"
```

---

### Task 3: Author's prompt gains the stale-threads note

**Files:**
- Modify: `novelizer/agents/author.py`
- Test: `tests/agents/test_author.py`

**Interfaces:**
- Consumes: `stale_threads_note` (Task 2); `ReadStore.list_chapters()` (existing).
- Produces: no new public interface — `Author.poll()`'s ctx dict gains a `"chapters"` key (the full chronological list, separate from the existing `"previous"` truncation); `_summarize()` appends a conditional stale-threads block, empty-safe.

- [ ] **Step 1: Write the failing tests**

Append to `tests/agents/test_author.py`:

```python
from novelizer.canon.events import ThreadPlanted


async def test_author_prompt_includes_stale_threads_note_when_present(stack):
    events, proj, read, committer = stack
    await events.append(EventType.THREAD_PLANTED, "the-locket", ThreadPlanted(id="the-locket", name="The Locket"))
    for i in range(4):
        await events.append(EventType.CHAPTER_CREATED, f"c{i}", Chapter(id=f"c{i}", title=str(i), prose="p"))
    await proj.catch_up()
    runner = FakeRunner(ChapterDraft(title="T", prose="P"))
    author = Author(runner, read, committer)
    ctx = await author.poll()
    await author.work(ctx)
    sent = runner.calls[-1]["messages"][0]["content"]
    assert "Stale threads" in sent
    assert "The Locket" in sent and "the-locket" in sent


async def test_author_prompt_omits_stale_threads_note_when_nothing_stale(stack):
    events, proj, read, committer = stack
    runner = FakeRunner(ChapterDraft(title="T", prose="P"))
    author = Author(runner, read, committer)
    ctx = await author.poll()
    await author.work(ctx)
    sent = runner.calls[-1]["messages"][0]["content"]
    assert "Stale threads" not in sent
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/agents/test_author.py -v`
Expected: FAIL — `test_author_prompt_includes_stale_threads_note_when_present` fails with `assert "Stale threads" in sent` being false, since `_summarize` doesn't yet build or append the note.

- [ ] **Step 3: Implement**

In `novelizer/agents/author.py`, add the import and update `poll()`/`_summarize()`:

```python
from __future__ import annotations
from novelizer.agents.base import BaseAgent, ChapterDraft, Runner
from novelizer.brain.context import stale_threads_note
from novelizer.canon.read_store import ReadStore
from novelizer.canon.committer import Committer
from novelizer.canon.events import EventType
from novelizer.canon.threads import TERMINAL_STATES
from novelizer.store.models import Chapter
```

```python
def _summarize(ctx: dict, casting_note: str = "", personality: str = "") -> str:
    world = "\n".join(f"- {e.title}: {e.body[:150]}" for e in ctx["world"][:10]) or "None yet."
    chars = "\n".join(f"- {c.name}: {c.traits} | arc: {c.arc_status}" for c in ctx["characters"][:8]) or "None yet."
    prev = "\n".join(f"- '{c.title}': {c.prose[:200]}" for c in ctx["previous"]) or "None yet."
    notes = "\n".join(f"Director: {s.body}" for s in ctx["signals"]) or "None."
    voice = f"\n\nWrite in this prose voice: {casting_note}" if casting_note else ""
    cast = f"\n\nIn character: {personality}" if personality else ""
    brain = stale_threads_note(ctx["threads"], ctx["chapters"])
    return (
        f"World lore:\n{world}\n\nCharacters:\n{chars}\n\n"
        f"Previous chapters:\n{prev}\n\nDirector notes:\n{notes}{voice}{cast}{brain}\n\nWrite the next chapter."
    )
```

```python
    async def poll(self) -> dict:
        chapters = await self._read.list_chapters()
        return {
            "world": await self._read.list_world_entries(),
            "characters": await self._read.list_characters(),
            "previous": chapters[-3:],
            "chapters": chapters,
            "signals": await self._read.list_unconsumed_signals(target_agent=self.name),
            "threads": await self._read.list_threads(),
        }
```

(Only the `stale_threads_note` import, the `"chapters"` key in `poll()`, and the `brain = ...`/`{brain}` additions to `_summarize` are new; `Author.__init__`, `readiness`, `work`, `commit`, `run_once`, `build_author_runner` are unchanged. `"Write the next chapter."` stays the final sentence, after the brain block, matching the existing `{voice}{cast}` tail-append convention.)

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/agents/test_author.py -v`
Expected: PASS (all prior + 2 new — including every existing `test_work_prompt_*`/`test_readiness_*`/`test_run_once_*` test, none of which set up a stale thread, so their prompts stay byte-identical). Then `uv run pytest tests/ -v` for the full suite green.

- [ ] **Step 5: Commit**

```bash
git add novelizer/agents/author.py tests/agents/test_author.py
git commit -m "feat: Author's prompt cites stale threads via novelizer.brain.context"
```

---

### Task 4: Editor's prompt gains the pacing-flags note

**Files:**
- Modify: `novelizer/agents/editor.py`
- Test: `tests/agents/test_editor.py`

**Interfaces:**
- Consumes: `pacing_flags_note` (Task 2); `ReadStore.list_structure_scores()` (existing, M3.2).
- Produces: no new public interface — `Editor.poll()`'s ctx dict gains a `"scores"` key; `Editor.work()` appends a conditional pacing-flags block, empty-safe.

- [ ] **Step 1: Write the failing tests**

Append to `tests/agents/test_editor.py`:

```python
from novelizer.canon.events import AnnotationStructureScored


async def test_editor_prompt_includes_pacing_flags_note_when_present(stack):
    events, proj, read, committer = stack
    await events.append(EventType.CHAPTER_CREATED, "c1", Chapter(id="c1", title="One", prose="p"))
    await events.append(EventType.ANNOTATION_STRUCTURE_SCORED, "c1",
                        AnnotationStructureScored(chapter_id="c1", tension=0.9, pacing_label="climax"))
    await events.append(EventType.ANNOTATION_STRUCTURE_SCORED, "c2",
                        AnnotationStructureScored(chapter_id="c2", tension=0.1, pacing_label="flat"))
    await events.append(EventType.ANNOTATION_STRUCTURE_SCORED, "c3",
                        AnnotationStructureScored(chapter_id="c3", tension=0.85, pacing_label="climax"))
    await proj.catch_up()
    runner = FakeRunner(EditorVerdict(verdict="approve", notes="clean"))
    agent = Editor(runner, read, committer)
    ctx = await agent.poll()
    await agent.work(ctx)
    sent = runner.calls[-1]["messages"][0]["content"]
    assert "Pacing flags" in sent
    assert "c2" in sent and "sag" in sent


async def test_editor_prompt_omits_pacing_flags_note_when_none_flagged(stack):
    events, proj, read, committer = stack
    await events.append(EventType.CHAPTER_CREATED, "c1", Chapter(id="c1", title="One", prose="p"))
    await proj.catch_up()
    runner = FakeRunner(EditorVerdict(verdict="approve", notes="clean"))
    agent = Editor(runner, read, committer)
    ctx = await agent.poll()
    await agent.work(ctx)
    sent = runner.calls[-1]["messages"][0]["content"]
    assert "Pacing flags" not in sent
    assert sent == f"Chapter title: One\n\nProse:\np"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/agents/test_editor.py -v`
Expected: FAIL — `test_editor_prompt_includes_pacing_flags_note_when_present` fails with `assert "Pacing flags" in sent` being false; `test_editor_prompt_omits_pacing_flags_note_when_none_flagged`'s exact-match assertion currently passes (proving the guard is a no-op today) and is included as the pinned byte-identical baseline the implementation must preserve.

- [ ] **Step 3: Implement**

In `novelizer/agents/editor.py`, add the import and update `poll()`/`work()`:

```python
from __future__ import annotations
from novelizer.agents.base import BaseAgent, Runner
from novelizer.agents.schemas import EditorVerdict
from novelizer.brain.context import pacing_flags_note
from novelizer.canon.read_store import ReadStore
from novelizer.canon.committer import Committer
from novelizer.canon.events import EventType
from novelizer.canon.threads import TERMINAL_STATES
from novelizer.store.models import DirectorSignal, SignalKind, EditorialStatus
```

```python
    async def poll(self) -> dict:
        drafts = await self._read.list_chapters(status=EditorialStatus.draft)
        return {
            "target": drafts[0] if drafts else None,
            "threads": await self._read.list_threads(),
            "scores": await self._read.list_structure_scores(),
        }
```

```python
    async def work(self, ctx: dict) -> EditorVerdict | None:
        ch = ctx["target"]
        if ch is None:
            return None
        voice = (
            f"\n\nEnforce this prose voice: {self._casting_note}; note any drift in your feedback."
            if self._casting_note
            else ""
        )
        cast = f"\n\nIn character: {self.personality}" if self.personality else ""
        voices = await self._character_voices_block(ch.character_ids)
        pacing = pacing_flags_note(ctx["scores"])
        msg = f"Chapter title: {ch.title}\n\nProse:\n{ch.prose}{voice}{cast}{voices}{pacing}"
        result = await self._runner.ainvoke({"messages": [{"role": "user", "content": msg}]})
        return result.get("structured_response")
```

(Only the `pacing_flags_note` import, the `"scores"` key in `poll()`, and the `pacing = ...`/`{pacing}` additions to `work()` are new; `SYSTEM_PROMPT`, `Editor.__init__`, `readiness`, `_character_voices_block`, `commit`, `run_once`, `build_editor_runner` are unchanged. `{voice}{cast}{voices}{pacing}` places the new section last, so `test_editor_prompt_omits_pacing_flags_note_when_none_flagged`'s byte-identical exact-match assertion — which pins the *pre-M3.3* prompt shape as a baseline — continues to hold whenever there's nothing to flag, exactly as `test_editor_prompt_omits_voices_section_when_none_set` from M2.3 already established for `voices`.)

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/agents/test_editor.py -v`
Expected: PASS (all prior + 2 new). Then `uv run pytest tests/ -v` for the full suite green.

- [ ] **Step 5: Commit**

```bash
git add novelizer/agents/editor.py tests/agents/test_editor.py
git commit -m "feat: Editor's prompt cites pacing flags via novelizer.brain.context"
```

---

### Task 5: `ThreadBoard` widget — pure line formatter + `Static` widget

**Files:**
- Create: `novelizer/tui/widgets/thread_board.py`
- Test: `tests/tui/test_thread_board.py`

**Interfaces:**
- Consumes: `is_thread_stale` (`novelizer.brain.staleness`, M3.2); `ThreadRecord`/`Chapter` (`novelizer.store.models`); `ReadStore.list_threads()`/`list_chapters()` (existing).
- Produces: `thread_board_line(thread: ThreadRecord, chapters: list[Chapter]) -> str`; `ThreadBoard(Static)` with `async def refresh_from(self, read) -> None`.

- [ ] **Step 1: Write the failing tests**

Create `tests/tui/test_thread_board.py`:

```python
from novelizer.tui.widgets.thread_board import thread_board_line
from novelizer.store.models import Chapter, ThreadRecord, ThreadState


def _chapters(n: int) -> list[Chapter]:
    return [Chapter(id=f"c{i}", title=str(i), prose="p") for i in range(n)]


def test_fresh_thread_line_shows_state_not_stale():
    chs = _chapters(2)
    t = ThreadRecord(id="t1", name="Fresh Thread", state=ThreadState.touched, last_chapter_id="c1")
    line = thread_board_line(t, chs)
    assert "Fresh Thread" in line and "STALE" not in line
    assert "touched" in line


def test_stale_thread_line_flags_stale():
    chs = _chapters(5)
    t = ThreadRecord(id="the-locket", name="The Locket", state=ThreadState.planted, last_chapter_id="c0")
    line = thread_board_line(t, chs)
    assert "The Locket" in line and "STALE" in line


def test_terminal_thread_never_flagged_stale():
    chs = _chapters(10)
    t = ThreadRecord(id="t1", name="Closed", state=ThreadState.paid_off, last_chapter_id="c0")
    line = thread_board_line(t, chs)
    assert "STALE" not in line and "paid_off" in line
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/tui/test_thread_board.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'novelizer.tui.widgets.thread_board'`.

- [ ] **Step 3: Implement**

Create `novelizer/tui/widgets/thread_board.py`:

```python
from __future__ import annotations
from textual.widgets import Static
from novelizer.brain.staleness import is_thread_stale
from novelizer.store.models import Chapter, ThreadRecord


def thread_board_line(thread: ThreadRecord, chapters: list[Chapter]) -> str:
    marker = "⚠ STALE" if is_thread_stale(thread, chapters) else thread.state.value
    return f"· {thread.name} (id:{thread.id})  [{marker}]"


class ThreadBoard(Static):
    async def refresh_from(self, read) -> None:
        threads = await read.list_threads()
        chapters = await read.list_chapters()
        lines = [thread_board_line(t, chapters) for t in threads]
        self.update("\n".join(lines) or "no threads yet")
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/tui/test_thread_board.py -v`
Expected: PASS (3 passed). Then `uv run pytest tests/ -v` for the full suite green.

- [ ] **Step 5: Commit**

```bash
git add novelizer/tui/widgets/thread_board.py tests/tui/test_thread_board.py
git commit -m "feat: ThreadBoard widget — flat thread list with live staleness via novelizer.brain"
```

---

### Task 6: `StoryShape` widget — pure line formatter + `Static` widget

**Files:**
- Create: `novelizer/tui/widgets/story_shape.py`
- Test: `tests/tui/test_story_shape.py`

**Interfaces:**
- Consumes: `detect_sag_spike` (`novelizer.brain.sag_spike`, M3.2); `StructureScore` (`novelizer.store.models`); `ReadStore.list_structure_scores()` (existing, M3.2).
- Produces: `story_shape_line(score: StructureScore, flag: str | None) -> str`; `StoryShape(Static)` with `async def refresh_from(self, read) -> None`.

- [ ] **Step 1: Write the failing tests**

Create `tests/tui/test_story_shape.py`:

```python
from novelizer.tui.widgets.story_shape import story_shape_line
from novelizer.store.models import StructureScore


def test_unflagged_score_line_has_no_marker():
    s = StructureScore(chapter_id="c1", tension=0.5, pacing_label="steady")
    line = story_shape_line(s, None)
    assert "c1" in line and "0.50" in line and "steady" in line
    assert "SAG" not in line and "SPIKE" not in line


def test_sag_flagged_score_line_shows_marker():
    s = StructureScore(chapter_id="c2", tension=0.1, pacing_label="flat")
    line = story_shape_line(s, "sag")
    assert "SAG" in line


def test_spike_flagged_score_line_shows_marker():
    s = StructureScore(chapter_id="c2", tension=0.95, pacing_label="climax")
    line = story_shape_line(s, "spike")
    assert "SPIKE" in line
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/tui/test_story_shape.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'novelizer.tui.widgets.story_shape'`.

- [ ] **Step 3: Implement**

Create `novelizer/tui/widgets/story_shape.py`:

```python
from __future__ import annotations
from textual.widgets import Static
from novelizer.brain.sag_spike import detect_sag_spike
from novelizer.store.models import StructureScore


def story_shape_line(score: StructureScore, flag: str | None) -> str:
    marker = f"  [{flag.upper()}]" if flag else ""
    return f"· {score.chapter_id}  tension={score.tension:.2f}  {score.pacing_label}{marker}"


class StoryShape(Static):
    async def refresh_from(self, read) -> None:
        scores = await read.list_structure_scores()
        flags = detect_sag_spike(scores)
        lines = [story_shape_line(s, flags.get(s.chapter_id)) for s in scores]
        self.update("\n".join(lines) or "no chapters scored yet")
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/tui/test_story_shape.py -v`
Expected: PASS (3 passed). Then `uv run pytest tests/ -v` for the full suite green.

- [ ] **Step 5: Commit**

```bash
git add novelizer/tui/widgets/story_shape.py tests/tui/test_story_shape.py
git commit -m "feat: StoryShape widget — per-chapter tension/pacing with live sag/spike flags"
```

---

### Task 7: Wire `ThreadBoard`/`StoryShape` into `NovelizerApp.compose()`

**Files:**
- Modify: `novelizer/tui/app.py`
- Modify: `novelizer/tui/app.tcss`
- Test: `tests/tui/test_app_layout.py`

**Interfaces:**
- Consumes: `ThreadBoard`, `StoryShape` (Tasks 5, 6).
- Produces: no new public interface — `NovelizerApp.compose()` yields two new panes, `#thread_board` and `#story_shape`; `on_mount` starts two new refresh-loop workers.

- [ ] **Step 1: Write the failing test**

Append to `tests/tui/test_app_layout.py`:

```python
@pytest.mark.asyncio
async def test_mission_control_shows_thread_board_and_story_shape_panes():
    from novelizer.canon.events import EventType, ThreadPlanted, AnnotationStructureScored
    from novelizer.store.models import Chapter

    fd, path = tempfile.mkstemp(suffix=".db"); os.close(fd)
    settings = Settings(db_path=path, projector_interval=0.1)
    rt = Runtime(settings, runners=_runners())
    await rt.start()
    for name in ["world_architect", "character_keeper", "author", "editor", "continuity_checker", "retconner", "structure_analyst"]:
        rt.scheduler.pause_agent(name)
    try:
        await rt.events.append(EventType.CHAPTER_CREATED, "c1", Chapter(id="c1", title="One", prose="p"))
        await rt.events.append(EventType.THREAD_PLANTED, "the-locket", ThreadPlanted(id="the-locket", name="The Locket"))
        await rt.events.append(EventType.ANNOTATION_STRUCTURE_SCORED, "c1",
                               AnnotationStructureScored(chapter_id="c1", tension=0.6, pacing_label="rising"))
        await rt.projector.catch_up()
        app = NovelizerApp(rt)
        async with app.run_test() as pilot:
            from textual.widgets import Static
            assert app.query_one("#thread_board", Static) is not None
            assert app.query_one("#story_shape", Static) is not None
            await pilot.pause(0.5)
            board_text = str(app.query_one("#thread_board", Static).renderable)
            shape_text = str(app.query_one("#story_shape", Static).renderable)
            assert "The Locket" in board_text
            assert "c1" in shape_text and "rising" in shape_text
    finally:
        await rt.close(); os.unlink(path)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/tui/test_app_layout.py::test_mission_control_shows_thread_board_and_story_shape_panes -v`
Expected: FAIL — `textual.css.query.NoMatches` (no widget with id `#thread_board`).

- [ ] **Step 3: Implement**

In `novelizer/tui/app.py`, add the imports, two new `compose()` panes, two new worker loops, and their `on_mount` registration:

```python
from novelizer.tui.widgets.roster import AgentRoster
from novelizer.tui.widgets.browser import StoryBrowser
from novelizer.tui.widgets.browser_model import detail_text
from novelizer.tui.widgets.proposals_model import pending_lines
from novelizer.tui.widgets.thread_board import ThreadBoard
from novelizer.tui.widgets.story_shape import StoryShape
```

```python
    def compose(self) -> ComposeResult:
        yield Header()
        with Horizontal(id="body"):
            with Vertical(id="left"):
                yield RichLog(highlight=False, markup=False, id="feed")
                yield AgentRoster(id="roster")
                yield Static("no pending proposals", id="proposals")
                yield ThreadBoard("no threads yet", id="thread_board")
                yield StoryShape("no chapters scored yet", id="story_shape")
            with Vertical(id="right"):
                yield StoryBrowser("Story", id="browser")
                yield Static("Select an item to view details.", id="detail")
        yield Static("AUTONOMY: loading…", id="statusbar")
        yield Input(id="command", placeholder="command… (seed/focus/pause/resume)")
        yield Footer()

    async def on_mount(self) -> None:
        self.run_worker(self._projector_loop(), exclusive=False)
        self.run_worker(self._scheduler_loop(), exclusive=False)
        self.run_worker(self._feed_loop(), exclusive=False)
        self.run_worker(self._roster_loop(), exclusive=False)
        self.run_worker(self._browser_loop(), exclusive=False)
        self.run_worker(self._proposals_loop(), exclusive=False)
        self.run_worker(self._statusbar_loop(), exclusive=False)
        self.run_worker(self._thread_board_loop(), exclusive=False)
        self.run_worker(self._story_shape_loop(), exclusive=False)
```

```python
    async def _thread_board_loop(self) -> None:
        while True:
            try:
                await self.query_one("#thread_board", ThreadBoard).refresh_from(self.runtime.read)
            except Exception as e:
                self._report_worker_error("thread_board", e)
            await asyncio.sleep(1.0)

    async def _story_shape_loop(self) -> None:
        while True:
            try:
                await self.query_one("#story_shape", StoryShape).refresh_from(self.runtime.read)
            except Exception as e:
                self._report_worker_error("story_shape", e)
            await asyncio.sleep(1.0)
```

(Only the two new imports, the two new `compose()` yields, the two new `run_worker(...)` calls in `on_mount`, and the two new loop methods are new; every other method — `_projector_loop`, `_scheduler_loop`, `_feed_loop`, `_roster_loop`, `_browser_loop`, `_proposals_loop`, `_statusbar_loop`, `action_focus_command`, `action_toggle_room`, `_run_command`, `on_input_submitted`, `on_tree_node_selected` — is unchanged.)

In `novelizer/tui/app.tcss`, add CSS for the two new panes (after `#proposals`):

```css
#thread_board { height: auto; max-height: 8; border: round $secondary; }
#story_shape { height: auto; max-height: 8; border: round $secondary; }
```

(Every existing rule is unchanged; these two lines are new.)

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/tui/test_app_layout.py -v`
Expected: PASS (all prior + 1 new). Then `uv run pytest tests/ -v` for the full suite green (this re-runs `test_app_smoke.py`/`test_app_resilience.py`/`test_app_commands.py` too, confirming the two new always-running worker loops don't destabilize any existing TUI test).

- [ ] **Step 5: Commit**

```bash
git add novelizer/tui/app.py novelizer/tui/app.tcss tests/tui/test_app_layout.py
git commit -m "feat: wire ThreadBoard/StoryShape panes into Mission Control"
```

---

### Task 8: Regression — brain-context injection leaves prompts byte-identical when the brain has nothing to report

**Files:**
- Test: `tests/agents/test_author.py`, `tests/agents/test_editor.py`

**Interfaces:** none produced — this task pins the byte-identical-when-empty contract explicitly across every existing prompt-shape assertion, as an aggregate regression check (each individual piece is already covered by Tasks 3/4's "omits" tests; this task adds one direct full-string pin per agent for extra confidence given how many optional sections now compose in one prompt).

- [ ] **Step 1: Write the tests**

Append to `tests/agents/test_author.py`:

```python
async def test_author_prompt_byte_identical_to_pre_m3_3_shape_when_brain_silent(stack):
    events, proj, read, committer = stack
    runner = FakeRunner(ChapterDraft(title="T", prose="P"))
    author = Author(runner, read, committer)
    ctx = await author.poll()
    await author.work(ctx)
    sent = runner.calls[-1]["messages"][0]["content"]
    expected = (
        "World lore:\nNone yet.\n\nCharacters:\nNone yet.\n\n"
        "Previous chapters:\nNone yet.\n\nDirector notes:\nNone.\n\nWrite the next chapter."
    )
    assert sent == expected
```

Append to `tests/agents/test_editor.py`:

```python
async def test_editor_prompt_byte_identical_to_pre_m3_3_shape_when_brain_silent(stack):
    events, proj, read, committer = stack
    await events.append(EventType.CHAPTER_CREATED, "c1", Chapter(id="c1", title="One", prose="p"))
    await proj.catch_up()
    runner = FakeRunner(EditorVerdict(verdict="approve", notes="clean"))
    agent = Editor(runner, read, committer)
    ctx = await agent.poll()
    await agent.work(ctx)
    sent = runner.calls[-1]["messages"][0]["content"]
    assert sent == "Chapter title: One\n\nProse:\np"
```

- [ ] **Step 2: Run tests to verify they pass**

Run: `uv run pytest tests/agents/test_author.py tests/agents/test_editor.py -v`
Expected: PASS immediately (both tasks 3/4 already guarantee this; these are aggregate pins, not new implementation).

- [ ] **Step 3: Commit**

```bash
git add tests/agents/test_author.py tests/agents/test_editor.py
git commit -m "test: pin byte-identical Author/Editor prompts when brain context has nothing to report"
```

---

### Task 9: M3 done-when, part (a) — the CI-verifiable mechanical chain

**Files:**
- Test: `tests/agents/test_author.py`

**Interfaces:** none produced — this is the doc's stated part-(a) black-box chain, composing Tasks 2–5's pieces into the exact sequence the M3-shape-and-threads.md done-when describes. No production code changes.

- [ ] **Step 1: Write the test**

Append to `tests/agents/test_author.py`:

```python
async def test_m3_done_when_mechanical_chain_stale_thread_to_touched_to_not_stale(stack):
    """The M3 done-when, part (a): seed a thread stale enough that
    StalenessAnalyzer flags it -> assert the Author's built prompt names it
    (asserted on literal prompt text) -> drive the Author with a FakeRunner
    preset whose structured output declares a thread_intents entry touching
    that exact id -> assert the resulting thread.touched event lands via the
    Committer -> assert the Thread Board's render-time helper (thread_board_line,
    via is_thread_stale) no longer reports the thread stale. No live model call."""
    from novelizer.canon.events import ThreadPlanted
    from novelizer.agents.schemas import ThreadIntent
    from novelizer.tui.widgets.thread_board import thread_board_line

    events, proj, read, committer = stack
    await events.append(EventType.THREAD_PLANTED, "the-locket", ThreadPlanted(id="the-locket", name="The Locket"))
    for i in range(4):
        await events.append(EventType.CHAPTER_CREATED, f"c{i}", Chapter(id=f"c{i}", title=str(i), prose="p"))
    await proj.catch_up()

    # Step 1: staleness is real before we touch anything.
    thread_before = await read.get_thread("the-locket")
    chapters = await read.list_chapters()
    assert "STALE" in thread_board_line(thread_before, chapters)

    # Step 2: the Author's prompt names the stale thread by name and id.
    probe_runner = FakeRunner(ChapterDraft(title="T", prose="P"))
    probe_author = Author(probe_runner, read, committer)
    ctx = await probe_author.poll()
    await probe_author.work(ctx)
    prompt = probe_runner.calls[-1]["messages"][0]["content"]
    assert "The Locket" in prompt and "the-locket" in prompt

    # Step 3: a scripted Author response declares a matching touch intent.
    draft = ChapterDraft(
        title="Chapter Five", prose="The locket surfaces again.",
        thread_intents=[ThreadIntent(action="touch", id="the-locket", note="resurfaces")],
    )
    author = Author(FakeRunner(draft), read, committer)
    await author.run_once()
    await proj.catch_up()

    # Step 4: the thread.touched event landed, and the thread is no longer stale.
    log = await events.events_since(0)
    assert any(e.event_type == EventType.THREAD_TOUCHED and e.payload["id"] == "the-locket" for e in log)
    thread_after = await read.get_thread("the-locket")
    assert thread_after.touch_count == 1
    chapters_after = await read.list_chapters()
    assert "STALE" not in thread_board_line(thread_after, chapters_after)
```

- [ ] **Step 2: Run the test to verify it passes**

Run: `uv run pytest tests/agents/test_author.py::test_m3_done_when_mechanical_chain_stale_thread_to_touched_to_not_stale -v`
Expected: PASS — every piece it composes (`stale_threads_note`/Task 3, `_commit_thread_intents`/M3.1, `thread_board_line`/Task 5) was already implemented and tested in isolation; this test is the integration proof, not new implementation. If it fails, the failure will point at exactly which link in the chain broke (prompt text, event landing, or staleness recomputation) — fix that task's implementation, not this test.

- [ ] **Step 3: Run the full suite**

Run: `uv run pytest tests/ -v`
Expected: all tests green.

- [ ] **Step 4: Commit**

```bash
git add tests/agents/test_author.py
git commit -m "test: M3 done-when part (a) — CI-verifiable stale-thread-to-touched mechanical chain"
```

---

### Task 10: M3 done-when, part (b) — the `live_llm`-marked live-LLM smoke test

**Files:**
- Create: `tests/agents/test_author_live_llm.py`

**Interfaces:** none produced — a new `live_llm`-marked test file, excluded from the default run (`addopts = "-m 'not ollama and not live_llm'"` in `pyproject.toml`) and run manually or in an environment with a live Ollama-compatible endpoint, following `tests/store/test_embeddings.py`'s existing precedent for this milestone-boundary marker.

- [ ] **Step 1: Write the test**

Create `tests/agents/test_author_live_llm.py`:

```python
"""M3 done-when, part (b): the true observation for M3, per the doc's own
framing -- a FakeRunner-driven test only proves the pipe is connected, not
that a real LLM will act on what flows through it. This test seeds the same
stale-thread fixture as the mechanical chain (tests/agents/test_author.py's
test_m3_done_when_mechanical_chain_stale_thread_to_touched_to_not_stale) and
runs the *real* Author -- via build_author_runner against a live
OpenAI-compatible endpoint (novelizer.config.Settings' llm_base_url,
author_model) -- with no director signal and no manual prompt beyond what
the room already injects, and asserts it reacts to the injected stale-thread
note by declaring a matching thread_intents entry, unprompted.

Requires the configured OpenAI-compatible LLM endpoint (`Settings().llm_base_url`)
to be reachable and serving the model named by NOVELIZER_AUTHOR_MODEL (see
.env.example / README's Configuration table). Run explicitly with:
uv run pytest -m live_llm tests/agents/test_author_live_llm.py -v
"""
import os
import tempfile
import pytest
from novelizer.config import Settings
from novelizer.canon.event_store import EventStore
from novelizer.canon.projector import Projector
from novelizer.canon.read_store import ReadStore
from novelizer.canon.committer import Committer
from novelizer.canon.events import EventType, ThreadPlanted
from novelizer.agents.author import Author, build_author_runner
from novelizer.store.models import Chapter


@pytest.fixture
async def stack():
    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    events = EventStore(path)
    await events.init()
    proj = Projector(events, path)
    await proj.init()
    read = ReadStore(path)
    await read.init()
    yield events, proj, read, Committer(events)
    await read.close()
    await proj.close()
    await events.close()
    os.unlink(path)


@pytest.mark.live_llm
async def test_real_author_reacts_to_a_stale_thread_unprompted(stack):
    events, proj, read, committer = stack
    await events.append(EventType.THREAD_PLANTED, "the-locket", ThreadPlanted(id="the-locket", name="The Locket"))
    for i in range(4):
        await events.append(
            EventType.CHAPTER_CREATED, f"c{i}",
            Chapter(id=f"c{i}", title=f"Chapter {i}", prose=f"Chapter {i} prose, nothing about the locket."),
        )
    await proj.catch_up()

    settings = Settings()
    runner = build_author_runner(settings)
    author = Author(runner, read, committer)
    await author.run_once()
    await proj.catch_up()

    log = await events.events_since(0)
    touched = [e for e in log if e.event_type == EventType.THREAD_TOUCHED and e.payload.get("id") == "the-locket"]
    assert touched, (
        "The real Author, given the injected stale-thread note and no other "
        "prompting, did not declare a thread_intents entry touching 'the-locket'."
    )
```

- [ ] **Step 2: Confirm it's excluded from the default run**

Run: `uv run pytest tests/ -v`
Expected: all tests green, and `tests/agents/test_author_live_llm.py::test_real_author_reacts_to_a_stale_thread_unprompted` does not appear in the run (excluded by `addopts = "-m 'not ollama and not live_llm'"`).

- [ ] **Step 3: Manually verify against a live endpoint (documented, not CI-run)**

With a local Ollama (or other OpenAI-compatible endpoint) serving `NOVELIZER_AUTHOR_MODEL`:

```bash
uv run pytest -m live_llm tests/agents/test_author_live_llm.py -v
```

Expected: PASS, confirming the real Author reacts to the injected stale-thread note. Record the result (pass/fail, model used) in the M3.3 plan's completion notes when this task is executed — this is the milestone's true done-when observation and, per the doc, CI cannot prove it; a documented manual run stands in for CI here exactly as M1/M2's own live-LLM checks did.

- [ ] **Step 4: Commit**

```bash
git add tests/agents/test_author_live_llm.py
git commit -m "test: M3 done-when part (b) — live_llm-marked live-LLM stale-thread reaction smoke test"
```

---

### Task 11: Docs — mark M3.3 and M3 complete, document the Story Brain views and brain context

**Files:**
- Modify: `docs/submilestones/M3-shape-and-threads.md`
- Modify: `docs/MILESTONES.md`
- Modify: `README.md`

**Interfaces:** none (documentation only).

- [ ] **Step 1: Update the sub-milestone table**

In `docs/submilestones/M3-shape-and-threads.md`, change the M3.3 row's `Status` cell from `⬜ not started` to `✅ complete`.

- [ ] **Step 2: Update the parent milestone table**

In `docs/MILESTONES.md`, change the M3 row's `Status` cell from `⬜ not started` (or whatever its current value is) to `✅ complete`.

- [ ] **Step 3: Extend the README**

In `README.md`, add a new subsection immediately after the "Staleness & pacing analysis" subsection added in M3.2 (before any following top-level heading):

```markdown
### Story Shape & Thread Board, and brain context in prompts

Mission Control's left column gains two more live panes: **Thread Board**
(every thread, its state, and a `STALE` marker once 3 chapters have passed
with no touch/pay-off/abandonment) and **Story Shape** (every scored
chapter's tension and pacing label, with `SAG`/`SPIKE` markers). Both read
straight from canon and call the exact same pure functions
(`novelizer.brain.staleness.is_thread_stale`, `novelizer.brain.sag_spike.detect_sag_spike`)
that build the notes injected into the Author's and Editor's prompts — so
the room's two views of "what's stale" or "what's sagging" can never
disagree.

The Author sees a **stale threads** note naming each stale thread and the
id it must cite to touch it back (per the thread identity rule — ids are
minted only at plant time, never invented); the Editor sees a **pacing
flags** note naming sagging/spiking chapters. Both notes are empty, and the
prompt is byte-identical to a story with no Story Brain signal, whenever
there's nothing to report — following the exact conditional-injection
pattern `casting_note`/`personality` (M2) and character voices (M2.3)
already established.
```

- [ ] **Step 4: Commit**

```bash
git add docs/submilestones/M3-shape-and-threads.md docs/MILESTONES.md README.md
git commit -m "docs: mark M3.3 and M3 complete; document Story Shape/Thread Board and brain context injection"
```

---

## Self-Review

**Spec coverage against the M3.3 row, its two-part done-when, and Load-bearing design decisions in `docs/submilestones/M3-shape-and-threads.md`:**
- "`story_shape.py` (renders per-chapter tension/pacing scores + flagged sag/spike, reading `annotation.structure_scored` rows)" — Task 6.
- "`thread_board.py` (renders threads by state; stale ones highlighted by calling the *same* `StalenessAnalyzer` function from M3.2 at render time via a small `ReadStore`-backed helper)" — Task 5 (`thread_board_line` imports `is_thread_stale` directly, no re-implementation).
- "staleness is never persisted as a projection field or recomputed with separate logic" — confirmed: `ThreadRecord` (M3.1) has no staleness field; `thread_board_line` and `stale_threads_note` both call `novelizer.brain.staleness` functions, never their own logic.
- "wired into `NovelizerApp.compose()`" — Task 7.
- "a small `BrainContext` provider... that Runtime builds from `ReadStore` queries and hands to Author/Editor as an additional optional constructor param... conditional string appended in `work()`/`_summarize()` only when non-empty, byte-identical output when the brain has nothing to report" — the injection *mechanism* is implemented verbatim (Tasks 3/4, pinned byte-identical by Task 8); the *where it's computed* is explicitly resolved in favor of `poll()`-time computation over a frozen constructor param, since the doc's own literal reading would freeze a signal that must be live — stated in the Architecture section and flagged again below as an open decision.
- "Author sees a 'stale threads' note (including the thread ids it's allowed to reference per M3.1's identity rule)" — Task 3 (`stale_threads_note` names both the freeform `name` and the citable `id`).
- "Editor sees pacing flags" — Task 4.
- Done-when (a), the CI-verifiable mechanical chain, described clause-by-clause in the doc — Task 9 implements every clause in the stated order: seed thread+chapters → `StalenessAnalyzer` flags it (asserted via `thread_board_line`) → `BrainContext` string contains the thread's name/id (asserted on literal prompt text) → `FakeRunner`-driven Author declares a matching `thread_intents` touch → `thread.touched` lands via the `Committer` → the Thread Board's render-time helper no longer reports it stale.
- Done-when (b), the `live_llm`-marked live-LLM smoke test, "the true done-when observation for M3" — Task 10, following the pattern of `tests/store/test_embeddings.py`'s existing `@pytest.mark.ollama` precedent (split into its own `live_llm` marker since this test hits the OpenAI-compatible chat endpoint, not Ollama specifically), with a documented-manual-run step since CI excludes it by default (`addopts = "-m 'not ollama and not live_llm'"`).
- The carry-over regression item from M3.2's review — Task 1, test-only, confirming (not changing) `Scheduler`/`NovelizerApp` exception-isolation behavior.

**Design decisions the M3.3 row left open or worded ambiguously, resolved here (flagged per the dispatch instructions):**
1. **`BrainContext` is computed live inside `poll()`, not passed as a frozen constructor parameter.** The doc's "hands to Author/Editor as an additional optional constructor param, following the exact M2 pattern" is read as describing the *conditional-string-in-work()* injection mechanism (which this plan follows exactly), not literally freezing the note at `Runtime.start()` — a frozen note would go stale the moment a new chapter is authored, defeating the point of "stale thread" detection. `Author.poll()`/`Editor.poll()` are extended (Tasks 3/4) exactly the way M3.1 already extended them with `ctx["threads"]`, keeping `_commit_thread_intents`'s signature (`intents, active_thread_ids, chapter_id=""`) completely unchanged, per the assignment's explicit instruction.
2. **Module home for the note builders: `novelizer/brain/context.py`**, alongside M3.2's `staleness.py`/`sag_spike.py` — pure, `ReadStore`-free functions, consistent with that package's established shape.
3. **Widget architecture: `AgentRoster`'s pattern (`Static` + pure line formatter), not `StoryBrowser`'s (`Tree` + section/drill-in model)** — Thread Board and Story Shape are flat lists of rows, not hierarchical browsable collections, so the simpler existing precedent fits better and avoids inventing unnecessary Tree-node plumbing.
4. **Placement: two more persistent `Static` panes in Mission Control's left column**, not a dedicated full-screen drill-in view with its own keybinding. The vision spec lists Story Shape/Thread Board among the *drill-in views* long-term, but M3.3's stated done-when only requires panes "wired into `NovelizerApp.compose()`" — this plan satisfies that literally and notes the drill-in/keybinding upgrade as a natural, explicitly-flagged follow-up rather than silently expanding or narrowing scope.
5. **The carry-over regression item targets two real catch-alls (`Scheduler.run()`, `NovelizerApp._scheduler_loop()`), not "`Scheduler.tick`'s catch-all"** — reading `scheduler.py` shows `tick()` has no try/except by design (confirmed by `tests/test_scheduler.py`'s existing behavior), so Task 1 corrects the assignment's framing to the two places where exception-swallowing actually happens, and adds the missing `pydantic.ValidationError`-from-a-malformed-score case alongside the existing `RuntimeError` case already covered by `tests/tui/test_app_resilience.py`.

**Placeholder scan:** every task's Step 3 shows complete code — full new files (`brain/context.py`, `thread_board.py`, `story_shape.py`, `test_author_live_llm.py`) or exact before/after snippets anchored to current file contents (re-read from the post-M3.2-merge `master` branch immediately before writing this plan: `scheduler.py`, `app.py`, `app.tcss`, `author.py`, `editor.py`, `roster.py`, `browser.py`, `browser_model.py`, and the full `tests/tui/` directory including `test_app_layout.py`/`test_app_resilience.py`/`test_app_smoke.py`/`test_roster.py`). No "similar to Task N", no `...` elisions, no TODOs.

**Type consistency:** `stale_threads_note(threads: list[ThreadRecord], chapters: list[Chapter]) -> str` (Task 2) matches `Author.poll()`'s `ctx["threads"]`/`ctx["chapters"]` types (Task 3) and `thread_board_line`'s parameters (Task 5) exactly — all three call the identical `novelizer.brain.staleness.is_thread_stale`/`stale_threads` functions with identical argument shapes, so there is exactly one staleness computation path in the codebase. `pacing_flags_note(scores: list[StructureScore]) -> str` (Task 2) matches `Editor.poll()`'s `ctx["scores"]` (Task 4) and `story_shape_line`'s `flags.get(...)` lookup (Task 6) exactly, all sourced from `ReadStore.list_structure_scores()`.

**DDD/SOLID:**
- Single Responsibility: `novelizer/brain/context.py` only builds prompt-note strings; `thread_board.py`/`story_shape.py` only render; `Author`/`Editor.poll()` are the only places that fetch `ReadStore` data for the brain; `NovelizerApp` only wires and refreshes.
- Open/Closed: `Author.poll()`/`_summarize()` and `Editor.poll()`/`work()` each gain one new dict key and one new conditional append, following the exact `casting_note`/`personality`/`voices` precedent — no existing section's logic is touched. `NovelizerApp.compose()`/`on_mount()` each gain new, additive lines; no existing pane or loop is modified.
- Dependency Inversion / bounded context: `novelizer/brain/` remains a pure, `ReadStore`-free analysis layer (Story Brain, per the vision spec); `Author`/`Editor` depend on it only through plain function calls over data they already fetch, never reaching into Brain internals; `ThreadBoard`/`StoryShape` depend on `ReadStore` and `novelizer.brain` only, never on agent internals.
- Event sourcing: no new event types or projections in this plan; every rendered value (thread state, structure score) already flows from the event log through existing M3.1/M3.2 projections.

**Backward-compatibility check:** `Author`/`Editor`'s prompts are byte-identical whenever the brain has nothing to report — guaranteed by Tasks 3/4's "omits" tests and pinned again in aggregate by Task 8; every pre-existing `Author`/`Editor` test in `tests/agents/test_author.py`/`test_editor.py` constructs fixtures with no stale thread and no flagged score, so none of them exercise the new sections and all continue to pass unmodified. `NovelizerApp.compose()`'s two new panes and `on_mount()`'s two new workers are additive; `tests/tui/test_app_smoke.py`, `test_app_resilience.py`, and `test_app_commands.py` (none of which assert on `#thread_board`/`#story_shape` or a fixed pane count) are re-run in full by every task's Step 4/5 and are unaffected. `Scheduler`/`Committer`/`GatingCommitter`/`_commit_thread_intents` are untouched by this plan.
