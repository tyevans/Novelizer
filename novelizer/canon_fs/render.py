from __future__ import annotations
from novelizer.store.models import Chapter, WorldEntry


def _frontmatter(pairs: list[tuple[str, str]]) -> str:
    lines = "\n".join(f"{k}: {v}" for k, v in pairs if v != "")
    return f"---\n{lines}\n---\n"


def render_chapter(chapter: Chapter) -> str:
    fm = _frontmatter([
        ("id", chapter.id),
        ("kind", "chapter"),
        ("status", chapter.editorial_status.value),
        ("characters", ", ".join(chapter.character_ids)),
    ])
    return f"{fm}\n# {chapter.title}\n\n{chapter.prose}\n"


def render_world_entry(entry: WorldEntry) -> str:
    fm = _frontmatter([
        ("id", entry.id),
        ("kind", "world"),
        ("domain", entry.domain.value),
        ("canon_status", entry.canon_status.value),
        ("tags", ", ".join(entry.tags)),
    ])
    return f"{fm}\n# {entry.title}\n\n{entry.body}\n"
