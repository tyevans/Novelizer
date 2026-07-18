from __future__ import annotations
import re

_SLUG_RE = re.compile(r"[^a-z0-9]+")

TERMINAL_STATES: set[str] = {"paid_off", "abandoned"}


def slugify_thread_name(name: str) -> str:
    """Turn a freeform thread name into a stable, id-safe slug.

    Lowercases, collapses runs of non-alphanumeric characters into single
    hyphens, and strips leading/trailing hyphens. Called exactly once, at
    thread.planted time, to mint a thread's aggregate_id from the Author's
    or Editor's freeform name — see M3.1's thread identity rule: no other
    thread.* event type ever mints or re-derives an id.
    """
    slug = _SLUG_RE.sub("-", name.strip().lower()).strip("-")
    return slug or "thread"
