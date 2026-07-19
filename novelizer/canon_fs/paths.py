from __future__ import annotations
import re

_SLUG_RE = re.compile(r"[^a-z0-9]+")


def slugify(text: str) -> str:
    """Lowercase filename-safe slug; never empty ("untitled" fallback)."""
    slug = _SLUG_RE.sub("-", text.lower()).strip("-")
    return slug or "untitled"
