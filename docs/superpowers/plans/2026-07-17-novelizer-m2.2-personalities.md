# M2.2 · Personalities & the Living Feed — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Recasting an agent's personality visibly changes what it says in the feed. Each agent's personality casting note (from the active voice pack's `agent_personalities[agent_name]`) is injected into that agent's work-time prompt, and each agent emits a short in-personality `feed_note` in its structured output, which becomes an `agent.remarked` event rendered as a personality-voiced line in the feed and in The Room.

**Architecture:** Two additive mechanisms bolted onto the M2.1 seams. (1) **Personality injection** — `BaseAgent` gains a `personality: str = ""` constructor parameter; each of the six agents prepends/appends an "In character: `<personality>`" line to the user message it already builds in `work()`, exactly mirroring how M2.1's `casting_note` was threaded into Author/Editor. (2) **The living feed** — each of the six response schemas (`ChapterDraft`, `WorldEntriesDraft`, `KeeperOutput`, `EditorVerdict`, `ContinuityOutput`, `RetconAmendments`) gains an optional `feed_note: str = ""` field; a new `BaseAgent._remark(note)` helper commits an `AGENT_REMARKED` event (payload `AgentRemark(agent_name, note)`) via the injected `Committer` whenever `note` is non-empty, and each agent's `commit()` calls it once. `agent.remarked` is added to `AutonomyPolicy._NEVER_GATED` (it is feed flavor, never a proposal) and is *not* projected — the `Projector._apply` if/elif chain simply has no branch for it, so it falls through to the trailing `await self._conn.commit()` with zero projection side effects, exactly like any other event type the Projector doesn't recognize. The feed (and The Room, which is the existing feed-only view toggled by `r`) reads `agent.remarked` directly off the event log via `events_since` + `format_event`, which gains a branch rendering `💬 <AgentLabel>: "<note>"`. `Runtime.start()` reads `self.voice_pack.agent_personalities.get(name, "")` for each of the six agents and passes it as the new `personality=` kwarg.

**Tech Stack:** Python 3.13, `pydantic` v2, `pytest`+`pytest-asyncio` (`asyncio_mode=auto`), Textual (`RichLog`/`format_event`, unchanged rendering pipeline).

