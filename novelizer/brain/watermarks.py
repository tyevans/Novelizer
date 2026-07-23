from __future__ import annotations
from novelizer.canon.events import StoredEvent


def current_done_ids(
    done_events: list[StoredEvent], revised_events: list[StoredEvent]
) -> set[str]:
    """Fold done/revised markers in global sequence order: a done-event adds
    its chapter, a later chapter.revised removes it — so revised chapters are
    automatically re-processed with no mutable state (generalizes
    brain/mining.already_mined_chapter_ids, which is revision-blind).
    Pure function: callers fetch the two lists via EventStore.events_since."""
    timeline = [(e.sequence, True, e.payload["chapter_id"]) for e in done_events]
    timeline += [(e.sequence, False, e.payload["chapter_id"]) for e in revised_events]
    done: set[str] = set()
    for _, is_done, chapter_id in sorted(timeline):
        if is_done:
            done.add(chapter_id)
        else:
            done.discard(chapter_id)
    return done
