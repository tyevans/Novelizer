"""The Story Brain panel: one TabbedContent over the four brain views.

Thin Textual shell — every rendered string/Text comes from the pure
brain_model functions; this includes spark lines, which are model-rendered
text (one block cell per chapter, e.g. "tension  ▃▅▆"). This widget only
fetches ReadStore data once per refresh and places the results. No
selection/targeting inside tabs (Phase 3).
"""
from __future__ import annotations

from rich.console import Group
from rich.text import Text
from textual.app import ComposeResult
from textual.containers import Vertical
from textual.widgets import Static, TabbedContent, TabPane

from novelizer.tui.widgets.brain_model import (
    alarm_strip,
    causeway_tab,
    outline_tab,
    secrets_tab,
    shape_tab,
    threads_tab,
)


def _joined(lines: list[Text]) -> Text:
    return Text("\n").join(lines)


class BrainPanel(Vertical):
    """One panel, four tabs, one persistent alarm strip. Polled by the app's
    _brain_loop once per second; every refresh updates all four tabs plus the
    strip so nothing is missed while another tab is open."""

    def compose(self) -> ComposeResult:
        with TabbedContent(id="brain_tabs"):
            with TabPane("1 Shape", id="tab_shape"):
                yield Static("", id="shape_body")
            with TabPane("2 Threads", id="tab_threads"):
                yield Static("", id="threads_body")
            with TabPane("3 Secrets", id="tab_secrets"):
                yield Static("", id="secrets_body")
            with TabPane("4 Cause", id="tab_causeway"):
                yield Static("", id="causeway_body")
            with TabPane("5 Outline", id="tab_outline"):
                yield Static("", id="outline_body")
        yield Static("", id="brain_strip")

    async def refresh_from(self, read, *, threshold: int, delta: float) -> None:
        """threshold/delta arrive from the app's _brain_loop, which reads
        settings.staleness_threshold_chapters / settings.sag_spike_delta
        every cycle (M5.3 single-sourcing: settings -> pure-function params;
        keyword-only with no defaults so the app cannot forget to pass them)."""
        chapters = await read.list_chapters()  # one snapshot shared by three tabs
        blueprint = await read.get_active_blueprint()
        beats = await read.list_beats()
        shape = shape_tab(
            await read.list_structure_scores(), chapters, delta, blueprint, beats
        )
        secret_records = await read.list_secrets()
        thread_records = await read.list_threads()  # one snapshot shared by threads_tab + outline_tab
        threads = threads_tab(
            thread_records, chapters, await read.list_promises(), secret_records, threshold
        )
        secrets = secrets_tab(
            secret_records, await read.list_characters(), await read.knowledge_matrix()
        )
        cause = causeway_tab(await read.list_causal_edges(), chapters)
        outline = outline_tab(
            blueprint, beats, await read.list_briefs(), thread_records, chapters
        )

        shape_rows = [
            r for r in (shape.spark, shape.target, shape.markers, shape.meta) if r is not None
        ]
        self.query_one("#shape_body", Static).update(Group(*shape_rows, *shape.callouts))
        self.query_one("#threads_body", Static).update(_joined(threads.lines))
        self.query_one("#secrets_body", Static).update(_joined(secrets.lines))
        self.query_one("#causeway_body", Static).update(_joined(cause.lines))
        self.query_one("#outline_body", Static).update(_joined(outline.lines))
        self.query_one("#brain_strip", Static).update(
            alarm_strip(
                shape.alarm_count, threads.alarm_count, secrets.alarm_count, cause.alarm_count,
                outline.alarm_count,
            )
        )

    def activate_tab(self, pane_id: str) -> None:
        self.query_one("#brain_tabs", TabbedContent).active = pane_id
