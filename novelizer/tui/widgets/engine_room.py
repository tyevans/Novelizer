"""The Engine Room: live token stream, vitals, and durable trace.

The stream body is a Static inside a VerticalScroll, not a RichLog: a
RichLog renders one line per write() call, which would put every
streamed token on its own line.
"""
from __future__ import annotations
import time
from rich.markup import escape
from textual.app import ComposeResult
from textual.content import Content
from textual.containers import Vertical, VerticalScroll
from textual.widgets import DataTable, Static, TabbedContent, TabPane
from novelizer.tui.identity import identity_for
from novelizer.tui.widgets.engine_room_model import (
    AGENT_NAMES, LiveRunState, live_body, styled_body, styled_vitals,
)


class EngineRoom(Vertical):
    """The thick machinery view: live vitals + token stream on top (an "All"
    tab plus one tab per agent so concurrent runs don't clobber each other),
    the durable trace below (rows filled by the app), prompt pane toggleable
    with `p` (off by default)."""

    _rendered_body: dict[str, str]

    def __init__(self, *args, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        self._rendered_body = {}

    def compose(self) -> ComposeResult:
        # markup=False throughout: these panes show raw prompts, token streams,
        # and payload text — untrusted content full of "[...]" sequences that
        # Textual's markup parser rejects (MarkupError crashes the telemetry
        # loops otherwise).
        with TabbedContent(id="er_tabs"):
            with TabPane("All", id="er_tab_all"):
                yield Static("idle — waiting for the scheduler", id="er_vitals",
                            classes="er-vitals", markup=False)
                with VerticalScroll(id="er_stream_scroll", classes="er-stream-scroll"):
                    yield Static("", id="er_stream", classes="er-stream", markup=False)
                yield Static("", id="er_prompt", markup=False)
            for agent_name in AGENT_NAMES:
                ident = identity_for(agent_name)
                # A plain str title is markup-parsed by TabPane (Widget.render_str
                # -> Content.from_markup), which silently drops any style not
                # spelled out as markup tags -- pass a pre-styled Content instead
                # so the tab title actually carries the agent's color.
                title = Content.styled(f"{ident.glyph} {ident.label}", ident.style)
                with TabPane(title, id=f"er_tab_{agent_name}"):
                    yield Static("idle — waiting for the scheduler",
                                id=f"er_vitals_{agent_name}", classes="er-vitals", markup=False)
                    with VerticalScroll(id=f"er_stream_scroll_{agent_name}",
                                       classes="er-stream-scroll"):
                        yield Static("", id=f"er_stream_{agent_name}",
                                    classes="er-stream", markup=False)
        yield DataTable(id="er_trace", cursor_type="row")
        yield Static("", id="er_detail", markup=False)

    def on_mount(self) -> None:
        self.query_one("#er_prompt", Static).display = False
        self.query_one("#er_detail", Static).display = False
        table = self.query_one("#er_trace", DataTable)
        table.add_column("machinery", key="line", width=110)

    # -- live pane -----------------------------------------------------------

    def render_live(self, state: LiveRunState, now: float | None = None) -> None:
        now = time.monotonic() if now is None else now
        self.query_one("#er_vitals", Static).update(styled_vitals(state, now))
        body = live_body(state)
        if body != self._rendered_body.get("__all__"):
            self.query_one("#er_stream", Static).update(styled_body(body))
            self._rendered_body["__all__"] = body
            self.query_one("#er_stream_scroll", VerticalScroll).scroll_end(animate=False)
        self.query_one("#er_prompt", Static).update(state.prompt or "(no call in flight)")

    def render_agent_live(self, agent_name: str, state: LiveRunState,
                          now: float | None = None) -> None:
        now = time.monotonic() if now is None else now
        self.query_one(f"#er_vitals_{agent_name}", Static).update(styled_vitals(state, now))
        body = live_body(state)
        if body != self._rendered_body.get(agent_name):
            self.query_one(f"#er_stream_{agent_name}", Static).update(styled_body(body))
            self._rendered_body[agent_name] = body
            self.query_one(f"#er_stream_scroll_{agent_name}", VerticalScroll).scroll_end(animate=False)

    def stream_text(self) -> str:
        return self._rendered_body.get("__all__", "")

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
            # DataTable runs str cells through Text.from_markup; trace lines
            # carry untrusted text (tool summaries, error messages), so escape.
            table.add_row(escape(line), key=key)

    def show_detail(self, text: str) -> None:
        detail = self.query_one("#er_detail", Static)
        detail.update(text)
        detail.display = True
