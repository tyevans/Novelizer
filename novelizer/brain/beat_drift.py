"""Story Brain faculty: beat drift.

Pure functions over ReadStore data. Never persisted, never mutates state —
callers pass BlueprintRecord/BeatRecord/Chapter snapshots and receive plain
data back. Compares each beat's expected chapter window (via beat_window)
against its fulfillment state to surface pacing drift.
"""

from dataclasses import dataclass
from typing import Literal

from novelizer.canon.beat_templates import beat_window
from novelizer.store.models import BeatRecord, BlueprintRecord, Chapter


@dataclass(frozen=True)
class BeatDrift:
    beat_id: str
    name: str
    window_lo: int
    window_hi: int
    kind: Literal["late", "early", "off_window"]
    detail: str


def _chapter_ordinal(chapter_id: str, chapters: list[Chapter]) -> int | None:
    for i, c in enumerate(chapters, start=1):
        if c.id == chapter_id:
            return i
    return None


def beat_drifts(
    blueprint: BlueprintRecord | None, beats: list[BeatRecord], chapters: list[Chapter],
) -> list[BeatDrift]:
    if blueprint is None or not beats:
        return []
    now = len(chapters)
    drifts = []
    for beat in beats:
        window_lo, window_hi = beat_window(beat.ideal_pct, beat.tolerance_pct, blueprint.target_chapter_count)
        if not beat.fulfilled_by_chapter_id:
            if now > window_hi:
                drifts.append(BeatDrift(
                    beat_id=beat.id, name=beat.name, window_lo=window_lo, window_hi=window_hi,
                    kind="late",
                    detail=f"{beat.name} not fulfilled by ch {now} (window {window_lo}-{window_hi})",
                ))
            continue
        ordinal = _chapter_ordinal(beat.fulfilled_by_chapter_id, chapters)
        if ordinal is None:
            drifts.append(BeatDrift(
                beat_id=beat.id, name=beat.name, window_lo=window_lo, window_hi=window_hi,
                kind="off_window",
                detail=(
                    f"{beat.name} fulfilled by unknown chapter "
                    f"{beat.fulfilled_by_chapter_id!r} (window {window_lo}-{window_hi})"
                ),
            ))
        elif ordinal < window_lo:
            drifts.append(BeatDrift(
                beat_id=beat.id, name=beat.name, window_lo=window_lo, window_hi=window_hi,
                kind="early",
                detail=f"{beat.name} fulfilled early at ch {ordinal} (window {window_lo}-{window_hi})",
            ))
        elif ordinal > window_hi:
            drifts.append(BeatDrift(
                beat_id=beat.id, name=beat.name, window_lo=window_lo, window_hi=window_hi,
                kind="off_window",
                detail=f"{beat.name} fulfilled late at ch {ordinal} (window {window_lo}-{window_hi})",
            ))
    return drifts


def next_expected_beat(
    blueprint: BlueprintRecord | None, beats: list[BeatRecord], chapters: list[Chapter],
) -> BeatRecord | None:
    if blueprint is None:
        return None
    unfulfilled = [b for b in beats if not b.fulfilled_by_chapter_id]
    if not unfulfilled:
        return None
    return min(unfulfilled, key=lambda b: b.ideal_pct)
