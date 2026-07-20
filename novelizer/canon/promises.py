from __future__ import annotations
import re

TERMINAL_PROMISE_STATES: set[str] = {"paid", "released"}

_SLUG_RE = re.compile(r"[^a-z0-9]+")


def slugify_promise_name(name: str) -> str:
    """Turn a freeform promise name into a stable, id-safe slug.

    Lowercases, collapses runs of non-alphanumeric characters into single
    hyphens, and strips leading/trailing hyphens. Called exactly once, at
    promise.made time, to mint a promise's aggregate_id from the Author's
    or Editor's freeform name — see M3.1's thread identity rule (mirrored
    here): no other promise.* event type ever mints or re-derives an id.
    """
    slug = _SLUG_RE.sub("-", name.lower()).strip("-")
    return slug or "promise"
