from __future__ import annotations
from textual.widgets import Static
from novelizer.tui.widgets.engine_room_model import LiveRunState, strip_line


class ActivityStrip(Static):
    """One-line ambient machinery status docked in Mission Control."""

    def render_state(self, state: LiveRunState, now: float, next_hint: str = "") -> None:
        self.update(strip_line(state, now, next_hint))
