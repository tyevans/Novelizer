# New-Story Config Form Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the story picker's lone name input with an inline form that also captures an optional premise (injected as a `director_signal.created` seed event) and a voice pack + prose profile (written to `story.toml` only when they differ from the inherited global values).

**Architecture:** Event-sourced TUI app (Textual). The picker (`StoryPickerApp`) is a standalone `App[Path | None]` that runs before the Runtime boots, so the seed event is appended by opening the story's `EventStore` directly. Voice packs are TOML files: the shipped `novelizer/voices/default.toml` plus any `*.toml` directly under the stories root (the `voice-scaffold` convention).

**Tech Stack:** Python 3.13, Textual 5.3, pydantic, aiosqlite, pytest (+pytest-asyncio, `asyncio_mode = "auto"` — async tests need no decorator). Run tests with `uv run pytest`.

**Spec:** `docs/superpowers/specs/2026-07-18-new-story-config-design.md`

## Global Constraints

- Story.toml is shareable: never write the voice pack/profile unless the chosen value differs from the inherited effective value; secrets (`llm_api_key`) must never be writable (`FORBIDDEN_STORY_KEYS`).
- Overrides must be validated against the existing `STORY_OVERRIDABLE_KEYS` (`novelizer/settings/models.py:10`); unknown keys raise `StoryConfigError` **before** the story directory is created.
- No new event types, no new domain concepts: the premise is exactly one `director_signal.created` (kind=`seed`) appended via the existing `commands.seed()`.
- Headless CLI auto-create (`_resolve_story` in `novelizer/director/cli.py`) is untouched.
- Keep widget id `#new_name` — existing tests and CSS target it.
- TDD: write the failing test first for every task; commit after each green task.

---

### Task 1: Voice pack discovery helper

**Files:**
- Create: `novelizer/voices/discovery.py`
- Test: `tests/voices/test_discovery.py`

**Interfaces:**
- Consumes: `load_voice_pack(path: str) -> VoicePack` from `novelizer/voices/loader.py` (raises `FileNotFoundError` on missing file, `KeyError` on missing `name`, pydantic `ValidationError` on bad shape).
- Produces: `discover_voice_packs(stories_root: Path) -> list[tuple[str, str]]` — `(label, path)` pairs, shipped default pack first (label `"default"`, its pack name), then user packs sorted by filename, labeled `"<pack name> (<filename>)"`. Task 4 consumes this.

- [ ] **Step 1: Write the failing tests**

```python
# tests/voices/test_discovery.py
from pathlib import Path

from novelizer.voices.discovery import discover_voice_packs

_SHIPPED = Path("novelizer/voices/default.toml").resolve()

_NOIR_PACK = '''name = "noir"

[prose_profiles.hardboiled]
name = "hardboiled"
casting_note = "Short sentences. Rain on glass."
'''


def test_shipped_default_pack_is_always_first(tmp_path):
    packs = discover_voice_packs(tmp_path)
    assert len(packs) == 1
    label, path = packs[0]
    assert label == "default"
    assert Path(path).resolve() == _SHIPPED


def test_user_packs_in_stories_root_are_listed_after_shipped(tmp_path):
    (tmp_path / "noir.toml").write_text(_NOIR_PACK, encoding="utf-8")
    packs = discover_voice_packs(tmp_path)
    assert [label for label, _ in packs] == ["default", "noir (noir.toml)"]
    assert packs[1][1] == str(tmp_path / "noir.toml")


def test_unparseable_toml_in_stories_root_is_skipped(tmp_path):
    (tmp_path / "broken.toml").write_text("not = [valid", encoding="utf-8")
    (tmp_path / "notapack.toml").write_text('title = "no name key"', encoding="utf-8")
    packs = discover_voice_packs(tmp_path)
    assert [label for label, _ in packs] == ["default"]


def test_missing_stories_root_yields_only_shipped(tmp_path):
    packs = discover_voice_packs(tmp_path / "does-not-exist")
    assert [label for label, _ in packs] == ["default"]
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/voices/test_discovery.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'novelizer.voices.discovery'`

