from __future__ import annotations
import re

TERMINAL_PROMISE_STATES: set[str] = {"paid", "released"}

_SLUG_RE = re.compile(r"[^a-z0-9]+")


def slugify_promise_name(name: str) -> str:
    slug = _SLUG_RE.sub("-", name.lower()).strip("-")
    return slug or "promise"
