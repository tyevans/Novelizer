from __future__ import annotations
from hypothesis import given, settings, strategies as st
from novelizer.brain.watermarks import current_done_ids
from novelizer.canon.events import StoredEvent


def _ev(seq: int, event_type: str, chapter_id: str) -> StoredEvent:
    return StoredEvent(
        sequence=seq,
        id=f"e{seq}",
        event_type=event_type,
        aggregate_id=chapter_id,
        payload={"chapter_id": chapter_id},
        created_at="2026-07-22T00:00:00",
    )


def test_done_then_revised_is_not_done():
    done = [_ev(1, "chapter.processed", "c1")]
    revised = [_ev(2, "chapter.revised", "c1")]
    assert current_done_ids(done, revised) == set()


def test_revised_then_done_is_done():
    done = [_ev(3, "chapter.processed", "c1")]
    revised = [_ev(2, "chapter.revised", "c1")]
    assert current_done_ids(done, revised) == {"c1"}


def test_empty():
    assert current_done_ids([], []) == set()


@given(st.lists(st.tuples(st.sampled_from(["done", "revised"]),
                          st.sampled_from(["a", "b", "c"])), max_size=30))
@settings(max_examples=200, deadline=None)
def test_matches_naive_fold(script):
    done, revised = [], []
    for seq, (kind, cid) in enumerate(script, start=1):
        (done if kind == "done" else revised).append(
            _ev(seq, "chapter.processed" if kind == "done" else "chapter.revised", cid)
        )
    expected: set[str] = set()
    for kind, cid in script:
        expected.add(cid) if kind == "done" else expected.discard(cid)
    assert current_done_ids(done, revised) == expected