- [ ] **Step 3: Write the implementation**

```python
# novelizer/voices/discovery.py
from __future__ import annotations

from pathlib import Path

from novelizer.voices.loader import load_voice_pack

_SHIPPED_DEFAULT = Path(__file__).parent / "default.toml"


def discover_voice_packs(stories_root: Path) -> list[tuple[str, str]]:
    """(label, path) pairs for the voice-pack picker: the shipped default pack
    first, then any *.toml files directly under `stories_root` (the
    `voice-scaffold` convention — its default output is stories/user_pack.toml),
    sorted by filename. Files that don't parse as voice packs are skipped;
    story directories' own story.toml files live in subdirectories and are
    never scanned."""
    shipped = load_voice_pack(str(_SHIPPED_DEFAULT))
    packs: list[tuple[str, str]] = [(shipped.name, str(_SHIPPED_DEFAULT))]
    if stories_root.is_dir():
        for p in sorted(stories_root.glob("*.toml")):
            try:
                pack = load_voice_pack(str(p))
            except Exception:
                continue
            packs.append((f"{pack.name} ({p.name})", str(p)))
    return packs
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/voices/test_discovery.py -v`
Expected: 4 PASS

- [ ] **Step 5: Commit**

```bash
git add novelizer/voices/discovery.py tests/voices/test_discovery.py
git commit -m "feat: discover_voice_packs — shipped default + stories-root user packs"
```

---

### Task 2: `create_story` accepts validated overrides

**Files:**
- Modify: `novelizer/settings/story_dir.py:33-37`
- Test: `tests/settings/test_story_dir.py` (append tests)

**Interfaces:**
- Consumes: `STORY_OVERRIDABLE_KEYS` (`novelizer/settings/models.py:10`), `StoryConfigError` (`novelizer/settings/layers.py:14`), `write_toml_file` (already imported).
- Produces: `create_story(root: Path, title: str, overrides: dict[str, object] | None = None) -> StoryDirectory`. Unknown/forbidden keys raise `StoryConfigError` before any mkdir. Task 4 consumes this. Existing callers (`cli.py`, tests) pass no overrides and are unaffected.

- [ ] **Step 1: Write the failing tests**

Append to `tests/settings/test_story_dir.py` (file already imports `create_story` and `load_toml_file`; add `import pytest` and `from novelizer.settings.layers import StoryConfigError` at the top if missing):

```python
def test_create_story_with_overrides_writes_them(tmp_path):
    sd = create_story(
        tmp_path / "s", title="S",
        overrides={"prose_profile": "lush", "voice_pack": "/packs/noir.toml"},
    )
    assert load_toml_file(sd.story_toml) == {
        "title": "S", "prose_profile": "lush", "voice_pack": "/packs/noir.toml",
    }


def test_create_story_without_overrides_unchanged(tmp_path):
    sd = create_story(tmp_path / "s", title="S")
    assert load_toml_file(sd.story_toml) == {"title": "S"}


def test_create_story_rejects_unknown_and_forbidden_keys(tmp_path):
    import pytest
    from novelizer.settings.layers import StoryConfigError

    with pytest.raises(StoryConfigError):
        create_story(tmp_path / "s", title="S", overrides={"llm_api_key": "sk-x"})
    with pytest.raises(StoryConfigError):
        create_story(tmp_path / "s", title="S", overrides={"nonsense": "x"})
    # validation happens before mkdir: no half-created story dir
    assert not (tmp_path / "s").exists()
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/settings/test_story_dir.py -v -k overrides`
Expected: FAIL — `TypeError: create_story() got an unexpected keyword argument 'overrides'`

- [ ] **Step 3: Implement**

Replace `create_story` in `novelizer/settings/story_dir.py` and add the two imports:

