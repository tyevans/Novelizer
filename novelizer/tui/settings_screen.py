from __future__ import annotations

from textual.app import ComposeResult
from textual.screen import Screen
from textual.widgets import DataTable, Footer, Input, Static

from novelizer.settings import build_settings_rows, load_layer_configs
from novelizer.settings.setup_core import probe_endpoint
from novelizer.settings.story_dir import StoryDirectory


class SettingsScreen(Screen):
    """Read/edit settings. Edits write TOML files only; the app's settings
    watcher applies them to the runtime (single apply path)."""

    BINDINGS = [
        ("escape", "dismiss_screen", "Back"),
        ("t", "test_connection", "Test connection"),
    ]

    def __init__(self, story_dir: StoryDirectory, effective_getter, probe=probe_endpoint) -> None:
        super().__init__()
        self._story_dir = story_dir
        self._effective = effective_getter
        self._probe = probe
        self._rows = []

    def compose(self) -> ComposeResult:
        yield Static("Settings — edits write config files; safe changes apply live", id="settings_title")
        table = DataTable(id="settings_table")
        yield table
        yield Input(id="edit_value", placeholder="new value (empty clears a story override)")
        yield Static("", id="settings_msg")
        yield Footer()

    def on_mount(self) -> None:
        self.query_one("#edit_value", Input).display = False
        table = self.query_one("#settings_table", DataTable)
        table.cursor_type = "row"
        table.add_columns("Setting", "Value", "Source", "Scope", "Notes")
        self.refresh_rows()
        table.focus()

    def refresh_rows(self) -> None:
        global_cfg, story_cfg, env = load_layer_configs(self._story_dir)
        self._rows = build_settings_rows(global_cfg, story_cfg, env, self._effective())
        table = self.query_one("#settings_table", DataTable)
        table.clear()
        for row in self._rows:
            notes = []
            if row.scope == "story" and row.source in ("default", "global"):
                notes.append("(inherited)")
            if row.source == "env":
                notes.append("(env — read only)")
            if row.restart_required:
                notes.append("(restart required)")
            table.add_row(row.key, row.value, row.source, row.scope, " ".join(notes))

    def action_dismiss_screen(self) -> None:
        self.app.pop_screen()

    def action_test_connection(self) -> None:
        pass  # Task 6
