# M2.3 · Character Voices & Voice Browser Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Characters accrue a voice card (dialogue patterns, vocabulary, verbal tics) built by the Character Keeper from recent chapters, cited by the Editor when reviewing chapters those characters appear in, and browsable in the TUI story browser's character detail pane. A director can also scaffold a brand-new prose profile into a voice pack from a one-line prompt, via CLI (and, per the vision doc's in-TUI browser/scaffolding intent, the CLI is the scaffolding surface for M2.3 with the TUI providing read-only browsing — see Architecture below for the exact split).

**Architecture:** Character voice is additive canon, exactly like M2.1's casting notes and M2.2's personalities: it flows through existing seams rather than new ones. (1) **Voice cards as canon** — `Character` gains a `voice: str = ""` field; `CharacterUpdate` gains a matching `voice: Optional[str] = None` field; `CharacterKeeper.commit()`'s existing per-field merge loop (`model_copy(update=fields)`) grows one more field name, so a `character.updated` event carries the revised voice through the same event type and the same projection code path — no new event type, no Projector change. The Keeper's `SYSTEM_PROMPT` is extended to ask it to note voice (dialogue patterns, vocabulary, tics) alongside arc updates. (2) **The Editor cites voice** — `Editor.work()` gains a guarded, additive prompt section: for the chapter under review, it looks up `ch.character_ids` via `ReadStore.get_character`, and if any of those characters have a non-empty `voice`, appends a "Character voices" block to the work-time message; when no character in the chapter has a voice, the prompt is byte-identical to pre-M2.3, so every existing `Editor` test keeps passing untouched. (3) **The browser surface** — `browser_model.detail_text`'s existing `characters` branch grows one more conditional line ("Voice: ..."), reusing the TUI's existing Tree-based Story Browser with zero new widgets; this is where character voice cards live per the M2-voices.md done-criterion ("characters accrue voice cards... browsable in the TUI"). (4) **Voice-pack/profile/personality browsing and scaffolding are CLI-only for M2.3** — the existing `novelizer voices` CLI command (M2.1) is extended to also list `agent_personalities` and any characters with a non-empty voice (read via `ReadStore.list_characters()`), and a new `novelizer voice-scaffold <profile-name> "<description>"` command hand-writes a new `[[prose_profiles]]`-shaped TOML entry into a separate **user pack file** (never the shipped `default.toml`), loadable back via the existing `load_voice_pack`. The vision doc's "TUI provides a voice-pack browser/picker and can scaffold" is *end-state*; M2.3 only claims character voice cards for the TUI (Part A, task 4) and defers the dedicated voice-editing/scaffolding pane to a later milestone (stated explicitly in Deferrals below) — the CLI is the fully-featured surface for this sub-milestone, consistent with M2.1/M2.2 keeping CLI (`novelizer voices`, `novelizer autonomy`) as the read/administrative surface for voice-pack-level concerns while the TUI surfaces canon (chapters/characters/world/retcons).

