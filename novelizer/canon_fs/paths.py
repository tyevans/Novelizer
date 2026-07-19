from __future__ import annotations
import re

from novelizer.store.models import (
    Chapter, Character, SecretRecord, ThemeRecord, ThreadRecord, WorldEntry,
)

_SLUG_RE = re.compile(r"[^a-z0-9]+")


def slugify(text: str) -> str:
    """Lowercase filename-safe slug; never empty ("untitled" fallback)."""
    slug = _SLUG_RE.sub("-", text.lower()).strip("-")
    return slug or "untitled"


def _claim(directory: str, name: str, record_id: str, taken: set[str]) -> str:
    path = f"/{directory}/{slugify(name)}.md"
    if path in taken:
        path = f"/{directory}/{slugify(name)}-{record_id[:8]}.md"
    taken.add(path)
    return path


def build_path_index(
    chapters: list[Chapter],
    characters: list[Character],
    world_entries: list[WorldEntry],
    threads: list[ThreadRecord],
    secrets: list[SecretRecord],
    themes: list[ThemeRecord],
) -> dict[str, tuple[str, str]]:
    """Deterministic virtual-tree map: path -> (kind, record id).

    Chapter ordinals come from list position (ReadStore.list_chapters is
    creation-ordered), so the tree reads in story order.
    """
    index: dict[str, tuple[str, str]] = {}
    taken: set[str] = set()
    for i, ch in enumerate(chapters, start=1):
        path = _claim("chapters", f"{i:03d}-{slugify(ch.title)}", ch.id, taken)
        index[path] = ("chapter", ch.id)
    for kind, directory, records, label in (
        ("character", "characters", characters, lambda r: r.name),
        ("world", "world", world_entries, lambda r: r.title),
        ("thread", "threads", threads, lambda r: r.name),
        ("secret", "secrets", secrets, lambda r: r.title),
        ("theme", "themes", themes, lambda r: r.title),
    ):
        for record in records:
            index[_claim(directory, label(record), record.id, taken)] = (kind, record.id)
    return index
