"""Pure feed rendering: StoredEvent -> rich.text.Text.

Same seam and testability as the other *_model.py modules — no Textual
imports, no I/O, unit-testable without a terminal.
"""
from __future__ import annotations

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