**Tech Stack:** Python 3.13 (stdlib `tomllib` for reading, already in use; **no new TOML-writing dependency** — scaffolding hand-writes a minimal, known-shape TOML block directly, see Task 6's rationale), `pydantic` v2, `click`+`rich`, `pytest`+`pytest-asyncio` (`asyncio_mode=auto`).

## Global Constraints

- Python >=3.13; stdlib `tomllib` for reading TOML. No `tomli-w` or other new TOML-writing dependency is added — scaffolding hand-writes the minimal known TOML shape (name/casting_note strings only, no nested structures, no user-supplied special characters beyond what a single quoted string requires) rather than pulling in a library for one write path. This choice is stated explicitly, not implied.
- Event sourcing: `Character.voice` flows through `character.updated` events via the Keeper's existing `Committer` seam; **no new canon event types** are introduced.
- DDD/SOLID: `Character.voice` is purely additive; only `CharacterKeeper`, `Editor`, `browser_model`, and the `voices` CLI module are touched. `canon/`'s `EventStore`/`Projector`/`ReadStore`/`Committer` core is untouched except the additive model field flowing through existing code paths.
- TDD, black-box-first: every task starts with a failing test asserting on observable events/projections/output, not internals.
- Backward compatibility: `Character.voice` defaults to `""`; `CharacterUpdate.voice` defaults to `None` (merge semantics identical to the four existing optional `CharacterUpdate` fields) — the existing 178-test suite stays green throughout.
- DRY: the Keeper's field-merge loop is extended by adding one string to its existing tuple, not duplicated; the Editor's voice-citation logic follows the exact `if/else` guarded-prompt-section pattern already used for `casting_note` and `personality`.

---

### Task 1: `Character.voice` field

**Files:**
- Modify: `novelizer/store/models.py:66-77` (the `Character` model)
- Test: `tests/store/test_models.py` (new file if none exists for `Character`)

**Interfaces:**
- Produces: `Character.voice: str = ""` — a new field on the existing `Character` pydantic model, positioned after `arc_status` for readability; no change to any existing field name or type.

- [ ] **Step 1: Write the failing test**

```python
# tests/store/test_models.py
from novelizer.store.models import Character


def test_character_voice_defaults_to_empty_string():
    c = Character(name="Mira")
    assert c.voice == ""


def test_character_voice_roundtrips_through_json():
    c = Character(name="Mira", voice="Clipped sentences, never says 'I love you' outright.")
    dumped = c.model_dump_json()
    restored = Character.model_validate_json(dumped)
    assert restored.voice == "Clipped sentences, never says 'I love you' outright."
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/store/test_models.py -v`
Expected: FAIL — `AttributeError: 'Character' object has no attribute 'voice'` (or a pydantic `ValidationError`/missing-field failure, since `voice` doesn't exist yet).

- [ ] **Step 3: Implement**

In `novelizer/store/models.py`, change the `Character` model:

```python
class Character(BaseModel):
    id: str = Field(default_factory=_uuid)
    created_at: datetime = Field(default_factory=_now)
    supersedes_id: Optional[str] = None
    name: str
    aliases: list[str] = Field(default_factory=list)
    traits: str = ""
    motivations: str = ""
    backstory: str = ""
    arc_status: str = ""
    voice: str = ""
    relationships: list[CharacterRelationship] = Field(default_factory=list)
    canon_status: CanonStatus = CanonStatus.active
```

(Only the `voice: str = ""` line is new; every other field, in the same order, is unchanged.)

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/store/test_models.py -v`
Expected: PASS (2 passed). Then `uv run pytest tests/ -v` to confirm the full existing suite (178 tests) is still green — `Character` is constructed by keyword everywhere in the codebase, so an additive defaulted field cannot break any existing call site.

- [ ] **Step 5: Commit**

```bash
git add novelizer/store/models.py tests/store/test_models.py
git commit -m "feat: add Character.voice field for per-character voice cards"
```

---

### Task 2: `CharacterUpdate.voice` + Keeper merge + Keeper prompt

**Files:**
- Modify: `novelizer/agents/schemas.py:19-24` (`CharacterUpdate`)
- Modify: `novelizer/agents/character_keeper.py` (`SYSTEM_PROMPT`, `CharacterKeeper.commit`)
- Test: `tests/agents/test_character_keeper.py`

**Interfaces:**
- Consumes: `Character.voice: str = ""` (Task 1).
- Produces: `CharacterUpdate.voice: Optional[str] = None`; `CharacterKeeper.commit()` merges `voice` into the `character.updated` event payload identically to `arc_status`/`traits`/`motivations`/`backstory`.

- [ ] **Step 1: Write the failing test**

```python
# appended to tests/agents/test_character_keeper.py
async def test_updates_character_voice_and_leaves_unset_voice_unchanged(stack):
    events, proj, read, committer = stack
    await events.append(
        EventType.CHARACTER_CREATED, "c1",
        Character(id="c1", name="Mira", traits="stoic", arc_status="wary", voice="Speaks in short, clipped sentences."),
    )
    await proj.catch_up()

    # First update: voice is set explicitly and should change.
    out = KeeperOutput(updated_characters=[
        CharacterUpdate(id="c1", voice="Now trails off mid-sentence when scared."),
    ])
    agent = CharacterKeeper(FakeRunner(out), read, committer)
    await agent.run_once()
    await proj.catch_up()
    mira = await read.get_character("c1")
    assert mira.voice == "Now trails off mid-sentence when scared."
    assert mira.traits == "stoic"  # untouched field unaffected

    # Second update: voice left None should not clobber the existing voice.
    out2 = KeeperOutput(updated_characters=[CharacterUpdate(id="c1", arc_status="cracking")])
    agent2 = CharacterKeeper(FakeRunner(out2), read, committer)
    await agent2.run_once()
    await proj.catch_up()
    mira2 = await read.get_character("c1")
    assert mira2.voice == "Now trails off mid-sentence when scared."
    assert mira2.arc_status == "cracking"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/agents/test_character_keeper.py::test_updates_character_voice_and_leaves_unset_voice_unchanged -v`
Expected: FAIL — `CharacterUpdate` has no field `voice` (pydantic raises `ValidationError: Extra inputs are not permitted` or a `TypeError` on construction, depending on model config; either way the test cannot get past constructing `CharacterUpdate(id="c1", voice=...)`).

- [ ] **Step 3: Implement**

In `novelizer/agents/schemas.py`, extend `CharacterUpdate`:

```python
class CharacterUpdate(BaseModel):
    id: str
    arc_status: Optional[str] = None
    traits: Optional[str] = None
    motivations: Optional[str] = None
    backstory: Optional[str] = None
    voice: Optional[str] = None
```

In `novelizer/agents/character_keeper.py`, extend the system prompt and the merge loop:

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
3. Note each character's voice: dialogue patterns, vocabulary, and verbal tics you observe
   in their lines, and revise it as their voice evolves across chapters.
Return updated_characters (id + revised arc_status, and any corrected traits/motivations/backstory/voice)
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
            for f in ("arc_status", "traits", "motivations", "backstory", "voice"):
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

(Only `"voice"` added to the merge-loop tuple, the three new `SYSTEM_PROMPT` lines, and `CharacterUpdate.voice` are new; every other line is unchanged from the current file.)

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/agents/test_character_keeper.py -v`
Expected: PASS (all prior tests + the new one). Then `uv run pytest tests/ -v` for the full suite green.

- [ ] **Step 5: Commit**

```bash
git add novelizer/agents/schemas.py novelizer/agents/character_keeper.py tests/agents/test_character_keeper.py
git commit -m "feat: Character Keeper builds and revises per-character voice cards"
```

---

### Task 3: Editor cites character voices in its work prompt

**Files:**
- Modify: `novelizer/agents/editor.py`
- Test: `tests/agents/test_editor.py`

**Interfaces:**
- Consumes: `Character.voice` (Task 1); `ReadStore.get_character(character_id: str) -> Optional[Character]` (existing); `Chapter.character_ids: list[str]` (existing).
- Produces: no new public interface — `Editor.work()`'s prompt gains an additive, guarded "Character voices" section.

- [ ] **Step 1: Write the failing tests**

```python
# appended to tests/agents/test_editor.py
async def test_editor_prompt_includes_character_voices_when_present(stack):
    events, proj, read, committer = stack
    await events.append(
        EventType.CHARACTER_CREATED, "ch1",
        Character(id="ch1", name="Mira", voice="Speaks in short, clipped sentences; never says 'I love you' outright."),
    )
    await events.append(
        EventType.CHAPTER_CREATED, "c1",
        Chapter(id="c1", title="One", prose="p", character_ids=["ch1"]),
    )
    await proj.catch_up()
    runner = FakeRunner(EditorVerdict(verdict="approve", notes="clean"))
    agent = Editor(runner, read, committer)
    ctx = await agent.poll()
    await agent.work(ctx)
    sent = runner.calls[-1]["messages"][0]["content"]
    assert "Mira" in sent
    assert "Speaks in short, clipped sentences" in sent
    assert "Character voices:" in sent


async def test_editor_prompt_omits_voices_section_when_none_set(stack):
    events, proj, read, committer = stack
    await events.append(EventType.CHARACTER_CREATED, "ch1", Character(id="ch1", name="Mira"))
    await events.append(
        EventType.CHAPTER_CREATED, "c1",
        Chapter(id="c1", title="One", prose="p", character_ids=["ch1"]),
    )
    await proj.catch_up()
    runner = FakeRunner(EditorVerdict(verdict="approve", notes="clean"))
    agent = Editor(runner, read, committer)
    ctx = await agent.poll()
    await agent.work(ctx)
    sent = runner.calls[-1]["messages"][0]["content"]
    assert "Character voices:" not in sent
    assert sent == f"Chapter title: One\n\nProse:\np"
```

Add `from novelizer.store.models import Character` to the imports at the top of `tests/agents/test_editor.py` (alongside the existing `Chapter, EditorialStatus` import).

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/agents/test_editor.py::test_editor_prompt_includes_character_voices_when_present tests/agents/test_editor.py::test_editor_prompt_omits_voices_section_when_none_set -v`
Expected: FAIL — the first asserts `"Character voices:" in sent`, which is absent from the current prompt; the second's exact-match assertion will actually pass today (proving the guard is currently a no-op), but is included here as the pinned byte-identical baseline the implementation must preserve.

- [ ] **Step 3: Implement**

In `novelizer/agents/editor.py`:

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

    async def _character_voices_block(self, character_ids: list[str]) -> str:
        lines = []
        for cid in character_ids:
            c = await self._read.get_character(cid)
            if c is not None and c.voice:
                lines.append(f"- {c.name}: {c.voice}")
        if not lines:
            return ""
        return "\n\nCharacter voices:\n" + "\n".join(lines)

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
        msg = f"Chapter title: {ch.title}\n\nProse:\n{ch.prose}{voice}{cast}{voices}"
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

(Only the new `_character_voices_block` helper and the `voices = ...` / `{voices}` additions to `work()` are new; `SYSTEM_PROMPT`, `readiness`, `poll`, `commit`, `run_once`, `build_editor_runner` are unchanged. The order `{voice}{cast}{voices}` places the new section last, so the byte-identical-prompt guarantee for chapters with no character voices holds regardless of whether `casting_note`/`personality` are set — each section independently no-ops to `""`.)

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/agents/test_editor.py -v`
Expected: PASS (all prior tests, including `test_editor_prompt_includes_active_prose_profile` and `test_editor_prompt_includes_personality_when_set`, + the 2 new tests). Then `uv run pytest tests/ -v` for the full suite green.

- [ ] **Step 5: Commit**

```bash
git add novelizer/agents/editor.py tests/agents/test_editor.py
git commit -m "feat: Editor cites relevant character voice cards in its work prompt"
```

---

### Task 4: Browser detail pane shows the voice card

**Files:**
- Modify: `novelizer/tui/widgets/browser_model.py:30-34` (the `characters` branch of `detail_text`)
- Test: `tests/tui/test_browser_model.py`

**Interfaces:**
- Consumes: `Character.voice` (Task 1).
- Produces: no new public interface — `detail_text(read, "characters", item_id)`'s returned string gains an additional trailing line when `voice` is non-empty.

- [ ] **Step 1: Write the failing test**

```python
# appended to tests/tui/test_browser_model.py
async def test_detail_text_for_character_includes_voice_card_when_present(stack):
    events, proj, read = stack
    await events.append(
        EventType.CHARACTER_CREATED, "ch1",
        Character(id="ch1", name="Mira", traits="stoic", arc_status="wary",
                  voice="Speaks in short, clipped sentences."),
    )
    await proj.catch_up()
    d = await detail_text(read, "characters", "ch1")
    assert "Voice: Speaks in short, clipped sentences." in d


async def test_detail_text_for_character_omits_voice_line_when_absent(stack):
    events, proj, read = stack
    await events.append(EventType.CHARACTER_CREATED, "ch1", Character(id="ch1", name="Mira", traits="stoic"))
    await proj.catch_up()
    d = await detail_text(read, "characters", "ch1")
    assert "Voice:" not in d
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/tui/test_browser_model.py::test_detail_text_for_character_includes_voice_card_when_present -v`
Expected: FAIL — `assert "Voice: Speaks in short, clipped sentences." in d` fails because `detail_text`'s `characters` branch doesn't emit a voice line yet.

- [ ] **Step 3: Implement**

In `novelizer/tui/widgets/browser_model.py`, update the `characters` branch of `detail_text`:

```python
    if section_key == "characters":
        c = await read.get_character(item_id)
        if not c:
            return ""
        detail = f"{c.name}\nTraits: {c.traits}\nArc: {c.arc_status}\nMotivations: {c.motivations}"
        if c.voice:
            detail += f"\nVoice: {c.voice}"
        return f"{detail}\n\n{c.backstory}"
```

(This replaces only the `return f"{c.name}\n..."` line inside the `characters` branch; every other branch of `detail_text` and all of `browser_sections` are unchanged.)

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/tui/test_browser_model.py -v`
Expected: PASS (all prior tests + 2 new). Then `uv run pytest tests/ -v` for the full suite green.

- [ ] **Step 5: Commit**

```bash
git add novelizer/tui/widgets/browser_model.py tests/tui/test_browser_model.py
git commit -m "feat: story browser character detail shows the voice card"
```

---

### Task 5: `voices` CLI lists agent personalities and character voice cards

**Files:**
- Modify: `novelizer/director/cli.py` (`voices` command)
- Test: `tests/director/test_cli.py` (new file if none exists for CLI commands; otherwise extend the existing one — check `tests/director/` first)

**Interfaces:**
- Consumes: `VoicePack.agent_personalities: dict[str, str]` (existing, M2.1); `ReadStore.list_characters() -> list[Character]` (existing); `Character.voice` (Task 1).
- Produces: `format_voice_report(pack: VoicePack, characters: list[Character], active_profile: str | None) -> str` — a new pure formatter function in `novelizer/director/cli.py`, unit-testable without spinning up Click's runner, consumed by the `voices` command.

- [ ] **Step 1: Write the failing test**

First check for an existing CLI test module:

```bash
ls tests/director/
```

If `tests/director/test_cli.py` does not exist, create it. Add:

```python
# tests/director/test_cli.py
from novelizer.director.cli import format_voice_report
from novelizer.voices.models import ProseProfile, VoicePack
from novelizer.store.models import Character


def test_report_includes_prose_profiles_with_active_marker():
    pack = VoicePack(
        name="default",
        prose_profiles={
            "plain": ProseProfile(name="plain", casting_note="Clean and neutral."),
            "sparse": ProseProfile(name="sparse", casting_note="Spare, concrete, unadorned."),
        },
    )
    report = format_voice_report(pack, characters=[], active_profile="plain")
    assert "plain" in report and "sparse" in report
    assert "Clean and neutral." in report


def test_report_includes_agent_personalities():
    pack = VoicePack(name="default", agent_personalities={"editor": "A precise, unsentimental line editor."})
    report = format_voice_report(pack, characters=[], active_profile=None)
    assert "editor" in report
    assert "A precise, unsentimental line editor." in report


def test_report_includes_only_characters_with_nonempty_voice():
    pack = VoicePack(name="default")
    characters = [
        Character(id="c1", name="Mira", voice="Clipped sentences."),
        Character(id="c2", name="Jonas", voice=""),
    ]
    report = format_voice_report(pack, characters=characters, active_profile=None)
    assert "Mira" in report and "Clipped sentences." in report
    assert "Jonas" not in report
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/director/test_cli.py -v`
Expected: FAIL — `ImportError: cannot import name 'format_voice_report' from 'novelizer.director.cli'`.

- [ ] **Step 3: Implement**

In `novelizer/director/cli.py`, add the `format_voice_report` helper and rewrite the `voices` command to use it:

```python
from __future__ import annotations
import asyncio
import click
from rich.console import Console
from rich.table import Table
from novelizer.config import Settings
from novelizer.runtime import Runtime
from novelizer.director import commands
from novelizer.voices.loader import load_voice_pack
from novelizer.voices.models import VoicePack
from novelizer.store.models import Character

console = Console()


async def _with_runtime(settings, fn):
    rt = Runtime(settings)
    # CLI commands that only touch the store don't need the LLM runner.
    await rt.events.init()
    await rt.projector.init()
    await rt.read.init()
    await rt.projector.catch_up()
    try:
        return await fn(rt)
    finally:
        await rt.close()


@click.group(invoke_without_command=True)
@click.pass_context
def cli(ctx):
    ctx.ensure_object(dict)
    ctx.obj["settings"] = Settings()
    if ctx.invoked_subcommand is None:
        _launch_tui(ctx.obj["settings"])


def _launch_tui(settings: Settings) -> None:
    from novelizer.tui.app import NovelizerApp

    async def _boot():
        rt = Runtime(settings)
        await rt.start()
        app = NovelizerApp(rt)
        try:
            await app.run_async()
        finally:
            await rt.close()

    asyncio.run(_boot())


@cli.command()
@click.argument("text")
@click.pass_context
def seed(ctx, text: str):
    """Inject a narrative seed as a director_signal.created event."""
    async def _run(rt: Runtime):
        await commands.seed(rt.events, text)
        console.print(f"[green]Seed injected:[/green] {text}")
    asyncio.run(_with_runtime(ctx.obj["settings"], _run))


@cli.command()
@click.pass_context
def chapters(ctx):
    """List chapters by editorial status."""
    async def _run(rt: Runtime):
        chs = await rt.read.list_chapters()
        if not chs:
            console.print("No chapters yet.")
            return
        table = Table(title="Chapters")
        table.add_column("ID", style="dim", no_wrap=True)
        table.add_column("Title")
        table.add_column("Status")
        for c in chs:
            table.add_row(c.id[:8], c.title, c.editorial_status.value)
        console.print(table)
    asyncio.run(_with_runtime(ctx.obj["settings"], _run))


@cli.command()
@click.argument("chapter_id")
@click.pass_context
def read(ctx, chapter_id: str):
    """Print a chapter's prose."""
    async def _run(rt: Runtime):
        ch = await rt.read.get_chapter(chapter_id)
        if not ch:
            console.print(f"[red]Chapter {chapter_id} not found.[/red]")
            return
        console.rule(ch.title)
        console.print(ch.prose)
    asyncio.run(_with_runtime(ctx.obj["settings"], _run))


@cli.command()
@click.pass_context
def retcons(ctx):
    """List open retcon requests."""
    async def _run(rt: Runtime):
        reqs = await rt.read.list_retcon_requests(status="open")
        if not reqs:
            console.print("No open retcon requests.")
            return
        table = Table(title="Open Retcon Requests")
        table.add_column("ID", style="dim", no_wrap=True)
        table.add_column("Description")
        table.add_column("Proposed Resolution")
        for r in reqs:
            table.add_row(r.id[:8], r.description, r.proposed_resolution)
        console.print(table)
    asyncio.run(_with_runtime(ctx.obj["settings"], _run))


def format_voice_report(pack: VoicePack, characters: list[Character], active_profile: str | None) -> str:
    """Pure formatter: voice pack + character voice cards -> a plain-text report.

    Kept dependency-free of Click/Rich/ReadStore so it is unit-testable without
    a runner or a database — the `voices` command below is a thin wrapper that
    fetches its inputs and prints this string via Rich.
    """
    lines = [f"Voice pack: {pack.name}", ""]
    lines.append("Prose profiles:")
    for name, profile in pack.prose_profiles.items():
        marker = "* " if name == active_profile else "  "
        snippet = profile.casting_note.strip().replace("\n", " ")[:80]
        lines.append(f"{marker}{name}: {snippet}")
    lines.append("")
    lines.append("Agent personalities:")
    for agent, note in pack.agent_personalities.items():
        lines.append(f"  {agent}: {note.strip().replace(chr(10), ' ')[:80]}")
    voiced = [c for c in characters if c.voice]
    if voiced:
        lines.append("")
        lines.append("Character voices:")
        for c in voiced:
            lines.append(f"  {c.name}: {c.voice.strip().replace(chr(10), ' ')[:80]}")
    return "\n".join(lines)


@cli.command()
@click.option("--pack", "pack_path", default=None, help="Inspect a voice pack other than the active one.")
@click.pass_context
def voices(ctx, pack_path: str | None):
    """Show the active (or given) voice pack's profiles, agent personalities, and character voice cards."""
    settings = ctx.obj["settings"]
    path = pack_path or settings.voice_pack
    pack = load_voice_pack(path)
    active_name = settings.prose_profile if pack_path is None else None

    async def _run(rt: Runtime):
        characters = await rt.read.list_characters()
        console.print(format_voice_report(pack, characters, active_name))
    asyncio.run(_with_runtime(ctx.obj["settings"], _run))


@cli.command()
@click.argument("level")
@click.argument("agent", required=False)
@click.pass_context
def autonomy(ctx, level: str, agent: str | None):
    """Set the global autonomy level, or a per-agent override."""
    from novelizer.canon.autonomy import AutonomyLevel, AutonomyState

    async def _run(rt: Runtime):
        try:
            lvl = AutonomyLevel(level)
        except ValueError:
            console.print(f"[red]Unknown autonomy level:[/red] {level}")
            return
        current = await rt.read.get_autonomy_state()
        if agent:
            overrides = dict(current.overrides)
            overrides[agent] = lvl
            next_state = AutonomyState(global_level=current.global_level, overrides=overrides)
            await commands.autonomy(rt.events, next_state)
            console.print(f"[green]Autonomy for {agent} set to {lvl.value}[/green]")
        else:
            next_state = AutonomyState(global_level=lvl, overrides=current.overrides)
            await commands.autonomy(rt.events, next_state)
            console.print(f"[green]Global autonomy set to {lvl.value}[/green]")
    asyncio.run(_with_runtime(ctx.obj["settings"], _run))


@cli.command()
@click.pass_context
def proposals(ctx):
    """List pending (open) proposals."""
    async def _run(rt: Runtime):
        props = await rt.read.list_proposals(status="open")
        if not props:
            console.print("No pending proposals.")
            return
        table = Table(title="Pending Proposals")
        table.add_column("ID", style="dim", no_wrap=True)
        table.add_column("Agent")
        table.add_column("Target Event")
        for p in props:
            table.add_row(p.id[:8], p.proposing_agent, p.target_event_type)
        console.print(table)
    asyncio.run(_with_runtime(ctx.obj["settings"], _run))


@cli.command()
@click.argument("proposal_id")
@click.pass_context
def approve(ctx, proposal_id: str):
    """Approve a pending proposal — appends its target event + proposal.approved."""
    async def _run(rt: Runtime):
        result = await commands.approve(rt.events, rt.read, proposal_id)
        console.print(f"[green]{result}[/green]")
    asyncio.run(_with_runtime(ctx.obj["settings"], _run))


@cli.command()
@click.argument("proposal_id")
@click.pass_context
def reject(ctx, proposal_id: str):
    """Reject a pending proposal — appends proposal.rejected."""
    async def _run(rt: Runtime):
        result = await commands.reject(rt.events, rt.read, proposal_id)
        console.print(f"[yellow]{result}[/yellow]")
    asyncio.run(_with_runtime(ctx.obj["settings"], _run))


def main():
    cli()
```

(The Rich `Table`-based rendering the `voices` command previously did inline is replaced by a call to the new pure `format_voice_report`; every other command — `seed`, `chapters`, `read`, `retcons`, `autonomy`, `proposals`, `approve`, `reject` — and `_with_runtime`/`_launch_tui`/`cli`/`main` are unchanged. Task 6 appends one more command, `voice-scaffold`, below `voices` in this same file.)

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/director/test_cli.py -v`
Expected: PASS (3 passed). Then `uv run pytest tests/ -v` for the full suite green (the CLI's own manual smoke test — `uv run novelizer voices` against a fresh `stories/world.db` — still prints a table-free but equivalent report; this is documented in Task 7's README update rather than re-tested here since `_with_runtime`/Click wiring is exercised by the existing CLI, and `format_voice_report` is the newly-tested unit).

- [ ] **Step 5: Commit**

```bash
git add novelizer/director/cli.py tests/director/test_cli.py
git commit -m "feat: voices CLI reports agent personalities and character voice cards"
```

---

### Task 6: `voice-scaffold` command writes a new prose profile without an LLM or a new dependency

**Files:**
- Create: `novelizer/voices/scaffold.py`
- Modify: `novelizer/director/cli.py` (new `voice-scaffold` command)
- Test: `tests/voices/test_scaffold.py`

**Interfaces:**
- Consumes: `load_voice_pack(path: str) -> VoicePack` (existing, M2.1).
- Produces: `scaffold_prose_profile(pack_path: str, profile_name: str, description: str) -> str` in `novelizer/voices/scaffold.py` — writes (creating the file if absent, appending/merging if present) a `[prose_profiles.<profile_name>]` TOML block with `name = "<profile_name>"` and `casting_note = "<description>"` into `pack_path`, and returns the path written. Idempotent: re-running with the same `profile_name` replaces that profile's block rather than duplicating it. Never writes to the path returned by `Settings().voice_pack` when it resolves to the shipped `novelizer/voices/default.toml` — the CLI command defaults its target to a separate user pack path instead (see Step 3).

- [ ] **Step 1: Write the failing tests**

```python
# tests/voices/test_scaffold.py
import os
import tempfile
import pytest
from novelizer.voices.scaffold import scaffold_prose_profile, DEFAULT_PACK_GUARD_MESSAGE
from novelizer.voices.loader import load_voice_pack

DEFAULT_PACK_PATH = os.path.join(
    os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
    "novelizer", "voices", "default.toml",
)


def test_scaffold_writes_a_new_pack_when_none_exists():
    with tempfile.TemporaryDirectory() as d:
        pack_path = os.path.join(d, "user_pack.toml")
        written = scaffold_prose_profile(pack_path, "brisk", "Fast, punchy, present-tense action prose.")
        assert written == pack_path
        pack = load_voice_pack(pack_path)
        assert pack.profile("brisk") is not None
        assert pack.profile("brisk").casting_note == "Fast, punchy, present-tense action prose."


def test_scaffold_appends_to_an_existing_user_pack_without_clobbering_other_profiles():
    with tempfile.TemporaryDirectory() as d:
        pack_path = os.path.join(d, "user_pack.toml")
        scaffold_prose_profile(pack_path, "brisk", "Fast, punchy, present-tense action prose.")
        scaffold_prose_profile(pack_path, "wistful", "Slow, nostalgic, past-tense reminiscence.")
        pack = load_voice_pack(pack_path)
        assert pack.profile("brisk").casting_note == "Fast, punchy, present-tense action prose."
        assert pack.profile("wistful").casting_note == "Slow, nostalgic, past-tense reminiscence."


def test_scaffold_is_idempotent_replacing_same_named_profile():
    with tempfile.TemporaryDirectory() as d:
        pack_path = os.path.join(d, "user_pack.toml")
        scaffold_prose_profile(pack_path, "brisk", "First description.")
        scaffold_prose_profile(pack_path, "brisk", "Revised description.")
        pack = load_voice_pack(pack_path)
        assert len(pack.prose_profiles) == 1
        assert pack.profile("brisk").casting_note == "Revised description."


def test_scaffold_refuses_to_write_the_shipped_default_pack():
    with pytest.raises(ValueError, match=DEFAULT_PACK_GUARD_MESSAGE):
        scaffold_prose_profile(DEFAULT_PACK_PATH, "brisk", "Should not land here.")
    # Confirm the shipped pack is untouched.
    pack = load_voice_pack(DEFAULT_PACK_PATH)
    assert pack.profile("brisk") is None


def test_scaffold_escapes_quotes_and_backslashes_in_description():
    with tempfile.TemporaryDirectory() as d:
        pack_path = os.path.join(d, "user_pack.toml")
        scaffold_prose_profile(pack_path, "quirky", 'She said "hello" and meant it \\ truly.')
        pack = load_voice_pack(pack_path)
        assert pack.profile("quirky").casting_note == 'She said "hello" and meant it \\ truly.'
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/voices/test_scaffold.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'novelizer.voices.scaffold'`.

- [ ] **Step 3: Implement**

Create `novelizer/voices/scaffold.py`:

```python
from __future__ import annotations
import os
from pathlib import Path
import tomllib
from novelizer.voices.models import VoicePack

DEFAULT_PACK_GUARD_MESSAGE = (
    "Refusing to scaffold into the shipped default voice pack; "
    "pass a separate user pack path instead."
)


def _toml_escape(s: str) -> str:
    """Escape a string for a TOML basic string ("...").

    Handles the two characters that must be escaped inside a TOML basic
    string: backslash and double-quote. Newlines are also escaped so the
    written value stays a single-line basic string regardless of input.
    """
    return s.replace("\\", "\\\\").replace('"', '\\"').replace("\n", "\\n")


def scaffold_prose_profile(pack_path: str, profile_name: str, description: str) -> str:
    """Write (creating or updating) a `[prose_profiles.<profile_name>]` TOML block
    into the pack at `pack_path`, using stdlib `tomllib` to read the existing pack
    (if any) and hand-written TOML text to write it back — no LLM call, no new
    TOML-writing dependency (see plan Global Constraints for that rationale).

    Refuses to write to a path whose basename is `default.toml` under the
    shipped `novelizer/voices/` package directory, to protect the shipped pack
    from being clobbered by scaffolding aimed at a user pack.
    """
    shipped_default = Path(__file__).parent / "default.toml"
    if Path(pack_path).resolve() == shipped_default.resolve():
        raise ValueError(DEFAULT_PACK_GUARD_MESSAGE)

    p = Path(pack_path)
    if p.is_file():
        with p.open("rb") as f:
            data = tomllib.load(f)
    else:
        data = {"name": p.stem, "prose_profiles": {}, "agent_personalities": {}}

    data.setdefault("prose_profiles", {})
    data["prose_profiles"][profile_name] = {"name": profile_name, "casting_note": description}

    _write_pack_toml(p, data)
    return str(p)


def _write_pack_toml(path: Path, data: dict) -> None:
    """Hand-write the known VoicePack TOML shape: a top-level `name`, a
    `[prose_profiles.<key>]` table per profile (each with `name`/`casting_note`
    string keys), and a `[agent_personalities]` table of string values.

    This is deliberately not a general-purpose TOML serializer — VoicePack's
    shape is small and fixed (see novelizer/voices/models.py), so hand-writing
    it avoids adding a TOML-writing dependency for one call site.
    """
    lines = [f'name = "{_toml_escape(data.get("name", path.stem))}"', ""]
    for key, profile in data.get("prose_profiles", {}).items():
        lines.append(f"[prose_profiles.{key}]")
        lines.append(f'name = "{_toml_escape(profile["name"])}"')
        lines.append(f'casting_note = "{_toml_escape(profile["casting_note"])}"')
        lines.append("")
    personalities = data.get("agent_personalities", {})
    if personalities:
        lines.append("[agent_personalities]")
        for agent, note in personalities.items():
            lines.append(f'{agent} = "{_toml_escape(note)}"')
        lines.append("")
    path.write_text("\n".join(lines), encoding="utf-8")
```

Add the `voice-scaffold` command to `novelizer/director/cli.py`, immediately after the `voices` command:

```python
@cli.command("voice-scaffold")
@click.argument("profile_name")
@click.argument("description")
@click.option(
    "--pack", "pack_path", default="stories/user_pack.toml",
    help="User pack file to write into (defaults to stories/user_pack.toml; never the shipped default pack).",
)
@click.pass_context
def voice_scaffold(ctx, profile_name: str, description: str, pack_path: str):
    """Scaffold a new prose profile into a user voice pack from a one-line description.

    No LLM call: the description you pass becomes the profile's casting note verbatim.
    """
    from novelizer.voices.scaffold import scaffold_prose_profile
    try:
        written = scaffold_prose_profile(pack_path, profile_name, description)
    except ValueError as e:
        console.print(f"[red]{e}[/red]")
        return
    console.print(f"[green]Scaffolded profile '{profile_name}' into {written}[/green]")
```

Add the import for `os` is not needed in `cli.py` (only used in `scaffold.py`); no other change to `cli.py`'s existing commands.

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/voices/test_scaffold.py -v`
Expected: PASS (5 passed). Then `uv run pytest tests/ -v` for the full suite green. Manually verify the CLI wiring:

```bash
uv run novelizer voice-scaffold brisk "Fast, punchy, present-tense action prose." --pack /tmp/manual_pack.toml
cat /tmp/manual_pack.toml
```

Expected: prints `Scaffolded profile 'brisk' into /tmp/manual_pack.toml`; the file contains a loadable `[prose_profiles.brisk]` block.

- [ ] **Step 5: Commit**

```bash
git add novelizer/voices/scaffold.py novelizer/director/cli.py tests/voices/test_scaffold.py
git commit -m "feat: voice-scaffold CLI command scaffolds a new prose profile from a one-line prompt"
```

---

### Task 7: Docs — mark M2.3 and M2 complete, document character voices + voice browser/scaffolding

**Files:**
- Modify: `docs/submilestones/M2-voices.md`
- Modify: `docs/MILESTONES.md`
- Modify: `README.md`

**Interfaces:** none (documentation only).

- [ ] **Step 1: Update the sub-milestone table**

In `docs/submilestones/M2-voices.md`, change the M2.3 row's `Status` cell from `⬜ not started` to `✅ complete`.

- [ ] **Step 2: Update the parent milestone table**

In `docs/MILESTONES.md`, change the M2 row's `Status` cell from `⬜ not started` to `✅ complete`.

- [ ] **Step 3: Extend the README's "Voices" section**

In `README.md`, add a new subsection immediately after the "Personalities & the living feed" subsection added in M2.2 (before any following top-level heading):

```markdown
### Character voices & the voice browser

The Character Keeper grows a **voice card** per character — dialogue patterns,
vocabulary, and verbal tics — as it reviews recent chapters, revising it
alongside `arc_status`. The Editor cites any voiced characters appearing in
the chapter it's reviewing, flagging drift the same way it flags prose-voice
drift against the active prose profile. Voice cards are visible in the Story
Browser's character detail pane (right pane, click a character):

```
Mira
Traits: stoic
Arc: cracking
Motivations: ...
Voice: Speaks in short, clipped sentences; never says "I love you" outright.

<backstory>
```

Inspect the active pack's prose profiles, agent personalities, and any
characters with a voice card from the CLI:

```bash
novelizer voices
novelizer voices --pack path/to/other_pack.toml
```

Scaffold a brand-new prose profile from a one-line description — no LLM call,
written straight to a user pack file (never the shipped default pack):

```bash
novelizer voice-scaffold brisk "Fast, punchy, present-tense action prose." --pack stories/user_pack.toml
```

Switch to it the same way any prose profile is activated (`NOVELIZER_VOICE_PACK`
and `NOVELIZER_PROSE_PROFILE` in `.env`, per the Configuration table above).
A dedicated in-TUI voice-editing/scaffolding pane, live in-run profile
switching, and LLM-expanded scaffolded profiles are deferred past M2.3.
```

- [ ] **Step 4: Commit**

```bash
git add docs/submilestones/M2-voices.md docs/MILESTONES.md README.md
git commit -m "docs: mark M2.3 and M2 complete; document character voices and voice browser/scaffolding"
```

---

## Self-Review

**Spec coverage against the M2.3 row and load-bearing design decisions in `docs/submilestones/M2-voices.md`, and the M2.3 task prompt's Part A/B breakdown:**
- "CharacterKeeper builds/updates a per-character voice card (dialogue patterns, vocabulary, tics)" — Task 1 (`Character.voice` field) + Task 2 (`CharacterUpdate.voice`, merge loop, `SYSTEM_PROMPT` addition).
- "Editor cites it" — Task 3 (`_character_voices_block`, guarded prompt section, byte-identical-prompt test when no character has a voice).
- "card shown in the story browser" — Task 4 (`detail_text`'s `characters` branch).
- "In-TUI voice-pack browser (packs / prose profiles / agent personalities / character voices)" — resolved as a CLI surface for M2.3 (Task 5's `format_voice_report`), with character voice cards specifically satisfied in the TUI by Task 4; this split is stated explicitly in the plan header's Architecture section, matching the task prompt's instruction to "decide and state a coherent, testable split."
- "scaffolding a new profile from a one-line prompt" — Task 6 (`scaffold_prose_profile`, `voice-scaffold` CLI command), explicitly deferring an in-TUI scaffolding pane and LLM expansion (both stated in Task 7's README addition and restated below).
- Done-criterion — "Characters accrue voice cards browsable in the TUI; you can scaffold a new voice profile from the TUI" — the TUI-browsability half is satisfied by Task 4; the "from the TUI" half of scaffolding is the one done-criterion clause not literally met by this plan's CLI-first scaffolding surface. This is a deliberate, explicitly-stated scope call (Architecture section + Deferrals below): scaffolding functionality exists and is fully tested, reachable via CLI immediately, with the TUI entry point deferred to a later milestone alongside the dedicated voice-editing pane — flagged here rather than silently narrowed.

**Deferrals (explicitly out of scope for M2.3, stated here and in the README per the task prompt's requirement):**
- LLM-expansion of scaffolded profiles (the `description` argument becomes the casting note verbatim — no model call).
- Live in-run switching of the active pack/profile (still `.env`/`Settings`-configured, read once at `Runtime.start()`, as in M2.1/M2.2).
- A fully dedicated TUI voice-editing/scaffolding pane (voice browsing beyond the character voice card is CLI-only for M2.3; the Story Browser gains no new widget).

**Placeholder scan:** every task's Step 3 contains complete, runnable file contents — full replacements for every touched file (`character_keeper.py`, `editor.py`, `browser_model.py`'s changed branch, `cli.py` in full for Task 5's rewrite) verified against the actual current source read during planning. No "similar to Task N", no `...` elisions, no TODOs. Task 6's `scaffold.py` is a complete new module with full escaping logic, not a stub.

**Type consistency:** `Character.voice: str = ""` (Task 1) matches `CharacterUpdate.voice: Optional[str] = None` (Task 2)'s merge-loop convention exactly as `arc_status`/`traits`/`motivations`/`backstory` already do — same tuple-driven `if v is not None: fields[f] = v` loop, now with `"voice"` appended. `Editor._character_voices_block(self, character_ids: list[str]) -> str` matches the existing `async def _x(...) -> ...` private-helper style (no direct precedent in `Editor` itself, but mirrors `BaseAgent._remark`/`BaseAgent._consume_signals`'s shape). `scaffold_prose_profile(pack_path: str, profile_name: str, description: str) -> str` and `format_voice_report(pack: VoicePack, characters: list[Character], active_profile: str | None) -> str` are both introduced once (Tasks 6 and 5 respectively) and used consistently in the CLI command wired immediately after each.

**DDD/SOLID:**
- Single Responsibility: `CharacterKeeper.commit()`'s merge loop is the one place that turns a `CharacterUpdate` into a `character.updated` event; `Editor._character_voices_block` is the one place that knows how to render voice cards into a prompt section; `browser_model.detail_text` is the one place that knows how to render a character's detail text; `format_voice_report`/`scaffold_prose_profile` are each single-purpose pure functions consumed by one thin CLI command apiece.
- Open/Closed: `Character`/`CharacterUpdate` are extended with one new optional/defaulted field each, not restructured; `Editor.work()`'s prompt gains one more independently-guarded section, following the exact pattern `casting_note`/`personality` already established in M2.1/M2.2 — no existing section's logic is touched.
- Dependency Inversion preserved: the CLI's `format_voice_report`/`scaffold_prose_profile` depend only on `VoicePack`/`Character` (plain pydantic models) and the filesystem, never on `ReadStore`/`EventStore` directly for their pure logic — `ReadStore.list_characters()` is fetched by the thin `voices` command wrapper and handed in, keeping the formatter unit-testable without a database.
- Bounded context: only `store/models.py`, `agents/schemas.py`, `agents/character_keeper.py`, `agents/editor.py`, `tui/widgets/browser_model.py`, `director/cli.py`, and the new `voices/scaffold.py` are touched; `canon/event_store.py`, `canon/projector.py`, `canon/committer.py`, `canon/read_store.py`, `runtime.py`, and `voices/loader.py`/`voices/models.py` are untouched — the Keeper's `character.updated` event flows through the exact same Projector dispatch and `ReadStore.get_character` query path that already existed for `arc_status`/`traits`/etc.
- Event sourcing: no new event type; `Character.voice` reaches the projections table via the existing `CHARACTER_UPDATED` event type and the existing `characters` projection table (which stores the full `Character.model_dump_json()`, so an additive field requires zero schema migration — confirmed by reading `novelizer/canon/read_store.py`'s `Character.model_validate_json(row[0])` calls, which round-trip whatever fields are present).

**Backward-compatibility check (explicit, per the task prompt's hardest constraint — "does adding Character.voice and CharacterUpdate.voice keep the existing suite green? does the Keeper's model_copy merge loop cleanly extend to voice? does the Editor guard keep existing Editor tests byte-identical?"):**
- `Character.voice: str = ""` is a new defaulted field on a pydantic model constructed by keyword everywhere in the codebase (confirmed by reading every `Character(...)` call site surfaced in `tests/agents/test_character_keeper.py`, `tests/tui/test_browser_model.py`, and `novelizer/store/models.py` itself) — no positional-argument call site exists that a new field could shift, so every existing construction remains valid and every existing round-trip (`model_dump_json`/`model_validate_json`) is unaffected since the projections table stores full JSON blobs, not a fixed-column schema.
- `CharacterUpdate.voice: Optional[str] = None` follows the identical shape and default as the four fields it sits beside; the Keeper's merge loop change is a single-token addition to an existing tuple (`("arc_status", "traits", "motivations", "backstory", "voice")`) reusing the same `if v is not None` guard already proven correct for the other four fields — no new branching logic, so the existing `test_updates_character_arc_and_files_retcon` and `test_noop_when_no_characters` tests are provably unaffected (neither test sets `voice`, so `getattr(upd, "voice")` is `None` and the loop's `if v is not None` guard skips it exactly as it does today for any of the other three untouched-optional fields in that test).
- The Editor's new `_character_voices_block` is called unconditionally in `work()` but returns `""` whenever `ch.character_ids` is empty (the default for every `Chapter(...)` construction across the existing 178-test suite that doesn't set `character_ids` explicitly) or when every character it does list has an empty `voice` (the field's own default) — so `msg = f"...{voice}{cast}{voices}"` reduces to the pre-M2.3 `f"...{voice}{cast}"` byte-for-byte whenever no character voice is set, which `test_editor_prompt_omits_voices_section_when_none_set` (Task 3) pins directly, and every pre-existing `Editor` test in `tests/agents/test_editor.py` constructs chapters without `character_ids`, so none of them exercise the new section at all.
