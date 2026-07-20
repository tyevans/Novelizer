from novelizer.brain.resolution_pacing import congested_windows, overdue_resolutions, overdue_reveals
from novelizer.store.models import Chapter, SecretRecord, ThreadRecord, ThreadState


def _chapters(n):
    return [Chapter(id=f"c{i}", title=str(i), prose="p") for i in range(n)]


def test_overdue_resolution_past_window():
    t = ThreadRecord(id="t", name="T", window_lo=2, window_hi=3)
    assert overdue_resolutions([t], _chapters(4)) == [t]
    assert overdue_resolutions([t], _chapters(3)) == []


def test_terminal_thread_never_overdue():
    t = ThreadRecord(id="t", name="T", state=ThreadState.paid_off, window_hi=1)
    assert overdue_resolutions([t], _chapters(9)) == []


def test_overdue_reveal_only_when_unrevealed():
    s = SecretRecord(id="s", title="S", reveal_window_lo=1, reveal_window_hi=2)
    assert overdue_reveals([s], _chapters(3)) == [s]
    revealed = s.model_copy(update={"revealed": True})
    assert overdue_reveals([revealed], _chapters(3)) == []


def test_congestion_groups_overlapping_windows():
    ts = [ThreadRecord(id=f"t{i}", name=str(i), window_lo=19, window_hi=21) for i in range(3)]
    spans = congested_windows(ts, [], max_per_window=2)
    assert spans == [(19, 21, 3)]


def test_no_congestion_below_threshold_or_disjoint():
    ts = [ThreadRecord(id="a", name="a", window_lo=1, window_hi=2),
          ThreadRecord(id="b", name="b", window_lo=5, window_hi=6)]
    assert congested_windows(ts, [], max_per_window=2) == []
