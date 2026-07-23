from novelizer.brain.mining import MINED_SOURCE_TAG, thread_touch_log
from novelizer.canon.events import StoredEvent


def _ev(event_type, payload):
    return StoredEvent(sequence=1, id="e1", event_type=event_type, aggregate_id="a", payload=payload, created_at="2026-01-01T00:00:00Z")


def test_thread_touch_log_pairs_id_and_chapter_id():
    events = [
        _ev("thread.planted", {"id": "t1", "chapter_id": "c1"}),
        _ev("thread.touched", {"id": "t1", "chapter_id": "c2"}),
    ]
    assert thread_touch_log(events) == {("t1", "c1"), ("t1", "c2")}


def test_thread_touch_log_skips_blank_chapter_id():
    events = [_ev("thread.touched", {"id": "t1", "chapter_id": ""})]
    assert thread_touch_log(events) == set()


def test_mined_source_tag_is_pinned():
    assert MINED_SOURCE_TAG == "[source: prose_miner]"
