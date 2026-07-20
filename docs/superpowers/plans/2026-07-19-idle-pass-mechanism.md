# Idle-Pass Mechanism Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Let idle maintenance agents (CharacterKeeper, WorldArchitect, ContinuityChecker) either skip dispatch entirely (readiness watermark) or explicitly pass ("nothing to revise — carry on") with an extended backoff, so the Author gets the scheduler slots.

**Architecture:** Two layers on `BaseAgent`: (1) an in-memory fingerprint watermark that zeroes readiness when the external story state an agent cares about is unchanged since its last successful run; (2) a `no_action` pass verdict in structured output that skips canon mutation, posts a one-line remark, and backs the agent off for 3 intervals via `note_pass()`. Spec: `docs/superpowers/specs/2026-07-19-idle-pass-mechanism-design.md`.

**Tech Stack:** Python ≥3.13, pydantic, pytest (`asyncio_mode = "auto"` — plain `async def` tests), hypothesis, `uv` for running.

## Global Constraints

- Run tests ONLY inside this worktree (`.claude/worktrees/idle-pass-mechanism`), NEVER in the main checkout (DB-lock incident on record).
- Test command: `uv run pytest <path> -v` (live-LLM tests are excluded by default via `addopts = "-m 'not live_llm'"`).
- Watermarks and backoff are in-memory only — no new persisted state, no new event types, no new `SignalKind`.
- Pass backoff multiplier is exactly 3 (`PASS_BACKOFF_MULTIPLIER = 3`).
- Default pass remark text is exactly: `Nothing needs my attention — carry on with the story.`
- `no_action` defaults to `False` in every schema — absent field ⇒ existing behavior.
- Editor, Retconner, StructureAnalyst, Author, chat feature: untouched.
- Follow existing test style: `stack` fixture (EventStore/Projector/ReadStore/Committer over a tempfile DB), `FakeRunner` returning a canned `structured_response`.

---

### Task 1: BaseAgent pass backoff (`note_pass`)

**Files:**
- Modify: `novelizer/agents/base.py` (constructor ~line 36-54; `ready_for_interval`/`seconds_until_ready` at lines 67-74; add constants near top)
- Test: `tests/agents/test_base.py` (append)

**Interfaces:**
- Produces: `PASS_BACKOFF_MULTIPLIER: int = 3` and `DEFAULT_PASS_REMARK: str` (module constants in `novelizer.agents.base`); `BaseAgent.note_pass(now: float | None = None) -> None`; `BaseAgent._backoff_until: float` (init 0.0). `ready_for_interval`/`seconds_until_ready` honor `_backoff_until`. Tasks 4-6 call `self.note_pass()` and import `DEFAULT_PASS_REMARK`.

- [ ] **Step 1: Write the failing tests** — append to `tests/agents/test_base.py`:

```python
from novelizer.agents.base import BaseAgent, DEFAULT_PASS_REMARK, PASS_BACKOFF_MULTIPLIER


def test_note_pass_extends_backoff_beyond_interval():
    agent = BaseAgent(runner=None, read_store=None, committer=None, interval=100)
    agent.mark_ran(1000.0)
    agent.note_pass(now=1000.0)
    # Normal interval has elapsed at t=1100, but the pass backoff (3x) has not.
    assert not agent.ready_for_interval(1100.0)
    assert agent.seconds_until_ready(1100.0) == 200.0
    assert agent.ready_for_interval(1300.0)


def test_note_pass_defaults_to_monotonic_clock():
    agent = BaseAgent(runner=None, read_store=None, committer=None, interval=100)
    agent.note_pass()
    import time
    assert agent._backoff_until > time.monotonic()


def test_no_pass_means_plain_interval_gate():
    agent = BaseAgent(runner=None, read_store=None, committer=None, interval=100)
    agent.mark_ran(1000.0)
    assert agent.ready_for_interval(1100.0)


def test_pass_constants():
    assert PASS_BACKOFF_MULTIPLIER == 3
    assert DEFAULT_PASS_REMARK == "Nothing needs my attention — carry on with the story."
```

- [ ] **Step 2: Run to verify failure**

