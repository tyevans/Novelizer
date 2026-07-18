from __future__ import annotations
from novelizer.canon.events import StoredEvent

MINED_SOURCE_TAG = "[source: prose_miner]"


def already_mined_chapter_ids(mined_events: list[StoredEvent]) -> set[str]:
    """Chapter ids that already have a chapter.mined marker -- the caller
    fetches these via EventStore.events_since(0, event_types=[CHAPTER_MINED])
    and passes the raw events in. Pure function, no DB access (M5.1 Locked
    decision 2's idempotency mechanism).
    """
    return {e.payload["chapter_id"] for e in mined_events}


def thread_touch_log(thread_events: list[StoredEvent]) -> set[tuple[str, str]]:
    """(thread_id, chapter_id) pairs already present in the raw thread.*
    event log -- a mining-only log read (M5.1 Locked decision 4).
    ThreadsProjection holds aggregate state, not per-chapter touch history,
    so mining dedups against the log directly rather than a new
    projection. Events with an empty chapter_id are skipped (nothing to
    dedup against).
    """
    pairs: set[tuple[str, str]] = set()
    for e in thread_events:
        chapter_id = e.payload.get("chapter_id", "")
        if not chapter_id:
            continue
        pairs.add((e.payload["id"], chapter_id))
    return pairs
