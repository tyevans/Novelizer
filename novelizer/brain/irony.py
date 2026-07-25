"""The dramatic-irony ledger: where the READER knows something a CHARACTER
on the page does not.

The exact inverse of novelizer/brain/leaks.py. A leak is a character using
knowledge they should not have — a mistake to flag. A gap here is a character
*lacking* knowledge the reader already has — an effect to exploit. Both read
the same canon and both share knowledge_cell_state as the single oracle for
"does this character know this secret at all"; neither persists anything, and
neither writes. Nothing in this module implies a new event or a new agent
write authority: it is a fold over rows the secret.* log already produces.

WHAT COUNTS AS READER KNOWLEDGE
-------------------------------
A secret's reader-onset chapter is the FIRST chapter, in story order, holding
a secret.referenced row for it. Three candidate signals were considered:

* the secret EXISTING (secret.created) — rejected. A planted secret is a note
  to the machine, not a page the reader has turned. Counting it would report
  irony for every secret the moment it was invented, which is exactly the
  misleading blank this ledger is meant to avoid.
* the secret being REFERENCED in prose (secret.referenced) — accepted. This is
  the only signal that means the secret reached the page, and the only one the
  read model anchors to a chapter (secret_references.chapter_id).
* the secret being REVEALED (secret.revealed) — accepted as a qualifier, not as
  an onset. secret.revealed is story-world status ("this is public now"), and
  the projection deliberately keeps it as a set-once secret-level flag with no
  chapter (Locked decision #2, novelizer/canon/projections/secrets.py) — the
  event carries a chapter_id that the read model does not retain. So a reveal
  can CLOSE a gap but cannot place one, and a secret marked revealed that was
  never referenced in prose is reported as exactly that rather than as irony.

Deliberately not fixed here: the missing reveal chapter. Recovering it would
mean adding a column to the secrets projection. That is a read-model change,
not a new event, so it is legal — but it is out of scope for a derived ledger
and would change a set-once handler, so this module reports the limitation
(REVEAL_LENGTH_UNMEASURABLE) instead of hiding it.

CHAPTER ORDER
-------------
A chapter's ordinal is its 1-based position in ReadStore.list_chapters(),
which is rowid order and therefore creation order — the same convention
novelizer/brain/ledger.py uses for `now` and novelizer/canon_fs/paths.py uses
for the `001-` filename prefix. Chapter ids are slugs and do NOT sort
chronologically, so they are never compared directly. A chapter id that is not
in the ordering (the empty default, or a stale id) has no ordinal and is
reported as unplaceable rather than silently sorted to the front.

WHICH CHARACTERS GET A GAP
--------------------------
Only characters who appear on the page (Chapter.character_ids) in at least one
chapter where the reader already knows and they still do not. Irony that never
shares a scene with its character is not an effect a writer can play, and
listing the whole cast against every secret would bury the handful of gaps
that matter.
"""
from __future__ import annotations

from typing import Optional

from pydantic import BaseModel

from novelizer.canon.secrets import knowledge_cell_state
from novelizer.store.models import (
    Chapter, SecretKnowledgeRecord, SecretRecord, SecretReferenceRecord,
)

# Honest notes for the cases where there is no irony to report. Rendered in
# place of a gap list so an empty entry states a fact instead of reading as a
# blank (novelizer/canon_fs/render.py renders these verbatim).
NOT_ON_PAGE_NOTE = (
    "not yet referenced in any chapter — the reader has not met this secret, "
    "so no irony is available."
)
REVEALED_OFF_PAGE_NOTE = (
    "marked revealed in canon but never referenced in prose — no on-page reader "
    "knowledge is recorded, so no gap can be measured."
)
UNANCHORED_NOTE = (
    "referenced in prose but with no chapter on record, so the reader's onset "
    "chapter cannot be placed."
)
NO_GAP_NOTE = (
    "every character who shares a scene with this secret already knew it when "
    "the reader did — no dramatic irony."
)


class IronyGap(BaseModel):
    """One (secret, character) window in which the reader is ahead.

    `length` is the number of chapters in which the reader knew and the
    character did not, counted from the reader's onset up to but excluding the
    chapter that closes the gap. It is None when closed_by == "reveal": the
    reveal's chapter is not in the read model (see this module's docstring),
    so the window has no measurable end.
    """

    secret_id: str
    character_id: str
    reader_from_ordinal: int
    character_from_ordinal: Optional[int] = None
    closed_by: str  # "learned" | "reveal" | "open"
    length: Optional[int] = None
    live_chapters: list[int] = []
    """Ordinals where the character is on the page and still in the dark —
    the scenes in which the irony can actually be played."""


class IronySecretEntry(BaseModel):
    """One secret's reader-vs-character knowledge timeline."""

    secret_id: str
    secret_title: str
    revealed: bool = False
    reader_from_ordinal: Optional[int] = None
    reader_from_chapter_id: str = ""
    gaps: list[IronyGap] = []
    note: str = ""


