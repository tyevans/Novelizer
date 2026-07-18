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
        name_input = app.query_one("#new_name", Input)
        name_input.display = True
        await pilot.pause()
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
        name_input = app.query_one("#new_name", Input)
        assert name_input.display  # revealed
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
