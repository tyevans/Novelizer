"""Canonicalisation for agent- and LLM-supplied canon ids.

Every `slugify_*_name` mints lowercase, hyphen-joined ids, so an id that arrives
citing canon in another shape is a citation of a real thing in the wrong spelling
-- not an unknown id. Normalising is therefore about reading a citation
correctly, and it has to happen in exactly one place: this rule used to live in
`intents.py` only, applied by the commit helpers, while callers' own membership
and dedupe guards compared raw model output. The two disagreed, so a citation
could fail a guard and be escalated to a human, or pass a dedupe it should have
failed and be committed twice.

Applies to *citing* ids only. Minting still goes through the `slugify_*_name`
functions, which own the stronger rule (collapsing punctuation runs, fallbacks).
"""
from __future__ import annotations


def normalize_id(raw: str) -> str:
    """Canonicalise a cited canon id for comparison and storage."""
    return raw.strip().lower()
