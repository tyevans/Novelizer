from __future__ import annotations
from textual.widgets import Static


def roster_line(status_row: dict) -> str:
    name = status_row["name"]
    if status_row.get("paused"):
        return f"· {name}  paused"
    if status_row.get("running"):
        return f"● {name}  running"
    return f"· {name}  idle"


class AgentRoster(Static):
    def update_from(self, status: list) -> None:
        self.update("\n".join(roster_line(s) for s in status) or "no agents")