```python
from novelizer.settings.layers import StoryConfigError
from novelizer.settings.models import STORY_OVERRIDABLE_KEYS


def create_story(
    root: Path, title: str, overrides: dict[str, object] | None = None
) -> StoryDirectory:
    """Create a story directory with a story.toml. `overrides` are optional
    story-scoped settings (validated against STORY_OVERRIDABLE_KEYS) written
    alongside the title; validation runs before mkdir so a bad call leaves
    no half-created directory."""
    data: dict[str, object] = {"title": title}
    if overrides:
        unknown = sorted(set(overrides) - STORY_OVERRIDABLE_KEYS)
        if unknown:
            raise StoryConfigError(
                f"{root / 'story.toml'}: {unknown} are not story-overridable settings"
            )
        data.update(overrides)
    sd = StoryDirectory(root=root)
    root.mkdir(parents=True, exist_ok=True)
    write_toml_file(sd.story_toml, data)
    return sd
```

(Import-cycle note: `layers.py` imports only from `models.py`; `story_dir.py` importing `layers` is acyclic.)

- [ ] **Step 4: Run the settings suite**

Run: `uv run pytest tests/settings/ -v`
Expected: all PASS (including pre-existing `test_create_story`)

- [ ] **Step 5: Commit**

```bash
git add novelizer/settings/story_dir.py tests/settings/test_story_dir.py
git commit -m "feat: create_story accepts validated story-overridable settings"
```

---

### Task 3: Seed-at-birth helper `seed_story_dir`

**Files:**
- Modify: `novelizer/director/commands.py` (add one function + imports)
- Test: `tests/director/test_commands.py` (append one test)

**Interfaces:**
- Consumes: `EventStore` (`novelizer/canon/event_store.py:33` — standalone: `__init__(path)`, `await init()`, `await close()`, `await events_since(0)`), existing `seed(events, text)` in the same module, `StoryDirectory`/`create_story` from `novelizer.settings.story_dir`.
- Produces: `async def seed_story_dir(story: StoryDirectory, text: str) -> None`. Task 4 consumes this.

- [ ] **Step 1: Write the failing test**

Append to `tests/director/test_commands.py` (asyncio_mode is auto — plain `async def` test):

```python
async def test_seed_story_dir_appends_seed_event_without_runtime(tmp_path):
    from novelizer.director.commands import seed_story_dir
    from novelizer.settings.story_dir import create_story

    sd = create_story(tmp_path / "s", title="S")
    await seed_story_dir(sd, "a tired thief takes one last job")

    events = EventStore(str(sd.db_path))
    await events.init()
    try:
        stored = await events.events_since(0)
    finally:
        await events.close()
    assert len(stored) == 1
    assert stored[0].event_type == EventType.DIRECTOR_SIGNAL_CREATED
    assert stored[0].payload["kind"] == SignalKind.seed.value
    assert stored[0].payload["body"] == "a tired thief takes one last job"
```

(`EventStore`, `EventType`, `SignalKind` are already imported at the top of this test file.)

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/director/test_commands.py -v -k seed_story_dir`
Expected: FAIL — `ImportError: cannot import name 'seed_story_dir'`

- [ ] **Step 3: Implement**

In `novelizer/director/commands.py`, add imports and the function (below the existing `seed`):

```python
from novelizer.canon.event_store import EventStore
from novelizer.settings.story_dir import StoryDirectory


async def seed_story_dir(story: StoryDirectory, text: str) -> None:
    """Append a seed signal directly to a story's event log, without a running
    Runtime. Used at story-creation time: the picker runs before Runtime boots,
    and EventStore is standalone (creates its own schema on init)."""
    events = EventStore(str(story.db_path))
    await events.init()
    try:
        await seed(events, text)
    finally:
        await events.close()
