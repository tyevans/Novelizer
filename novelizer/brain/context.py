from __future__ import annotations
from novelizer.brain.sag_spike import detect_sag_spike
from novelizer.brain.staleness import stale_threads
from novelizer.store.models import Chapter, StructureScore, ThreadRecord


def stale_threads_note(threads: list[ThreadRecord], chapters: list[Chapter]) -> str:
    """Build the Author-facing prompt block naming every currently-stale
    thread and the id the Author must cite to touch it back (per M3.1's
    thread identity rule -- ids are never invented, only cited). Empty
    string when nothing is stale, so Author.work()'s prompt stays
    byte-identical to pre-M3.3 output whenever the brain has nothing to say.
    """
    stale = stale_threads(threads, chapters)
    if not stale:
        return ""
    lines = "\n".join(f"- {t.name} (id:{t.id})" for t in stale)
    return f"\n\nStale threads (consider touching one, citing its id exactly):\n{lines}"


def pacing_flags_note(scores: list[StructureScore]) -> str:
    """Build the Editor-facing prompt block naming every chapter the pure
    sag/spike detector has flagged. Empty string when nothing is flagged.
    """
    flags = detect_sag_spike(scores)
    if not flags:
        return ""
    lines = "\n".join(f"- chapter {chapter_id}: {flag}" for chapter_id, flag in flags.items())
    return f"\n\nPacing flags:\n{lines}"
