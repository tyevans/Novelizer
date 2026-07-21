"""Review and clear escalated flags. Auto-clear on resolution happens
upstream (owning agents, see retconner._decline/commit); this screen is for
human-initiated clears and for judging critical/repeatedly-failing issues
with full timeline context."""
from __future__ import annotations

from textual.app import ComposeResult
from textual.containers import Horizontal, Vertical
from textual.screen import Screen
from textual.widgets import Button, DataTable, Footer, Input, Static

from novelizer.canon.events import EventType
from novelizer.store.models import Flag
from novelizer.tui.widgets.escalations_model import escalated_flags, escalation_timeline


class EscalationsScreen(Screen):
    """Lists escalated flags with a detail/timeline pane and a "clear
    escalation" action. Mirrors ResearchScreen/ExportScreen: takes the
    running Runtime (runtime.read / runtime.events / runtime.committer),
    not individually-injected stores."""

    BINDINGS = [("escape", "dismiss_screen", "Back")]

    def __init__(self, runtime) -> None:
        super().__init__()
        self.runtime = runtime
        self._flags: list[Flag] = []
        self._selected: Flag | None = None

    def compose(self) -> ComposeResult:
        with Horizontal():
            with Vertical(id="escalations-list-pane"):
                yield DataTable(id="escalations-table")
            with Vertical(id="escalations-detail-pane"):
                yield Static(id="escalations-detail")
                yield Static(id="escalations-related")
                yield Input(placeholder="Clear note (optional)", id="escalations-clear-note")
                yield Button("Clear escalation", id="escalations-clear-button")
        yield Footer()

    async def on_mount(self) -> None:
        table = self.query_one("#escalations-table", DataTable)
        table.cursor_type = "row"
        table.add_columns("Severity", "Category", "Description")
        await self.refresh_rows()
        table.focus()

    async def refresh_rows(self) -> None:
        self._flags = await escalated_flags(self.runtime.read)
        table = self.query_one("#escalations-table", DataTable)
        table.clear()
        for flag in self._flags:
            table.add_row(flag.severity or "-", flag.category, flag.description[:60], key=flag.id)
        self._selected = None
        self.query_one("#escalations-detail", Static).update("")
        self.query_one("#escalations-related", Static).update("")

    async def on_data_table_row_selected(self, event: DataTable.RowSelected) -> None:
        flag_id = event.row_key.value
        self._selected = next((f for f in self._flags if f.id == flag_id), None)
        if self._selected is None:
            return
        timeline = await escalation_timeline(self.runtime.events, flag_id)
        lines = "\n".join(f"{t.created_at}  {t.summary}" for t in timeline)
        self.query_one("#escalations-detail", Static).update(
            f"{self._selected.description}\n\nTimeline:\n{lines}"
        )
        related = ", ".join(self._selected.related_entry_ids) or "(none)"
        self.query_one("#escalations-related", Static).update(f"Related entries: {related}")

    async def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id != "escalations-clear-button" or self._selected is None:
            return
        cleared = self._selected.model_copy(update={"escalated": False})
        await self.runtime.committer.commit(
            "human", EventType.FLAG_ESCALATION_CLEARED, cleared.id, cleared,
        )
        self.query_one("#escalations-clear-note", Input).value = ""
        await self.refresh_rows()

    def action_dismiss_screen(self) -> None:
        self.app.pop_screen()