Run: `uv run pytest tests/agents/test_base.py -v -k "note_pass or pass_constants or plain_interval"`
Expected: FAIL (ImportError: cannot import `DEFAULT_PASS_REMARK`).

- [ ] **Step 3: Implement** in `novelizer/agents/base.py`. After the `logger = ...` line add:

```python
# An agent that ran on fresh material but explicitly chose not to act steps
# back for this many intervals instead of one, freeing dispatch slots.
PASS_BACKOFF_MULTIPLIER = 3
DEFAULT_PASS_REMARK = "Nothing needs my attention — carry on with the story."
```

In `BaseAgent.__init__`, after `self._last_run = 0.0` add:

```python
        self._backoff_until = 0.0
```

Replace `ready_for_interval` and `seconds_until_ready` with:

```python
    def ready_for_interval(self, now: float) -> bool:
        return (now - self._last_run) >= self.interval and now >= self._backoff_until

    def seconds_until_ready(self, now: float) -> float:
        return max(0.0, self.interval - (now - self._last_run), self._backoff_until - now)
```

After `mark_ran` add:

```python
    def note_pass(self, now: float | None = None) -> None:
        """Record an explicit "nothing to do" verdict: back off for
        PASS_BACKOFF_MULTIPLIER intervals instead of one. Same clock family
        as the scheduler's default (time.monotonic)."""
        if now is None:
            now = time.monotonic()
        self._backoff_until = now + self.interval * PASS_BACKOFF_MULTIPLIER
```

(`time` is already imported in base.py.)

- [ ] **Step 4: Run to verify pass**

Run: `uv run pytest tests/agents/test_base.py -v`
Expected: PASS (all, including pre-existing tests).

- [ ] **Step 5: Commit**

```bash
git add novelizer/agents/base.py tests/agents/test_base.py
git commit -m "feat(agents): note_pass backoff — explicit idle pass extends interval 3x"
```

---

### Task 2: BaseAgent readiness watermark

**Files:**
- Modify: `novelizer/agents/base.py`
- Test: `tests/agents/test_base.py` (append)

**Interfaces:**
- Consumes: nothing new.
- Produces: `BaseAgent._fingerprint() -> tuple | None` (async, default `None` = watermarking disabled); `BaseAgent._gate_on_watermark(score: float) -> float` (async; returns `0.0` when the current fingerprint equals the recorded one, else `score`); `BaseAgent._record_watermark() -> None` (async; stores the current fingerprint — call at the END of a successful `_run`, after commits); `BaseAgent._last_fingerprint: tuple | None` (init `None`). Tasks 4 and 6 override `_fingerprint` and call the other two.

- [ ] **Step 1: Write the failing tests** — append to `tests/agents/test_base.py`:

```python
class _WatermarkAgent(BaseAgent):
    def __init__(self, fp):
        super().__init__(runner=None, read_store=None, committer=None, interval=0)
        self.fp = fp

    async def _fingerprint(self):
        return self.fp

    async def readiness(self) -> float:
        return await self._gate_on_watermark(0.5)


async def test_watermark_zeroes_readiness_until_state_changes():
    agent = _WatermarkAgent((1, "ch1"))
    assert await agent.readiness() == 0.5      # never ran: full score
    await agent._record_watermark()
    assert await agent.readiness() == 0.0      # same state: gated
    agent.fp = (2, "ch2")
    assert await agent.readiness() == 0.5      # external change: restored


async def test_default_fingerprint_disables_watermarking():
    agent = BaseAgent(runner=None, read_store=None, committer=None, interval=0)
    await agent._record_watermark()
    assert await agent._gate_on_watermark(0.7) == 0.7
```

- [ ] **Step 2: Run to verify failure**

Run: `uv run pytest tests/agents/test_base.py -v -k watermark`
Expected: FAIL (AttributeError: `_gate_on_watermark`).

- [ ] **Step 3: Implement** in `novelizer/agents/base.py`. In `__init__`, after `self._backoff_until = 0.0` add:

```python
        self._last_fingerprint: tuple | None = None
```

After `note_pass` add:

