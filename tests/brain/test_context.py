from novelizer.brain.context import stale_threads_note, pacing_flags_note
from novelizer.store.models import Chapter, ThreadRecord, ThreadState, StructureScore


def _chapters(n: int) -> list[Chapter]:
    return [Chapter(id=f"c{i}", title=str(i), prose="p") for i in range(n)]


def test_stale_threads_note_empty_when_nothing_stale():
    chs = _chapters(2)
    fresh = ThreadRecord(id="t1", name="Fresh", state=ThreadState.touched, last_chapter_id="c1")
    assert stale_threads_note([fresh], chs) == ""


def test_stale_threads_note_lists_stale_thread_name_and_id():
    chs = _chapters(5)
    stale = ThreadRecord(id="the-locket", name="The Locket", state=ThreadState.planted, last_chapter_id="c0")
    note = stale_threads_note([stale], chs)
    assert "The Locket" in note
    assert "the-locket" in note
    assert note.startswith("\n\n")


def test_stale_threads_note_omits_terminal_threads():
    chs = _chapters(10)
    closed = ThreadRecord(id="t1", name="Closed", state=ThreadState.paid_off, last_chapter_id="c0")
    assert stale_threads_note([closed], chs) == ""


def test_pacing_flags_note_empty_when_no_flags():
    scores = [StructureScore(chapter_id=f"c{i}", tension=0.5, pacing_label="steady") for i in range(3)]
    assert pacing_flags_note(scores) == ""


def test_pacing_flags_note_lists_flagged_chapter_and_direction():
    scores = [
        StructureScore(chapter_id="c1", tension=0.9, pacing_label="climax"),
        StructureScore(chapter_id="c2", tension=0.1, pacing_label="flat"),
        StructureScore(chapter_id="c3", tension=0.85, pacing_label="climax"),
    ]
    note = pacing_flags_note(scores)
    assert "c2" in note and "sag" in note
    assert note.startswith("\n\n")
