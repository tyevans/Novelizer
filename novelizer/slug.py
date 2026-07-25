"""The one slug rule: freeform human text -> a lowercase, hyphen-joined token.

Every aggregate that mints an id, the canon_fs path builder, and story-directory
discovery all need the same transformation, and each had grown its own copy of
it. Copies of a rule nothing compares drift silently: an eighth minter that kept
dots, or a seventh that started keeping underscores, would have broken no test
while quietly splitting canon ids and filenames into two dialects.

Deliberately *not* in `novelizer.canon.ids`: that module is scoped to reading a
*cited* id and says so, leaving the stronger minting rule to the `slugify_*_name`
functions. Two callers here mint filesystem names rather than canon ids at all,
so the shared mechanics sit above canon, beside `text_chunk`, where canon,
canon_fs and settings can each reach it without importing one another.

`fallback` is required, not defaulted: it is the one part of the rule that
legitimately differs per caller ("thread", "untitled", "story"), and a default
is how every aggregate ends up minting the same word for an unslugifiable name.
"""
from __future__ import annotations

import re

# Anything outside [a-z0-9] is a separator. Applied after lowercasing, so an
# uppercase ASCII letter survives while a non-ASCII one (é, М) does not -- ids
# and filenames stay ASCII-safe rather than merely lowercase.
_SLUG_RE = re.compile(r"[^a-z0-9]+")


def slugify(text: str, fallback: str) -> str:
    """Lowercase `text`, collapse runs of non-alphanumerics into single hyphens,
    and trim hyphens from both ends. Returns `fallback` if nothing survives."""
    slug = _SLUG_RE.sub("-", text.lower()).strip("-")
    return slug or fallback
