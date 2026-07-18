from __future__ import annotations

from pathlib import Path

from textual.app import App, ComposeResult
from textual.widgets import Footer, Header, Input, OptionList, Static
from textual.widgets.option_list import Option

from novelizer.settings.discovery import StoryMeta, order_stories, slugify
from novelizer.settings.story_dir import create_story

_NEW_STORY_ID = "__new__"


class StoryPickerApp(App[Path | None]):
    """Pick an existing story or create a new one.

    run() returns the chosen story root, or None if the user quit.
    Ordering/slug logic lives in settings.discovery; this is the TUI shell.
    """

    TITLE = "Novelizer — Choose a story"
    BINDINGS = [("q", "quit", "Quit")]
    CSS = """
    #stories {
        height: auto;
        max-height: 10;
    }
    #picker_error {
        height: 1;
    }
    """

    def __init__(
        self,
        stories: list[StoryMeta],
        stories_dir: Path,
        last_opened: str | None = None,
    ) -> None:
        super().__init__()
        self._stories = order_stories(stories, last_opened)
        self._stories_dir = stories_dir

    def compose(self) -> ComposeResult:
        yield Header()
        options = [Option("➕  New story", id=_NEW_STORY_ID)]
        options += [Option(f"{s.title}  ({s.root})", id=str(s.root)) for s in self._stories]
        option_list = OptionList(*options, id="stories")
        yield option_list
        yield Input(id="new_name", placeholder="New story name…")
        yield Static("", id="picker_error")
        yield Footer()

    def on_mount(self) -> None:
        self.query_one("#new_name", Input).display = False
        option_list = self.query_one("#stories", OptionList)
        # Preselect the last-opened story (index 1) when present, else "new story".
        option_list.highlighted = 1 if self._stories else 0
        option_list.focus()

    def on_option_list_option_selected(self, event: OptionList.OptionSelected) -> None:
        if event.option.id == _NEW_STORY_ID:
            name_input = self.query_one("#new_name", Input)
            name_input.display = True
            name_input.focus()
        else:
            self.exit(Path(event.option.id))

    def on_input_submitted(self, event: Input.Submitted) -> None:
        if event.input.id != "new_name":
            return
        name = event.value.strip()
        if not name:
            self.query_one("#picker_error", Static).update("✗ name required")
            return
        root = self._stories_dir / slugify(name)
        if root.exists():
            self.query_one("#picker_error", Static).update(f"✗ {root} already exists")
            return
        create_story(root, title=name)
        self.exit(root)