```

- [ ] **Step 4: Run tests**

Run: `uv run pytest tests/director/test_commands.py -v`
Expected: all PASS

- [ ] **Step 5: Commit**

```bash
git add novelizer/director/commands.py tests/director/test_commands.py
git commit -m "feat: seed_story_dir — append a seed event to a story log sans Runtime"
```

---

### Task 4: Inline new-story form in the picker

**Files:**
- Modify: `novelizer/tui/story_picker.py` (full rework shown below)
- Test: `tests/tui/test_story_picker.py` (two existing tests updated, new tests appended)

**Interfaces:**
- Consumes: `discover_voice_packs` (Task 1), `create_story(root, title, overrides)` (Task 2), `seed_story_dir(story_directory, text)` (Task 3), `load_voice_pack`, `EffectiveSettings` (its defaults: `voice_pack` = shipped pack path, `prose_profile` = `"plain"`).
- Produces: `StoryPickerApp(stories, stories_dir, last_opened=None, default_voice_pack=None, default_prose_profile=None)`. `None` defaults fall back to `EffectiveSettings()` built-ins, so all existing constructions keep working. Task 5 passes the real effective values.

- [ ] **Step 1: Update the two existing tests that assume the bare input**

In `tests/tui/test_story_picker.py`, visibility now lives on the form container `#new_story_form` (the `#new_name` Input itself stays `display: true` inside it):

Replace `test_new_name_input_renders_at_natural_height` body lines:

```python
async def test_new_name_input_renders_at_natural_height(tmp_path):
    """Regression: #new_name must not be crunched to height:1/border:none."""
    app = StoryPickerApp([], stories_dir=tmp_path)
    async with app.run_test(size=(80, 50)) as pilot:
        app.query_one("#new_story_form").display = True
        await pilot.pause()
        name_input = app.query_one("#new_name", Input)
        assert name_input.outer_size.height >= 3
        assert name_input.styles.border_top[0] != "none"
        app.exit(None)
```

In `test_new_story_flow_creates_and_returns`, replace the reveal assertion:

```python
        assert app.query_one("#new_story_form").display  # form revealed
        name_input = app.query_one("#new_name", Input)
```

(the rest of that test is unchanged — defaults-only create must still produce `{"title": "My Great Novel!"}` and nothing else.)

- [ ] **Step 2: Append the new tests**

Append to `tests/tui/test_story_picker.py`:

```python
from textual.widgets import Button, Select, TextArea

from novelizer.canon.event_store import EventStore
from novelizer.canon.events import EventType
from novelizer.settings.story_dir import StoryDirectory

_NOIR_PACK = '''name = "noir"

[prose_profiles.hardboiled]
name = "hardboiled"
casting_note = "Short sentences. Rain on glass."
'''


async def _read_events(root):
    events = EventStore(str(StoryDirectory(root=root).db_path))
    await events.init()
    try:
        return await events.events_since(0)
    finally:
        await events.close()


async def test_create_with_defaults_writes_only_title_and_no_seed(tmp_path):
    app = StoryPickerApp([], stories_dir=tmp_path)
    async with app.run_test(size=(80, 50)) as pilot:
        app.query_one("#stories", OptionList).highlighted = 0
        await pilot.press("enter")
        app.query_one("#new_name", Input).value = "Plain One"
        await app._create()
    root = app.return_value
    assert load_toml_file(root / "story.toml") == {"title": "Plain One"}
    assert not (root / "world.db").exists()  # no premise -> no event log yet


async def test_create_with_premise_and_voice_writes_overrides_and_seed(tmp_path):
    (tmp_path / "noir.toml").write_text(_NOIR_PACK, encoding="utf-8")
    app = StoryPickerApp([], stories_dir=tmp_path)
    async with app.run_test(size=(80, 50)) as pilot:
        app.query_one("#stories", OptionList).highlighted = 0
        await pilot.press("enter")
        app.query_one("#new_name", Input).value = "Iron Harvest"
        app.query_one("#new_premise", TextArea).text = "A tired thief takes one last job."
        app.query_one("#new_voice_pack", Select).value = str(tmp_path / "noir.toml")
        await pilot.pause()
        assert app.query_one("#new_profile", Select).value == "hardboiled"
        await app._create()
    root = app.return_value
    assert root == tmp_path / "iron-harvest"
    assert load_toml_file(root / "story.toml") == {
        "title": "Iron Harvest",
        "voice_pack": str(tmp_path / "noir.toml"),
        "prose_profile": "hardboiled",
    }
    stored = await _read_events(root)
    assert len(stored) == 1
    assert stored[0].event_type == EventType.DIRECTOR_SIGNAL_CREATED
    assert stored[0].payload["kind"] == "seed"
    assert stored[0].payload["body"] == "A tired thief takes one last job."


async def test_profile_select_repopulates_when_pack_changes(tmp_path):
    (tmp_path / "noir.toml").write_text(_NOIR_PACK, encoding="utf-8")
    app = StoryPickerApp([], stories_dir=tmp_path)
    async with app.run_test(size=(80, 50)) as pilot:
        app.query_one("#stories", OptionList).highlighted = 0
        await pilot.press("enter")
        profile = app.query_one("#new_profile", Select)
        assert profile.value == "plain"  # shipped default pack, effective default profile
        app.query_one("#new_voice_pack", Select).value = str(tmp_path / "noir.toml")
        await pilot.pause()
        assert profile.value == "hardboiled"
        app.exit(None)


async def test_cancel_button_and_escape_collapse_the_form(tmp_path):
    app = StoryPickerApp([], stories_dir=tmp_path)
    async with app.run_test(size=(80, 50)) as pilot:
        app.query_one("#stories", OptionList).highlighted = 0
        await pilot.press("enter")
        form = app.query_one("#new_story_form")
        assert form.display
        await pilot.click("#cancel_btn")
        assert not form.display
        await pilot.press("enter")  # reopen via highlighted "new story"
        assert form.display
        app.query_one("#new_name", Input).focus()
        await pilot.pause()
        await pilot.press("escape")
        assert not form.display
        app.exit(None)
    assert app.return_value is None
```

