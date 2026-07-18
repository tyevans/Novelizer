from __future__ import annotations

from novelizer.canon.threads import TERMINAL_STATES
from novelizer.store.models import Chapter, ThreadRecord

STALENESS_THRESHOLD_CHAPTERS = 3


def chapters_elapsed_since(chapter_id: str, chapters: list[Chapter]) -> int:
    """Count of chapters strictly after `chapter_id` in `chapters`' chronological
    order (the order novelizer.canon.read_store.ReadStore.list_chapters()
    already returns them in). If `chapter_id` isn't found among `chapters`
    (an empty id, or a thread whose last event carried no chapter reference),
    every chapter counts as elapsed -- a conservative, maximally-stale default.
    """
    ids = [c.id for c in chapters]
    if chapter_id not in ids:
        return len(chapters)
    return len(chapters) - 1 - ids.index(chapter_id)


def is_thread_stale(
    thread: ThreadRecord, chapters: list[Chapter], threshold: int = STALENESS_THRESHOLD_CHAPTERS
) -> bool:
    """A thread is stale once `threshold` chapters have elapsed since its last
    planted/touched event, with no terminal (paid_off/abandoned) event since.
    Pure and computed live over ReadStore data -- never persisted -- so that
    M3.3's BrainContext provider and Thread Board widget, which will both
    import this function, can never disagree about which threads are stale.
    """
    if thread.state.value in TERMINAL_STATES:
        return False
    return chapters_elapsed_since(thread.last_chapter_id, chapters) >= threshold


def stale_threads(
    threads: list[ThreadRecord], chapters: list[Chapter], threshold: int = STALENESS_THRESHOLD_CHAPTERS
) -> list[ThreadRecord]:
    """Filter `threads` down to the ones is_thread_stale flags, preserving order."""
    return [t for t in threads if is_thread_stale(t, chapters, threshold)]
