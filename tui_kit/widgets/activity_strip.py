from __future__ import annotations
from textual.widgets import Static
from tui_kit.contracts import AgentTheme
from tui_kit.run_model import LiveRunState, strip_line


class ActivityStrip(Static):
    """One-line ambient machinery status."""

    def __init__(self, *args, theme: AgentTheme, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        self._theme = theme

    def render_state(self, state: LiveRunState, now: float, next_hint: str = "") -> None:
        self.update(strip_line(state, now, self._theme, next_hint))
