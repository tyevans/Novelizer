"""Story Brain faculty: arc alignment.

Pure functions over ReadStore data. Never persisted, never mutates state --
callers pass ArcRecord/Character/Chapter/BeatRecord/BlueprintRecord
snapshots and receive plain data back. Flags three kinds of arc trouble:
a resolved arc whose outcome contradicts its declared type, an unresolved
arc that has gone quiet, and an unresolved arc whose planned pivot beat's
window has closed without a recorded advance inside it.
"""

from dataclasses import dataclass

from novelizer.brain.staleness import chapters_elapsed_since
from novelizer.canon.beat_templates import beat_window
from novelizer.store.models import ArcRecord, BeatRecord, BlueprintRecord, Chapter, Character

ARC_CONSISTENT_OUTCOMES: dict[str, set[str]] = {
    "positive": {"truth_embraced"},
    "flat": {"world_changed", "truth_embraced"},
    "disillusionment": {"truth_tragic"},
    "fall": {"lie_embraced"},
    "corruption": {"lie_embraced"},
}

STAGNATION_CHAPTERS = 4


@dataclass(frozen=True)
class ArcFinding:
    arc_id: str
    character_id: str
    kind: str  # "contradiction" | "stagnant" | "pivot_missed"
    detail: str
    beat_id: str = ""


def _chapter_ordinal(chapter_id: str, chapters: list[Chapter]) -> int | None:
    for i, c in enumerate(chapters, start=1):
        if c.id == chapter_id:
            return i
    return None


def arc_findings(
    arcs: list[ArcRecord],
    characters: list[Character],
    chapters: list[Chapter],
    beats: list[BeatRecord],
    blueprint: BlueprintRecord | None,
    stagnation_chapters: int = STAGNATION_CHAPTERS,
) -> list[ArcFinding]:
    beats_by_id = {b.id: b for b in beats}
    findings: list[ArcFinding] = []
    now = len(chapters)

    for arc in arcs:
        if arc.resolved:
            # contradiction: a resolved arc whose outcome isn't among the
            # consistent outcomes for its declared arc_type. An unknown
            # arc_type or a blank outcome carries no signal either way --
            # quiet, not flagged.
            # A resolved arc that is no longer active has been superseded --
            # the character's re-declaration of a new arc IS the Director/
            # Keeper's adjudication of the contradiction, so the alarm
            # clears rather than persisting forever.
            consistent = ARC_CONSISTENT_OUTCOMES.get(arc.arc_type)
            if arc.active and consistent and arc.outcome and arc.outcome not in consistent:
                findings.append(ArcFinding(
                    arc_id=arc.id, character_id=arc.character_id, kind="contradiction",
                    detail=f"{arc.arc_type} arc resolved {arc.outcome}",
                ))
            continue

        if not arc.active:
            continue

        # stagnant: reuses chapters_elapsed_since, which already treats an
        # unmatched (including empty) last_chapter_id as maximally stale --
        # every chapter counts elapsed -- so an arc that has never advanced
        # is flagged once len(chapters) >= stagnation_chapters, without any
        # special-casing here.
        elapsed = chapters_elapsed_since(arc.last_chapter_id, chapters)
        if elapsed >= stagnation_chapters:
            findings.append(ArcFinding(
                arc_id=arc.id, character_id=arc.character_id, kind="stagnant",
                detail=f"no advance in {elapsed} chapters (threshold {stagnation_chapters})",
            ))

        # pivot_missed: coarse by design. Only the arc's *last* advance
        # chapter is retained on ArcRecord (no per-advance history), so this
        # cannot tell whether an earlier advance already landed inside a
        # given pivot's window -- it can only ask whether the single most
        # recent advance (or none) falls before the window opened. A pivot
        # advanced early, then followed by a later advance that lands after
        # the window closes, would misreport as missed. A future refinement
        # would need the full advance history (mirrors the Outline grid's
        # honesty note about single-snapshot approximations).
        if blueprint is not None:
            last_ordinal = _chapter_ordinal(arc.last_chapter_id, chapters) if arc.last_chapter_id else None
            for pivot in arc.pivots:
                beat = beats_by_id.get(pivot.beat_id)
                if beat is None:
                    continue
                lo, hi = beat_window(beat.ideal_pct, beat.tolerance_pct, blueprint.target_chapter_count)
                if now > hi and (last_ordinal is None or last_ordinal < lo):
                    findings.append(ArcFinding(
                        arc_id=arc.id, character_id=arc.character_id, kind="pivot_missed",
                        detail=f"pivot on beat '{beat.name}' missed (window {lo}-{hi})",
                        beat_id=beat.id,
                    ))

    return findings
