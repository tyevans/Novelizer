from __future__ import annotations
from novelizer.store.models import Chapter, WorldEntry, Character, SecretRecord, ThemeRecord, ThreadRecord
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


def render_thread(thread: ThreadRecord) -> str:
    fm = _frontmatter([
        ("id", thread.id),
        ("kind", "thread"),
        ("state", thread.state.value),
        ("touch_count", str(thread.touch_count)),
        ("last_chapter_id", thread.last_chapter_id),
    ])
    note = f"\n{thread.last_note}\n" if thread.last_note else ""
    return f"{fm}\n# {thread.name}\n{note}"


def render_secret(
    secret: SecretRecord, matrix: dict[str, dict], characters: list[Character]
) -> str:
    known = sorted(
        c.name for c in characters
        if knowledge_cell_state(matrix, secret.id, c.id) == "known"
    )
    who = f"known to: {', '.join(known)}" if known else "known to no one"
    fm = _frontmatter([
        ("id", secret.id),
        ("kind", "secret"),
        ("revealed", str(secret.revealed)),
    ])
    return f"{fm}\n# {secret.title}\n\n{who}\n"


def render_theme(theme: ThemeRecord) -> str:
    fm = _frontmatter([
        ("id", theme.id),
        ("kind", "theme"),
        ("touch_count", str(theme.touch_count)),
        ("last_chapter_id", theme.last_chapter_id),
    ])
    note = f"\n{theme.last_note}\n" if theme.last_note else ""
    return f"{fm}\n# {theme.title}\n{note}"