**Deferred (explicitly out of scope for M2.2, so a reviewer doesn't read these as gaps):**
- **Character voice cards** (Character Keeper builds per-character dialogue/vocabulary/tic cards; Editor/Continuity Checker cite them) — **M2.3**.
- **In-TUI voice browser** (packs / prose profiles / agent personalities / character voices, scaffolding a new profile from a prompt) — **M2.3**.
- **Live personality switching mid-run** — like M2.1's prose profile, the active personality is resolved once per `Runtime.start()`; switching it takes effect on process restart until M2.3's in-TUI browser lands.

**Context — current state after M2.1 (on `m1.2-mission-control`):**

- `novelizer/agents/base.py` — `BaseAgent(runner, read_store, committer, interval, name=None)`: stores `self._runner/self._read/self._committer/self.interval/self.name/self.paused/self._last_run`; `pause()/resume()/ready_for_interval(now)/mark_ran(now)`; `async readiness() -> float` (default `0.0`); `async run_once()` (no-op default); `async _consume_signals(signals)` commits `DIRECTOR_SIGNAL_CONSUMED` per signal via `self._committer.commit(self.name, EventType.DIRECTOR_SIGNAL_CONSUMED, sig.id, consumed)`. Also defines `class ChapterDraft(BaseModel): title: str, prose: str, character_ids: list[str] = Field(default_factory=list)` and `class Runner(Protocol): async def ainvoke(self, inputs: dict) -> dict: ...`.
- `novelizer/agents/schemas.py` — six-ish response models: `WorldEntryDraft`, `WorldEntriesDraft(entries: list[WorldEntryDraft])`, `CharacterUpdate`, `RetconDraft`, `KeeperOutput(updated_characters: list[CharacterUpdate], retcon_requests: list[RetconDraft])`, `EditorVerdict(verdict: Literal["approve","revise"] = "approve", notes: str = "")`, `ContinuityOutput(retcon_requests: list[RetconDraft])`, `RetconAmendments(amended_entries: list[WorldEntryDraft])`.
- `novelizer/agents/author.py` — `Author(BaseAgent)`: `__init__(self, runner, read_store, committer, interval=300, casting_note="")` → `super().__init__(runner, read_store, committer, interval, name="author")`; stores `self._casting_note`. Module-level `_summarize(ctx, casting_note="") -> str` builds the user message, appending `f"\n\nWrite in this prose voice: {casting_note}"` only when non-empty, ending `"...\n\nWrite the next chapter."`. `work(ctx)` calls `_summarize(ctx, self._casting_note)`, then `self._runner.ainvoke({"messages": [{"role": "user", "content": content}]})`, returns `result.get("structured_response")` (a `ChapterDraft | None`). `commit(draft, ctx)` builds a `Chapter(title=draft.title, prose=draft.prose, character_ids=draft.character_ids)`, commits `EventType.CHAPTER_CREATED`, then `await self._consume_signals(ctx["signals"])`.
- `novelizer/agents/editor.py` — `Editor(BaseAgent)`: `__init__(self, runner, read_store, committer, interval=120, casting_note="")`; stores `self._casting_note`. `work(ctx)` builds `voice = f"\n\nEnforce this prose voice: {self._casting_note}; note any drift in your feedback." if self._casting_note else ""`, then `msg = f"Chapter title: {ch.title}\n\nProse:\n{ch.prose}{voice}"`. `commit(verdict, ctx)`: on `verdict.verdict == "approve"` appends `CHAPTER_STATUS_CHANGED` with the chapter promoted to `reviewed` + `editor_notes`; else appends `DIRECTOR_SIGNAL_CREATED` targeting `"author"`.
- `novelizer/agents/world_architect.py` — `WorldArchitect(BaseAgent)`: `__init__(self, runner, read_store, committer, interval=120)`, `name="world_architect"`. `poll()` returns `{"entries", "signals"}`. `work(ctx)` builds `msg = f"Existing world entries:\n{existing}\n\nDirector seeds:\n{seeds}\n\nGenerate new world entries."`, returns `WorldEntriesDraft | None`. `commit(draft, ctx)` iterates `draft.entries`, commits `WORLD_ENTRY_CREATED` per entry, then `_consume_signals(ctx["signals"])`.
- `novelizer/agents/character_keeper.py` — `CharacterKeeper(BaseAgent)`: `__init__(self, runner, read_store, committer, interval=120)`, `name="character_keeper"`. `poll()` returns `{"characters", "recent"}`. `work(ctx)` returns `None` if no characters, else builds `msg = f"Characters:\n{chars}\n\nRecent chapters:\n{chapters}"`, returns `KeeperOutput | None`. `commit(out, ctx)` iterates `out.updated_characters` (committing `CHARACTER_UPDATED`) and `out.retcon_requests` (committing `RETCON_REQUEST_CREATED`); no-ops if `out is None`.
- `novelizer/agents/continuity_checker.py` — `ContinuityChecker(BaseAgent)`: `__init__(self, runner, read_store, committer, interval=900)`, `name="continuity_checker"`. `poll()` returns `{"world", "characters", "chapters"}`. `work(ctx)` builds `msg = f"World entries:\n{world}\n\nCharacters:\n{chars}\n\nRecent chapters:\n{chapters}"`, returns `ContinuityOutput | None`. `commit(out, ctx)` iterates `out.retcon_requests`, commits `RETCON_REQUEST_CREATED` per request; no-ops if `out is None`.
- `novelizer/agents/retconner.py` — `Retconner(BaseAgent)`: `__init__(self, runner, read_store, committer, interval=120)`, `name="retconner"`. `poll()` returns `{"target", "world"}`. `work(ctx)` returns `None` if no target retcon, else builds `msg = f"Contradiction: {req.description}\n\nProposed resolution: {req.proposed_resolution}\n\nConflicting entries:\n{text}"`, returns `RetconAmendments | None`. `commit(out, ctx)`: no-ops if `req is None or out is None`; else commits `WORLD_ENTRY_SUPERSEDED` per amended entry, then `RETCON_REQUEST_RESOLVED` on the target request.
- `novelizer/canon/events.py` — `class EventType` string constants (`WORLD_ENTRY_CREATED`, `WORLD_ENTRY_SUPERSEDED`, `CHARACTER_CREATED`, `CHARACTER_UPDATED`, `CHAPTER_CREATED`, `CHAPTER_STATUS_CHANGED`, `DIRECTOR_SIGNAL_CREATED`, `DIRECTOR_SIGNAL_CONSUMED`, `RETCON_REQUEST_CREATED`, `RETCON_REQUEST_RESOLVED`, `RETCON_REQUEST_REJECTED`, `PROPOSAL_CREATED`, `PROPOSAL_APPROVED`, `PROPOSAL_REJECTED`, `AUTONOMY_CHANGED`); `class StoredEvent(BaseModel)`: `sequence: int, id: str, event_type: str, aggregate_id: str, payload: dict[str, Any], created_at: str`.
- `novelizer/canon/committer.py` — `Committer(event_store)`: `async def commit(self, agent_name, event_type, aggregate_id, payload: BaseModel) -> None` → `await self._events.append(event_type, aggregate_id, payload)`. `GatingCommitter(event_store, policy)`: same signature; if `await self._policy.is_gated(agent_name, event_type)`, wraps as a `Proposal` and appends `PROPOSAL_CREATED` instead; else delegates like `Committer`.
- `novelizer/canon/policy.py` — `_RETCON_EVENTS`, `_CANON_EVENTS`, `_NEVER_GATED = {EventType.DIRECTOR_SIGNAL_CREATED, EventType.DIRECTOR_SIGNAL_CONSUMED}`; `_GATED_SETS` maps `AutonomyLevel` → gated set (`gated_all` resolved dynamically as "everything not in `_NEVER_GATED`"). `AutonomyPolicy(read_store).is_gated(agent_name, event_type) -> bool`: returns `False` immediately if `event_type in _NEVER_GATED`, else resolves the agent's level via `state.level_for(agent_name)` and checks membership.
- `novelizer/canon/projector.py` — `_apply(ev)` is a single `if/elif` chain keyed on `ev.event_type`, ending with an unconditional `await self._conn.commit()` after the chain (not inside any branch) — **confirmed**: an event type with no matching `elif` branch (e.g. a future `agent.remarked`) simply skips every branch and falls straight to the trailing commit, i.e. zero projection writes, zero errors. This is exactly the "feed-only, no projection" behavior M2.2 relies on.
- `novelizer/runtime.py` — `Runtime(settings, runner=None, runners=None)`. `start()`: after building `self.committer`/`self.proposals`, loads `self.voice_pack = load_voice_pack(self.settings.voice_pack)`, resolves `self.active_prose_profile = self.voice_pack.profile(self.settings.prose_profile)`, computes `casting_note = self.active_prose_profile.casting_note if self.active_prose_profile else ""`; then constructs all six agents via `self._runner_for(name, builder)`, passing `casting_note=casting_note` only to `Author`/`Editor`. Exact current construction lines:
  ```python
  self.author = Author(self._runner_for("author", build_author_runner), self.read, self.committer, interval=s.author_interval, casting_note=casting_note)
  self.world_architect = WorldArchitect(self._runner_for("world_architect", build_world_architect_runner), self.read, self.committer, interval=s.default_agent_interval)
  self.character_keeper = CharacterKeeper(self._runner_for("character_keeper", build_character_keeper_runner), self.read, self.committer, interval=s.default_agent_interval)
  self.editor = Editor(self._runner_for("editor", build_editor_runner), self.read, self.committer, interval=s.default_agent_interval, casting_note=casting_note)
  self.continuity_checker = ContinuityChecker(self._runner_for("continuity_checker", build_continuity_checker_runner), self.read, self.committer, interval=s.continuity_interval)
  self.retconner = Retconner(self._runner_for("retconner", build_retconner_runner), self.read, self.committer, interval=s.default_agent_interval)
  ```
- `novelizer/voices/models.py` — `VoicePack.agent_personalities: dict[str, str] = Field(default_factory=dict)` (already carried since M2.1, unused until now); `VoicePack.profile(name) -> Optional[ProseProfile]`.
- `novelizer/voices/default.toml` — ships `[agent_personalities]` with all six agent names populated (from M2.1 Task 2), e.g. `author = "A restless, slightly romantic chronicler..."`, `editor = "A precise, unsentimental line editor..."`, etc. — no changes needed here for M2.2.
- `novelizer/tui/app.py` — `_LABELS: dict[str, str]` maps `EventType.X` → display name (`"Author"`, `"Architect"`, `"Keeper"`, `"Director"`, `"Retcon"`, `"Editor"`); `format_event(ev: StoredEvent) -> str` looks up `who = _LABELS.get(ev.event_type, "System")`, builds a `detail` string per event type via `if/elif`, falling back to `detail = ev.event_type`, returns `f"◆ {who} — {detail}"`. `action_toggle_room(self)` does `self.query_one("#body").toggle_class("room")` — a pure CSS-class toggle on the existing `#body` container; the feed (`RichLog#feed`) is already rendered via the same `format_event` regardless of Room state, so extending `format_event` automatically reaches The Room with no separate code path.
- `novelizer/store/models.py` — `Domain/CanonStatus/EditorialStatus/RetconStatus/SignalKind` `StrEnum`s + `WorldEntry/Character/Chapter/RetconRequest/DirectorSignal` pydantic models, module-level `_now()`/`_uuid()` factories. **Not touched by this plan.**
- Test layout: `tests/canon/`, `tests/director/`, `tests/agents/` (`test_author.py`/`test_editor.py`/`test_world_architect.py`/`test_character_keeper.py`/`test_continuity_checker.py`/`test_retconner.py`/`test_base.py`/`test_schemas.py`), `tests/tui/` (`test_app.py` has `format_event`/`_status_line` tests), `tests/test_scheduler.py`, `tests/test_runtime.py`. Every agent test file follows the identical fixture pattern: a `stack` fixture yielding `(events, proj, read, committer)` built from a fresh `tempfile.mkstemp(suffix=".db")` SQLite path, with a local `FakeRunner` class recording `self.calls` in `ainvoke`.

## Global Constraints

- **Python** `>=3.13`.
- **Event sourcing is preserved.** `agent.remarked` is appended like any other event, never mutated in place; it is feed-flavor (like `director_signal.*`), so it is added to `AutonomyPolicy._NEVER_GATED` and is **never projected** — the `Projector._apply` if/elif chain gains no branch for it, confirmed to fall through harmlessly to the trailing commit.
- **Voice is injected at work-time, not construction** — exactly as M2.1 established. `personality` is a constructor parameter stored on the agent, read only inside `work()` when building that run's user message, never folded into a deepagents `system_prompt` at agent-build time.
- **DDD/SOLID.** Personality storage + the `_remark` helper are centralized on `BaseAgent` (single responsibility, DRY); each of the six agents' changes are small, additive, and independent (Open/Closed — extending, not modifying, existing collaborators). Only `agents/*.py`, `agents/base.py`, `agents/schemas.py`, `runtime.py`, `canon/events.py`, `canon/policy.py`, and `tui/app.py` are touched; `canon/committer.py`, `canon/projector.py`'s core dispatch shape, `canon/read_store.py`, `store/models.py`, and the scheduler are untouched (only `policy.py`'s `_NEVER_GATED` set gains one member).
- **TDD, black-box first:** every task is failing-test → run-fail → implement → run-pass → commit. Prompts/events are asserted by capturing the exact `inputs` dict a `FakeRunner.ainvoke` receives and the exact events a fake/real committer/event store records — never by mocking internals. Parametrize where an invariant generalizes (e.g. "personality present → prompt line present, personality empty → prompt byte-identical to before").
- **`asyncio_mode = "auto"`** — `async def test_*` needs no decorator.
- **Backward compatibility is non-negotiable.** `personality: str = ""` and `feed_note: str = ""` are additive, defaulted fields/parameters. Every existing call site (all six agents' current tests, `Runtime`'s `runner=`/`runners=` overrides, every fixture in `tests/agents/*`) must keep passing completely unmodified — empty personality ⇒ byte-identical prompt; empty/absent `feed_note` ⇒ zero extra events. The full existing test suite (144+ tests pre-M2.2) must stay green throughout.
- **DRY** — one `_remark` helper on `BaseAgent`, one `AgentRemark` payload shape, one `format_event` branch; the six per-agent edits are the minimum necessary duplication (an injected-argument line + one `_remark` call), not six divergent implementations.
- **`EventType` constants only** — no magic strings for event types anywhere outside `events.py`.

---

### Task 1: `AGENT_REMARKED` event type, `AgentRemark` payload, never-gated

**Files:**
- Modify: `novelizer/canon/events.py`
- Modify: `novelizer/canon/policy.py`
- Test: `tests/canon/test_events.py` (extend), `tests/canon/test_policy.py` (extend)

**Interfaces:**
- Produces: `EventType.AGENT_REMARKED = "agent.remarked"`. `class AgentRemark(BaseModel)` in `novelizer/canon/events.py`: `agent_name: str`, `note: str`. `_NEVER_GATED` in `novelizer/canon/policy.py` gains `EventType.AGENT_REMARKED` alongside the existing two director-signal members.

- [ ] **Step 1: Write the failing tests**

Append to `tests/canon/test_events.py`:
```python
def test_agent_remarked_event_type_exists():
    from novelizer.canon.events import EventType
    assert EventType.AGENT_REMARKED == "agent.remarked"


def test_agent_remark_payload_model_roundtrips():
    from novelizer.canon.events import AgentRemark
    remark = AgentRemark(agent_name="author", note="Another storm, another chapter.")
    again = AgentRemark.model_validate_json(remark.model_dump_json())
    assert again == remark
```

Append to `tests/canon/test_policy.py`:
```python
@pytest.mark.parametrize("level", list(AutonomyLevel))
async def test_agent_remarked_is_never_gated(level):
    policy = AutonomyPolicy(FakeRead(AutonomyState(global_level=level)))
    assert await policy.is_gated("any_agent", EventType.AGENT_REMARKED) is False
```
(`AutonomyLevel`, `AutonomyState`, `FakeRead`, `EventType`, `AutonomyPolicy`, and `pytest` are all already imported at the top of `tests/canon/test_policy.py` from the M1.3 plan — no new imports needed.)

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/canon/test_events.py tests/canon/test_policy.py -v`
Expected: FAIL (`AttributeError: type object 'EventType' has no attribute 'AGENT_REMARKED'`; then, once that's fixed transiently in a scratch run, `ImportError: cannot import name 'AgentRemark'`).

- [ ] **Step 3: Implement**

In `novelizer/canon/events.py`, add the constant to `EventType` and a new model below `StoredEvent`:
```python
from __future__ import annotations
from typing import Any
from pydantic import BaseModel


class EventType:
    WORLD_ENTRY_CREATED = "world_entry.created"
    WORLD_ENTRY_SUPERSEDED = "world_entry.superseded"
    CHARACTER_CREATED = "character.created"
    CHARACTER_UPDATED = "character.updated"
    CHAPTER_CREATED = "chapter.created"
    CHAPTER_STATUS_CHANGED = "chapter.status_changed"
    DIRECTOR_SIGNAL_CREATED = "director_signal.created"
    DIRECTOR_SIGNAL_CONSUMED = "director_signal.consumed"
    RETCON_REQUEST_CREATED = "retcon_request.created"
    RETCON_REQUEST_RESOLVED = "retcon_request.resolved"
    RETCON_REQUEST_REJECTED = "retcon_request.rejected"
    PROPOSAL_CREATED = "proposal.created"
    PROPOSAL_APPROVED = "proposal.approved"
    PROPOSAL_REJECTED = "proposal.rejected"
    AUTONOMY_CHANGED = "autonomy.changed"
    AGENT_REMARKED = "agent.remarked"


class StoredEvent(BaseModel):
    sequence: int
    id: str
    event_type: str
    aggregate_id: str
    payload: dict[str, Any]
    created_at: str


class AgentRemark(BaseModel):
    """Payload for agent.remarked — a short in-personality feed line.

    Feed-flavor only: never gated (see AutonomyPolicy._NEVER_GATED), never
    projected (the Projector has no _apply branch for it, by design).
    """

    agent_name: str
    note: str
```
(Only the `AGENT_REMARKED` constant and the new `AgentRemark` class are additions — every existing `EventType` member and `StoredEvent` are unchanged.)

In `novelizer/canon/policy.py`, add `EventType.AGENT_REMARKED` to `_NEVER_GATED`:
```python
_NEVER_GATED = {
    EventType.DIRECTOR_SIGNAL_CREATED,
    EventType.DIRECTOR_SIGNAL_CONSUMED,
    EventType.AGENT_REMARKED,
}
```
(Only this set literal changes; `_RETCON_EVENTS`, `_CANON_EVENTS`, `_GATED_SETS`, and `AutonomyPolicy` are unchanged.)

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/canon/test_events.py tests/canon/test_policy.py -v`
Expected: PASS (all prior + 3 new).

- [ ] **Step 5: Commit**

```bash
git add novelizer/canon/events.py novelizer/canon/policy.py tests/canon/test_events.py tests/canon/test_policy.py
git commit -m "feat: add agent.remarked event type + AgentRemark payload, never gated"
```

---

### Task 2: `BaseAgent` personality parameter + `_remark` helper

**Files:**
- Modify: `novelizer/agents/base.py`
- Test: `tests/agents/test_base.py` (extend)

**Interfaces:**
- Produces: `BaseAgent.__init__(self, runner, read_store, committer, interval, name=None, personality: str = "")` — stores `self.personality = personality`, additive trailing kwarg (every existing positional/keyword call site unaffected). `async def _remark(self, note: str) -> None` — if `note` is falsy/empty, no-op; else `await self._committer.commit(self.name, EventType.AGENT_REMARKED, self.name, AgentRemark(agent_name=self.name, note=note))` (uses `self.name` as the `aggregate_id` — remarks have no aggregate of their own, and reusing the agent's name keeps every remark from the same agent grouped, which is harmless since the feed reads sequentially off the log, not by aggregate).

- [ ] **Step 1: Write the failing tests**

Read `tests/agents/test_base.py` first to confirm its existing fixture/import shape, then append:
```python
import os
import tempfile
import pytest
from novelizer.canon.event_store import EventStore
from novelizer.canon.projector import Projector
from novelizer.canon.read_store import ReadStore
from novelizer.canon.committer import Committer
from novelizer.canon.events import EventType
from novelizer.agents.base import BaseAgent


@pytest.fixture
async def stack():
    fd, path = tempfile.mkstemp(suffix=".db"); os.close(fd)
    events = EventStore(path); await events.init()
    proj = Projector(events, path); await proj.init()
    read = ReadStore(path); await read.init()
    yield events, proj, read, Committer(events)
    await read.close(); await proj.close(); await events.close(); os.unlink(path)


def test_personality_defaults_to_empty_string(stack):
    events, proj, read, committer = stack
    agent = BaseAgent(None, read, committer, interval=60, name="test_agent")
    assert agent.personality == ""


def test_personality_is_stored_when_provided(stack):
    events, proj, read, committer = stack
    agent = BaseAgent(None, read, committer, interval=60, name="test_agent", personality="A dry wit.")
    assert agent.personality == "A dry wit."


async def test_remark_emits_agent_remarked_event_when_note_present(stack):
    events, proj, read, committer = stack
    agent = BaseAgent(None, read, committer, interval=60, name="author")
    await agent._remark("Another storm brewing.")
    await proj.catch_up()
    log = await events.events_since(0)
    assert len(log) == 1
    assert log[0].event_type == EventType.AGENT_REMARKED
    assert log[0].payload["agent_name"] == "author"
    assert log[0].payload["note"] == "Another storm brewing."


async def test_remark_is_a_noop_when_note_is_empty(stack):
    events, proj, read, committer = stack
    agent = BaseAgent(None, read, committer, interval=60, name="author")
    await agent._remark("")
    log = await events.events_since(0)
    assert log == []
```
(If `tests/agents/test_base.py` already defines a differently-shaped fixture, adapt the fixture name/unpacking above to match it exactly rather than introducing a second one — the assertions are what matter.)

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/agents/test_base.py -v`
Expected: FAIL (`TypeError: BaseAgent.__init__() got an unexpected keyword argument 'personality'`; `AttributeError: 'BaseAgent' object has no attribute '_remark'`).

- [ ] **Step 3: Implement**

Replace `novelizer/agents/base.py`:
```python
from __future__ import annotations
from typing import Protocol
from pydantic import BaseModel, Field
from novelizer.canon.events import EventType, AgentRemark


class ChapterDraft(BaseModel):
    title: str
    prose: str
    character_ids: list[str] = Field(default_factory=list)


class Runner(Protocol):
    async def ainvoke(self, inputs: dict) -> dict: ...


class BaseAgent:
    name: str = "agent"

    def __init__(
        self,
        runner,
        read_store,
        committer,
        interval: int,
        name: str | None = None,
        personality: str = "",
    ) -> None:
        self._runner = runner
        self._read = read_store
        self._committer = committer
        self.interval = interval
        if name is not None:
            self.name = name
        self.personality = personality
        self.paused = False
        self._last_run = 0.0

    def pause(self) -> None:
        self.paused = True

    def resume(self) -> None:
        self.paused = False

    def ready_for_interval(self, now: float) -> bool:
        return (now - self._last_run) >= self.interval

    def mark_ran(self, now: float) -> None:
        self._last_run = now

    async def readiness(self) -> float:
        return 0.0

    async def run_once(self) -> None:
        pass

    async def _consume_signals(self, signals) -> None:
        for sig in signals:
            consumed = sig.model_copy(update={"consumed": True})
            await self._committer.commit(self.name, EventType.DIRECTOR_SIGNAL_CONSUMED, sig.id, consumed)

    async def _remark(self, note: str) -> None:
        """Emit a short in-personality feed line as agent.remarked. No-op if note is empty."""
        if not note:
            return
        await self._committer.commit(
            self.name, EventType.AGENT_REMARKED, self.name, AgentRemark(agent_name=self.name, note=note)
        )
```
(Only the new `personality` parameter/attribute, the `AgentRemark` import, and the `_remark` method are additions — `pause/resume/ready_for_interval/mark_ran/readiness/run_once/_consume_signals`, `ChapterDraft`, and `Runner` are unchanged.)

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/agents/test_base.py -v`
Expected: PASS (all prior + 4 new).

- [ ] **Step 5: Commit**

```bash
git add novelizer/agents/base.py tests/agents/test_base.py
git commit -m "feat: BaseAgent gains personality param and _remark(note) -> agent.remarked helper"
```

---

### Task 3: `feed_note` on all six response schemas

**Files:**
- Modify: `novelizer/agents/schemas.py`
- Modify: `novelizer/agents/base.py` (`ChapterDraft`)
- Test: `tests/agents/test_schemas.py` (extend)

**Interfaces:**
- Produces: `feed_note: str = ""` added to `ChapterDraft` (in `base.py`), `WorldEntriesDraft`, `KeeperOutput`, `EditorVerdict`, `ContinuityOutput`, `RetconAmendments` (in `schemas.py`). Deliberately **not** added to the inner per-item drafts (`WorldEntryDraft`, `CharacterUpdate`, `RetconDraft`) — `feed_note` is a whole-turn remark from the agent, one per `work()` call, matching the shape `commit()` already receives as its single `out`/`draft`/`verdict` argument.

- [ ] **Step 1: Write the failing tests**

Append to `tests/agents/test_schemas.py`:
```python
from novelizer.agents.base import ChapterDraft
from novelizer.agents.schemas import (
    WorldEntriesDraft, KeeperOutput, EditorVerdict, ContinuityOutput, RetconAmendments,
)


def test_feed_note_defaults_empty_on_all_response_schemas():
    assert ChapterDraft(title="T", prose="P").feed_note == ""
    assert WorldEntriesDraft().feed_note == ""
    assert KeeperOutput().feed_note == ""
    assert EditorVerdict().feed_note == ""
    assert ContinuityOutput().feed_note == ""
    assert RetconAmendments().feed_note == ""


def test_feed_note_roundtrips_when_set():
    draft = ChapterDraft(title="T", prose="P", feed_note="Another storm, another chapter.")
    assert draft.model_validate_json(draft.model_dump_json()).feed_note == "Another storm, another chapter."
    verdict = EditorVerdict(verdict="approve", notes="clean", feed_note="Finally, a clean draft.")
    assert verdict.feed_note == "Finally, a clean draft."
```
(If `tests/agents/test_schemas.py` does not yet exist or has a different import layout, match its existing style; the file is listed as already present in `tests/agents/`.)

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/agents/test_schemas.py -v`
Expected: FAIL (`pydantic_core._pydantic_core.ValidationError` or `AttributeError: 'ChapterDraft' object has no attribute 'feed_note'`).

- [ ] **Step 3: Implement**

In `novelizer/agents/base.py`, extend `ChapterDraft`:
```python
class ChapterDraft(BaseModel):
    title: str
    prose: str
    character_ids: list[str] = Field(default_factory=list)
    feed_note: str = ""
```

Replace `novelizer/agents/schemas.py`:
```python
from __future__ import annotations
from typing import Literal, Optional
from pydantic import BaseModel, Field


class WorldEntryDraft(BaseModel):
    title: str
    body: str
    domain: str = "physical"
    tags: list[str] = Field(default_factory=list)
    supersedes_id: Optional[str] = None


class WorldEntriesDraft(BaseModel):
    entries: list[WorldEntryDraft] = Field(default_factory=list)
    feed_note: str = ""


class CharacterUpdate(BaseModel):
    id: str
    arc_status: Optional[str] = None
    traits: Optional[str] = None
    motivations: Optional[str] = None
    backstory: Optional[str] = None


class RetconDraft(BaseModel):
    description: str
    conflicting_entry_ids: list[str] = Field(default_factory=list)
    proposed_resolution: str = ""


class KeeperOutput(BaseModel):
    updated_characters: list[CharacterUpdate] = Field(default_factory=list)
    retcon_requests: list[RetconDraft] = Field(default_factory=list)
    feed_note: str = ""


class EditorVerdict(BaseModel):
    verdict: Literal["approve", "revise"] = "approve"
    notes: str = ""
    feed_note: str = ""


class ContinuityOutput(BaseModel):
    retcon_requests: list[RetconDraft] = Field(default_factory=list)
    feed_note: str = ""


class RetconAmendments(BaseModel):
    amended_entries: list[WorldEntryDraft] = Field(default_factory=list)
    feed_note: str = ""
```
(`WorldEntryDraft`, `CharacterUpdate`, `RetconDraft` are unchanged; only `WorldEntriesDraft`, `KeeperOutput`, `EditorVerdict`, `ContinuityOutput`, `RetconAmendments` gain `feed_note: str = ""`.)

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/agents/test_schemas.py -v`
Expected: PASS. Then `uv run pytest tests/agents/ -v` to confirm every existing agent test (which constructs these models without `feed_note`) is still green.

- [ ] **Step 5: Commit**

```bash
git add novelizer/agents/base.py novelizer/agents/schemas.py tests/agents/test_schemas.py
git commit -m "feat: add optional feed_note field to all six agent response schemas"
```

---

### Task 4: Author + Editor — personality injection and feed_note emission

**Files:**
- Modify: `novelizer/agents/author.py`
- Modify: `novelizer/agents/editor.py`
- Test: `tests/agents/test_author.py` (extend), `tests/agents/test_editor.py` (extend)

**Interfaces:**
- `Author.__init__(self, runner, read_store, committer, interval=300, casting_note="", personality="")` — passes `personality=personality` up to `BaseAgent.__init__`. Module `_summarize(ctx, casting_note="", personality="")` gains an `"\n\nIn character: {personality}"` line, appended only when `personality` is non-empty, placed after the voice line so both compose cleanly when both are set. `commit(draft, ctx)` gains `await self._remark(draft.feed_note)` (after the existing `CHAPTER_CREATED` commit, before `_consume_signals`, matching the file's existing statement order).
- `Editor.__init__(self, runner, read_store, committer, interval=120, casting_note="", personality="")` — same pattern. `work(ctx)`'s message gains an `"\n\nIn character: {personality}"` line (same non-empty guard). `commit(verdict, ctx)` gains `await self._remark(verdict.feed_note)` at the end of the method (after either branch, since a remark can accompany approval or revision).

- [ ] **Step 1: Write the failing tests**

Append to `tests/agents/test_author.py`:
```python
async def test_work_prompt_includes_personality_when_set(stack):
    events, proj, read, committer = stack
    draft = ChapterDraft(title="T", prose="P")
    runner = FakeRunner(draft)
    author = Author(runner, read, committer, personality="A restless, romantic chronicler.")
    ctx = await author.poll()
    await author.work(ctx)
    sent = runner.calls[-1]["messages"][0]["content"]
    assert "A restless, romantic chronicler." in sent
    assert "In character:" in sent


async def test_work_prompt_omits_personality_line_when_unset(stack):
    events, proj, read, committer = stack
    draft = ChapterDraft(title="T", prose="P")
    runner = FakeRunner(draft)
    author = Author(runner, read, committer)
    ctx = await author.poll()
    await author.work(ctx)
    sent = runner.calls[-1]["messages"][0]["content"]
    assert "In character:" not in sent


async def test_commit_emits_agent_remarked_when_feed_note_present(stack):
    events, proj, read, committer = stack
    draft = ChapterDraft(title="T", prose="P", feed_note="Another chapter, another heartbreak.")
    author = Author(FakeRunner(draft), read, committer)
    await author.run_once()
    await proj.catch_up()
    log = await events.events_since(0)
    remarks = [e for e in log if e.event_type == EventType.AGENT_REMARKED]
    assert len(remarks) == 1
    assert remarks[0].payload["agent_name"] == "author"
    assert remarks[0].payload["note"] == "Another chapter, another heartbreak."


async def test_commit_emits_no_remark_when_feed_note_empty(stack):
    events, proj, read, committer = stack
    draft = ChapterDraft(title="T", prose="P")
    author = Author(FakeRunner(draft), read, committer)
    await author.run_once()
    await proj.catch_up()
    log = await events.events_since(0)
    assert [e for e in log if e.event_type == EventType.AGENT_REMARKED] == []
```

Append to `tests/agents/test_editor.py`:
```python
async def test_editor_prompt_includes_personality_when_set(stack):
    events, proj, read, committer = stack
    await events.append(EventType.CHAPTER_CREATED, "c1", Chapter(id="c1", title="One", prose="p"))
    await proj.catch_up()
    runner = FakeRunner(EditorVerdict(verdict="approve", notes="clean"))
    agent = Editor(runner, read, committer, personality="A precise, unsentimental line editor.")
    ctx = await agent.poll()
    await agent.work(ctx)
    sent = runner.calls[-1]["messages"][0]["content"]
    assert "A precise, unsentimental line editor." in sent
    assert "In character:" in sent


async def test_editor_commit_emits_remark_on_approval(stack):
    events, proj, read, committer = stack
    await events.append(EventType.CHAPTER_CREATED, "c1", Chapter(id="c1", title="One", prose="p"))
    await proj.catch_up()
    verdict = EditorVerdict(verdict="approve", notes="clean", feed_note="Finally, a clean draft.")
    agent = Editor(FakeRunner(verdict), read, committer)
    await agent.run_once()
    await proj.catch_up()
    log = await events.events_since(0)
    remarks = [e for e in log if e.event_type == EventType.AGENT_REMARKED]
    assert len(remarks) == 1
    assert remarks[0].payload["note"] == "Finally, a clean draft."


async def test_editor_commit_emits_remark_on_revision(stack):
    events, proj, read, committer = stack
    await events.append(EventType.CHAPTER_CREATED, "c1", Chapter(id="c1", title="One", prose="p"))
    await proj.catch_up()
    verdict = EditorVerdict(verdict="revise", notes="middle sags", feed_note="This needs more tension.")
    agent = Editor(FakeRunner(verdict), read, committer)
    await agent.run_once()
    await proj.catch_up()
    log = await events.events_since(0)
    remarks = [e for e in log if e.event_type == EventType.AGENT_REMARKED]
    assert len(remarks) == 1
    assert remarks[0].payload["note"] == "This needs more tension."
```
(`FakeRunner`, `EditorVerdict`, `Chapter`, `EventType`, `Editor` are already imported at the top of `tests/agents/test_editor.py` from the M2.1 plan; `FakeRunner` in `test_author.py` already records `self.calls`.)

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/agents/test_author.py tests/agents/test_editor.py -v`
Expected: FAIL (`TypeError: Author.__init__() got an unexpected keyword argument 'personality'`).

- [ ] **Step 3: Implement**

Replace `novelizer/agents/author.py`:
```python
from __future__ import annotations
from novelizer.agents.base import BaseAgent, ChapterDraft, Runner
from novelizer.canon.read_store import ReadStore
from novelizer.canon.committer import Committer
from novelizer.canon.events import EventType
from novelizer.store.models import Chapter

AUTHOR_SYSTEM_PROMPT = """You are the Author of a living fictional world. Write the next prose chapter.
You receive world lore, active characters, previous chapter summaries, and director notes.
Write a self-contained chapter with a clear narrative beat, 2-5 paragraphs.
Return a title, the full prose, and the ids of characters who appear."""


def _summarize(ctx: dict, casting_note: str = "", personality: str = "") -> str:
    world = "\n".join(f"- {e.title}: {e.body[:150]}" for e in ctx["world"][:10]) or "None yet."
    chars = "\n".join(f"- {c.name}: {c.traits} | arc: {c.arc_status}" for c in ctx["characters"][:8]) or "None yet."
    prev = "\n".join(f"- '{c.title}': {c.prose[:200]}" for c in ctx["previous"]) or "None yet."
    notes = "\n".join(f"Director: {s.body}" for s in ctx["signals"]) or "None."
    voice = f"\n\nWrite in this prose voice: {casting_note}" if casting_note else ""
    cast = f"\n\nIn character: {personality}" if personality else ""
    return (
        f"World lore:\n{world}\n\nCharacters:\n{chars}\n\n"
        f"Previous chapters:\n{prev}\n\nDirector notes:\n{notes}{voice}{cast}\n\nWrite the next chapter."
    )


class Author(BaseAgent):
    def __init__(
        self,
        runner: Runner,
        read_store: ReadStore,
        committer: Committer,
        interval: int = 300,
        casting_note: str = "",
        personality: str = "",
    ) -> None:
        super().__init__(runner, read_store, committer, interval, name="author", personality=personality)
        self._casting_note = casting_note

    async def readiness(self) -> float:
        drafts = len(await self._read.list_chapters(status="draft"))
        return max(0.0, 1.0 - drafts / 3)

    async def poll(self) -> dict:
        chapters = await self._read.list_chapters()
        return {
            "world": await self._read.list_world_entries(),
            "characters": await self._read.list_characters(),
            "previous": chapters[-3:],
            "signals": await self._read.list_unconsumed_signals(target_agent=self.name),
        }

    async def work(self, ctx: dict) -> ChapterDraft | None:
        content = _summarize(ctx, self._casting_note, self.personality)
        result = await self._runner.ainvoke({"messages": [{"role": "user", "content": content}]})
        return result.get("structured_response")

    async def commit(self, draft: ChapterDraft | None, ctx: dict) -> None:
        if draft is None:
            return
        chapter = Chapter(title=draft.title, prose=draft.prose, character_ids=draft.character_ids)
        await self._committer.commit(self.name, EventType.CHAPTER_CREATED, chapter.id, chapter)
        await self._remark(draft.feed_note)
        await self._consume_signals(ctx["signals"])

    async def run_once(self) -> None:
        ctx = await self.poll()
        draft = await self.work(ctx)
        await self.commit(draft, ctx)


def build_author_runner(settings):
    from deepagents import create_deep_agent
    from novelizer.agents.llm import build_chat_model
    model = build_chat_model(settings.author_model, settings.llm_base_url, settings.llm_api_key, settings.author_temperature)
    return create_deep_agent(model=model, system_prompt=AUTHOR_SYSTEM_PROMPT, response_format=ChapterDraft)
```

Replace `novelizer/agents/editor.py`:
```python
from __future__ import annotations
from novelizer.agents.base import BaseAgent, Runner
from novelizer.agents.schemas import EditorVerdict
from novelizer.canon.read_store import ReadStore
from novelizer.canon.committer import Committer
from novelizer.canon.events import EventType
from novelizer.store.models import DirectorSignal, SignalKind, EditorialStatus

SYSTEM_PROMPT = """You are the Editor of a living fictional world's story. Review the given chapter
for prose quality, narrative coherence, and pacing. Return a verdict of "approve" or "revise" and
notes: if revising, specific actionable feedback; if approving, brief praise."""


class Editor(BaseAgent):
    def __init__(
        self,
        runner: Runner,
        read_store: ReadStore,
        committer: Committer,
        interval: int = 120,
        casting_note: str = "",
        personality: str = "",
    ) -> None:
        super().__init__(runner, read_store, committer, interval, name="editor", personality=personality)
        self._casting_note = casting_note

    async def readiness(self) -> float:
        drafts = len(await self._read.list_chapters(status=EditorialStatus.draft))
        return min(1.0, drafts / 3)

    async def poll(self) -> dict:
        drafts = await self._read.list_chapters(status=EditorialStatus.draft)
        return {"target": drafts[0] if drafts else None}

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
        msg = f"Chapter title: {ch.title}\n\nProse:\n{ch.prose}{voice}{cast}"
        result = await self._runner.ainvoke({"messages": [{"role": "user", "content": msg}]})
        return result.get("structured_response")

    async def commit(self, verdict: EditorVerdict | None, ctx: dict) -> None:
        ch = ctx["target"]
        if ch is None or verdict is None:
            return
        if verdict.verdict == "approve":
            updated = ch.model_copy(update={"editorial_status": EditorialStatus.reviewed, "editor_notes": verdict.notes})
            await self._committer.commit(self.name, EventType.CHAPTER_STATUS_CHANGED, updated.id, updated)
        else:
            sig = DirectorSignal(kind=SignalKind.note, body=f"[Editor on '{ch.title}'] {verdict.notes}", target_agent="author")
            await self._committer.commit(self.name, EventType.DIRECTOR_SIGNAL_CREATED, sig.id, sig)
        await self._remark(verdict.feed_note)

    async def run_once(self) -> None:
        ctx = await self.poll()
        verdict = await self.work(ctx)
        await self.commit(verdict, ctx)


def build_editor_runner(settings):
    from deepagents import create_deep_agent
    from novelizer.agents.llm import build_chat_model
    model = build_chat_model(settings.agent_model, settings.llm_base_url, settings.llm_api_key, settings.agent_temperature)
    return create_deep_agent(model=model, system_prompt=SYSTEM_PROMPT, response_format=EditorVerdict)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/agents/test_author.py tests/agents/test_editor.py -v`
Expected: PASS (all prior + 7 new).

- [ ] **Step 5: Commit**

```bash
git add novelizer/agents/author.py novelizer/agents/editor.py tests/agents/test_author.py tests/agents/test_editor.py
git commit -m "feat: Author/Editor inject personality into work prompt and emit feed_note remarks"
```

---

### Task 5: World Architect + Character Keeper — personality injection and feed_note emission

**Files:**
- Modify: `novelizer/agents/world_architect.py`
- Modify: `novelizer/agents/character_keeper.py`
- Test: `tests/agents/test_world_architect.py` (extend), `tests/agents/test_character_keeper.py` (extend)

**Interfaces:**
- `WorldArchitect.__init__(self, runner, read_store, committer, interval=120, personality="")` — passes `personality=personality` to `BaseAgent.__init__`. `work(ctx)`'s `msg` gains `"\n\nIn character: {personality}"` appended before `"Generate new world entries."` when non-empty. `commit(draft, ctx)` gains `await self._remark(draft.feed_note)` (guarded by `draft is not None`, since `WorldEntriesDraft` — unlike `ChapterDraft` — can legitimately be `None`).
- `CharacterKeeper.__init__(self, runner, read_store, committer, interval=120, personality="")` — same pattern. `work(ctx)`'s `msg` gains the same `"In character:"` line. `commit(out, ctx)` gains `await self._remark(out.feed_note)`, guarded by the existing `if out is None: return` early return.

- [ ] **Step 1: Write the failing tests**

Append to `tests/agents/test_world_architect.py`:
```python
async def test_work_prompt_includes_personality_when_set(stack):
    events, proj, read, committer = stack
    runner = FakeRunner(WorldEntriesDraft())
    agent = WorldArchitect(runner, read, committer, personality="A quietly obsessive worldbuilder.")
    ctx = await agent.poll()
    await agent.work(ctx)
    sent = runner.calls[-1]["messages"][0]["content"]
    assert "A quietly obsessive worldbuilder." in sent
    assert "In character:" in sent


async def test_work_prompt_omits_personality_line_when_unset(stack):
    events, proj, read, committer = stack
    runner = FakeRunner(WorldEntriesDraft())
    agent = WorldArchitect(runner, read, committer)
    ctx = await agent.poll()
    await agent.work(ctx)
    sent = runner.calls[-1]["messages"][0]["content"]
    assert "In character:" not in sent


async def test_commit_emits_remark_when_feed_note_present(stack):
    events, proj, read, committer = stack
    draft = WorldEntriesDraft(feed_note="Another corner of the map, filled in.")
    agent = WorldArchitect(FakeRunner(draft), read, committer)
    await agent.run_once()
    await proj.catch_up()
    log = await events.events_since(0)
    remarks = [e for e in log if e.event_type == EventType.AGENT_REMARKED]
    assert len(remarks) == 1
    assert remarks[0].payload["agent_name"] == "world_architect"
    assert remarks[0].payload["note"] == "Another corner of the map, filled in."
```
(`FakeRunner`, `WorldEntriesDraft`, `WorldArchitect`, `EventType` are already imported at the top of `tests/agents/test_world_architect.py`.)

Append to `tests/agents/test_character_keeper.py`:
```python
async def test_work_prompt_includes_personality_when_set(stack):
    events, proj, read, committer = stack
    await events.append(EventType.CHARACTER_CREATED, "ch1", Character(id="ch1", name="Mara", traits="wary"))
    await proj.catch_up()
    runner = FakeRunner(KeeperOutput())
    agent = CharacterKeeper(runner, read, committer, personality="A protective, watchful presence.")
    ctx = await agent.poll()
    await agent.work(ctx)
    sent = runner.calls[-1]["messages"][0]["content"]
    assert "A protective, watchful presence." in sent
    assert "In character:" in sent


async def test_commit_emits_remark_when_feed_note_present(stack):
    events, proj, read, committer = stack
    out = KeeperOutput(feed_note="Mara's arc is bending toward trust.")
    agent = CharacterKeeper(FakeRunner(out), read, committer)
    await agent.commit(out, {"characters": [], "recent": []})
    await proj.catch_up()
    log = await events.events_since(0)
    remarks = [e for e in log if e.event_type == EventType.AGENT_REMARKED]
    assert len(remarks) == 1
    assert remarks[0].payload["note"] == "Mara's arc is bending toward trust."
```
(`FakeRunner`, `KeeperOutput`, `CharacterKeeper`, `Character`, `EventType` are already imported/available at the top of `tests/agents/test_character_keeper.py` per the file's existing pattern — `Character` comes from `novelizer.store.models`; add that import if not already present.)

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/agents/test_world_architect.py tests/agents/test_character_keeper.py -v`
Expected: FAIL (`TypeError: WorldArchitect.__init__() got an unexpected keyword argument 'personality'`).

- [ ] **Step 3: Implement**

Replace `novelizer/agents/world_architect.py`:
```python
from __future__ import annotations
from novelizer.agents.base import BaseAgent, Runner
from novelizer.agents.schemas import WorldEntriesDraft
from novelizer.canon.read_store import ReadStore
from novelizer.canon.committer import Committer
from novelizer.canon.events import EventType
from novelizer.store.models import WorldEntry

SYSTEM_PROMPT = """You are the World Architect for an ever-expanding fictional world.
Generate new lore, geography, factions, history, and cosmology. You receive a summary of
what already exists plus any director seeds; identify thin or unexplored areas and expand them.
Return 1-3 new world entries, each with a title, 2-4 paragraphs of rich body lore, a domain
(one of: physical, social, metaphysical, historical, other), and tags."""


class WorldArchitect(BaseAgent):
    def __init__(
        self,
        runner: Runner,
        read_store: ReadStore,
        committer: Committer,
        interval: int = 120,
        personality: str = "",
    ) -> None:
        super().__init__(runner, read_store, committer, interval, name="world_architect", personality=personality)

    async def readiness(self) -> float:
        count = len(await self._read.list_world_entries())
        return max(0.2, 1.0 - count / 50)

    async def poll(self) -> dict:
        return {
            "entries": await self._read.list_world_entries(),
            "signals": await self._read.list_unconsumed_signals(target_agent=self.name),
        }

    async def work(self, ctx: dict) -> WorldEntriesDraft | None:
        existing = "\n".join(f"- [{e.domain}] {e.title}: {e.body[:100]}" for e in ctx["entries"][:20]) or "The world is empty."
        seeds = "\n".join(f"Director seed: {s.body}" for s in ctx["signals"]) or "None."
        cast = f"\n\nIn character: {self.personality}" if self.personality else ""
        msg = f"Existing world entries:\n{existing}\n\nDirector seeds:\n{seeds}{cast}\n\nGenerate new world entries."
        result = await self._runner.ainvoke({"messages": [{"role": "user", "content": msg}]})
        return result.get("structured_response")

    async def commit(self, draft: WorldEntriesDraft | None, ctx: dict) -> None:
        if draft is not None:
            for e in draft.entries:
                entry = WorldEntry(title=e.title, body=e.body, domain=e.domain, tags=e.tags)
                await self._committer.commit(self.name, EventType.WORLD_ENTRY_CREATED, entry.id, entry)
            await self._remark(draft.feed_note)
        await self._consume_signals(ctx["signals"])

    async def run_once(self) -> None:
        ctx = await self.poll()
        draft = await self.work(ctx)
        await self.commit(draft, ctx)


def build_world_architect_runner(settings):
    from deepagents import create_deep_agent
    from novelizer.agents.llm import build_chat_model
    model = build_chat_model(settings.agent_model, settings.llm_base_url, settings.llm_api_key, settings.agent_temperature)
    return create_deep_agent(model=model, system_prompt=SYSTEM_PROMPT, response_format=WorldEntriesDraft)
```

Replace `novelizer/agents/character_keeper.py`:
```python
from __future__ import annotations
from novelizer.agents.base import BaseAgent, Runner
from novelizer.agents.schemas import KeeperOutput
from novelizer.canon.read_store import ReadStore
from novelizer.canon.committer import Committer
from novelizer.canon.events import EventType
from novelizer.store.models import RetconRequest

SYSTEM_PROMPT = """You are the Character Keeper for a living fictional world.
You receive characters (with traits and arcs) and recent prose chapters. Your tasks:
1. Update each character's arc_status to reflect what recent chapters show.
2. Flag behavioral contradictions between a character's defined traits and their actions.
Return updated_characters (id + revised arc_status, and any corrected traits/motivations/backstory)
and retcon_requests (description, conflicting_entry_ids, proposed_resolution)."""


class CharacterKeeper(BaseAgent):
    def __init__(
        self,
        runner: Runner,
        read_store: ReadStore,
        committer: Committer,
        interval: int = 120,
        personality: str = "",
    ) -> None:
        super().__init__(runner, read_store, committer, interval, name="character_keeper", personality=personality)

    async def readiness(self) -> float:
        chars = await self._read.list_characters()
        chapters = await self._read.list_chapters()
        return 0.5 if (chars and chapters) else 0.2

    async def poll(self) -> dict:
        chapters = await self._read.list_chapters()
        return {"characters": await self._read.list_characters(), "recent": chapters[-5:]}

    async def work(self, ctx: dict) -> KeeperOutput | None:
        if not ctx["characters"]:
            return None
        chars = "\n".join(f"- {c.name} (id:{c.id}): traits={c.traits}, arc={c.arc_status}" for c in ctx["characters"])
        chapters = "\n\n".join(f"Chapter '{c.title}': {c.prose[:300]}" for c in ctx["recent"]) or "None."
        cast = f"\n\nIn character: {self.personality}" if self.personality else ""
        msg = f"Characters:\n{chars}\n\nRecent chapters:\n{chapters}{cast}"
        result = await self._runner.ainvoke({"messages": [{"role": "user", "content": msg}]})
        return result.get("structured_response")

    async def commit(self, out: KeeperOutput | None, ctx: dict) -> None:
        if out is None:
            return
        for upd in out.updated_characters:
            current = await self._read.get_character(upd.id)
            if current is None:
                continue
            fields = {}
            for f in ("arc_status", "traits", "motivations", "backstory"):
                v = getattr(upd, f)
                if v is not None:
                    fields[f] = v
            updated = current.model_copy(update=fields)
            await self._committer.commit(self.name, EventType.CHARACTER_UPDATED, updated.id, updated)
        for r in out.retcon_requests:
            req = RetconRequest(description=r.description, conflicting_entry_ids=r.conflicting_entry_ids,
                                proposed_resolution=r.proposed_resolution)
            await self._committer.commit(self.name, EventType.RETCON_REQUEST_CREATED, req.id, req)
        await self._remark(out.feed_note)

    async def run_once(self) -> None:
        ctx = await self.poll()
        out = await self.work(ctx)
        await self.commit(out, ctx)


def build_character_keeper_runner(settings):
    from deepagents import create_deep_agent
    from novelizer.agents.llm import build_chat_model
    model = build_chat_model(settings.agent_model, settings.llm_base_url, settings.llm_api_key, settings.agent_temperature)
    return create_deep_agent(model=model, system_prompt=SYSTEM_PROMPT, response_format=KeeperOutput)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/agents/test_world_architect.py tests/agents/test_character_keeper.py -v`
Expected: PASS (all prior + 5 new).

- [ ] **Step 5: Commit**

```bash
git add novelizer/agents/world_architect.py novelizer/agents/character_keeper.py \
        tests/agents/test_world_architect.py tests/agents/test_character_keeper.py
git commit -m "feat: WorldArchitect/CharacterKeeper inject personality into work prompt and emit feed_note remarks"
```

---

### Task 6: Continuity Checker + Retconner — personality injection and feed_note emission

**Files:**
- Modify: `novelizer/agents/continuity_checker.py`
- Modify: `novelizer/agents/retconner.py`
- Test: `tests/agents/test_continuity_checker.py` (extend), `tests/agents/test_retconner.py` (extend)

**Interfaces:**
- `ContinuityChecker.__init__(self, runner, read_store, committer, interval=900, personality="")` — same pattern. `work(ctx)`'s `msg` gains the `"In character:"` line. `commit(out, ctx)` gains `await self._remark(out.feed_note)`, guarded by the existing `if out is None: return`.
- `Retconner.__init__(self, runner, read_store, committer, interval=120, personality="")` — same pattern. `work(ctx)`'s `msg` gains the `"In character:"` line. `commit(out, ctx)` gains `await self._remark(out.feed_note)`, guarded by the existing `if req is None or out is None: return`.

- [ ] **Step 1: Write the failing tests**

Append to `tests/agents/test_continuity_checker.py`:
```python
async def test_work_prompt_includes_personality_when_set(stack):
    events, proj, read, committer = stack
    runner = FakeRunner(ContinuityOutput())
    agent = ContinuityChecker(runner, read, committer, personality="A dry, pedantic fact-checker.")
    ctx = await agent.poll()
    await agent.work(ctx)
    sent = runner.calls[-1]["messages"][0]["content"]
    assert "A dry, pedantic fact-checker." in sent
    assert "In character:" in sent


async def test_commit_emits_remark_when_feed_note_present(stack):
    events, proj, read, committer = stack
    out = ContinuityOutput(feed_note="Two suns again. Nobody else noticed.")
    agent = ContinuityChecker(FakeRunner(out), read, committer)
    await agent.commit(out, {})
    await proj.catch_up()
    log = await events.events_since(0)
    remarks = [e for e in log if e.event_type == EventType.AGENT_REMARKED]
    assert len(remarks) == 1
    assert remarks[0].payload["note"] == "Two suns again. Nobody else noticed."
```
(`FakeRunner`, `ContinuityOutput`, `ContinuityChecker`, `EventType` are already imported at the top of `tests/agents/test_continuity_checker.py`.)

Append to `tests/agents/test_retconner.py`:
```python
async def test_work_prompt_includes_personality_when_set(stack):
    events, proj, read, committer = stack
    from novelizer.store.models import RetconRequest
    req = RetconRequest(id="r1", description="scar mismatch", conflicting_entry_ids=[], proposed_resolution="")
    await events.append(EventType.RETCON_REQUEST_CREATED, "r1", req)
    await proj.catch_up()
    runner = FakeRunner(RetconAmendments())
    agent = Retconner(runner, read, committer, personality="A calm, surgical fixer.")
    ctx = await agent.poll()
    await agent.work(ctx)
    sent = runner.calls[-1]["messages"][0]["content"]
    assert "A calm, surgical fixer." in sent
    assert "In character:" in sent


async def test_commit_emits_remark_when_feed_note_present(stack):
    events, proj, read, committer = stack
    from novelizer.store.models import RetconRequest
    req = RetconRequest(id="r1", description="scar mismatch", conflicting_entry_ids=[], proposed_resolution="")
    out = RetconAmendments(feed_note="Tidied up. No drama needed.")
    agent = Retconner(FakeRunner(out), read, committer)
    await agent.commit(out, {"target": req, "world": []})
    await proj.catch_up()
    log = await events.events_since(0)
    remarks = [e for e in log if e.event_type == EventType.AGENT_REMARKED]
    assert len(remarks) == 1
    assert remarks[0].payload["note"] == "Tidied up. No drama needed."
```
(`FakeRunner`, `RetconAmendments`, `Retconner`, `EventType` are already imported at the top of `tests/agents/test_retconner.py`.)

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/agents/test_continuity_checker.py tests/agents/test_retconner.py -v`
Expected: FAIL (`TypeError: ContinuityChecker.__init__() got an unexpected keyword argument 'personality'`).

- [ ] **Step 3: Implement**

Replace `novelizer/agents/continuity_checker.py`:
```python
from __future__ import annotations
from novelizer.agents.base import BaseAgent, Runner
from novelizer.agents.schemas import ContinuityOutput
from novelizer.canon.read_store import ReadStore
from novelizer.canon.committer import Committer
from novelizer.canon.events import EventType
from novelizer.store.models import RetconRequest, RetconStatus

SYSTEM_PROMPT = """You are the Continuity Checker for a living fictional world. Review the given world
entries, characters, and chapter excerpts for contradictions, anachronisms, or logical inconsistencies.
Return retcon_requests, each with a description (what contradicts what), conflicting_entry_ids (the ids
of the conflicting records), and a proposed_resolution. Return an empty list if you find nothing."""


class ContinuityChecker(BaseAgent):
    def __init__(
        self,
        runner: Runner,
        read_store: ReadStore,
        committer: Committer,
        interval: int = 900,
        personality: str = "",
    ) -> None:
        super().__init__(runner, read_store, committer, interval, name="continuity_checker", personality=personality)

    async def readiness(self) -> float:
        open_retcons = len(await self._read.list_retcon_requests(status=RetconStatus.open))
        return max(0.1, 1.0 - open_retcons / 5)

    async def poll(self) -> dict:
        chapters = await self._read.list_chapters()
        return {
            "world": await self._read.list_world_entries(),
            "characters": await self._read.list_characters(),
            "chapters": chapters[-10:],
        }

    async def work(self, ctx: dict) -> ContinuityOutput | None:
        world = "\n".join(f"[{e.id[:8]}] {e.title}: {e.body[:200]}" for e in ctx["world"][:20]) or "None."
        chars = "\n".join(f"[{c.id[:8]}] {c.name}: {c.traits}" for c in ctx["characters"][:10]) or "None."
        chapters = "\n".join(f"[{c.id[:8]}] {c.title}: {c.prose[:300]}" for c in ctx["chapters"]) or "None."
        cast = f"\n\nIn character: {self.personality}" if self.personality else ""
        msg = f"World entries:\n{world}\n\nCharacters:\n{chars}\n\nRecent chapters:\n{chapters}{cast}"
        result = await self._runner.ainvoke({"messages": [{"role": "user", "content": msg}]})
        return result.get("structured_response")

    async def commit(self, out: ContinuityOutput | None, ctx: dict) -> None:
        if out is None:
            return
        for r in out.retcon_requests:
            req = RetconRequest(description=r.description, conflicting_entry_ids=r.conflicting_entry_ids,
                                proposed_resolution=r.proposed_resolution)
            await self._committer.commit(self.name, EventType.RETCON_REQUEST_CREATED, req.id, req)
        await self._remark(out.feed_note)

    async def run_once(self) -> None:
        ctx = await self.poll()
        out = await self.work(ctx)
        await self.commit(out, ctx)


def build_continuity_checker_runner(settings):
    from deepagents import create_deep_agent
    from novelizer.agents.llm import build_chat_model
    model = build_chat_model(settings.agent_model, settings.llm_base_url, settings.llm_api_key, settings.agent_temperature)
    return create_deep_agent(model=model, system_prompt=SYSTEM_PROMPT, response_format=ContinuityOutput)
```

Replace `novelizer/agents/retconner.py`:
```python
from __future__ import annotations
from novelizer.agents.base import BaseAgent, Runner
from novelizer.agents.schemas import RetconAmendments
from novelizer.canon.read_store import ReadStore
from novelizer.canon.committer import Committer
from novelizer.canon.events import EventType
from novelizer.store.models import WorldEntry, RetconStatus

SYSTEM_PROMPT = """You are the Retconner for a living fictional world. You receive a contradiction report
and the conflicting world entries. Propose amended versions of the conflicting entries that resolve the
contradiction. Return amended_entries, each with a title, revised body, domain, tags, and supersedes_id
set to the id of the entry it replaces. Only include entries that need to change."""


class Retconner(BaseAgent):
    def __init__(
        self,
        runner: Runner,
        read_store: ReadStore,
        committer: Committer,
        interval: int = 120,
        personality: str = "",
    ) -> None:
        super().__init__(runner, read_store, committer, interval, name="retconner", personality=personality)

    async def readiness(self) -> float:
        open_retcons = len(await self._read.list_retcon_requests(status=RetconStatus.open))
        return min(1.0, open_retcons / 3)

    async def poll(self) -> dict:
        open_reqs = await self._read.list_retcon_requests(status=RetconStatus.open)
        return {"target": open_reqs[0] if open_reqs else None, "world": await self._read.list_world_entries()}

    async def work(self, ctx: dict) -> RetconAmendments | None:
        req = ctx["target"]
        if req is None:
            return None
        conflicting = [e for e in ctx["world"] if e.id in req.conflicting_entry_ids]
        text = "\n".join(f"[{e.id}] {e.title}: {e.body}" for e in conflicting) or "(entries not found)"
        cast = f"\n\nIn character: {self.personality}" if self.personality else ""
        msg = f"Contradiction: {req.description}\n\nProposed resolution: {req.proposed_resolution}\n\nConflicting entries:\n{text}{cast}"
        result = await self._runner.ainvoke({"messages": [{"role": "user", "content": msg}]})
        return result.get("structured_response")

    async def commit(self, out: RetconAmendments | None, ctx: dict) -> None:
        req = ctx["target"]
        if req is None or out is None:
            return
        for e in out.amended_entries:
            entry = WorldEntry(title=e.title, body=e.body, domain=e.domain, tags=e.tags, supersedes_id=e.supersedes_id)
            await self._committer.commit(self.name, EventType.WORLD_ENTRY_SUPERSEDED, entry.id, entry)
        resolved = req.model_copy(update={"status": RetconStatus.resolved, "resolved_by": self.name})
        await self._committer.commit(self.name, EventType.RETCON_REQUEST_RESOLVED, req.id, resolved)
        await self._remark(out.feed_note)

    async def run_once(self) -> None:
        ctx = await self.poll()
        out = await self.work(ctx)
        await self.commit(out, ctx)


def build_retconner_runner(settings):
    from deepagents import create_deep_agent
    from novelizer.agents.llm import build_chat_model
    model = build_chat_model(settings.agent_model, settings.llm_base_url, settings.llm_api_key, settings.agent_temperature)
    return create_deep_agent(model=model, system_prompt=SYSTEM_PROMPT, response_format=RetconAmendments)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/agents/test_continuity_checker.py tests/agents/test_retconner.py -v`
Expected: PASS (all prior + 4 new).

- [ ] **Step 5: Commit**

```bash
git add novelizer/agents/continuity_checker.py novelizer/agents/retconner.py \
        tests/agents/test_continuity_checker.py tests/agents/test_retconner.py
git commit -m "feat: ContinuityChecker/Retconner inject personality into work prompt and emit feed_note remarks"
```

---

### Task 7: Runtime — wire each agent's personality from the active voice pack

**Files:**
- Modify: `novelizer/runtime.py`
- Test: `tests/test_runtime.py` (extend)

**Interfaces:**
- `Runtime.start()` computes, alongside the existing `casting_note` resolution: `personalities = self.voice_pack.agent_personalities` (already a `dict[str, str]` on `VoicePack`). Each of the six agent constructions gains `personality=personalities.get("<agent_name>", "")` — e.g. `personality=personalities.get("author", "")`. Unknown/missing pack entries degrade to `""` (no crash), matching how M2.1's unknown `prose_profile` degrades to an empty `casting_note`.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_runtime.py` (reusing the file's existing `_FakeAgentRunner`/`_all_fake_runners` helpers from the M2.1 plan, or defining them locally if this is the first extension to reach them — check the file first):
```python
async def test_runtime_wires_each_agents_personality_from_the_pack():
    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    try:
        settings = Settings(db_path=path)
        rt = Runtime(settings, runners=_all_fake_runners())
        await rt.start()
        assert rt.author.personality == rt.voice_pack.agent_personalities["author"]
        assert rt.editor.personality == rt.voice_pack.agent_personalities["editor"]
        assert rt.world_architect.personality == rt.voice_pack.agent_personalities["world_architect"]
        assert rt.character_keeper.personality == rt.voice_pack.agent_personalities["character_keeper"]
        assert rt.continuity_checker.personality == rt.voice_pack.agent_personalities["continuity_checker"]
        assert rt.retconner.personality == rt.voice_pack.agent_personalities["retconner"]
        assert rt.author.personality != rt.editor.personality
        await rt.close()
    finally:
        os.unlink(path)


async def test_runtime_missing_personality_falls_back_to_empty_string():
    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    custom_pack_path = path + ".pack.toml"
    with open(custom_pack_path, "w") as f:
        f.write('name = "sparse-pack"\n')
    try:
        settings = Settings(db_path=path, voice_pack=custom_pack_path)
        rt = Runtime(settings, runners=_all_fake_runners())
        await rt.start()
        assert rt.author.personality == ""
        assert rt.retconner.personality == ""
        await rt.close()
    finally:
        os.unlink(path)
        os.unlink(custom_pack_path)
```
(If `_FakeAgentRunner`/`_all_fake_runners` are not yet present in `tests/test_runtime.py`, add them exactly as defined in the M2.1 plan's Task 5:
```python
class _FakeAgentRunner:
    async def ainvoke(self, inputs):
        return {"structured_response": None}


def _all_fake_runners():
    return {
        name: _FakeAgentRunner()
        for name in ("author", "world_architect", "character_keeper", "editor", "continuity_checker", "retconner")
    }
```
and ensure `import os, tempfile` and `from novelizer.config import Settings` / `from novelizer.runtime import Runtime` are present at module level — these are all already used elsewhere in the file per the M1.3/M2.1 plans.)

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_runtime.py -v`
Expected: FAIL (`AssertionError: '' == 'A restless, slightly romantic chronicler...'` — agents constructed with no `personality` kwarg still default to `""`).

- [ ] **Step 3: Implement**

In `novelizer/runtime.py`, insert the personality lookup right after `casting_note` is computed in `start()`:
```python
        self.voice_pack = load_voice_pack(self.settings.voice_pack)
        self.active_prose_profile = self.voice_pack.profile(self.settings.prose_profile)
        casting_note = self.active_prose_profile.casting_note if self.active_prose_profile else ""
        personalities = self.voice_pack.agent_personalities
        s = self.settings
        self.author = Author(
            self._runner_for("author", build_author_runner), self.read, self.committer,
            interval=s.author_interval, casting_note=casting_note, personality=personalities.get("author", ""),
        )
        self.world_architect = WorldArchitect(
            self._runner_for("world_architect", build_world_architect_runner), self.read, self.committer,
            interval=s.default_agent_interval, personality=personalities.get("world_architect", ""),
        )
        self.character_keeper = CharacterKeeper(
            self._runner_for("character_keeper", build_character_keeper_runner), self.read, self.committer,
            interval=s.default_agent_interval, personality=personalities.get("character_keeper", ""),
        )
        self.editor = Editor(
            self._runner_for("editor", build_editor_runner), self.read, self.committer,
            interval=s.default_agent_interval, casting_note=casting_note, personality=personalities.get("editor", ""),
        )
        self.continuity_checker = ContinuityChecker(
            self._runner_for("continuity_checker", build_continuity_checker_runner), self.read, self.committer,
            interval=s.continuity_interval, personality=personalities.get("continuity_checker", ""),
        )
        self.retconner = Retconner(
            self._runner_for("retconner", build_retconner_runner), self.read, self.committer,
            interval=s.default_agent_interval, personality=personalities.get("retconner", ""),
        )
```
(This replaces the six construction lines shown in the "Context" section above — everything before them in `start()`, and everything after, i.e. `self.agents = [...]` and `self.scheduler = Scheduler(...)`, is unchanged.)

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_runtime.py -v`
Expected: PASS (all prior + 2 new). Then `uv run pytest -q` for the full suite green.

- [ ] **Step 5: Commit**

```bash
git add novelizer/runtime.py tests/test_runtime.py
git commit -m "feat: Runtime wires each agent's personality from the active voice pack"
```

---

### Task 8: Feed rendering — `agent.remarked` as a personality-voiced line

**Files:**
- Modify: `novelizer/tui/app.py`
- Test: `tests/tui/test_app.py` (extend)

**Interfaces:**
- Produces: `_LABELS` gains no new entry for `AGENT_REMARKED` (its label is resolved from the payload's `agent_name`, not from `_LABELS`, since one event type now maps to any of six possible speakers). `format_event(ev)` gains a branch: when `ev.event_type == EventType.AGENT_REMARKED`, render `f'💬 {_agent_label(p.get("agent_name", "?"))}: "{p.get("note", "")}"'` instead of the generic `f"◆ {who} — {detail}"` shape. A small module-level `_AGENT_LABELS = {"author": "Author", "editor": "Editor", "world_architect": "Architect", "character_keeper": "Keeper", "continuity_checker": "Continuity", "retconner": "Retconner"}` plus `_agent_label(name) -> str` (falls back to `name.replace("_", " ").title()` for any name not in the map) backs the lookup. The Room view requires no code change — it already renders the same `RichLog#feed` content via `format_event` (per `action_toggle_room`'s pure CSS-class toggle on `#body`), so the new branch reaches it automatically.

- [ ] **Step 1: Write the failing tests**

Append to `tests/tui/test_app.py`:
```python
def test_format_agent_remarked_renders_personality_voiced_line():
    from novelizer.tui.app import format_event
    from novelizer.canon.events import StoredEvent, EventType
    ev = StoredEvent(sequence=1, id="e1", event_type=EventType.AGENT_REMARKED,
                     aggregate_id="author", payload={"agent_name": "author", "note": "Another storm, another chapter."},
                     created_at="t")
    line = format_event(ev)
    assert "Another storm, another chapter." in line
    assert "Author" in line
    assert "💬" in line


def test_format_agent_remarked_labels_each_agent_distinctly():
    from novelizer.tui.app import format_event
    from novelizer.canon.events import StoredEvent, EventType
    for agent_name, expected_label in [
        ("author", "Author"), ("editor", "Editor"), ("world_architect", "Architect"),
        ("character_keeper", "Keeper"), ("continuity_checker", "Continuity"), ("retconner", "Retconner"),
    ]:
        ev = StoredEvent(sequence=1, id="e1", event_type=EventType.AGENT_REMARKED,
                         aggregate_id=agent_name, payload={"agent_name": agent_name, "note": "hm."},
                         created_at="t")
        assert expected_label in format_event(ev)


def test_format_agent_remarked_falls_back_for_unknown_agent_name():
    from novelizer.tui.app import format_event
    from novelizer.canon.events import StoredEvent, EventType
    ev = StoredEvent(sequence=1, id="e1", event_type=EventType.AGENT_REMARKED,
                     aggregate_id="mystery_agent", payload={"agent_name": "mystery_agent", "note": "?"},
                     created_at="t")
    assert "Mystery Agent" in format_event(ev)


def test_room_toggle_still_works_after_agent_remarked_rendering_change():
    # action_toggle_room is a pure CSS-class toggle on #body; regression guard
    # that adding the agent.remarked branch to format_event didn't touch it.
    import inspect
    from novelizer.tui.app import NovelizerApp
    src = inspect.getsource(NovelizerApp.action_toggle_room)
    assert 'toggle_class("room")' in src
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/tui/test_app.py -v`
Expected: FAIL (`AssertionError` — `format_event` currently falls through to the generic `◆ System — agent.remarked` shape for an unrecognized event type, since `_LABELS.get(ev.event_type, "System")` misses and the `if/elif` chain's final `else: detail = ev.event_type` branch fires).

- [ ] **Step 3: Implement**

In `novelizer/tui/app.py`, add the agent-label map and rework `format_event`:
```python
from __future__ import annotations
import asyncio
from textual.app import App, ComposeResult
from textual.containers import Horizontal, Vertical
from textual.widgets import Header, Footer, RichLog, Static, Tree, Input
from novelizer.canon.events import StoredEvent, EventType
from novelizer.canon.autonomy import AutonomyState
from novelizer.director import commands
from novelizer.tui.widgets.roster import AgentRoster
from novelizer.tui.widgets.browser import StoryBrowser
from novelizer.tui.widgets.browser_model import detail_text
from novelizer.tui.widgets.proposals_model import pending_lines

_LABELS = {
    EventType.CHAPTER_CREATED: "Author",
    EventType.WORLD_ENTRY_CREATED: "Architect",
    EventType.CHARACTER_CREATED: "Keeper",
    EventType.DIRECTOR_SIGNAL_CREATED: "Director",
    EventType.RETCON_REQUEST_CREATED: "Retcon",
    EventType.CHAPTER_STATUS_CHANGED: "Editor",
}

_AGENT_LABELS = {
    "author": "Author",
    "editor": "Editor",
    "world_architect": "Architect",
    "character_keeper": "Keeper",
    "continuity_checker": "Continuity",
    "retconner": "Retconner",
}


def _agent_label(agent_name: str) -> str:
    return _AGENT_LABELS.get(agent_name, agent_name.replace("_", " ").title())


def format_event(ev: StoredEvent) -> str:
    p = ev.payload
    if ev.event_type == EventType.AGENT_REMARKED:
        label = _agent_label(p.get("agent_name", "?"))
        note = p.get("note", "")
        return f'💬 {label}: "{note}"'
    who = _LABELS.get(ev.event_type, "System")
    if ev.event_type == EventType.CHAPTER_CREATED:
        detail = f"new chapter: {p.get('title', '')}"
    elif ev.event_type == EventType.WORLD_ENTRY_CREATED:
        detail = f"lore: {p.get('title', '')}"
    elif ev.event_type == EventType.DIRECTOR_SIGNAL_CREATED:
        detail = f"signal: {p.get('body', '')}"
    elif ev.event_type == EventType.RETCON_REQUEST_CREATED:
        detail = f"retcon: {p.get('description', '')}"
    elif ev.event_type == EventType.CHAPTER_STATUS_CHANGED:
        detail = f"chapter reviewed: {p.get('title', '')}"
    else:
        detail = ev.event_type
    return f"◆ {who} — {detail}"
```
(Only the new `_AGENT_LABELS`/`_agent_label` and the new `if ev.event_type == EventType.AGENT_REMARKED: ...` early-return branch at the top of `format_event` are additions — every existing `_LABELS`/`if/elif` branch and `_status_line`/`NovelizerApp` are unchanged; `action_toggle_room` is untouched, confirmed by Task 8's regression test.)

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/tui/test_app.py -v`
Expected: PASS (all prior + 4 new). Then `uv run pytest tests/tui/ -v` for the full TUI suite green.

- [ ] **Step 5: Commit**

```bash
git add novelizer/tui/app.py tests/tui/test_app.py
git commit -m "feat: render agent.remarked as a personality-voiced feed line in the Room"
```

---

### Task 9: Docs — mark M2.2 complete, document personalities and the living feed

**Files:**
- Modify: `docs/submilestones/M2-voices.md`
- Modify: `README.md`

**Interfaces:** none (documentation only).

- [ ] **Step 1: Update the sub-milestone table**

In `docs/submilestones/M2-voices.md`, change the M2.2 row's `Status` cell from `⬜ not started` to `✅ complete`.

- [ ] **Step 2: Extend the README's "Voices" section**

In `README.md`, extend the `## Voices` section added in M2.1 with a new subsection (placement: immediately after the existing `novelizer voices` code block, before any following top-level heading):

```markdown
### Personalities & the living feed

Each roster member also has a **personality** — a short casting note from the
active pack's `[agent_personalities]` table (e.g. the Editor's "precise,
unsentimental line editor" vs. the Author's "restless, romantic chronicler").
The personality is injected into that agent's work-time prompt the same way
the prose profile is, and agents may emit a short in-personality remark as
part of their structured output. Remarks are appended to canon as
`agent.remarked` events — feed flavor only, never gated, never projected —
and rendered in the activity feed (and the full-screen Room view, toggled
with `r`) as personality-voiced lines:

```
💬 Editor: "Finally, a clean draft."
💬 Author: "Another storm, another chapter."
```

Recasting an agent (editing its entry in `[agent_personalities]` in the
active voice pack) changes both what it says in the feed and how it
approaches its next turn of work — on the next process start, per M2.2's
scope; live in-TUI recasting lands with the voice browser in M2.3.
```

- [ ] **Step 3: Commit**

```bash
git add docs/submilestones/M2-voices.md README.md
git commit -m "docs: mark M2.2 complete; document personalities and the living feed"
```

---

## Self-Review

**Spec coverage against the M2.2 row and load-bearing design decisions in `docs/submilestones/M2-voices.md`:**
- "Per-agent personality casting notes (from the pack) injected into each agent's work prompt" — Tasks 4–6 (all six agents), wired end-to-end by Task 7 (`Runtime` reads `voice_pack.agent_personalities`).
- "agents emit a short in-personality `feed_note` in their structured output" — Task 3 (`feed_note: str = ""` on all six schemas).
- "→ an `agent.remarked` event (never gated)" — Task 1 (`EventType.AGENT_REMARKED`, `_NEVER_GATED` membership), Task 2 (`BaseAgent._remark` centralizes the commit), Tasks 4–6 (each agent's `commit()` calls it).
- "→ feed + The Room view render personality-voiced lines" — Task 8 (`format_event` branch); The Room requires no separate code path since it's a CSS-class toggle over the same `RichLog#feed`, confirmed by `action_toggle_room`'s source and Task 8's regression test.
- Done-criterion — "Recasting an agent's personality visibly changes what it says in the feed" — directly demonstrated: Task 4–6's `test_work_prompt_includes_personality_when_set` prove the prompt changes per-agent, Task 7's `test_runtime_wires_each_agents_personality_from_the_pack` proves two different agents in the same pack get two different personalities wired, and Task 8's rendering tests prove the resulting `agent.remarked` event surfaces as a personality-labeled feed line.
- "Voice lives in files, injected at work-time... NOT baked into the deepagents `system_prompt` at construction" — every agent's personality line is built inside `work()`'s per-call message, never inside any `SYSTEM_PROMPT`/`AUTHOR_SYSTEM_PROMPT` constant (all of which are unchanged, used only by the `build_*_runner` deepagents-construction functions).
- "The Runtime is the voice source... Agents gain a small additive voice parameter" — Task 7; `personality: str = ""` is additive on every agent, `Committer`/`ReadStore`/scheduler seams untouched.
- "Personality reaches the feed via canon... never gated... auditable" — Task 1's `_NEVER_GATED` addition + Task 1's confirmation (documented in the plan header's Context section) that the Projector's `_apply` if/elif chain has no branch for `agent.remarked` and therefore performs zero projection writes for it, by design.
- Character voice cards and the in-TUI voice browser are explicitly deferred to M2.3 in the plan header, matching `docs/submilestones/M2-voices.md`'s M2.3 row.

**Placeholder scan:** every task's Step 3 contains complete, runnable file contents (full replacements for every touched agent/schema/base/policy/events/runtime/app file) verified against the actual current source read during planning — no "similar to Task N", no `...` elisions in code, no TODOs left for the implementer to fill in. Tasks 4–6 each write out both agents' complete files in full rather than deferring to a shared description, per the "no placeholders" rule.

**Type consistency:** `AgentRemark(BaseModel)` matches `StoredEvent`'s plain-`BaseModel`, no-factory convention (it's an event payload, not canon with identity/timestamps — consistent with how `DirectorSignal`/`Proposal` *do* have factories because they're canon aggregates, while a payload like this one doesn't need one). `BaseAgent.__init__`'s new `personality: str = ""` trailing kwarg matches the existing `name: str | None = None` trailing-kwarg style; every one of the six agents' `__init__` signatures adds `personality: str = ""` as its own new trailing kwarg, consistent with how `casting_note: str = ""` was added to `Author`/`Editor` in M2.1. `_remark(self, note: str) -> None` matches `_consume_signals`'s existing `async def _x(self, ...) -> None` shape. `feed_note: str = ""` is spelled identically across all six schemas and `ChapterDraft`.

**DDD/SOLID:**
- Single Responsibility: `BaseAgent._remark` is the one place that knows how to turn a note into an `agent.remarked` event; each agent's `commit()` calls it but does not duplicate its logic. `format_event`'s new branch is the one place that knows how to render a remark; `_agent_label` is the one place that knows agent-name → display-label.
- Open/Closed: all six agents are extended (new optional constructor parameter, one new prompt line, one new `_remark` call) without modifying `BaseAgent`'s existing public contract, `Committer`/`GatingCommitter`'s `commit()` signature, `ReadStore`, or the scheduler.
- Dependency Inversion preserved: `Runtime` depends on `VoicePack.agent_personalities` (a plain `dict[str, str]`) and hands each agent a plain string — agents remain ignorant of the voice-pack/TOML layer entirely, exactly as they're ignorant of `EventStore`/SQL via the `Committer`/`ReadStore` seams.
- Bounded context: only `agents/*`, `runtime.py`, `canon/events.py`, `canon/policy.py`, and `tui/app.py` are touched; `canon/committer.py`, `canon/projector.py`'s dispatch shape, `canon/read_store.py`, `store/models.py`, and `voices/*` are untouched (the pack format already carried `agent_personalities` since M2.1 — no format migration needed).
- Event sourcing: `agent.remarked` is append-only like every other event; confirmed zero new projection tables, zero new `ReadStore` methods, and the Projector's existing if/elif-then-commit structure guarantees an unrecognized event type is a true no-op (no exception, no partial write) rather than a silent bug — this was explicitly verified against the actual `_apply` method's control flow before committing to the "no projection" design.

**Backward-compatibility check (explicit, since this is the plan's hardest constraint):** every new parameter (`personality` on `BaseAgent`/all six agents, `feed_note` on all six schemas) defaults to `""`, so: (a) every existing call site in `tests/agents/*`, `tests/test_runtime.py`'s existing tests, and `Runtime`'s own five non-Author/Editor construction lines from before Task 7's edit are all still valid Python with unchanged behavior; (b) `_summarize`/each `work()`'s new "In character:" line is guarded by `if personality`/`if self.personality`, so an unset personality produces a byte-identical prompt to pre-M2.2; (c) `_remark` is a no-op on an empty/absent `feed_note`, so no agent's existing tests (which never set `feed_note`) emit any new events; (d) `format_event`'s new branch only fires for `EventType.AGENT_REMARKED`, so every pre-existing `format_event` test and every pre-existing event type's rendering is untouched.
