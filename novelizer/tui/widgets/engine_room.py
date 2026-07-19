"""The Engine Room: live token stream, vitals, and durable trace.

The stream body is a Static inside a VerticalScroll, not a RichLog: a
RichLog renders one line per write() call, which would put every
streamed token on its own line.
"""
from __future__ import annotations
import time
from textual.app import ComposeResult
from textual.containers import Vertical, VerticalScroll
from textual.widgets import DataTable, Static
from novelizer.tui.widgets.engine_room_model import LiveRunState, live_body, vitals_line


class EngineRoom(Vertical):
    """The thick machinery view: live vitals + token stream on top, the
    durable trace below (rows filled by the app), prompt pane toggleable
    with `p` (off by default)."""

    _rendered_body: str = ""

    def compose(self) -> ComposeResult:
        yield Static("idle — waiting for the scheduler", id="er_vitals")
        with VerticalScroll(id="er_stream_scroll"):
            yield Static("", id="er_stream")
        yield Static("", id="er_prompt")
        yield DataTable(id="er_trace", cursor_type="row")
        yield Static("", id="er_detail")

    def on_mount(self) -> None:
        self.query_one("#er_prompt", Static).display = False
        self.query_one("#er_detail", Static).display = False
        table = self.query_one("#er_trace", DataTable)
        table.add_column("machinery", key="line", width=110)

    # -- live pane -----------------------------------------------------------

    def render_live(self, state: LiveRunState, now: float | None = None) -> None:
        now = time.monotonic() if now is None else now
        self.query_one("#er_vitals", Static).update(vitals_line(state, now))
        body = live_body(state)
        if body != self._rendered_body:
            self.query_one("#er_stream", Static).update(body)
            self._rendered_body = body
            self.query_one("#er_stream_scroll", VerticalScroll).scroll_end(animate=False)
        self.query_one("#er_prompt", Static).update(state.prompt or "(no call in flight)")

    def stream_text(self) -> str:
        return self._rendered_body

    def toggle_prompt(self) -> bool:
        pane = self.query_one("#er_prompt", Static)
        pane.display = not pane.display
        return pane.display

    # -- trace pane (rows managed by the app; see Task 15) --------------------

    def set_trace_rows(self, rows: list[tuple[str, str]]) -> None:
        """rows: (row_key, rendered_line), newest first."""
        table = self.query_one("#er_trace", DataTable)
        table.clear()
        for key, line in rows:
            table.add_row(line, key=key)

    def show_detail(self, text: str) -> None:
        detail = self.query_one("#er_detail", Static)
        detail.update(text)
        detail.display = True
