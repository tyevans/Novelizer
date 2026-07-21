from __future__ import annotations
import time
from textual.app import ComposeResult
from textual.containers import Vertical, VerticalScroll
from textual.widgets import Static
from novelizer.tui.widgets.engine_room_model import LiveRunState, live_body, styled_body, styled_vitals


class LiveStreamPanel(Vertical):
    """A single-agent live token/tool-call stream, the same rendering
    Engine Room gives each autonomous agent's tab, without the tab strip.
    Owns no bus subscription and no identity — the mounting screen computes
    the LiveRunState for its own key and calls render()."""

    _VITALS_ID = "#lsp_vitals"
    _STREAM_ID = "#lsp_stream"
    _STREAM_SCROLL_ID = "#lsp_stream_scroll"

    def __init__(self, *args, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        self._rendered_body: str = ""

    def compose(self) -> ComposeResult:
        yield Static("idle — waiting for the scheduler",
                     id=self._VITALS_ID.removeprefix("#"), classes="lsp-vitals", markup=False)
        with VerticalScroll(id=self._STREAM_SCROLL_ID.removeprefix("#"), classes="lsp-stream-scroll"):
            yield Static("", id=self._STREAM_ID.removeprefix("#"), classes="lsp-stream", markup=False)

    def render(self, state: LiveRunState | None = None, now: float | None = None):
        if state is None:
            # Textual's internal Widget.render() calls this with no args to
            # get this container's own visual; Vertical paints nothing itself.
            return ""
        now = time.monotonic() if now is None else now
        self.query_one(self._VITALS_ID, Static).update(styled_vitals(state, now))
        body = live_body(state)
        if body != self._rendered_body:
            self.query_one(self._STREAM_ID, Static).update(styled_body(body))
            self._rendered_body = body
            self.query_one(self._STREAM_SCROLL_ID, VerticalScroll).scroll_end(animate=False)
