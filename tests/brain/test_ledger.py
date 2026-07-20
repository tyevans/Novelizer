from novelizer.brain.ledger import due_promises, open_promises, overdue_promises
from novelizer.store.models import Chapter, PromiseRecord, PromiseState


def _chapters(n):
    return [Chapter(id=f"c{i}", title=str(i), prose="p") for i in range(n)]


def test_open_promises_excludes_terminal_states():
    ps = [PromiseRecord(id="a", name="A"),
          PromiseRecord(id="b", name="B", state=PromiseState.paid),
          PromiseRecord(id="c", name="C", state=PromiseState.released)]
    assert [p.id for p in open_promises(ps)] == ["a"]


def test_overdue_promise_past_window_hi():
    p = PromiseRecord(id="a", name="A", window_lo=2, window_hi=3)
    assert overdue_promises([p], _chapters(4)) == [p]
    assert overdue_promises([p], _chapters(3)) == []


def test_unset_window_never_overdue_or_due():
    p = PromiseRecord(id="a", name="A")
    assert overdue_promises([p], _chapters(10)) == []
    assert due_promises([p], _chapters(10)) == []


def test_due_promise_inside_window():
    p = PromiseRecord(id="a", name="A", window_lo=2, window_hi=4)
    assert due_promises([p], _chapters(1)) == []
    assert due_promises([p], _chapters(3)) == [p]


def test_terminal_promise_never_overdue():
    p = PromiseRecord(id="a", name="A", window_hi=1, state=PromiseState.paid)
    assert overdue_promises([p], _chapters(5)) == []
