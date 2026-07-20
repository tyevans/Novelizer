"""Story Brain faculty: tension target curve.

Pure functions over ReadStore data. Never persisted, never mutates state --
callers pass BlueprintRecord/BeatRecord/StructureScore/Chapter snapshots and
receive plain data back. A polarity-bearing beat implies a target tension
level at its ideal chapter position; interpolating between those anchors (and
an implicit gentle-open/gentle-close start and end) yields a full target
curve to compare each chapter's scored tension against.
"""

from novelizer.store.models import BeatRecord, BlueprintRecord, Chapter, StructureScore

_POLARITY_TARGETS = {"up": 0.75, "down": 0.35, "flip": 0.85}
_IMPLICIT_START = 0.3
_IMPLICIT_END = 0.5


def target_curve(blueprint: BlueprintRecord | None, beats: list[BeatRecord]) -> list[float]:
    """Interpolate a per-chapter target tension curve. Anchors come from each
    polarity-bearing beat (round(ideal_pct * n), clamped 1..n) plus an
    implicit (1, 0.3) start and (n, 0.5) end -- unless a beat anchors those
    ordinals itself, in which case the beat's value wins. `blueprint is None`
    -> [] (nothing to target)."""
    if blueprint is None:
        return []
    n = blueprint.target_chapter_count
    if n <= 0:
        return []
    anchors: dict[int, float] = {}
    for beat in beats:
        value = _POLARITY_TARGETS.get(beat.expected_polarity)
        if value is None:
            continue
        ordinal = min(n, max(1, round(beat.ideal_pct * n)))
        anchors[ordinal] = value
    anchors.setdefault(1, _IMPLICIT_START)
    anchors.setdefault(n, _IMPLICIT_END)

    points = sorted(anchors.items())
    curve = [0.0] * n
    for (o1, v1), (o2, v2) in zip(points, points[1:]):
        span = o2 - o1
        for o in range(o1, o2 + 1):
            t = (o - o1) / span if span else 0.0
            curve[o - 1] = v1 + t * (v2 - v1)
    if len(points) == 1:
        only_value = points[0][1]
        curve = [only_value] * n
    return curve


def tension_deviations(
    blueprint: BlueprintRecord | None,
    beats: list[BeatRecord],
    scores: list[StructureScore],
    chapters: list[Chapter],
    delta: float = 0.25,
) -> list[tuple[str, float, float]]:
    """Compare each scored chapter's StructureScore.tension against the
    target curve at its ordinal, returning (chapter_id, actual, target) for
    every deviation exceeding `delta`. Scores are last-wins by chapter id
    (mirroring shape_tab's ordering), chapter order gives the ordinal, and
    chapters beyond the curve's length compare against the curve's last
    value. Unscored chapters are skipped."""
    if blueprint is None:
        return []
    curve = target_curve(blueprint, beats)
    if not curve:
        return []

    by_chapter: dict[str, StructureScore] = {}
    for s in scores:
        by_chapter[s.chapter_id] = s  # last score per chapter wins

    chapter_ids = {c.id for c in chapters}
    deviations: list[tuple[str, float, float]] = []

    for ordinal, c in enumerate(chapters, start=1):
        score = by_chapter.get(c.id)
        if score is None:
            continue
        target = curve[ordinal - 1] if ordinal <= len(curve) else curve[-1]
        if abs(score.tension - target) > delta:
            deviations.append((c.id, score.tension, target))

    extras = [(cid, s) for cid, s in by_chapter.items() if cid not in chapter_ids]
    for offset, (cid, score) in enumerate(extras, start=1):
        ordinal = len(chapters) + offset
        target = curve[ordinal - 1] if ordinal <= len(curve) else curve[-1]
        if abs(score.tension - target) > delta:
            deviations.append((cid, score.tension, target))

    return deviations
