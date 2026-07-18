# M2.1 · Voice Packs & Prose Profiles — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Switching the active prose profile changes the Author's next-chapter prompt — the profile's natural-language *casting note* appears in the Author's work-time input, and two different profiles produce two different prompts. The Editor references the same active profile to flag prose-voice drift in its verdicts.

**Architecture:** Voice lives in files, not code. A **voice pack** is a human-editable TOML document — pydantic models in `novelizer/voices/models.py`, loaded by `novelizer/voices/loader.py` via stdlib `tomllib`. A pack carries `prose_profiles` (name → natural-language casting note, e.g. "sparse", "lush", "plain") and `agent_personalities` (short casting notes per agent, carried now, *consumed* starting M2.2). `Settings` gains `voice_pack` (path to the active pack, defaulting to the shipped `novelizer/voices/default.toml`) and `prose_profile` (the active profile's name within that pack). `Runtime.start()` loads the pack once, resolves the active `ProseProfile`, and hands its `casting_note` to `Author` and `Editor` as an additive constructor parameter (default `""`, so every other call site — tests, the other four agents — is unaffected). Critically, the casting note is threaded into the **work-time prompt** (the user message `work()` builds each run), never into the deepagents `system_prompt` baked in at construction — so a future profile switch (M2.3's in-TUI switcher) takes effect on the very next chapter without rebuilding any agent. A read-only `voices` CLI command lists the active pack's profiles for inspection.

**Tech Stack:** Python 3.13 (stdlib `tomllib`), `pydantic` v2, `click`+`rich`, `pytest`+`pytest-asyncio` (`asyncio_mode=auto`).

**Deferred (explicitly out of scope for M2.1, so a reviewer doesn't read these as gaps):**
- **Live in-TUI profile switching** — M2.1's active profile comes from `Settings` (resolved once at `Runtime.start()`); this plan demonstrates "switching changes the next chapter" by building two `Author`/`Editor` instances with two different profiles and diffing the resulting prompts. The in-TUI voice browser/picker that lets the director switch mid-run is **M2.3**.
- **Personality injection into agent work / the feed** (`agent_personalities` consumption, `feed_note`, `agent.remarked` events) is **M2.2**. This plan's pack format *carries* `agent_personalities` (so M2.2 doesn't need a pack-format migration) but nothing in M2.1 reads them.
- **Character voice cards** (Character Keeper builds per-character dialogue/vocabulary/tic cards; Editor/Continuity Checker cite them; voice browser) are **M2.3**.

**Context — current state after M1.3 (on `m1.2-mission-control`):**

- `novelizer/config.py` — `Settings(BaseSettings)` with `model_config = SettingsConfigDict(env_prefix="NOVELIZER_", env_file=".env", extra="ignore")`; fields incl. `db_path`, `llm_base_url`, `llm_api_key`, `author_model`, `author_temperature`, `agent_model`, `agent_temperature`, `author_interval`, `default_agent_interval`, `continuity_interval`, `projector_interval`.
- `novelizer/runtime.py` — `Runtime(settings, runner=None, runners=None)`. `__init__` builds `self.events/self.projector/self.read`; `self.committer = None` (built in `start()`). `_runner_for(name, builder)` returns `self._runners[name]` if `runners=` was passed, else `self._runner` for `name == "author"` if a single `runner=` was passed (back-compat), else calls `builder(self.settings)`. `start()`: `await self.events.init()/self.projector.init()/self.read.init()/self.projector.catch_up()`; builds `self.policy = AutonomyPolicy(self.read)`, `self.committer = GatingCommitter(self.events, self.policy)`, `self.proposals = ProposalService(self.events)`; then constructs all six agents via `self._runner_for(name, builder_fn)`, e.g. `self.author = Author(self._runner_for("author", build_author_runner), self.read, self.committer, interval=s.author_interval)`; `self.agents = [world_architect, character_keeper, author, editor, continuity_checker, retconner]`; `self.scheduler = Scheduler(self.agents, self.read)`.
- `novelizer/agents/base.py` — `BaseAgent(runner, read_store, committer, interval, name=None)`: stores `self._runner/self._read/self._committer/self.interval/self.name/self.paused/self._last_run`; `pause()/resume()/ready_for_interval(now)/mark_ran(now)`; `async readiness() -> float` (default `0.0`); `async run_once()` (no-op default); `async _consume_signals(signals)` commits `DIRECTOR_SIGNAL_CONSUMED` per signal via the committer.
- `novelizer/agents/author.py` — `Author(BaseAgent)`: `__init__(self, runner, read_store, committer, interval=300)` → `super().__init__(runner, read_store, committer, interval, name="author")`. `readiness()` scores by draft backlog. `poll()` returns `{"world", "characters", "previous", "signals"}`. `work(ctx)` calls `self._runner.ainvoke({"messages": [{"role": "user", "content": _summarize(ctx)}]})` and returns `result.get("structured_response")` (a `ChapterDraft | None`). `commit(draft, ctx)` builds a `Chapter` and commits `EventType.CHAPTER_CREATED`, then `_consume_signals`. `run_once()` chains poll→work→commit. Module-level `_summarize(ctx) -> str` builds the user message from world/characters/previous/director notes, ending `"...\n\nWrite the next chapter."`. `build_author_runner(settings)` builds a deepagents agent with `system_prompt=AUTHOR_SYSTEM_PROMPT, response_format=ChapterDraft`.
- `novelizer/agents/editor.py` — `Editor(BaseAgent)`: `__init__(self, runner, read_store, committer, interval=120)`. `readiness()` scales with draft count. `poll()` returns `{"target": <oldest draft Chapter or None>}`. `work(ctx)` builds `msg = f"Chapter title: {ch.title}\n\nProse:\n{ch.prose}"`, calls the runner, returns `EditorVerdict | None`. `commit(verdict, ctx)`: on `verdict.verdict == "approve"` appends `CHAPTER_STATUS_CHANGED` with the chapter promoted to `reviewed` + `editor_notes`; else appends a `DIRECTOR_SIGNAL_CREATED` note targeting `"author"`. `build_editor_runner(settings)` uses `settings.agent_model/agent_temperature`.
- `novelizer/director/cli.py` — `click` group; `_with_runtime(settings, fn)` boots events/projector/read only (no agents, no LLM); existing commands: `seed/chapters/read/retcons/autonomy/proposals/approve/reject`; bare invocation launches the TUI via `_launch_tui` (which does call `Runtime.start()`, building real agents).
- `novelizer/store/models.py` — `Domain/CanonStatus/EditorialStatus/RetconStatus/SignalKind` `StrEnum`s + `WorldEntry/Character/Chapter/RetconRequest/DirectorSignal` pydantic models, module-level `_now()`/`_uuid()` factories. **Not touched by this plan** — voice models live in a new `novelizer/voices/` package, following the same factory conventions.
- `novelizer/canon/autonomy.py` — example of the project's `StrEnum` + pydantic-model-with-factory convention this plan's new models should match stylistically (not imported here).
- Test layout: `tests/canon/`, `tests/director/`, `tests/agents/`, `tests/tui/`, `tests/test_scheduler.py`, `tests/test_runtime.py`. This plan adds `tests/voices/`.
- `novelizer` is a `hatchling`-packaged project (`pyproject.toml`, `requires-python = ">=3.13"`) — `importlib.resources.files("novelizer")` resolves to the installed package root regardless of cwd, which is how this plan locates the shipped default pack robustly (see Task 3).

