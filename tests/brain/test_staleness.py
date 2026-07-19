from hypothesis import given, settings, strategies as st
from novelizer.brain.staleness import (
    STALENESS_THRESHOLD_CHAPTERS, chapters_elapsed_since, is_thread_stale, stale_threads,
)
from novelizer.store.models import Chapter, ThreadRecord, ThreadState


def _chapters(n: int) -> list[Chapter]:
    return [Chapter(id=f"c{i}", title=str(i), prose="p") for i in range(n)]


def test_chapters_elapsed_since_counts_chapters_after_the_given_one():
    chs = _chapters(5)  # c0..c4
    assert chapters_elapsed_since("c4", chs) == 0
    assert chapters_elapsed_since("c2", chs) == 2
    assert chapters_elapsed_since("c0", chs) == 4


def test_chapters_elapsed_since_unknown_id_is_maximally_stale():
    chs = _chapters(3)
    assert chapters_elapsed_since("does-not-exist", chs) == 3
    assert chapters_elapsed_since("", chs) == 3


def test_thread_not_stale_before_threshold():
    chs = _chapters(4)  # c0..c3
    thread = ThreadRecord(id="t1", name="T", state=ThreadState.touched, last_chapter_id="c2")
    assert chapters_elapsed_since("c2", chs) == 1
    assert is_thread_stale(thread, chs) is False


def test_thread_stale_once_three_chapters_have_elapsed():
    chs = _chapters(5)  # 4 chapters have elapsed since c0's touch (c1..c4)
    thread = ThreadRecord(id="t1", name="T", state=ThreadState.planted, last_chapter_id="c0")
    assert chapters_elapsed_since("c0", chs) == 4
    assert is_thread_stale(thread, chs) is True


def test_thread_stale_at_exactly_the_threshold():
    chs = _chapters(4)  # c1, c2, c3 elapsed since c0 -> exactly 3
    thread = ThreadRecord(id="t1", name="T", state=ThreadState.touched, last_chapter_id="c0")
    assert chapters_elapsed_since("c0", chs) == 3
    assert is_thread_stale(thread, chs) is True


def test_terminal_threads_are_never_stale_regardless_of_elapsed_chapters():
    chs = _chapters(10)
    for state in (ThreadState.paid_off, ThreadState.abandoned):
        thread = ThreadRecord(id="t1", name="T", state=state, last_chapter_id="c0")
        assert is_thread_stale(thread, chs) is False


def test_stale_threads_filters_a_mixed_list():
    chs = _chapters(5)
    fresh = ThreadRecord(id="fresh", name="Fresh", state=ThreadState.touched, last_chapter_id="c4")
    stale = ThreadRecord(id="stale", name="Stale", state=ThreadState.planted, last_chapter_id="c0")
    closed = ThreadRecord(id="closed", name="Closed", state=ThreadState.paid_off, last_chapter_id="c0")
    assert {t.id for t in stale_threads([fresh, stale, closed], chs)} == {"stale"}


@given(elapsed=st.integers(min_value=0, max_value=20))
@settings(max_examples=50)
def test_staleness_boundary_holds_for_any_elapsed_count(elapsed):
    """For any number of elapsed chapters, a non-terminal thread is stale iff
    elapsed >= STALENESS_THRESHOLD_CHAPTERS -- the boundary is exact, not off
    by one in either direction."""
    chs = _chapters(elapsed + 1)  # thread's last chapter is c0; elapsed chapters follow it
    thread = ThreadRecord(id="t1", name="T", state=ThreadState.touched, last_chapter_id="c0")
    assert chapters_elapsed_since("c0", chs) == elapsed
    assert is_thread_stale(thread, chs) is (elapsed >= STALENESS_THRESHOLD_CHAPTERS)

def test_find_stale_threads_respects_explicit_threshold():
    chs = _chapters(3)  # two chapters elapsed since c0
    thread = ThreadRecord(id="t1", name="T", state=ThreadState.planted, last_chapter_id="c0")
    assert is_thread_stale(thread, chs, threshold=3) is False
    assert is_thread_stale(thread, chs, threshold=1) is True
