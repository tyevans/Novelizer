from __future__ import annotations
from novelizer.store.models import StructureScore

SAG_SPIKE_DELTA = 0.3


def detect_sag_spike(scores: list[StructureScore], delta: float = SAG_SPIKE_DELTA) -> dict[str, str]:
    """Pure, deterministic sag/spike detection over already-emitted structure
    scores -- no LLM call, no judgment beyond a fixed numeric threshold. A
    chapter whose tension deviates from the mean of `scores` by at least
    `delta` is flagged "sag" (below the mean) or "spike" (above it); chapters
    within the threshold are omitted from the result. Fewer than two scores
    can't establish a mean worth deviating from, so nothing is flagged.
    """
    if len(scores) < 2:
        return {}
    mean = sum(s.tension for s in scores) / len(scores)
    flags: dict[str, str] = {}
    for s in scores:
        diff = s.tension - mean
        if diff <= -delta:
            flags[s.chapter_id] = "sag"
        elif diff >= delta:
            flags[s.chapter_id] = "spike"
    return flags
