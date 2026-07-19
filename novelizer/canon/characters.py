from __future__ import annotations
import re

_SLUG_RE = re.compile(r"[^a-z0-9]+")


def slugify_character_name(name: str) -> str:
    """Turn a freeform character name into a stable, id-safe slug.

    Lowercases, collapses runs of non-alphanumeric characters into single
    hyphens, and strips leading/trailing hyphens. Called exactly once, at
    character.created time, to mint the character's aggregate_id from the
    Character Keeper's freeform name — no other character.* event type ever
    mints or re-derives an id (same identity rule as threads/secrets/themes).
    """
    slug = _SLUG_RE.sub("-", name.strip().lower()).strip("-")
    return slug or "character"