- [ ] **Step 3: Run tests to verify the new ones fail**

Run: `uv run pytest tests/tui/test_story_picker.py -v`
Expected: new tests FAIL (`NoMatches` for `#new_story_form` etc.); old ones updated in Step 1 also FAIL until implementation lands.

- [ ] **Step 4: Implement — full replacement of `novelizer/tui/story_picker.py`**

```python
from __future__ import annotations

from pathlib import Path

from textual.app import App, ComposeResult
from textual.containers import Horizontal, Vertical
from textual.widgets import (
    Button, Footer, Header, Input, OptionList, Select, Static, TextArea,
)
from textual.widgets.option_list import Option

from novelizer.director.commands import seed_story_dir
from novelizer.settings.discovery import StoryMeta, order_stories, slugify
from novelizer.settings.models import EffectiveSettings
from novelizer.settings.story_dir import create_story
from novelizer.voices.discovery import discover_voice_packs
from novelizer.voices.loader import load_voice_pack

_NEW_STORY_ID = "__new__"


def _same_path(a: str, b: str) -> bool:
    return Path(a).resolve() == Path(b).resolve()


class StoryPickerApp(App[Path | None]):
    """Pick an existing story or create one via the inline new-story form
    (title, optional premise-as-seed, voice pack + prose profile).

    run() returns the chosen story root, or None if the user quit.
    Ordering/slug logic lives in settings.discovery; this is the TUI shell.
    """

    TITLE = "Novelizer — Choose a story"
    BINDINGS = [("q", "quit", "Quit"), ("escape", "cancel_new", "Cancel")]
    CSS = """
    #stories {
        height: auto;
        max-height: 10;
    }
    #picker_error {
        height: 1;
    }
    #new_story_form {
        height: auto;
    }
    #new_premise {
        height: 4;
    }
    #form_buttons {
        height: auto;
    }
    """

    def __init__(
        self,
        stories: list[StoryMeta],
        stories_dir: Path,
        last_opened: str | None = None,
        default_voice_pack: str | None = None,
        default_prose_profile: str | None = None,
    ) -> None:
        super().__init__()
        self._stories = order_stories(stories, last_opened)
        self._stories_dir = stories_dir
        fallback = EffectiveSettings()
        self._default_voice_pack = default_voice_pack or fallback.voice_pack
        self._default_prose_profile = default_prose_profile or fallback.prose_profile

    # -- option/select data ------------------------------------------------

    def _pack_options(self) -> list[tuple[str, str]]:
        """Discovered packs, guaranteeing the inherited default is present."""
        packs = discover_voice_packs(self._stories_dir)
        if not any(_same_path(p, self._default_voice_pack) for _, p in packs):
            packs.insert(0, (Path(self._default_voice_pack).stem, self._default_voice_pack))
        return packs

    def _profile_options(self, pack_path: str) -> list[tuple[str, str]]:
        try:
            pack = load_voice_pack(pack_path)
        except Exception:
            pack = None
        if pack and pack.prose_profiles:
            return [(key, key) for key in pack.prose_profiles]
        # Unloadable/empty pack: fall back to the inherited profile name so the
        # Select always has a value; create() then writes no profile override.
        return [(self._default_prose_profile, self._default_prose_profile)]

    def _default_profile_for(self, options: list[tuple[str, str]]) -> str:
        values = [v for _, v in options]
        return (
            self._default_prose_profile
            if self._default_prose_profile in values
            else values[0]
        )

    # -- layout ------------------------------------------------------------

    def compose(self) -> ComposeResult:
        yield Header()
        options = [Option("➕  New story", id=_NEW_STORY_ID)]
        options += [Option(f"{s.title}  ({s.root})", id=str(s.root)) for s in self._stories]
        yield OptionList(*options, id="stories")
        packs = self._pack_options()
        pack_value = next(
            (p for _, p in packs if _same_path(p, self._default_voice_pack)), packs[0][1]
        )
        profiles = self._profile_options(pack_value)
        with Vertical(id="new_story_form"):
            yield Input(id="new_name", placeholder="New story name…")
            yield TextArea(id="new_premise")
            yield Select(packs, id="new_voice_pack", allow_blank=False, value=pack_value)
            yield Select(
                profiles, id="new_profile", allow_blank=False,
                value=self._default_profile_for(profiles),
            )
            with Horizontal(id="form_buttons"):
                yield Button("Create", id="create_btn", variant="primary")
                yield Button("Cancel", id="cancel_btn")
        yield Static("", id="picker_error")
        yield Footer()

    def on_mount(self) -> None:
        self.query_one("#new_story_form").display = False
        option_list = self.query_one("#stories", OptionList)
        # Preselect the last-opened story (index 1) when present, else "new story".
        option_list.highlighted = 1 if self._stories else 0
        option_list.focus()

    # -- events ------------------------------------------------------------

    def on_option_list_option_selected(self, event: OptionList.OptionSelected) -> None:
        if event.option.id == _NEW_STORY_ID:
            self.query_one("#new_story_form").display = True
            self.query_one("#new_name", Input).focus()
        else:
            self.exit(Path(event.option.id))

    async def on_input_submitted(self, event: Input.Submitted) -> None:
        if event.input.id == "new_name":
            await self._create()

    async def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "create_btn":
            await self._create()
        elif event.button.id == "cancel_btn":
            self._hide_form()

    def on_select_changed(self, event: Select.Changed) -> None:
        if event.select.id != "new_voice_pack":
            return
        profile_select = self.query_one("#new_profile", Select)
        options = self._profile_options(str(event.value))
        profile_select.set_options(options)
        profile_select.value = self._default_profile_for(options)

    def action_cancel_new(self) -> None:
        if self.query_one("#new_story_form").display:
            self._hide_form()

    # -- form logic --------------------------------------------------------

    def _hide_form(self) -> None:
        self.query_one("#new_story_form").display = False
        self.query_one("#picker_error", Static).update("")
        self.query_one("#stories", OptionList).focus()

    async def _create(self) -> None:
        error = self.query_one("#picker_error", Static)
        name = self.query_one("#new_name", Input).value.strip()
        if not name:
            error.update("✗ name required")
            return
        root = self._stories_dir / slugify(name)
        if root.exists():
            error.update(f"✗ {root} already exists")
            return
        overrides: dict[str, object] = {}
        pack = str(self.query_one("#new_voice_pack", Select).value)
        profile = str(self.query_one("#new_profile", Select).value)
        # story.toml is shareable: only pin values that differ from the
        # inherited effective settings (never the shipped pack's abs path).
        if not _same_path(pack, self._default_voice_pack):
            overrides["voice_pack"] = pack
        if profile != self._default_prose_profile:
            overrides["prose_profile"] = profile
        sd = create_story(root, title=name, overrides=overrides or None)
        premise = self.query_one("#new_premise", TextArea).text.strip()
        if premise:
            try:
                await seed_story_dir(sd, premise)
            except OSError as e:
                error.update(f"✗ story created, but seed failed: {e}")
                return
        self.exit(root)
```