def chapter_ordinals(chapters: list[Chapter]) -> dict[str, int]:
    """Map chapter id -> 1-based story position. List position, never id
    order: ids are slugs and uuids, which do not sort chronologically."""
    return {ch.id: i for i, ch in enumerate(chapters, start=1)}


def _reader_onset(
    references: list[SecretReferenceRecord], ordinals: dict[str, int]
) -> tuple[Optional[int], str]:
    """Earliest placeable referencing chapter, as (ordinal, chapter_id).
    References whose chapter is not in the ordering are skipped here and
    surface as UNANCHORED_NOTE when they were the only ones."""
    placed = [
        (ordinals[ref.chapter_id], ref.chapter_id)
        for ref in references
        if ref.chapter_id in ordinals
    ]
    return min(placed) if placed else (None, "")


def _gap_for(
    secret: SecretRecord,
    character_id: str,
    reader_from: int,
    learned_at: Optional[int],
    matrix: dict[str, dict],
    on_page: dict[str, set[int]],
    last_ordinal: int,
) -> Optional[IronyGap]:
    state = knowledge_cell_state(matrix, secret.id, character_id)
    if learned_at is not None and learned_at <= reader_from:
        return None  # the character was never behind the reader
    if learned_at is not None:
        closed_at, closed_by, length = learned_at, "learned", learned_at - reader_from
    elif state == "revealed":
        # The reveal closes the gap but carries no chapter, so the window is
        # treated as running through the drafted story for scene purposes and
        # its length is left unmeasured rather than guessed.
        closed_at, closed_by, length = last_ordinal + 1, "reveal", None
    else:
        closed_at, closed_by, length = last_ordinal + 1, "open", last_ordinal - reader_from + 1
    live = sorted(o for o in on_page.get(character_id, ()) if reader_from <= o < closed_at)
    if not live:
        return None
    return IronyGap(
        secret_id=secret.id, character_id=character_id,
        reader_from_ordinal=reader_from, character_from_ordinal=learned_at,
        closed_by=closed_by, length=length, live_chapters=live,
    )


def build_irony_ledger(
    secrets: list[SecretRecord],
    references: list[SecretReferenceRecord],
    knowledge: list[SecretKnowledgeRecord],
    chapters: list[Chapter],
    matrix: dict[str, dict],
) -> list[IronySecretEntry]:
    """Fold canon into one entry per secret, ordered by the chapter the reader
    learned it (secrets the reader has not met sort last, then by id).

    Pure and never persisted — recomputed from ReadStore data on every read,
    same precedent as novelizer/brain/leaks.py's find_leaks and
    novelizer/brain/staleness.py's is_thread_stale. Every secret gets an entry,
    including ones absent from `matrix`; a secret with nothing to report
    carries a `note` saying which of the no-irony cases it is.
    """
    ordinals = chapter_ordinals(chapters)
    last_ordinal = len(chapters)
    refs_by_secret: dict[str, list[SecretReferenceRecord]] = {}
    for ref in references:
        refs_by_secret.setdefault(ref.secret_id, []).append(ref)
    learned_by: dict[str, dict[str, int]] = {}
    for row in knowledge:
        if row.chapter_id in ordinals:
            learned_by.setdefault(row.secret_id, {})[row.character_id] = ordinals[row.chapter_id]
    on_page: dict[str, set[int]] = {}
    for ordinal, chapter in enumerate(chapters, start=1):
        for character_id in chapter.character_ids:
            on_page.setdefault(character_id, set()).add(ordinal)

    entries: list[IronySecretEntry] = []
    for secret in secrets:
        secret_refs = refs_by_secret.get(secret.id, [])
        reader_from, reader_chapter = _reader_onset(secret_refs, ordinals)
        entry = IronySecretEntry(
            secret_id=secret.id, secret_title=secret.title, revealed=secret.revealed,
            reader_from_ordinal=reader_from, reader_from_chapter_id=reader_chapter,
        )
        if reader_from is None:
            if secret_refs:
                entry.note = UNANCHORED_NOTE
            elif secret.revealed:
                entry.note = REVEALED_OFF_PAGE_NOTE
            else:
                entry.note = NOT_ON_PAGE_NOTE
            entries.append(entry)
            continue
        learned = learned_by.get(secret.id, {})
        gaps = [
            gap for gap in (
                _gap_for(
                    secret, character_id, reader_from, learned.get(character_id),
                    matrix, on_page, last_ordinal,
                )
                for character_id in sorted(on_page)
            )
            if gap is not None
        ]
        # First live chapter, then character id: the reader wants the earliest
        # playable scene first, and the id keeps ties deterministic.
        entry.gaps = sorted(gaps, key=lambda g: (g.live_chapters[0], g.character_id))
        if not entry.gaps:
            entry.note = NO_GAP_NOTE
        entries.append(entry)

    return sorted(
        entries,
        key=lambda e: (e.reader_from_ordinal is None, e.reader_from_ordinal or 0, e.secret_id),
    )
