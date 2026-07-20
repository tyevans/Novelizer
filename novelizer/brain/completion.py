"""Story Brain faculty: completion.

Pure functions over ReadStore data. Never persisted, never mutates state --
callers pass BlueprintRecord/BeatRecord/PromiseRecord/ArcRecord/Chapter
snapshots and receive plain data back. Determines whether the blueprint's
adopted shape has been fully realized: every beat fulfilled, every promise
settled (paid or released, never left open), and every active arc resolved.
Chapter counts are reported for context but never gate completion -- a book
may land early or late; the blueprint's shape is the criterion, not its
length.
"""

from dataclasses import dataclass, field

from novelizer.store.models import ArcRecord, BeatRecord, BlueprintRecord, Chapter, PromiseRecord, PromiseState


@dataclass(frozen=True)
class CompletionStatus:
    complete: bool
    beats_total: int
    beats_fulfilled: int
    promises_open: int
    arcs_unresolved: int
    chapters: int
    target_chapters: int
    blockers: list[str] = field(default_factory=list)


def completion_status(
    blueprint: BlueprintRecord | None,
    beats: list[BeatRecord],
    promises: list[PromiseRecord],
    arcs: list[ArcRecord],
    chapters: list[Chapter],
) -> CompletionStatus | None:
    """Assess whether the adopted blueprint's shape is fully realized.

    Returns None when there is no blueprint -- a story with no adopted
    shape can never be "complete". An empty beat list never completes
    (there is nothing to have fulfilled). Resolved-but-inactive
    (superseded) arcs are ignored; released and paid promises don't block,
    only ``open`` ones do.
    """
    if blueprint is None:
        return None

    beats_total = len(beats)
    unfulfilled = [b for b in beats if not b.fulfilled_by_chapter_id]
    beats_fulfilled = beats_total - len(unfulfilled)

    open_promises = [p for p in promises if p.state == PromiseState.open]
    unresolved_arcs = [a for a in arcs if a.active and not a.resolved]

    blockers: list[str] = []

    if beats_total == 0:
        blockers.append("no beats adopted yet")
    elif unfulfilled:
        names = ", ".join(b.slug for b in unfulfilled)
        blockers.append(f"{len(unfulfilled)} of {beats_total} beats unfulfilled: {names}")

    if open_promises:
        count = len(open_promises)
        noun = "promise" if count == 1 else "promises"
        blockers.append(f"{count} {noun} still open")

    if unresolved_arcs:
        count = len(unresolved_arcs)
        noun = "arc" if count == 1 else "arcs"
        ids = ", ".join(a.character_id for a in unresolved_arcs)
        blockers.append(f"{count} {noun} unresolved: {ids}")

    complete = beats_total > 0 and not unfulfilled and not open_promises and not unresolved_arcs

    return CompletionStatus(
        complete=complete,
        beats_total=beats_total,
        beats_fulfilled=beats_fulfilled,
        promises_open=len(open_promises),
        arcs_unresolved=len(unresolved_arcs),
        chapters=len(chapters),
        target_chapters=blueprint.target_chapter_count,
        blockers=blockers,
    )
