"""Pure proposal rendering: open-proposal records -> the banner line and
(Task 4) the approval modal's rows and payload context. No Textual imports,
no I/O — same seam as the other *_model.py modules."""
from __future__ import annotations

from rich.text import Text

# The one high-contrast line on the dashboard (spec Zone 3: "then it is the
# most visible thing on screen").
BANNER_STYLE = "bold black on gold3"


def banner_line(count: int) -> Text:
    """'▼ 2 proposals awaiting approval — press a'. Only called with
    count >= 1 — the app hides the banner widget entirely when the queue is
    empty (zero rows spent)."""
    noun = "proposal" if count == 1 else "proposals"
    return Text(f"▼ {count} {noun} awaiting approval — press a", style=BANNER_STYLE)
