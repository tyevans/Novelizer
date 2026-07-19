from __future__ import annotations
from novelizer.store.models import Chapter, WorldEntry, Character, SecretRecord
from novelizer.canon.secrets import knowledge_cell_state


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


def render_character(
    character: Character, matrix: dict[str, dict], secrets: list[SecretRecord]
) -> str:
    fm = _frontmatter([
        ("id", character.id),
        ("kind", "character"),
        ("aliases", ", ".join(character.aliases)),
        ("traits", character.traits),
        ("motivations", character.motivations),
        ("arc_status", character.arc_status),
        ("voice", character.voice),
    ])
    body = [f"\n# {character.name}\n"]
    if character.backstory:
        body.append(f"\n{character.backstory}\n")
    if character.relationships:
        rel = "\n".join(f"- {r.target_character_id}: {r.description}" for r in character.relationships)
        body.append(f"\n## Relationships\n\n{rel}\n")
    known = [
        s for s in secrets
        if knowledge_cell_state(matrix, s.id, character.id) == "known"
    ]
    if known:
        lines = "\n".join(f"- {s.id} ({s.title})" for s in known)
        body.append(f"\n## Knows\n\n{lines}\n")
    return fm + "".join(body)
