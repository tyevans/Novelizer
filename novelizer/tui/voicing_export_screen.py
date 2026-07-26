"""Voicing export as a modal drill-in, reachable from the command palette
(AppCommand "export_voicing" in app.py). Mirrors ExportScreen's shape.

write_export() holds all the logic and no widgets so it can be tested without
mounting the app -- TUI harness tests in this repo are load-flaky (see
docs/TESTING-TUI.md)."""
from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

from textual.app import ComposeResult
from textual.containers import Vertical
from textual.screen import ModalScreen
from textual.widgets import Button, Input, Select, Static

from novelizer.export.voicing import build_voicing_export, render_annotated, render_json
from novelizer.settings.discovery import slugify

DEFAULT_CHUNK_SIZE = 800


class VoicingExportScreen(ModalScreen):
    BINDINGS = [("escape", "close", "Close")]

    def __init__(self, runtime) -> None:
        super().__init__()
        self.runtime = runtime
        self.title_value = runtime.settings.story_title or Path(runtime.settings.db_path).parent.name
        self.chunk_by = "budget"
        self.chunk_size = DEFAULT_CHUNK_SIZE
        self.export_format = "json"
        self._error: str | None = None

    def compose(self) -> ComposeResult:
        with Vertical(id="voicing_box") as box:
            box.border_title = "EXPORT FOR VOICING"
            yield Input(value=self.title_value, placeholder="Title", id="voicing_title")
            yield Select(
                [("Voicing JSON", "json"), ("Annotated prose", "annotated")],
                id="voicing_format", allow_blank=False, value=self.export_format,
            )
            yield Select(
                [("Per segment", "segment"), ("Per chapter", "chapter"),
                 ("Packed to budget", "budget")],
                id="voicing_chunk_by", allow_blank=False, value=self.chunk_by,
            )
            yield Input(value=str(self.chunk_size), placeholder="Chunk size (characters)",
                        id="voicing_chunk_size")
            yield Static("", id="voicing_error")
            yield Button("Export", id="voicing_confirm")

    def action_close(self) -> None:
        self.dismiss()

    def on_input_changed(self, event: Input.Changed) -> None:
        if event.input.id == "voicing_title":
            self.title_value = event.value
        elif event.input.id == "voicing_chunk_size":
            self.chunk_size = int(event.value) if event.value.strip().isdigit() else DEFAULT_CHUNK_SIZE

    def on_select_changed(self, event: Select.Changed) -> None:
        if event.select.id == "voicing_chunk_by":
            self.chunk_by = event.value
        elif event.select.id == "voicing_format":
            self.export_format = event.value

    async def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "voicing_confirm":
            path = await self.write_export()
            if path is None:
                return
            self.dismiss()
            message = f"» exported voicing document → {path}"
            try:
                from textual.widgets import RichLog

                self.app.query_one("#feed", RichLog).write(message)
            except Exception:
                pass
            self.app.messages.append(message)

    async def write_export(self) -> Path | None:
        """Build and write the document. Returns its path, or None on error."""
        chapters = await self.runtime.read.list_chapters()
        rows = await self.runtime.read.list_speech_segments()
        if not rows:
            self._set_error(
                "No attributed segments yet — the Attributor has not run on any chapter."
            )
            return None

        by_chapter: dict[str, list] = {c.id: [] for c in chapters}
        for row in rows:
            by_chapter.setdefault(row.chapter_id, []).append(row)

        chunk_by = self.chunk_by
        if self.export_format == "annotated" and chunk_by == "chapter":
            # render_annotated has no speaker to re-wrap on a chapter chunk and
            # refuses rather than silently dropping every tag.
            self._set_error(
                "Annotated prose needs per-segment or budget chunking, not per-chapter."
            )
            return None

        chunks = build_voicing_export(
            chapters, by_chapter, chunk_by=chunk_by, chunk_size=self.chunk_size,
        )
        if self.export_format == "annotated":
            document = render_annotated(chunks)
            suffix = ".txt"
        else:
            document = render_json(chunks, title=self.title_value)
            suffix = ".json"

        story_root = Path(self.runtime.settings.db_path).parent
        export_dir = story_root / "export"
        export_dir.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
        final_path = export_dir / f"{slugify(self.title_value)}-voicing-{stamp}{suffix}"
        tmp_path = final_path.with_suffix(f"{suffix}.tmp")
        try:
            tmp_path.write_text(document, encoding="utf-8")
            tmp_path.rename(final_path)
        except OSError as e:
            self._set_error(f"write failed: {e}")
            return None
        return final_path

    def _set_error(self, text: str) -> None:
        self._error = text
        try:
            self.query_one("#voicing_error", Static).update(text)
        except Exception:
            pass
