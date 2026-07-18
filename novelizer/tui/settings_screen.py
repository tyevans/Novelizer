from __future__ import annotations

from textual.app import ComposeResult
from textual.screen import Screen
from textual.widgets import DataTable, Footer, Input, Static

from novelizer.settings import apply_edit, build_settings_rows, load_layer_configs
from novelizer.settings.view_model import _SECRET_KEYS
from novelizer.settings.setup_core import probe_endpoint
from novelizer.settings.story_dir import StoryDirectory


class SettingsScreen(Screen):
    """Read/edit settings. Edits write TOML files only; the app's settings
    watcher applies them to the runtime (single apply path)."""

    BINDINGS = [
        ("escape", "dismiss_screen", "Back"),
        ("t", "test_connection", "Test connection"),
    ]

    REFRESH_INTERVAL: float = 1.0

    def __init__(self, story_dir: StoryDirectory, effective_getter, probe=probe_endpoint) -> None:
        super().__init__()
        self._story_dir = story_dir
        self._effective = effective_getter
        self._probe = probe
        self._rows = []

    def compose(self) -> ComposeResult:
        yield Static(
            "Settings — edits write config files; safe changes apply live; "
            "voice & temperature affect the next draft",
            id="settings_title",
        )
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
        self.set_interval(self.REFRESH_INTERVAL, self.refresh_rows)

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

    def on_data_table_row_selected(self, event: DataTable.RowSelected) -> None:
        self._begin_edit(event.cursor_row)

    def _begin_edit(self, row_index: int) -> None:
        if not (0 <= row_index < len(self._rows)):
            return
        row = self._rows[row_index]
        msg = self.query_one("#settings_msg", Static)
        if not row.editable:
            msg.update(f"{row.key} is set by NOVELIZER_{row.key.upper()} — read only here")
            return
        self._editing_key = row.key
        box = self.query_one("#edit_value", Input)
        box.display = True
        box.password = row.key in _SECRET_KEYS
        box.value = "" if row.value == "••••••" else row.value
        msg.update(f"editing {row.key} ({row.scope}) — empty clears a story override")
        box.focus()

    def on_input_submitted(self, event: Input.Submitted) -> None:
        if event.input.id != "edit_value" or not getattr(self, "_editing_key", None):
            return
        msg = self.query_one("#settings_msg", Static)
        try:
            outcome = apply_edit(self._editing_key, event.value, story_dir=self._story_dir)
        except ValueError as e:
            msg.update(str(e))
            return
        self._editing_key = None
        event.input.display = False
        event.input.value = ""
        self.refresh_rows()
        msg.update(f"✓ {outcome} — watcher will apply it")
        self.query_one("#settings_table", DataTable).focus()

    def action_test_connection(self) -> None:
        self.run_worker(self._run_probe(), exclusive=True)

    async def _run_probe(self) -> None:
        effective = self._effective()
        result = await self._probe(effective.llm_base_url, api_key=effective.llm_api_key)
        msg = self.query_one("#settings_msg", Static)
        if result.ok:
            msg.update(f"✓ connected — models: {', '.join(result.models) or '(none reported)'}")
        else:
            msg.update(f"✗ {result.error}")
