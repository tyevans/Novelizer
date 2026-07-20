from pathlib import Path

from textual.widgets import Input, OptionList, Static

from novelizer.settings.discovery import StoryMeta
from novelizer.settings.story_dir import is_story_dir
from novelizer.settings.toml_io import load_toml_file
from novelizer.tui.story_picker import StoryPickerApp


def _metas(tmp_path) -> list[StoryMeta]:
    return [
        StoryMeta(root=tmp_path / "old", title="Old One", mtime=100.0),
        StoryMeta(root=tmp_path / "recent", title="Recent", mtime=200.0),
    ]


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


async def test_lists_stories_recent_first_and_preselects_last_opened(tmp_path):
    app = StoryPickerApp(_metas(tmp_path), stories_dir=tmp_path, last_opened=str(tmp_path / "old"))
    async with app.run_test(size=(80, 50)) as pilot:
        options = app.query_one("#stories", OptionList)
        # index 0 is "new story"; last-opened comes first among stories
        assert options.get_option_at_index(1).id == str(tmp_path / "old")
        assert options.get_option_at_index(2).id == str(tmp_path / "recent")
        assert options.highlighted == 1
        app.exit(None)


async def test_selecting_story_returns_its_root(tmp_path):
    app = StoryPickerApp(_metas(tmp_path), stories_dir=tmp_path)
    async with app.run_test(size=(80, 50)) as pilot:
        options = app.query_one("#stories", OptionList)
        options.highlighted = 1  # most recent story
        await pilot.press("enter")
    assert app.return_value == tmp_path / "recent"


async def test_new_story_flow_creates_and_returns(tmp_path):
    app = StoryPickerApp([], stories_dir=tmp_path)
    async with app.run_test(size=(80, 50)) as pilot:
        options = app.query_one("#stories", OptionList)
        options.highlighted = 0  # "new story"
        await pilot.press("enter")
        assert app.query_one("#new_story_form").display  # form revealed
        name_input = app.query_one("#new_name", Input)
        name_input.value = "My Great Novel!"
        name_input.focus()
        await pilot.pause()
        await pilot.press("enter")
    root = app.return_value
    assert root == tmp_path / "my-great-novel"
    assert is_story_dir(root)
    assert load_toml_file(root / "story.toml") == {"title": "My Great Novel!"}


async def test_new_story_duplicate_slug_shows_error(tmp_path):
    (tmp_path / "taken").mkdir()
    app = StoryPickerApp([], stories_dir=tmp_path)
    async with app.run_test(size=(80, 50)) as pilot:
        options = app.query_one("#stories", OptionList)
        options.highlighted = 0
        await pilot.press("enter")
        name_input = app.query_one("#new_name", Input)
        name_input.value = "Taken"
        name_input.focus()
        await pilot.pause()
        await pilot.press("enter")
        await pilot.pause()
        assert "exists" in str(app.query_one("#picker_error", Static).renderable)
        app.exit(None)
    assert app.return_value is None


from textual.widgets import Select, TextArea

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


async def _read_blueprint(root):
    from novelizer.canon.projector import Projector
    from novelizer.canon.read_store import ReadStore

    path = str(StoryDirectory(root=root).db_path)
    events = EventStore(path); await events.init()
    proj = Projector(events, path); await proj.init()
    read = ReadStore(path); await read.init()
    try:
        await proj.catch_up()
        return await read.get_active_blueprint()
    finally:
        await read.close(); await proj.close(); await events.close()


async def test_frame_fields_present_on_new_story_form(tmp_path):
    app = StoryPickerApp([], stories_dir=tmp_path)
    async with app.run_test(size=(80, 50)) as pilot:
        app.query_one("#stories", OptionList).highlighted = 0
        await pilot.press("enter")
        assert app.query_one("#new_framework", Select) is not None
        assert app.query_one("#new_target_chapters", Input) is not None
        assert app.query_one("#new_genre", Input) is not None
        app.exit(None)


async def test_create_with_framing_adopts_blueprint(tmp_path):
    app = StoryPickerApp([], stories_dir=tmp_path)
    async with app.run_test(size=(80, 50)) as pilot:
        app.query_one("#stories", OptionList).highlighted = 0
        await pilot.press("enter")
        app.query_one("#new_name", Input).value = "Framed Tale"
        app.query_one("#new_framework", Select).value = "six-position"
        app.query_one("#new_target_chapters", Input).value = "30"
        app.query_one("#new_genre", Input).value = "noir"
        await app._create()
    root = app.return_value
    assert root is not None
    blueprint = await _read_blueprint(root)
    assert blueprint is not None
    assert blueprint.framework == "six-position"
    assert blueprint.target_chapter_count == 30
    assert blueprint.genre == "noir"


async def test_create_without_framing_has_no_blueprint(tmp_path):
    app = StoryPickerApp([], stories_dir=tmp_path)
    async with app.run_test(size=(80, 50)) as pilot:
        app.query_one("#stories", OptionList).highlighted = 0
        await pilot.press("enter")
        app.query_one("#new_name", Input).value = "Unframed Tale"
        await app._create()
    root = app.return_value
    assert root is not None
    blueprint = await _read_blueprint(root)
    assert blueprint is None


async def test_create_with_unparseable_target_shows_error_and_stays_open(tmp_path):
    app = StoryPickerApp([], stories_dir=tmp_path)
    async with app.run_test(size=(80, 50)) as pilot:
        app.query_one("#stories", OptionList).highlighted = 0
        await pilot.press("enter")
        app.query_one("#new_name", Input).value = "Bad Target"
        app.query_one("#new_framework", Select).value = "six-position"
        app.query_one("#new_target_chapters", Input).value = "not-a-number"
        await app._create()
        assert "target chapters must be a number" in str(
            app.query_one("#picker_error", Static).renderable
        )
        app.exit(None)
    assert app.return_value is None


async def test_unparseable_target_leaves_no_story_dir_so_retry_succeeds(tmp_path):
    app = StoryPickerApp([], stories_dir=tmp_path)
    async with app.run_test(size=(80, 50)) as pilot:
        app.query_one("#stories", OptionList).highlighted = 0
        await pilot.press("enter")
        app.query_one("#new_name", Input).value = "Retry Tale"
        app.query_one("#new_framework", Select).value = "six-position"
        app.query_one("#new_target_chapters", Input).value = "24 ch"
        await app._create()
        assert "target chapters must be a number" in str(
            app.query_one("#picker_error", Static).renderable
        )
        # The story must not have been created on the failed attempt, so a
        # retry with a corrected target doesn't hit the root.exists() guard.
        assert not (tmp_path / "retry-tale").exists()
        app.query_one("#new_target_chapters", Input).value = "24"
        await app._create()
    root = app.return_value
    assert root is not None
    blueprint = await _read_blueprint(root)
    assert blueprint is not None
    assert blueprint.target_chapter_count == 24


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
