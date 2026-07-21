"""EPUB export as a modal drill-in, reachable only from the command
palette (AppCommand "export_epub" in app.py). Mirrors ApprovalScreen's
shape: a small form, a confirm action, escape to close."""
from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

from textual.app import ComposeResult
from textual.containers import Vertical
from textual.screen import ModalScreen
from textual.widgets import Button, Input, Select, Static

from novelizer.export.epub import build_epub
from novelizer.settings.discovery import slugify


class ExportScreen(ModalScreen):
    """Prompts for title/author/status, then writes an .epub under
    <story_root>/export/. runtime.read must already be initialized
    (true for every screen pushed from the running app)."""

    BINDINGS = [("escape", "close", "Close")]

    def __init__(self, runtime) -> None:
        super().__init__()
        self.runtime = runtime
        self.title_value = runtime.settings.story_title or Path(runtime.settings.db_path).parent.name
        self.author_value = ""
        self.status_value = "final"
        self._error: str | None = None

    def compose(self) -> ComposeResult:
        with Vertical(id="export_box") as box:
            box.border_title = "EXPORT EPUB"
            yield Input(value=self.title_value, placeholder="Title", id="export_title")
            yield Input(value=self.author_value, placeholder="Author", id="export_author")
            yield Select(
                [("Final only", "final"), ("All chapters", "all")],
                id="export_status",
                allow_blank=False,
                value=self.status_value,
            )
            yield Static("", id="export_error")
            yield Button("Export", id="export_confirm")

    def action_close(self) -> None:
        self.dismiss()

    def on_input_changed(self, event: Input.Changed) -> None:
        if event.input.id == "export_title":
            self.title_value = event.value
        elif event.input.id == "export_author":
            self.author_value = event.value

    def on_select_changed(self, event: Select.Changed) -> None:
        if event.select.id == "export_status":
            self.status_value = event.value

    async def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "export_confirm":
            await self.do_export()

    async def do_export(self) -> None:
        status = None if self.status_value == "all" else self.status_value
        chapters = await self.runtime.read.list_chapters(status=status)
        if not chapters:
            self._set_error(f"No chapters found for status={self.status_value!r}.")
            return

        try:
            data = build_epub(chapters, title=self.title_value, author=self.author_value)
        except ValueError as e:
            self._set_error(str(e))
            return

        story_root = Path(self.runtime.settings.db_path).parent
        export_dir = story_root / "export"
        export_dir.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
        slug = slugify(self.title_value)
        final_path = export_dir / f"{slug}-{self.status_value}-{stamp}.epub"
        tmp_path = final_path.with_suffix(".epub.tmp")
        try:
            tmp_path.write_bytes(data)
            tmp_path.rename(final_path)
        except OSError as e:
            self._set_error(f"write failed: {e}")
            return

        self.dismiss()
        message = f"» exported {len(chapters)} chapters → {final_path}"
        try:
            from textual.widgets import RichLog

            self.app.query_one("#feed", RichLog).write(message)
        except Exception:
            pass
        self.app.messages.append(message)

    def _set_error(self, text: str) -> None:
        self._error = text
        try:
            self.query_one("#export_error", Static).update(text)
        except Exception:
            pass