## Global Constraints

- **Python** `>=3.13` — use stdlib `tomllib` (no third-party TOML dependency).
- **Event sourcing is unchanged.** M2.1 adds **no** canon events — voice comes entirely from files/config, resolved once per `Runtime.start()`. Agents still write canon only via the injected `Committer`/`GatingCommitter` seam; nothing here touches `EventStore`, `Projector`, or `ReadStore`.
- **Voice is injected at work-time, not construction.** The casting note is a constructor parameter stored on the agent, but it is only ever *read* inside `work()` when building that run's user message — never folded into a deepagents `system_prompt` at agent-build time. This is what lets a future profile switch (M2.3) take effect on the next tick without rebuilding the agent.
- **Casting notes are natural language** — no parameter soup, no structured "voice DSL". A `ProseProfile` is a name plus a paragraph of prose description.
- **TDD, black-box first:** every task is failing-test → run-fail → implement → run-pass → commit. Prompts are asserted by capturing the exact `inputs` dict a `FakeRunner.ainvoke` receives — never by mocking internals. Parametrize where an invariant generalizes (e.g. "profile A's note appears / profile B's note appears and they differ").
- **`asyncio_mode = "auto"`** — `async def test_*` needs no decorator.
- **`novelizer/store/models.py` is unchanged.** New voice models live in `novelizer/voices/models.py`, reusing this project's pydantic-model conventions.
- **Only `Author` and `Editor` gain voice.** `WorldArchitect`, `CharacterKeeper`, `ContinuityChecker`, `Retconner` are untouched — zero changes to their constructors, `work()`, or tests.
- **`runner=`/`runners=` overrides on `Runtime` keep working unchanged** — voice wiring is additional constructor arguments to `Author`/`Editor`, not a change to how their runners are selected or injected.
- **DRY** — one loader, one pack format, one place (`Runtime.start()`) that resolves "active pack + active profile" into casting notes.

---

### Task 1: Voice-pack models — `ProseProfile`, `VoicePack`

**Files:**
- Create: `novelizer/voices/__init__.py` (empty)
- Create: `novelizer/voices/models.py`
- Test: `tests/voices/__init__.py` (empty), `tests/voices/test_models.py`

