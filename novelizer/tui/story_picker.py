from __future__ import annotations

from pathlib import Path

from textual.app import App, ComposeResult
from textual.containers import Horizontal, Vertical
from textual.widgets import (
    Button,
    Footer,
    Header,
    Input,
    OptionList,
    Select,
    Static,
    TextArea,
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
        # Select always has a value; _create() then writes no profile override.
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
                profiles,
                id="new_profile",
                allow_blank=False,
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