```python
    async def _fingerprint(self) -> tuple | None:
        """External story state this agent's work depends on. None (default)
        disables watermarking. Subclasses return a small tuple; captured
        AFTER the agent's own commits, so its own writes never re-trigger it."""
        return None

    async def _gate_on_watermark(self, score: float) -> float:
        fp = await self._fingerprint()
        if fp is not None and fp == self._last_fingerprint:
            return 0.0
        return score

    async def _record_watermark(self) -> None:
        self._last_fingerprint = await self._fingerprint()
```

- [ ] **Step 4: Run to verify pass**

Run: `uv run pytest tests/agents/test_base.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add novelizer/agents/base.py tests/agents/test_base.py
git commit -m "feat(agents): readiness watermark — gate dispatch on unchanged external state"
```

---

### Task 3: `no_action` schema field + shared pass-prompt text

**Files:**
- Modify: `novelizer/agents/schemas.py` (`WorldEntriesDraft` ~line 25, `KeeperOutput` ~line 131, `ContinuityOutput` ~line 163)
- Modify: `novelizer/agents/base.py` (one more constant)
- Test: `tests/agents/test_schemas.py` (append)

**Interfaces:**
- Produces: `no_action: bool = False` on `WorldEntriesDraft`, `KeeperOutput`, `ContinuityOutput`; `PASS_PROMPT_INSTRUCTION: str` constant in `novelizer.agents.base`. Tasks 4-6 append the instruction to their SYSTEM_PROMPTs and branch on `out.no_action`.

- [ ] **Step 1: Write the failing test** — append to `tests/agents/test_schemas.py`:

```python
from novelizer.agents.schemas import ContinuityOutput, KeeperOutput, WorldEntriesDraft


def test_no_action_defaults_false_on_pass_capable_outputs():
    assert KeeperOutput().no_action is False
    assert WorldEntriesDraft().no_action is False
    assert ContinuityOutput().no_action is False
```

- [ ] **Step 2: Run to verify failure**

Run: `uv run pytest tests/agents/test_schemas.py -v -k no_action`
Expected: FAIL (AttributeError: `no_action`).

- [ ] **Step 3: Implement.** Add `no_action: bool = False` as the last field of `WorldEntriesDraft`, `KeeperOutput`, and `ContinuityOutput` in `novelizer/agents/schemas.py`. In `novelizer/agents/base.py`, under `DEFAULT_PASS_REMARK` add:

```python
PASS_PROMPT_INSTRUCTION = (
    "\nIf nothing needs your attention, set no_action=true, leave every list empty, "
    "and give a one-line feed_note in character saying you're standing aside so the "
    "story can continue."
)
```

- [ ] **Step 4: Run to verify pass**

Run: `uv run pytest tests/agents/test_schemas.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add novelizer/agents/schemas.py novelizer/agents/base.py tests/agents/test_schemas.py
git commit -m "feat(schemas): no_action pass verdict on Keeper/WorldEntries/Continuity outputs"
```

---

### Task 4: CharacterKeeper — watermark + pass

**Files:**
- Modify: `novelizer/agents/character_keeper.py` (SYSTEM_PROMPT lines 15-28; `readiness` lines 42-49; `commit` line 72; `_run` lines 134-137)
- Test: `tests/agents/test_character_keeper.py` (append)

**Interfaces:**
- Consumes: `note_pass`, `_gate_on_watermark`, `_record_watermark`, `DEFAULT_PASS_REMARK`, `PASS_PROMPT_INSTRUCTION` from Tasks 1-3; `KeeperOutput.no_action` from Task 3.
- Produces: no new public surface.

