from __future__ import annotations

from novelizer.slug import slugify as _slugify
from novelizer.store.models import (
    Chapter, Character, SecretRecord, ThemeRecord, ThreadRecord, WorldEntry,
)


def slugify(text: str) -> str:
    """Lowercase filename-safe slug; never empty ("untitled" fallback)."""
    return _slugify(text, "untitled")


def _claim(directory: str, name: str, record_id: str, taken: set[str]) -> str:
    slug = slugify(name)

    # Try base path
    path = f"/{directory}/{slug}.md"
    if path not in taken:
        taken.add(path)
        return path

    # Try with 8-char ID suffix
    path = f"/{directory}/{slug}-{record_id[:8]}.md"
    if path not in taken:
        taken.add(path)
        return path

    # Fall back to full ID suffix
    path = f"/{directory}/{slug}-{record_id}.md"
    if path not in taken:
        taken.add(path)
        return path

    # Same record listed twice — this should not happen
    raise ValueError(f"Path already claimed: {path}")


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
    seen_ids: dict[str, set[str]] = {}  # kind -> set of record IDs

    for i, ch in enumerate(chapters, start=1):
        if "chapter" not in seen_ids:
            seen_ids["chapter"] = set()
        if ch.id in seen_ids["chapter"]:
            raise ValueError(f"Duplicate chapter record ID: {ch.id}")
        seen_ids["chapter"].add(ch.id)
        path = _claim("chapters", f"{i:03d}-{slugify(ch.title)}", ch.id, taken)
        index[path] = ("chapter", ch.id)
    for kind, directory, records, label in (
        ("character", "characters", characters, lambda r: r.name),
        ("world", "world", world_entries, lambda r: r.title),
        ("thread", "threads", threads, lambda r: r.name),
        ("secret", "secrets", secrets, lambda r: r.title),
        ("theme", "themes", themes, lambda r: r.title),
    ):
        if kind not in seen_ids:
            seen_ids[kind] = set()
        for record in records:
            if record.id in seen_ids[kind]:
                raise ValueError(f"Duplicate {kind} record ID: {record.id}")
            seen_ids[kind].add(record.id)
            index[_claim(directory, label(record), record.id, taken)] = (kind, record.id)
    return index