- [ ] **Step 5: Run the picker suite**

Run: `uv run pytest tests/tui/test_story_picker.py -v`
Expected: all PASS (updated + new)

- [ ] **Step 6: Run the full TUI suite to catch collateral**

Run: `uv run pytest tests/tui/ -q`
Expected: all PASS

- [ ] **Step 7: Commit**

```bash
git add novelizer/tui/story_picker.py tests/tui/test_story_picker.py
git commit -m "feat: inline new-story form — premise-as-seed + voice pack/profile selects"
```

---

### Task 5: Wire effective defaults through the CLI boot path

**Files:**
- Modify: `novelizer/director/cli.py:88-91` (`_run_picker_app`) and `cli.py:122` (call site in `_interactive_startup`)
- Test: `tests/director/test_startup.py` (two fake_picker signatures)

**Interfaces:**
- Consumes: `StoryPickerApp(..., default_voice_pack=..., default_prose_profile=...)` (Task 4); `base: EffectiveSettings` already in scope at the call site.
- Produces: `run_picker` injection point now receives a 4th positional arg `base: EffectiveSettings`.

- [ ] **Step 1: Update the fakes (failing first)**

In `tests/director/test_startup.py`, both named fakes gain the new arg and one asserts it flows through:

```python
    def fake_picker(stories, stories_dir, last_opened, base):
        assert stories_dir == Path("stories")
        assert base.prose_profile  # effective settings reach the picker
        return story.root
```

