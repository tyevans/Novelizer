from __future__ import annotations
from novelizer.store.models import Chapter, WorldEntry, Character, SecretRecord, ThemeRecord, ThreadRecord
from novelizer.brain.irony import IronyGap, IronySecretEntry
from novelizer.canon.secrets import knowledge_cell_state


def _frontmatter(pairs: list[tuple[str, str]]) -> str:
    sanitized = [(k, " ".join(str(v).split())) for k, v in pairs]
    lines = "\n".join(f"{k}: {v}" for k, v in sanitized if v != "")
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


# Said in full when there is nothing to report, because the live story can
# legitimately have zero secrets and a blank ledger would read as "no irony
# found" rather than "nothing to look at yet".
EMPTY_LEDGER_NOTE = (
    "No secrets are recorded in canon, so there is no reader/character knowledge "
    "gap to compute. This ledger fills in on its own once secrets are planted "
    "(a `secret` intent) and then referenced in prose (a `reference` intent on an "
    "existing secret id) — nothing here is authored by hand."
)

_LEDGER_PREAMBLE = (
    "What the READER knows versus what each CHARACTER knows, per secret, in "
    "chapter order. The reader's clock starts at the first chapter where a secret "
    "is REFERENCED in prose — a secret that merely exists has not reached the "
    "page, so it counts for nothing here. Chapter numbers are story positions: "
    "chapter 3 is /chapters/003-*.md. Derived entirely from existing canon; it is "
    "read-only and there is nothing to write back."
)


def _gap_line(gap: IronyGap) -> str:
    if gap.closed_by == "learned":
        window = (
            f"chapter {gap.reader_from_ordinal}" if gap.length == 1
            else f"chapters {gap.reader_from_ordinal}-{gap.character_from_ordinal - 1}"
        )
        state = f"in the dark {window}, learns in chapter {gap.character_from_ordinal} ({gap.length} ch)"
    elif gap.closed_by == "reveal":
        state = (
            f"in the dark from chapter {gap.reader_from_ordinal}; closed by the reveal, "
            f"whose chapter is not on record"
        )
    else:
        state = (
            f"in the dark from chapter {gap.reader_from_ordinal} onward, never learns "
            f"({gap.length} ch)"
        )
    scenes = ", ".join(str(o) for o in gap.live_chapters)
    return f"- {gap.character_id}: {state} — on page in {scenes}"


def render_irony_ledger(entries: list[IronySecretEntry], chapters: list[Chapter]) -> str:
    """Render the dramatic-irony ledger as one canon file. Empty-state honest:
    with no secrets it says so and explains what would fill it, and a secret
    with no measurable gap prints the reason (novelizer/brain/irony.py's notes)
    rather than an empty bullet list."""
    gap_count = sum(len(e.gaps) for e in entries)
    fm = _frontmatter([
        ("kind", "irony_ledger"),
        ("secrets", str(len(entries))),
        ("gaps", str(gap_count)),
        ("chapters", str(len(chapters))),
    ])
    body = [f"\n# Dramatic Irony Ledger\n\n{_LEDGER_PREAMBLE}\n"]
    if not entries:
        return fm + "".join(body) + f"\n{EMPTY_LEDGER_NOTE}\n"
    for entry in entries:
        body.append(f"\n## {entry.secret_title} [id: {entry.secret_id}]\n")
        if entry.reader_from_ordinal is None:
            body.append(f"\n{entry.note}\n")
            continue
        revealed = "yes" if entry.revealed else "no"
        body.append(
            f"\nreader knows from chapter {entry.reader_from_ordinal} "
            f"({entry.reader_from_chapter_id}) · revealed: {revealed}\n"
        )
        if entry.gaps:
            body.append("\n" + "\n".join(_gap_line(g) for g in entry.gaps) + "\n")
        else:
            body.append(f"\n{entry.note}\n")
    return fm + "".join(body)


def render_theme(theme: ThemeRecord) -> str:
    fm = _frontmatter([
        ("id", theme.id),
        ("kind", "theme"),
        ("touch_count", str(theme.touch_count)),
        ("last_chapter_id", theme.last_chapter_id),
    ])
    note = f"\n{theme.last_note}\n" if theme.last_note else ""
    return f"{fm}\n# {theme.title}\n{note}"
