"""EngineRoom: live vitals, one unified token stream, and a durable trace.

There used to be a TabbedContent here with one TabPane per agent. The tab
bar stopped fitting at thirteen agents, and -- worse -- the design
structurally hid the one thing this view exists to show: that two agents
are running at the same time. The stream is now a single StreamView with
agent filter chips; the roster arrives as data via set_agents().
"""
from __future__ import annotations
import time
from rich.markup import escape
from textual.app import ComposeResult
from textual.containers import Vertical
from textual.widgets import DataTable, Static
from tui_kit.contracts import AgentTheme
from tui_kit.run_model import LiveRunState, styled_vitals
from tui_kit.stream_source import StreamSource
from tui_kit.widgets.stream_view import StreamView


class EngineRoom(Vertical):
    """The thick machinery view: live vitals + one interleaved token stream
    on top, the durable trace below (rows filled by the caller), prompt pane
    toggleable (off by default)."""

    def __init__(self, theme: AgentTheme, source: StreamSource, *args, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        self._theme = theme
        self._source = source
        # render_live is called with the *whole* current run every time, from
        # both the bus loop and the 0.5s refresh loop. These two track which
        # run we are mid-forwarding and how many of its blocks the StreamView
        # already owns, so re-stating the run updates rather than duplicates.
        self._run_id: str = ""
        self._forwarded: int = 0

    def compose(self) -> ComposeResult:
        # markup=False throughout: these panes show raw prompts, token streams,
        # and payload text -- untrusted content full of "[...]" sequences that
        # Textual's markup parser rejects (MarkupError crashes the caller
        # otherwise).
        yield Static("idle — waiting for the scheduler", id="er_vitals",
                     classes="er-vitals", markup=False)
        yield StreamView(theme=self._theme, source=self._source, id="er_stream",
                         classes="er-stream")
        yield Static("", id="er_prompt", markup=False)
        yield DataTable(id="er_trace", cursor_type="row")
        yield Static("", id="er_detail", markup=False)

    def on_mount(self) -> None:
        self.query_one("#er_prompt", Static).display = False
        self.query_one("#er_detail", Static).display = False
        table = self.query_one("#er_trace", DataTable)
        table.add_column("machinery", key="line", width=110)

    # -- live pane -----------------------------------------------------------

    def set_agents(self, names: list[str]) -> None:
        """The roster is data, not structure -- that is the whole point of
        replacing the tabs."""
        self.query_one("#er_stream", StreamView).set_agents(names)

    def render_live(self, state: LiveRunState, now: float | None = None) -> None:
        now = time.monotonic() if now is None else now
        self.query_one("#er_vitals", Static).update(styled_vitals(state, now, self._theme))
        if state.run_id != self._run_id:
            # A new run appends after the previous one rather than replacing
            # it: the unified stream is a history, not a per-run pane.
            self._run_id = state.run_id
            self._forwarded = 0
        self.query_one("#er_stream", StreamView).sync_tail(state.blocks, self._forwarded)
        self._forwarded = len(state.blocks)
        self.query_one("#er_prompt", Static).update(state.prompt or "(no call in flight)")

    def stream_text(self) -> str:
        """Plain text of everything currently mounted in the stream window.
        A convenience for callers and tests; the widgets are the truth."""
        stream = self.query_one("#er_stream", StreamView)
        return "\n".join(str(w.renderable) for w in stream.query("#sv_window Static"))

    def toggle_prompt(self) -> bool:
        pane = self.query_one("#er_prompt", Static)
        pane.display = not pane.display
        return pane.display

    # -- trace pane (rows managed by the caller) ------------------------------

    def set_trace_rows(self, rows: list[tuple[str, str]]) -> None:
        """rows: (row_key, rendered_line), newest first."""
        table = self.query_one("#er_trace", DataTable)
        table.clear()
        for key, line in rows:
            # DataTable runs str cells through Text.from_markup; trace lines
            # carry untrusted text (tool summaries, error messages), so escape.
            table.add_row(escape(line), key=key)

    def show_detail(self, text: str) -> None:
        detail = self.query_one("#er_detail", Static)
        detail.update(text)
        detail.display = True
