"""Pure feed rendering: StoredEvent -> rich.text.Text.

Same seam and testability as the other *_model.py modules — no Textual
imports, no I/O, unit-testable without a terminal.
"""
from __future__ import annotations

import re
import textwrap

from rich.text import Text

from novelizer.agents.editor import VOICE_SOURCE_TAG
from novelizer.brain.leaks import LEAK_SOURCE_TAG
from novelizer.brain.mining import MINED_SOURCE_TAG
from novelizer.brain.paradoxes import PARADOX_SOURCE_TAG

# The four alarm sources map to short badges instead of printing
# "[source: voice_drift]" raw in the feed. Keys are the imported constants —
# if a tag string ever changes at its source, the mapping follows.
SOURCE_BADGES: dict[str, str] = {
    VOICE_SOURCE_TAG: "[drift]",
    LEAK_SOURCE_TAG: "[leak]",
    PARADOX_SOURCE_TAG: "[paradox]",
    MINED_SOURCE_TAG: "[mined]",
}


def parse_source_badge(description: str) -> tuple[str | None, str]:
    """Split a retcon description into (badge, remaining text).

    A description prefixed by a known *_SOURCE_TAG yields its short badge and
    the text with the tag stripped; anything else yields (None, description)
    untouched — unknown tags stay visible rather than being silently eaten.
    """
    for tag, badge in SOURCE_BADGES.items():
        if description.startswith(tag):
            return badge, description[len(tag):].lstrip()
    return None, description


CLAMP_WIDTH = 76
CLAMP_LINES = 2

_BOLD_RE = re.compile(r"\*\*(.+?)\*\*")


def clamp_text(s: str, width: int = CLAMP_WIDTH, max_lines: int = CLAMP_LINES) -> tuple[str, bool]:
    """Collapse whitespace and clamp to at most max_lines lines of at most
    width chars. Returns (clamped, truncated). The feed is a pulse, not a
    document — the full text is always one selection away in the detail pane.
    """
    collapsed = " ".join(s.split())
    if not collapsed:
        return "", False
    lines = textwrap.wrap(collapsed, width=width, break_long_words=True, break_on_hyphens=False)
    if len(lines) <= max_lines:
        return "\n".join(lines), False
    return "\n".join(lines[:max_lines]), True


def md_inline(s: str) -> Text:
    """Render inline markdown bold (**x** -> bold span); never show ** raw.

    Only balanced ** pairs are treated as markup; anything unpaired stays
    literal, so stray asterisks in agent prose survive.
    """
    text = Text()
    pos = 0
    for m in _BOLD_RE.finditer(s):
        text.append(s[pos:m.start()])
        text.append(m.group(1), style="bold")
        pos = m.end()
    text.append(s[pos:])
    return text
