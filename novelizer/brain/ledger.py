from novelizer.store.models import Chapter, PromiseRecord, PromiseState


def open_promises(promises: list[PromiseRecord]) -> list[PromiseRecord]:
    return [p for p in promises if p.state == PromiseState.open]


def overdue_promises(promises: list[PromiseRecord], chapters: list[Chapter]) -> list[PromiseRecord]:
    now = len(chapters)
    return [p for p in open_promises(promises) if p.window_hi > 0 and now > p.window_hi]


def due_promises(promises: list[PromiseRecord], chapters: list[Chapter]) -> list[PromiseRecord]:
    now = len(chapters)
    return [
        p for p in open_promises(promises)
        if p.window_hi > 0 and p.window_lo <= now <= p.window_hi
    ]