**Interfaces:**
- Produces:
  - `class ProseProfile(BaseModel)`: `name: str`, `casting_note: str`.
  - `class VoicePack(BaseModel)`: `name: str`, `prose_profiles: dict[str, ProseProfile] = {}`, `agent_personalities: dict[str, str] = {}` (agent name → short casting note; unused until M2.2 but carried now so the pack format doesn't need to change later). `def profile(self, name: str) -> ProseProfile | None` — dict lookup, `None` if absent.

- [ ] **Step 1: Write the failing test**

Create `tests/voices/__init__.py` (empty file).

Create `tests/voices/test_models.py`:
```python
from novelizer.voices.models import ProseProfile, VoicePack


def test_prose_profile_holds_name_and_casting_note():
    p = ProseProfile(name="sparse", casting_note="Spare, concrete, unadorned.")
    assert p.name == "sparse"
    assert "Spare" in p.casting_note


def test_voice_pack_defaults_are_empty():
    pack = VoicePack(name="empty-pack")
    assert pack.prose_profiles == {}
    assert pack.agent_personalities == {}
    assert pack.profile("sparse") is None


def test_voice_pack_profile_lookup():
    sparse = ProseProfile(name="sparse", casting_note="Spare, concrete, unadorned.")
    lush = ProseProfile(name="lush", casting_note="Ornate, sensory, gothic.")
    pack = VoicePack(
        name="test-pack",
        prose_profiles={"sparse": sparse, "lush": lush},
        agent_personalities={"author": "A weary chronicler."},
    )
    assert pack.profile("sparse") is sparse
    assert pack.profile("lush").casting_note == "Ornate, sensory, gothic."
    assert pack.profile("nonexistent") is None
    assert pack.agent_personalities["author"] == "A weary chronicler."
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/voices/test_models.py -v`
Expected: FAIL (`ModuleNotFoundError: No module named 'novelizer.voices'`).

- [ ] **Step 3: Implement**

Create `novelizer/voices/__init__.py` (empty).

Create `novelizer/voices/models.py`:
```python
from __future__ import annotations
from typing import Optional
from pydantic import BaseModel, Field


class ProseProfile(BaseModel):
    """A natural-language casting note describing a prose voice.

    Deliberately not a parameter DSL: `casting_note` is prose a human wrote,
    handed to the Author/Editor verbatim at work-time.
    """

    name: str
    casting_note: str


class VoicePack(BaseModel):
    """A voice pack: prose profiles the Author can be cast in, plus
    per-agent personality casting notes (consumed starting M2.2).
    """

    name: str
    prose_profiles: dict[str, ProseProfile] = Field(default_factory=dict)
    agent_personalities: dict[str, str] = Field(default_factory=dict)

    def profile(self, name: str) -> Optional[ProseProfile]:
        return self.prose_profiles.get(name)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/voices/test_models.py -v`
Expected: PASS (3 passed).

- [ ] **Step 5: Commit**

```bash
git add novelizer/voices/__init__.py novelizer/voices/models.py tests/voices/__init__.py tests/voices/test_models.py
git commit -m "feat: add ProseProfile/VoicePack voice-pack domain models"
```

---

### Task 2: TOML loader + shipped default pack

**Files:**
- Create: `novelizer/voices/loader.py`
- Create: `novelizer/voices/default.toml`
- Test: `tests/voices/test_loader.py`

**Interfaces:**
- Consumes: `ProseProfile`, `VoicePack` (Task 1).
- Produces: `def load_voice_pack(path: str) -> VoicePack` — parses TOML at `path` via stdlib `tomllib`, builds a `VoicePack`. Raises `FileNotFoundError` with a clear, actionable message if `path` does not exist (caught and re-raised with pack-specific context rather than a bare `open()` traceback).

**TOML shape** (see full shipped file below):
```toml
name = "default"

[prose_profiles.sparse]
name = "sparse"
casting_note = "..."

[prose_profiles.lush]
name = "lush"
casting_note = "..."

[prose_profiles.plain]
name = "plain"
casting_note = "..."

[agent_personalities]
author = "..."
editor = "..."
world_architect = "..."
character_keeper = "..."
continuity_checker = "..."
retconner = "..."
```

- [ ] **Step 1: Write the failing test**

Create `tests/voices/test_loader.py`:
```python
import os
import tempfile
import pytest
from novelizer.voices.loader import load_voice_pack

DEFAULT_PACK_PATH = os.path.join(
    os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
    "novelizer", "voices", "default.toml",
)


def test_load_default_pack_yields_expected_profiles():
    pack = load_voice_pack(DEFAULT_PACK_PATH)
    assert pack.name == "default"
    assert set(pack.prose_profiles) == {"sparse", "lush", "plain"}
    assert pack.profile("sparse").casting_note
    assert pack.profile("lush").casting_note
    assert pack.profile("sparse").casting_note != pack.profile("lush").casting_note


def test_load_default_pack_has_six_agent_personalities():
    pack = load_voice_pack(DEFAULT_PACK_PATH)
    expected_agents = {
        "author", "editor", "world_architect",
        "character_keeper", "continuity_checker", "retconner",
    }
    assert expected_agents <= set(pack.agent_personalities)
    for agent in expected_agents:
        assert pack.agent_personalities[agent].strip()


def test_profile_lookup_on_loaded_pack():
    pack = load_voice_pack(DEFAULT_PACK_PATH)
    assert pack.profile("plain") is not None
    assert pack.profile("nonexistent-profile") is None


def test_missing_file_raises_clear_error():
    with tempfile.TemporaryDirectory() as d:
        missing = os.path.join(d, "does-not-exist.toml")
        with pytest.raises(FileNotFoundError, match="Voice pack not found"):
            load_voice_pack(missing)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/voices/test_loader.py -v`
Expected: FAIL (`ModuleNotFoundError: No module named 'novelizer.voices.loader'`).

- [ ] **Step 3: Implement**

Create `novelizer/voices/default.toml`:
```toml
name = "default"

[prose_profiles.sparse]
name = "sparse"
casting_note = """
Spare, concrete, unadorned. Short declarative sentences. Nouns and verbs carry
the weight; adjectives are rationed. No throat-clearing, no interiority spelled
out — feeling shows through action and object, never through explanation.
Dialogue is clipped, often unattributed when the speaker is clear from context.
White space and understatement do the emotional work. Think dry heat, dust,
distances measured in effort rather than miles. Never explain what a gesture
means; let the reader do that work.
"""

[prose_profiles.lush]
name = "lush"
casting_note = """
Ornate, sensory, gothic. Long, cumulative sentences that pile clause on clause,
each adding a texture, a smell, a half-remembered dread. Interiority is welcome
and rendered at length — characters brood, and the prose broods with them.
Weather, architecture, and decay are characters in their own right. Adjectives
travel in pairs and triads. Metaphor reaches for the uncanny and the bodily.
Dialogue is formal, sometimes archaic, laced with things left ominously unsaid.
"""

[prose_profiles.plain]
name = "plain"
casting_note = """
Clean and neutral. Standard modern prose register — clear sentences of varying
length, ordinary vocabulary, no stylistic thumbprint drawing attention to
itself. Description is functional: enough to orient the reader, no more.
Dialogue reads naturally, fully attributed where it might otherwise confuse.
This is the voice to fall back to when no other casting note is active, or
when the story calls for transparency over style.
"""

[agent_personalities]
author = "A restless, slightly romantic chronicler who falls a little in love with every character they write and privately roots for the ones in trouble."
editor = "A precise, unsentimental line editor who respects the writer's voice but has zero patience for a sagging middle or a dropped thread."
world_architect = "A quietly obsessive worldbuilder who would rather add one more load-bearing detail than ship on time, and treats every unexplored corner of the map as an insult."
character_keeper = "A protective, watchful presence who tracks every character like a case file and gets genuinely uneasy when someone acts out of character."
continuity_checker = "A dry, pedantic fact-checker who takes real satisfaction in catching the two suns nobody else noticed."
retconner = "A calm, surgical fixer who treats contradictions as puzzles to solve rather than failures to lament, and never blames the agent who caused them."
```

Create `novelizer/voices/loader.py`:
```python
from __future__ import annotations
import tomllib
from pathlib import Path
from novelizer.voices.models import ProseProfile, VoicePack


def load_voice_pack(path: str) -> VoicePack:
    """Load a voice pack from a TOML file on disk.

    Raises FileNotFoundError with a clear, pack-specific message if `path`
    does not exist, rather than letting a bare `open()` traceback surface.
    """
    p = Path(path)
    if not p.is_file():
        raise FileNotFoundError(f"Voice pack not found at '{path}'.")
    with p.open("rb") as f:
        data = tomllib.load(f)

    profiles = {
        key: ProseProfile(**profile_data)
        for key, profile_data in data.get("prose_profiles", {}).items()
    }
    return VoicePack(
        name=data["name"],
        prose_profiles=profiles,
        agent_personalities=data.get("agent_personalities", {}),
    )
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/voices/test_loader.py -v`
Expected: PASS (4 passed).

- [ ] **Step 5: Commit**

```bash
git add novelizer/voices/loader.py novelizer/voices/default.toml tests/voices/test_loader.py
git commit -m "feat: add TOML voice-pack loader and shipped default pack"
```

---

### Task 3: Config — `voice_pack` and `prose_profile` settings

**Files:**
- Modify: `novelizer/config.py`
- Test: `tests/test_config.py` (new, or extend if a `tests/test_config.py` already exists — check first with `ls tests/test_config.py`; this plan creates it fresh)

**Design decision — resolving the default pack path robustly:** `Settings.voice_pack` must default to the shipped `novelizer/voices/default.toml` regardless of the process's current working directory (the CLI, the TUI, and tests all run from different cwds). `importlib.resources.files("novelizer")` resolves to the installed/importable package root — since `novelizer` is a real installed package (`hatchling`, `pyproject.toml`), this works whether the package is installed in editable mode (`uv run`) or as a wheel, unlike a `Path(__file__).parent`-style relative hack living in `config.py` (which is one directory removed from `voices/` and would need brittle relative traversal). We use `importlib.resources.files("novelizer.voices").joinpath("default.toml")` and `str(...)` it into the default — a module-level constant computed once at import time, not per-instantiation, so `Settings()` construction stays cheap.

**Interfaces:**
- Produces: `Settings.voice_pack: str` (default: shipped `novelizer/voices/default.toml`, resolved via `importlib.resources`), `Settings.prose_profile: str` (default: `"plain"` — the default pack's neutral profile). Both overridable via `NOVELIZER_VOICE_PACK`/`NOVELIZER_PROSE_PROFILE` env vars (inherited automatically from `BaseSettings`'s `env_prefix="NOVELIZER_"`).

- [ ] **Step 1: Write the failing test**

Create `tests/test_config.py`:
```python
import os
from novelizer.config import Settings


def test_voice_pack_defaults_to_shipped_default_pack():
    s = Settings()
    assert s.voice_pack.endswith("default.toml")
    assert os.path.isfile(s.voice_pack)


def test_prose_profile_defaults_to_plain():
    s = Settings()
    assert s.prose_profile == "plain"


def test_voice_pack_env_override(monkeypatch):
    monkeypatch.setenv("NOVELIZER_VOICE_PACK", "/tmp/custom-pack.toml")
    s = Settings()
    assert s.voice_pack == "/tmp/custom-pack.toml"


def test_prose_profile_env_override(monkeypatch):
    monkeypatch.setenv("NOVELIZER_PROSE_PROFILE", "lush")
    s = Settings()
    assert s.prose_profile == "lush"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_config.py -v`
Expected: FAIL (`pydantic_core._pydantic_core.ValidationError` or `AttributeError: 'Settings' object has no attribute 'voice_pack'`).

- [ ] **Step 3: Implement**

In `novelizer/config.py`, add the import and a module-level default-path constant, plus the two new fields:
```python
import importlib.resources
from pydantic_settings import BaseSettings, SettingsConfigDict

_DEFAULT_VOICE_PACK = str(importlib.resources.files("novelizer.voices").joinpath("default.toml"))


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="NOVELIZER_", env_file=".env", extra="ignore")

    # Storage
    db_path: str = "stories/world.db"
    chroma_path: str = "stories/chroma"   # reserved for M1 embeddings
    embed_model: str = "nomic-embed-text"  # reserved for M1 embeddings

    # OpenAI-compatible LLM endpoint
    llm_base_url: str = "http://localhost:8080/v1"
    llm_api_key: str = "not-needed"
    author_model: str = "local-model"
    author_temperature: float = 0.8
    agent_model: str = "local-model"
    agent_temperature: float = 0.7

    # Cadence (seconds)
    author_interval: int = 300
    default_agent_interval: int = 120
    continuity_interval: int = 900
    projector_interval: float = 0.5

    # Voice (M2.1): active voice pack + active prose profile within it.
    voice_pack: str = _DEFAULT_VOICE_PACK
    prose_profile: str = "plain"
```
(Only the new import, the `_DEFAULT_VOICE_PACK` constant, and the two trailing fields are additions — every existing field is unchanged.)

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/test_config.py -v`
Expected: PASS (4 passed).

- [ ] **Step 5: Commit**

```bash
git add novelizer/config.py tests/test_config.py
git commit -m "feat: add Settings.voice_pack/prose_profile, resolved via importlib.resources"
```

---

### Task 4: Author/Editor gain a `casting_note` parameter, injected at work-time

**Files:**
- Modify: `novelizer/agents/author.py`
- Modify: `novelizer/agents/editor.py`
- Test: `tests/agents/test_author.py` (extend), `tests/agents/test_editor.py` (extend)

**Interfaces:**
- `Author.__init__(self, runner, read_store, committer, interval=300, casting_note: str = "")` — stores `self._casting_note = casting_note`. `_summarize(ctx, casting_note)` (module function, now takes the note) appends a `"Write in this prose voice: <casting_note>"` line when `casting_note` is non-empty; when empty, the message is byte-for-byte what it was before this plan (back-compat for existing callers/tests that build an `Author` with no `casting_note`).
- `Editor.__init__(self, runner, read_store, committer, interval=120, casting_note: str = "")` — stores `self._casting_note`. `work(ctx)`'s message gains an `"Enforce this prose voice: <casting_note>; note any drift in your feedback."` line when non-empty, otherwise unchanged.

- [ ] **Step 1: Write the failing tests**

Append to `tests/agents/test_author.py` (reusing the file's existing `FakeRunner`/`stack` fixture — `FakeRunner` already records nothing beyond returning a draft; extend it here to capture the exact inputs it was called with):
```python
async def test_work_prompt_includes_casting_note_when_set(stack):
    events, proj, read, committer = stack
    draft = ChapterDraft(title="T", prose="P")
    runner = FakeRunner(draft)
    author = Author(runner, read, committer, casting_note="Spare, concrete, unadorned.")
    ctx = await author.poll()
    await author.work(ctx)
    sent = runner.calls[-1]["messages"][0]["content"]
    assert "Spare, concrete, unadorned." in sent
    assert "Write in this prose voice:" in sent


async def test_work_prompt_omits_casting_note_when_unset(stack):
    events, proj, read, committer = stack
    author = Author(FakeRunner(ChapterDraft(title="T", prose="P")), read, committer)
    ctx = await author.poll()
    await author.work(ctx)
    sent = author._casting_note
    assert sent == ""


async def test_two_profiles_yield_different_prompts(stack):
    events, proj, read, committer = stack
    draft = ChapterDraft(title="T", prose="P")
    sparse_runner = FakeRunner(draft)
    lush_runner = FakeRunner(draft)
    sparse_author = Author(sparse_runner, read, committer, casting_note="Spare, concrete, unadorned.")
    lush_author = Author(lush_runner, read, committer, casting_note="Ornate, sensory, gothic.")
    ctx = await sparse_author.poll()
    await sparse_author.work(ctx)
    await lush_author.work(ctx)
    sparse_prompt = sparse_runner.calls[-1]["messages"][0]["content"]
    lush_prompt = lush_runner.calls[-1]["messages"][0]["content"]
    assert sparse_prompt != lush_prompt
    assert "Spare, concrete, unadorned." in sparse_prompt
    assert "Ornate, sensory, gothic." in lush_prompt
```
`FakeRunner` in this file already stores `self.calls` (see the class in the current file: `self.calls = []` and `self.calls.append(inputs)` inside `ainvoke`) — no change needed to the fixture.

Append to `tests/agents/test_editor.py`:
```python
async def test_editor_prompt_includes_active_prose_profile(stack):
    events, proj, read, committer = stack
    await events.append(EventType.CHAPTER_CREATED, "c1", Chapter(id="c1", title="One", prose="p"))
    await proj.catch_up()

    class RecordingRunner:
        def __init__(self, out):
            self._out = out
            self.calls = []

        async def ainvoke(self, inputs):
            self.calls.append(inputs)
            return {"structured_response": self._out}

    runner = RecordingRunner(EditorVerdict(verdict="approve", notes="clean"))
    agent = Editor(runner, read, committer, casting_note="Spare, concrete, unadorned.")
    ctx = await agent.poll()
    await agent.work(ctx)
    sent = runner.calls[-1]["messages"][0]["content"]
    assert "Spare, concrete, unadorned." in sent
    assert "Enforce this prose voice:" in sent
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/agents/test_author.py tests/agents/test_editor.py -v`
Expected: FAIL (`TypeError: Author.__init__() got an unexpected keyword argument 'casting_note'`).

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


def _summarize(ctx: dict, casting_note: str = "") -> str:
    world = "\n".join(f"- {e.title}: {e.body[:150]}" for e in ctx["world"][:10]) or "None yet."
    chars = "\n".join(f"- {c.name}: {c.traits} | arc: {c.arc_status}" for c in ctx["characters"][:8]) or "None yet."
    prev = "\n".join(f"- '{c.title}': {c.prose[:200]}" for c in ctx["previous"]) or "None yet."
    notes = "\n".join(f"Director: {s.body}" for s in ctx["signals"]) or "None."
    voice = f"\n\nWrite in this prose voice: {casting_note}" if casting_note else ""
    return (
        f"World lore:\n{world}\n\nCharacters:\n{chars}\n\n"
        f"Previous chapters:\n{prev}\n\nDirector notes:\n{notes}{voice}\n\nWrite the next chapter."
    )


class Author(BaseAgent):
    def __init__(
        self,
        runner: Runner,
        read_store: ReadStore,
        committer: Committer,
        interval: int = 300,
        casting_note: str = "",
    ) -> None:
        super().__init__(runner, read_store, committer, interval, name="author")
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
        content = _summarize(ctx, self._casting_note)
        result = await self._runner.ainvoke({"messages": [{"role": "user", "content": content}]})
        return result.get("structured_response")

    async def commit(self, draft: ChapterDraft | None, ctx: dict) -> None:
        if draft is None:
            return
        chapter = Chapter(title=draft.title, prose=draft.prose, character_ids=draft.character_ids)
        await self._committer.commit(self.name, EventType.CHAPTER_CREATED, chapter.id, chapter)
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
    ) -> None:
        super().__init__(runner, read_store, committer, interval, name="editor")
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
        msg = f"Chapter title: {ch.title}\n\nProse:\n{ch.prose}{voice}"
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
Expected: PASS (all prior tests + 4 new tests, since `casting_note` defaults to `""` every pre-existing call site is untouched).

- [ ] **Step 5: Commit**

```bash
git add novelizer/agents/author.py novelizer/agents/editor.py tests/agents/test_author.py tests/agents/test_editor.py
git commit -m "feat: inject active prose-profile casting note into Author/Editor work-time prompts"
```

---

### Task 5: Runtime — resolve the active pack/profile and wire the casting note

**Files:**
- Modify: `novelizer/runtime.py`
- Test: `tests/test_runtime.py` (extend)

**Interfaces:**
- `Runtime.start()` gains, right after `self.read.init()`/before agent construction: `pack = load_voice_pack(self.settings.voice_pack)`; `profile = pack.profile(self.settings.prose_profile)`; `casting_note = profile.casting_note if profile else ""`. Exposes `self.voice_pack: VoicePack` and `self.active_prose_profile: ProseProfile | None` as new `Runtime` attributes (useful for the CLI in Task 6 and the future M2.3 browser) — set once in `start()`, alongside the existing attribute set.
- `self.author = Author(..., casting_note=casting_note)` and `self.editor = Editor(..., casting_note=casting_note)`; the other four agent constructions are byte-for-byte unchanged.
- If `pack.profile(settings.prose_profile)` misses (unknown profile name), `casting_note` falls back to `""` rather than raising — a typo'd `prose_profile` degrades to "no voice cast" instead of crashing `Runtime.start()`.

- [ ] **Step 1: Write the failing test**

Append to `tests/test_runtime.py` (reusing whatever fixture pattern the file already uses for building a `Runtime` with `runners=` overrides — inspect the file first; the tests below assume a temp-db `settings` fixture and a `runners` dict of fakes matching the pattern already established for the other five agents in this file):
```python
import tempfile
from novelizer.config import Settings
from novelizer.runtime import Runtime


class _FakeAgentRunner:
    async def ainvoke(self, inputs):
        return {"structured_response": None}


def _all_fake_runners():
    return {
        name: _FakeAgentRunner()
        for name in ("author", "world_architect", "character_keeper", "editor", "continuity_checker", "retconner")
    }


async def test_runtime_wires_active_prose_profile_into_author():
    fd, path = tempfile.mkstemp(suffix=".db")
    import os
    os.close(fd)
    try:
        settings = Settings(db_path=path, prose_profile="sparse")
        rt = Runtime(settings, runners=_all_fake_runners())
        await rt.start()
        assert rt.active_prose_profile is not None
        assert rt.active_prose_profile.name == "sparse"
        assert rt.author._casting_note == rt.active_prose_profile.casting_note
        assert rt.editor._casting_note == rt.active_prose_profile.casting_note
        await rt.close()
    finally:
        os.unlink(path)


async def test_runtime_unknown_profile_falls_back_to_empty_casting_note():
    fd, path = tempfile.mkstemp(suffix=".db")
    import os
    os.close(fd)
    try:
        settings = Settings(db_path=path, prose_profile="does-not-exist")
        rt = Runtime(settings, runners=_all_fake_runners())
        await rt.start()
        assert rt.active_prose_profile is None
        assert rt.author._casting_note == ""
        await rt.close()
    finally:
        os.unlink(path)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_runtime.py -v`
Expected: FAIL (`AttributeError: 'Runtime' object has no attribute 'active_prose_profile'`).

- [ ] **Step 3: Implement**

In `novelizer/runtime.py`, add the import:
```python
from novelizer.voices.loader import load_voice_pack
```

In `__init__`, add two attributes alongside the existing ones (after `self.scheduler: Optional[Scheduler] = None`):
```python
        self.voice_pack = None
        self.active_prose_profile = None
```

In `start()`, insert pack/profile resolution right after `self.proposals = ProposalService(self.events)` and before `s = self.settings`:
```python
        self.voice_pack = load_voice_pack(self.settings.voice_pack)
        self.active_prose_profile = self.voice_pack.profile(self.settings.prose_profile)
        casting_note = self.active_prose_profile.casting_note if self.active_prose_profile else ""
```
Then change the `Author`/`Editor` construction lines to pass it:
```python
        self.author = Author(self._runner_for("author", build_author_runner), self.read, self.committer, interval=s.author_interval, casting_note=casting_note)
        self.world_architect = WorldArchitect(self._runner_for("world_architect", build_world_architect_runner), self.read, self.committer, interval=s.default_agent_interval)
        self.character_keeper = CharacterKeeper(self._runner_for("character_keeper", build_character_keeper_runner), self.read, self.committer, interval=s.default_agent_interval)
        self.editor = Editor(self._runner_for("editor", build_editor_runner), self.read, self.committer, interval=s.default_agent_interval, casting_note=casting_note)
        self.continuity_checker = ContinuityChecker(self._runner_for("continuity_checker", build_continuity_checker_runner), self.read, self.committer, interval=s.continuity_interval)
        self.retconner = Retconner(self._runner_for("retconner", build_retconner_runner), self.read, self.committer, interval=s.default_agent_interval)
```
(Only the `author` and `editor` lines change — `world_architect`/`character_keeper`/`continuity_checker`/`retconner` are shown unchanged for clarity but require no edit.)

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/test_runtime.py -v`
Expected: PASS (all prior + 2 new). Then `uv run pytest -q` for the full suite green.

- [ ] **Step 5: Commit**

```bash
git add novelizer/runtime.py tests/test_runtime.py
git commit -m "feat: Runtime resolves active voice pack/prose profile and wires it into Author/Editor"
```

---

### Task 6: `voices` CLI command

**Files:**
- Modify: `novelizer/director/cli.py`
- Test: `tests/director/test_cli.py` (extend)

**Interfaces:**
- Produces: `@cli.command() def voices(ctx, pack: str | None)` — click option `--pack PATH` (optional; defaults to `ctx.obj["settings"].voice_pack`). Read-only: loads the pack via `load_voice_pack`, prints a `rich` table of `name | casting note snippet (first ~80 chars)` with the active profile (from `settings.prose_profile`, only meaningful when `--pack` was not overridden) marked with an arrow/asterisk. Does not require `_with_runtime` (no event store needed) — loads the pack directly.

- [ ] **Step 1: Write the failing test**

Append to `tests/director/test_cli.py` (matching whatever `CliRunner` fixture pattern the file already uses — if it constructs `Settings` with a temp `db_path` via an env var or `--` option, reuse the same mechanism; otherwise use `monkeypatch.setenv` as below):
```python
def test_voices_lists_default_pack_profiles(monkeypatch, tmp_path):
    from click.testing import CliRunner
    from novelizer.director.cli import cli

    db_path = tmp_path / "world.db"
    monkeypatch.setenv("NOVELIZER_DB_PATH", str(db_path))
    runner = CliRunner()
    result = runner.invoke(cli, ["voices"])
    assert result.exit_code == 0
    assert "sparse" in result.output
    assert "lush" in result.output
    assert "plain" in result.output
    assert "*" in result.output or "active" in result.output.lower()


def test_voices_with_explicit_pack_path(monkeypatch, tmp_path):
    from click.testing import CliRunner
    from novelizer.director.cli import cli

    db_path = tmp_path / "world.db"
    monkeypatch.setenv("NOVELIZER_DB_PATH", str(db_path))
    custom_pack = tmp_path / "custom.toml"
    custom_pack.write_text(
        'name = "custom"\n\n[prose_profiles.terse]\nname = "terse"\ncasting_note = "Very short sentences."\n'
    )
    runner = CliRunner()
    result = runner.invoke(cli, ["voices", "--pack", str(custom_pack)])
    assert result.exit_code == 0
    assert "terse" in result.output
    assert "sparse" not in result.output
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/director/test_cli.py -v -k voices`
Expected: FAIL (`Error: No such command 'voices'.` via click's exit code/output).

- [ ] **Step 3: Implement**

In `novelizer/director/cli.py`, add the import and command (append near the other read-only commands, e.g. after `retcons`):
```python
from novelizer.voices.loader import load_voice_pack
```
```python
@cli.command()
@click.option("--pack", "pack_path", default=None, help="Inspect a voice pack other than the active one.")
@click.pass_context
def voices(ctx, pack_path: str | None):
    """List the active (or given) voice pack's prose profiles."""
    settings = ctx.obj["settings"]
    path = pack_path or settings.voice_pack
    pack = load_voice_pack(path)
    active_name = settings.prose_profile if pack_path is None else None
    table = Table(title=f"Voice pack: {pack.name}")
    table.add_column("Active", style="green", no_wrap=True)
    table.add_column("Profile")
    table.add_column("Casting note")
    for name, profile in pack.prose_profiles.items():
        marker = "*" if name == active_name else ""
        snippet = profile.casting_note.strip().replace("\n", " ")[:80]
        table.add_row(marker, name, snippet)
    console.print(table)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/director/test_cli.py -v -k voices`
Expected: PASS (2 passed).

- [ ] **Step 5: Commit**

```bash
git add novelizer/director/cli.py tests/director/test_cli.py
git commit -m "feat: add read-only 'voices' CLI command listing active pack's prose profiles"
```

---

### Task 7: Docs — mark M2.1 complete, document voice packs

**Files:**
- Modify: `docs/submilestones/M2-voices.md`
- Modify: `README.md`

**Interfaces:** none (documentation only).

- [ ] **Step 1: Update the sub-milestone table**

In `docs/submilestones/M2-voices.md`, change the M2.1 row's `Status` cell from `⬜ not started` to `✅ complete`.

- [ ] **Step 2: Add a README "Voices" subsection**

In `README.md`, add a `## Voices` section (placement: after the existing configuration/usage section, matching whatever heading level the file already uses at that point — inspect `README.md`'s current heading structure before inserting, to match style). Content:

```markdown
## Voices

Prose voice is data, not code: a **voice pack** is a human-editable TOML file
(`novelizer/voices/default.toml` ships as the default) listing named
**prose profiles** — each a natural-language "casting note" describing the
prose the Author should write in (e.g. `sparse`, `lush`, `plain`).

The active pack and active profile are read from `Settings` at startup:

- `NOVELIZER_VOICE_PACK` — path to a voice-pack TOML file (defaults to the
  shipped pack).
- `NOVELIZER_PROSE_PROFILE` — the profile name within that pack to cast the
  Author (and the Editor's enforcement) in (defaults to `plain`).

Inspect any pack's profiles without starting a run:

```bash
novelizer voices                       # list the active pack's profiles
novelizer voices --pack my-pack.toml   # inspect another pack
```

Switching `NOVELIZER_PROSE_PROFILE` changes the casting note handed to the
Author's next chapter and the Editor's next review — no code changes, no
agent rebuild. Live in-TUI switching of the active profile, and per-agent
personality casting (also carried in the pack format today), land in later
sub-milestones.
```

- [ ] **Step 3: Commit**

```bash
git add docs/submilestones/M2-voices.md README.md
git commit -m "docs: mark M2.1 complete; document voice packs and prose profiles"
```

---

## Self-Review

**Spec coverage against the M2.1 row and load-bearing design decisions in `docs/submilestones/M2-voices.md`:**
- ✅ "TOML voice-pack format + loader (pydantic models) + a shipped default pack" — Tasks 1–2.
- ✅ "config for the active pack + active prose profile" — Task 3.
- ✅ "the active prose profile's natural-language casting note injected into the Author's work-time prompt and referenced by the Editor's enforcement" — Task 4 (agent-level), Task 5 (Runtime wiring).
- ✅ "CLI/command to list packs and switch the active prose profile" — Task 6 delivers listing; *switching* is via `Settings`/env var (Task 3) per the plan's stated M2.1 scope (live in-TUI switching is explicitly M2.3 per `docs/submilestones/M2-voices.md`'s own M2.3 row — "In-TUI voice-pack browser").
- ✅ Done-criterion — "switching the prose profile changes the Author's next-chapter prompt... profile A vs B produces different prompts" — directly asserted in Task 4 Step 1 (`test_two_profiles_yield_different_prompts`) and Task 5 (`test_runtime_wires_active_prose_profile_into_author`).
- ✅ "Voice lives in files, injected at work-time... NOT baked into the deepagents system_prompt at construction" — Task 4's `_summarize`/`work()` build the note into the per-call user message; `AUTHOR_SYSTEM_PROMPT`/editor `SYSTEM_PROMPT` (used only by `build_author_runner`/`build_editor_runner` at deepagents-construction time) are untouched.
- ✅ "The Runtime is the voice source... Agents gain a small additive voice parameter" — Task 5; `casting_note: str = ""` is additive and defaults preserve every existing call site.
- ✅ `agent_personalities` carried in the pack now, unused until M2.2 — Task 1/2 model + default.toml content; explicitly called out as deferred.
- ✅ Character voice cards explicitly deferred to M2.3.

**Placeholder scan:** every task's Step 3 contains complete, runnable file contents or precise inline diffs against exact current source (verified against the actual `novelizer/config.py`, `novelizer/runtime.py`, `novelizer/agents/author.py`, `novelizer/agents/editor.py`, `novelizer/director/cli.py` read during planning) — no "similar to Task N", no `...` elisions in code, no TODOs left for the implementer to fill in.

**Type consistency:** `ProseProfile`/`VoicePack` are plain pydantic `BaseModel`s matching `store/models.py`'s conventions (no custom `_uuid()`/`_now()` needed — voice packs are config, not canon, and have no identity/timestamp). `Settings.voice_pack`/`prose_profile` are plain `str` fields, consistent with every other `Settings` field. `Author`/`Editor`'s new `casting_note: str = ""` parameter matches the existing all-keyword-defaultable trailing-parameter style already used for `interval`.

**DDD/SOLID:**
- Single Responsibility: `voices/models.py` (shape), `voices/loader.py` (I/O + parsing), `Runtime` (wiring/resolution) are cleanly separated; no component does more than one job.
- Open/Closed: `Author`/`Editor` are extended (new optional parameter, new prompt line) without modifying their existing collaborators' contracts (`BaseAgent`, `Committer`/`GatingCommitter`, `ReadStore` are all untouched) or breaking any existing caller.
- Dependency Inversion preserved: `Runtime` depends on the `load_voice_pack(path) -> VoicePack` function and hands agents a plain string, not a loader/pack object — agents remain ignorant of TOML/file I/O entirely, matching how they're already ignorant of `EventStore`/SQL via the `Committer`/`ReadStore` seams.
- Bounded context: `novelizer/voices/` is a new, self-contained package with no imports from `canon`/`store` and no reverse dependency — `store/models.py` remains untouched, as required.
- Event sourcing: confirmed zero new `EventType` constants, zero new projections, zero new `ReadStore` methods — voice is config-resolved once per process, not canon.

**Risk / follow-up noted for the implementer:** Task 6's CLI test relies on whatever `CliRunner`/env-var pattern `tests/director/test_cli.py` already establishes for isolating `db_path` per test; if that file's actual fixture differs from the `monkeypatch.setenv("NOVELIZER_DB_PATH", ...)` shown here, adapt the two new tests' setup to match the file's existing convention exactly rather than introducing a second pattern — the assertions (profile names present/absent in `result.output`, exit code 0) are what matters and do not depend on the exact fixture mechanics.
