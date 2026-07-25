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
from tui_kit.fleet_model import (
    FleetState, blocks as fleet_blocks, fleet_vitals, primary_state, set_run_state,
)
from tui_kit.run_model import LiveRunState
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
        # render_live is called with the whole current fleet every time, from
        # both the bus loop and the 0.5s refresh loop. _forwarded is how many
        # merged blocks the StreamView already owns, so re-stating updates
        # rather than duplicates. Blocks mutate anywhere in the list (a tool
        # call several agents back finishes), so the whole forwarded span is
        # re-stated -- _reconcile is the thing that decides what actually
        # changed.
        self._fleet = FleetState()
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

    def render_live(self, state: FleetState | LiveRunState, now: float | None = None,
                    holds: str = "") -> None:
        """`state` is the whole fleet. A single LiveRunState is accepted too
        and is merged in as that agent's current run -- a new run appends
        after the previous one rather than replacing it, because the unified
        stream is a history, not a per-run pane.

        `holds` is the fleet-wide reason nothing is producing; it captions the
        vitals line only when nothing is running.
        """
        now = time.monotonic() if now is None else now
        if isinstance(state, LiveRunState):
            self._fleet = set_run_state(self._fleet, state.agent_name, state)
        else:
            self._fleet = state
        self.query_one("#er_vitals", Static).update(
            fleet_vitals(self._fleet, now, self._theme, holds))
        merged = fleet_blocks(self._fleet)
        self.query_one("#er_stream", StreamView).sync_tail(merged, self._forwarded)
        self._forwarded = len(merged)
        prompt = primary_state(self._fleet).prompt
        self.query_one("#er_prompt", Static).update(prompt or "(no call in flight)")

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