and

```python
    def fake_picker(stories, stories_dir, last_opened, base):
        seen["stories_dir"] = stories_dir
        seen["titles"] = [s.title for s in stories]
        return story.root
```

(the `lambda *a: ...` fakes need no change.)

- [ ] **Step 2: Run to verify failure**

Run: `uv run pytest tests/director/test_startup.py -v`
Expected: FAIL — `TypeError: fake_picker() missing 1 required positional argument: 'base'` (call site still passes 3 args)

- [ ] **Step 3: Implement**

In `novelizer/director/cli.py` replace `_run_picker_app`:

```python
def _run_picker_app(
    stories, stories_dir: Path, last_opened: str | None, base: EffectiveSettings
):
    from novelizer.tui.story_picker import StoryPickerApp

    return StoryPickerApp(
        stories,
        stories_dir=stories_dir,
        last_opened=last_opened,
        default_voice_pack=base.voice_pack,
        default_prose_profile=base.prose_profile,
    ).run()
```

and in `_interactive_startup` change the call:

```python
        chosen = run_picker(list_stories(stories_root), stories_root, base.last_opened_story, base)
```

- [ ] **Step 4: Run director tests**

Run: `uv run pytest tests/director/ -v`
Expected: all PASS

- [ ] **Step 5: Full suite + commit**

Run: `uv run pytest -q`
Expected: all PASS

```bash
git add novelizer/director/cli.py tests/director/test_startup.py
git commit -m "feat: pass effective voice defaults into the story picker"
```