- [ ] **Step 1: Write the failing tests** — append to `tests/agents/test_character_keeper.py` (uses the file's existing `stack` fixture and `FakeRunner`):

```python
class BoomRunner:
    async def ainvoke(self, inputs):
        raise RuntimeError("boom")


async def test_keeper_readiness_zero_when_state_unchanged(stack):
    events, proj, read, committer = stack
    await events.append(EventType.CHAPTER_CREATED, "ch1", Chapter(id="ch1", title="One", prose="Mira arrives."))
    await proj.catch_up()
    out = KeeperOutput(new_characters=[NewCharacter(name="Mira")])
    agent = CharacterKeeper(FakeRunner(out), read, committer)
    assert await agent.readiness() > 0.0
    await agent.run_once()
    await proj.catch_up()
    # Its own minted character must not re-trigger it; no new external state.
    assert await agent.readiness() == 0.0
    await events.append(EventType.CHAPTER_CREATED, "ch2", Chapter(id="ch2", title="Two", prose="More."))
    await proj.catch_up()
    assert await agent.readiness() > 0.0


async def test_keeper_failed_run_leaves_watermark_unset(stack):
    events, proj, read, committer = stack
    # Seed BOTH a chapter and a character so readiness takes the gated 0.5
    # path, not the ungated 0.8 cast-bootstrap branch — otherwise this test
    # would pass even if a failed run wrongly recorded the watermark.
    await events.append(EventType.CHAPTER_CREATED, "ch1", Chapter(id="ch1", title="One", prose="text"))
    await events.append(EventType.CHARACTER_CREATED, "c1", Character(id="c1", name="Mira"))
    await proj.catch_up()
    agent = CharacterKeeper(BoomRunner(), read, committer)
    with pytest.raises(RuntimeError):
        await agent.run_once()
    assert await agent.readiness() == 0.5


async def test_keeper_no_action_pass_commits_nothing_and_backs_off(stack):
    events, proj, read, committer = stack
    await events.append(EventType.CHAPTER_CREATED, "ch1", Chapter(id="ch1", title="One", prose="text"))
    await proj.catch_up()
    out = KeeperOutput(no_action=True, new_characters=[NewCharacter(name="Ghost")],
                       feed_note="All quiet on the cast front — write on.")
    agent = CharacterKeeper(FakeRunner(out), read, committer)
    await agent.run_once()
    await proj.catch_up()
    assert await read.list_characters() == []          # populated list ignored on a pass
    log = await events.events_since(0)
    remarks = [e for e in log if e.event_type == EventType.AGENT_REMARKED]
    assert [e.payload["note"] for e in remarks] == ["All quiet on the cast front — write on."]
    import time
    assert agent.seconds_until_ready(time.monotonic()) > agent.interval


async def test_keeper_pass_uses_default_remark_when_feed_note_empty(stack):
    events, proj, read, committer = stack
    agent = CharacterKeeper(FakeRunner(KeeperOutput(no_action=True)), read, committer)
    await agent.commit(KeeperOutput(no_action=True), {"characters": [], "recent": [], "secrets": [], "hands": []})
    log = await events.events_since(0)
    remarks = [e for e in log if e.event_type == EventType.AGENT_REMARKED]
    assert [e.payload["note"] for e in remarks] == [DEFAULT_PASS_REMARK]
```

Add the imports the new tests need at the top of the file: `import pytest`, and extend the existing `novelizer.agents.base` imports with `from novelizer.agents.base import DEFAULT_PASS_REMARK`.

- [ ] **Step 2: Run to verify failure**

Run: `uv run pytest tests/agents/test_character_keeper.py -v -k "watermark or no_action or pass or unchanged"`
Expected: FAIL (readiness stays 0.5 after run; pass test mints "Ghost").

- [ ] **Step 3: Implement** in `novelizer/agents/character_keeper.py`:

Import additions (line 3 area): `from novelizer.agents.base import BaseAgent, Runner, DEFAULT_PASS_REMARK, PASS_PROMPT_INSTRUCTION`.

Append the pass instruction to the prompt (after the closing `"""` of SYSTEM_PROMPT):

```python
SYSTEM_PROMPT = """...existing text unchanged...""" + PASS_PROMPT_INSTRUCTION
```

Replace `readiness` with a watermark-gated version and add `_fingerprint`:

```python
    async def readiness(self) -> float:
        chars = await self._read.list_characters()
        chapters = await self._read.list_chapters()
        if chapters and not chars:
            # Prose exists but the cast is empty: bootstrapping the cast is
            # the Keeper's most urgent work — nothing else mints characters.
            return 0.8
        score = 0.5 if (chars and chapters) else 0.2
        return await self._gate_on_watermark(score)

    async def _fingerprint(self) -> tuple:
        chapters = await self._read.list_chapters()
        open_retcons = await self._read.list_retcon_requests(status=RetconStatus.open)
        return (len(chapters), chapters[-1].id if chapters else "", len(open_retcons))
```

(The cast-bootstrap 0.8 branch stays ungated: an empty cast with prose is always work.)

At the top of `commit`, after the `if out is None: return` guard, add:

```python
        if out.no_action:
            await self._remark(out.feed_note or DEFAULT_PASS_REMARK)
            self.note_pass()
            return
```

Replace `_run` with:

```python
    async def _run(self) -> None:
        ctx = await self.poll()
        out = await self.work(ctx)
        await self.commit(out, ctx)
        await self._record_watermark()
```

- [ ] **Step 4: Run to verify pass**

Run: `uv run pytest tests/agents/test_character_keeper.py tests/agents/test_character_keeper_uptake.py -v`
Expected: PASS (all, including pre-existing tests).

- [ ] **Step 5: Commit**

```bash
git add novelizer/agents/character_keeper.py tests/agents/test_character_keeper.py
git commit -m "feat(character-keeper): watermark readiness + no_action pass"
```

---

### Task 5: WorldArchitect — pass verdict only (no watermark)

**Files:**
- Modify: `novelizer/agents/world_architect.py` (SYSTEM_PROMPT lines 10-14; `commit` lines 48-54)
- Test: `tests/agents/test_world_architect.py` (append)

**Interfaces:**
- Consumes: `note_pass`, `DEFAULT_PASS_REMARK`, `PASS_PROMPT_INSTRUCTION` (Tasks 1, 3); `WorldEntriesDraft.no_action` (Task 3).
- Produces: no new public surface. Readiness is deliberately UNCHANGED (generative floor 0.2 is intentional).

- [ ] **Step 1: Write the failing tests** — append to `tests/agents/test_world_architect.py`, following that file's existing fixture/FakeRunner pattern (mirror the `stack` fixture from `tests/agents/test_character_keeper.py` if the file lacks one):

```python
async def test_architect_no_action_pass_commits_nothing_and_backs_off(stack):
    events, proj, read, committer = stack
    draft = WorldEntriesDraft(no_action=True, feed_note="The world is rich enough — let the story breathe.")
    agent = WorldArchitect(FakeRunner(draft), read, committer)
    await agent.run_once()
    await proj.catch_up()
    assert await read.list_world_entries() == []
    log = await events.events_since(0)
    remarks = [e for e in log if e.event_type == EventType.AGENT_REMARKED]
    assert [e.payload["note"] for e in remarks] == ["The world is rich enough — let the story breathe."]
    import time
    assert agent.seconds_until_ready(time.monotonic()) > agent.interval


async def test_architect_pass_ignored_when_director_seed_pending(stack):
    events, proj, read, committer = stack
    from novelizer.store.models import DirectorSignal, SignalKind
    sig = DirectorSignal(kind=SignalKind.seed, body="a drowned city", target_agent="world_architect")
    await events.append(EventType.DIRECTOR_SIGNAL_CREATED, sig.id, sig)
    await proj.catch_up()
    draft = WorldEntriesDraft(no_action=True)
    agent = WorldArchitect(FakeRunner(draft), read, committer)
    await agent.run_once()
    await proj.catch_up()
    # The seed must not be silently dropped by a pass: normal path runs,
    # the signal is consumed, and no backoff is taken.
    assert await read.list_unconsumed_signals(target_agent="world_architect") == []
    assert agent._backoff_until == 0.0


async def test_architect_readiness_floor_unchanged(stack):
    events, proj, read, committer = stack
    agent = WorldArchitect(FakeRunner(WorldEntriesDraft()), read, committer)
    await agent.run_once()
    assert await agent.readiness() >= 0.2
```

- [ ] **Step 2: Run to verify failure**

Run: `uv run pytest tests/agents/test_world_architect.py -v -k "pass or floor"`
Expected: FAIL (`no_action` pass still returns normally but seconds_until_ready assertion fails / remark differs).

- [ ] **Step 3: Implement** in `novelizer/agents/world_architect.py`:

Import: `from novelizer.agents.base import BaseAgent, Runner, DEFAULT_PASS_REMARK, PASS_PROMPT_INSTRUCTION`.

Prompt — append to SYSTEM_PROMPT:

```python
SYSTEM_PROMPT = """...existing text unchanged...""" + PASS_PROMPT_INSTRUCTION + """
Never set no_action when director seeds are present — a seed is always your work."""
```

Replace `commit` with:

```python
    async def commit(self, draft: WorldEntriesDraft | None, ctx: dict) -> None:
        if draft is not None and draft.no_action and not ctx["signals"]:
            # Honored only with no pending seeds: a pass must never silently
            # consume (or strand) director input.
            await self._remark(draft.feed_note or DEFAULT_PASS_REMARK)
            self.note_pass()
            return
        if draft is not None:
            for e in draft.entries:
                entry = WorldEntry(title=e.title, body=e.body, domain=e.domain, tags=e.tags)
                await self._committer.commit(self.name, EventType.WORLD_ENTRY_CREATED, entry.id, entry)
            await self._remark(draft.feed_note)
        await self._consume_signals(ctx["signals"])
```

- [ ] **Step 4: Run to verify pass**

Run: `uv run pytest tests/agents/test_world_architect.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add novelizer/agents/world_architect.py tests/agents/test_world_architect.py
git commit -m "feat(world-architect): no_action pass with director-seed guard"
```

---

### Task 6: ContinuityChecker — watermark + pass with deterministic-work guard

**Files:**
- Modify: `novelizer/agents/continuity_checker.py` (`readiness` lines 61-63; `commit` lines 313-354; `_run` lines 356-359)
- Test: `tests/agents/test_continuity_checker.py` (append)

**Interfaces:**
- Consumes: Tasks 1-3 surface; `ContinuityOutput.no_action`.
- Produces: no new public surface. `commit` keeps its exact signature `commit(out, ctx, mined_facts=None)`.

- [ ] **Step 1: Write the failing tests** — append to `tests/agents/test_continuity_checker.py`, using that file's existing fixture and runner fakes (it constructs `ContinuityChecker(runner, mining_runner, read, committer, event_store)`; reuse its `stack`-equivalent fixture and `FakeRunner`; a mining runner returning `MinedFactsOutput()` mines "nothing" but still stamps `chapter.mined`):

```python
async def test_continuity_readiness_zero_when_state_unchanged(stack):
    events, proj, read, committer = stack
    await events.append(EventType.CHAPTER_CREATED, "ch1", Chapter(id="ch1", title="One", prose="text"))
    await proj.catch_up()
    agent = ContinuityChecker(FakeRunner(ContinuityOutput()), FakeRunner(MinedFactsOutput()),
                              read, committer, events)
    assert await agent.readiness() > 0.0
    await agent.run_once()      # mines ch1, stamps chapter.mined
    await proj.catch_up()
    assert await agent.readiness() == 0.0
    await events.append(EventType.CHAPTER_CREATED, "ch2", Chapter(id="ch2", title="Two", prose="more"))
    await proj.catch_up()
    assert await agent.readiness() > 0.0


async def test_continuity_pass_skips_llm_retcons_but_still_mines(stack):
    events, proj, read, committer = stack
    await events.append(EventType.CHAPTER_CREATED, "ch1", Chapter(id="ch1", title="One", prose="text"))
    await proj.catch_up()
    out = ContinuityOutput(no_action=True,
                           retcon_requests=[RetconDraft(description="phantom", proposed_resolution="x")])
    agent = ContinuityChecker(FakeRunner(out), FakeRunner(MinedFactsOutput()), read, committer, events)
    await agent.run_once()
    await proj.catch_up()
    # LLM retcon ignored on a pass...
    assert await read.list_retcon_requests(status=RetconStatus.open) == []
    # ...but the deterministic mining pass still ran and stamped the chapter.
    mined = await events.events_since(0, event_types=[EventType.CHAPTER_MINED])
    assert [e.payload["chapter_id"] for e in mined] == ["ch1"]
    # Mining WAS deterministic work, so no backoff this run.
    assert agent._backoff_until == 0.0


async def test_continuity_pass_backs_off_when_no_deterministic_work(stack):
    events, proj, read, committer = stack
    await events.append(EventType.CHAPTER_CREATED, "ch1", Chapter(id="ch1", title="One", prose="text"))
    await proj.catch_up()
    quiet = ContinuityChecker(FakeRunner(ContinuityOutput()), FakeRunner(MinedFactsOutput()),
                              read, committer, events)
    await quiet.run_once()      # first run mines ch1
    passing = ContinuityChecker(FakeRunner(ContinuityOutput(no_action=True, feed_note="All threads hold.")),
                                FakeRunner(MinedFactsOutput()), read, committer, events)
    await passing.run_once()    # nothing left to mine, no leaks/paradoxes
    log = await events.events_since(0)
    remarks = [e for e in log if e.event_type == EventType.AGENT_REMARKED]
    assert remarks[-1].payload["note"] == "All threads hold."
    import time
    assert passing.seconds_until_ready(time.monotonic()) > passing.interval
```

Add imports the tests need if absent: `ContinuityOutput`, `MinedFactsOutput`, `RetconDraft` from `novelizer.agents.schemas`; `RetconStatus`, `Chapter` from `novelizer.store.models`; `EventType` from `novelizer.canon.events`.

- [ ] **Step 2: Run to verify failure**

Run: `uv run pytest tests/agents/test_continuity_checker.py -v -k "unchanged or pass"`
Expected: FAIL (readiness stays ≥0.1; phantom retcon gets filed).

- [ ] **Step 3: Implement** in `novelizer/agents/continuity_checker.py`:

Import: `from novelizer.agents.base import BaseAgent, Runner, DEFAULT_PASS_REMARK, PASS_PROMPT_INSTRUCTION`.

Prompt — append `PASS_PROMPT_INSTRUCTION` to SYSTEM_PROMPT (NOT to MINING_SYSTEM_PROMPT — mining output has no `no_action`).

Replace `readiness` and add `_fingerprint`:

```python
    async def readiness(self) -> float:
        open_retcons = len(await self._read.list_retcon_requests(status=RetconStatus.open))
        return await self._gate_on_watermark(max(0.1, 1.0 - open_retcons / 5))

    async def _fingerprint(self) -> tuple:
        chapters = await self._read.list_chapters()
        mined_events = await self._events.events_since(0, event_types=[EventType.CHAPTER_MINED])
        already_mined = already_mined_chapter_ids(mined_events)
        unmined = sum(1 for c in chapters if c.id not in already_mined)
        refs = await self._read.list_secret_references()
        edges = await self._read.list_causal_edges()
        return (len(chapters), chapters[-1].id if chapters else "", unmined, len(refs), len(edges))
```

Rework `commit` (keep signature). The LLM-derived block runs only on a real verdict; leak/paradox/mining blocks are UNCHANGED except each leak/paradox `RETCON_REQUEST_CREATED` commit increments a local `deterministic_filed` counter; the pass branch runs last:

```python
    async def commit(
        self, out: ContinuityOutput | None, ctx: dict, mined_facts: dict[str, MinedFactsOutput] | None = None,
    ) -> None:
        open_reqs = await self._read.list_retcon_requests(status=RetconStatus.open)
        seen_descriptions = {r.description for r in open_reqs}
        deterministic_filed = 0

        if out is not None and not out.no_action:
            for r in out.retcon_requests:
                if r.description in seen_descriptions:
                    continue
                seen_descriptions.add(r.description)
                req = RetconRequest(description=r.description, conflicting_entry_ids=r.conflicting_entry_ids,
                                    proposed_resolution=r.proposed_resolution)
                await self._committer.commit(self.name, EventType.RETCON_REQUEST_CREATED, req.id, req)
            await self._remark(out.feed_note)

        # ...existing find_leaks loop, unchanged except add `deterministic_filed += 1`
        #    immediately after its `await self._committer.commit(...)` line...
        # ...existing find_paradoxes loop, same one-line addition...
        # ...existing mined_facts loop, byte-for-byte unchanged...

        if out is not None and out.no_action:
            await self._remark(out.feed_note or DEFAULT_PASS_REMARK)
            if not mined_facts and deterministic_filed == 0:
                self.note_pass()
```

Replace `_run` with:

```python
    async def _run(self) -> None:
        ctx = await self.poll()
        out, mined = await self.work(ctx)
        await self.commit(out, ctx, mined)
        await self._record_watermark()
```

- [ ] **Step 4: Run to verify pass**

Run: `uv run pytest tests/agents/test_continuity_checker.py tests/agents/test_continuity_uptake.py -v`
Expected: PASS (all, including pre-existing tests).

- [ ] **Step 5: Commit**

```bash
git add novelizer/agents/continuity_checker.py tests/agents/test_continuity_checker.py
git commit -m "feat(continuity-checker): watermark readiness + no_action pass with deterministic-work guard"
```

---

### Task 7: Property test — a pass never mutates canon

**Files:**
- Test: `tests/agents/test_character_keeper_property.py` (append)

**Interfaces:**
- Consumes: Task 4's pass branch. Follows the file's existing `asyncio.run(_helper(...))` + `@given` pattern (hypothesis + async fixtures don't mix, so the file builds its own stack per example).

- [ ] **Step 1: Write the test** — append to `tests/agents/test_character_keeper_property.py`:

```python
from novelizer.agents.schemas import CharacterUpdate, KnowledgeIntent, RetconDraft
from novelizer.store.models import RetconStatus


async def _run_pass(out: KeeperOutput) -> None:
    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    try:
        events = EventStore(path)
        await events.init()
        proj = Projector(events, path)
        await proj.init()
        read = ReadStore(path)
        await read.init()

        await events.append(EventType.CHAPTER_CREATED, "ch1", Chapter(id="ch1", title="One", prose="..."))
        await proj.catch_up()

        keeper = CharacterKeeper(FakeRunner(out), read, committer=Committer(events))
        await keeper.run_once()
        await proj.catch_up()

        # However populated the lists, a pass never mutates canon:
        # the only event beyond the seeded chapter may be one agent.remarked.
        assert await read.list_characters() == []
        assert await read.list_retcon_requests(status=RetconStatus.open) == []
        log = await events.events_since(0)
        assert {e.event_type for e in log} <= {EventType.CHAPTER_CREATED, EventType.AGENT_REMARKED}

        await read.close()
        await proj.close()
        await events.close()
    finally:
        os.unlink(path)


_texts = st.text(max_size=12)


@given(
    st.builds(
        KeeperOutput,
        no_action=st.just(True),
        feed_note=_texts,
        new_characters=st.lists(st.builds(NewCharacter, name=st.text(min_size=1, max_size=12)), max_size=4),
        updated_characters=st.lists(st.builds(CharacterUpdate, id=_texts), max_size=4),
        retcon_requests=st.lists(st.builds(RetconDraft, description=st.text(min_size=1, max_size=12)), max_size=4),
        knowledge_intents=st.lists(
            st.builds(KnowledgeIntent, action=st.just("learn"), id=_texts, character_id=_texts), max_size=4
        ),
    )
)
@settings(max_examples=25, deadline=None)
def test_no_action_pass_never_mutates_canon(out: KeeperOutput):
    asyncio.run(_run_pass(out))
```

- [ ] **Step 2: Run to verify pass** (Task 4 already implemented the behavior, so this property should pass immediately — its value is regression armor):

Run: `uv run pytest tests/agents/test_character_keeper_property.py -v`
Expected: PASS. If it FAILS, the pass branch in `CharacterKeeper.commit` is not early-returning before entity commits — fix that, not the test.

- [ ] **Step 3: Commit**

```bash
git add tests/agents/test_character_keeper_property.py
git commit -m "test(character-keeper): property — no_action pass never mutates canon"
```

---

### Task 8: Full-suite verification and ship

**Files:** none new.

- [ ] **Step 1: Run the full suite (worktree only)**

Run: `uv run pytest`
Expected: PASS, zero warnings. If scheduler or TUI tests fail on `seconds_until_ready`/`ready_for_interval` semantics, the backoff terms in Task 1 regressed the plain-interval path — `_backoff_until` must default to 0.0 so unpassed agents behave identically.

- [ ] **Step 2: Push and open a draft PR**

```bash
git push -u origin worktree-idle-pass-mechanism
gh pr create --draft --title "Idle-pass mechanism: revision agents signal 'nothing to revise, carry on'" --body "..."
```
