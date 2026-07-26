"""Resolve a freeform speaker name to a character id.

Follows the name+alias lowercase map already used by
novelizer/store/kg_projector.py, with slugify_character_name as a fallback so a
spacing or punctuation variant still lands on the right character.

Never mints an id: an unresolvable speaker returns None and is flagged
upstream. Inventing a character here would let a typo in prose create canon.
"""
from __future__ import annotations

from novelizer.canon.characters import slugify_character_name


def build_name_index(characters) -> dict[str, str]:
    """Map every lowercased canonical name and alias to its character id."""
    index: dict[str, str] = {}
    for character in characters:
        index[character.name.lower()] = character.id
        for alias in character.aliases:
            index[alias.lower()] = character.id
    return index


def resolve_speaker(name: str, index: dict[str, str]) -> str | None:
    """Return the character id for `name`, or None if it cannot be resolved."""
    cleaned = name.strip()
    if not cleaned:
        return None
    direct = index.get(cleaned.lower())
    if direct is not None:
        return direct
    slug = slugify_character_name(cleaned)
    return slug if slug in set(index.values()) else None
