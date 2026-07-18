from novelizer.tui.widgets.thread_board import thread_board_line
from novelizer.store.models import Chapter, ThreadRecord, ThreadState


def _chapters(n: int) -> list[Chapter]:
    return [Chapter(id=f"c{i}", title=str(i), prose="p") for i in range(n)]


def test_fresh_thread_line_shows_state_not_stale():
    chs = _chapters(2)
    t = ThreadRecord(id="t1", name="Fresh Thread", state=ThreadState.touched, last_chapter_id="c1")
    line = thread_board_line(t, chs)
    assert "Fresh Thread" in line and "STALE" not in line
    assert "touched" in line


def test_stale_thread_line_flags_stale():
    chs = _chapters(5)
    t = ThreadRecord(id="the-locket", name="The Locket", state=ThreadState.planted, last_chapter_id="c0")
    line = thread_board_line(t, chs)
    assert "The Locket" in line and "STALE" in line


def test_terminal_thread_never_flagged_stale():
    chs = _chapters(10)
    t = ThreadRecord(id="t1", name="Closed", state=ThreadState.paid_off, last_chapter_id="c0")
    line = thread_board_line(t, chs)
    assert "STALE" not in line and "paid_off" in line
