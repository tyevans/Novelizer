import asyncio
import os
import tempfile

from hypothesis import given, settings
from hypothesis import strategies as st

from novelizer.canon.event_store import EventStore
from novelizer.canon.events import (
    EventType, PromiseMade, PromiseProgressed, PromisePaid, PromiseReleased,
)
from novelizer.canon.projector import Projector
from novelizer.canon.read_store import ReadStore

ACTIONS = ["made", "progressed", "paid", "released"]
_EVENT_BY_ACTION = {
    "made": (EventType.PROMISE_MADE, lambda: PromiseMade(id="p1", name="P One")),
    "progressed": (EventType.PROMISE_PROGRESSED, lambda: PromiseProgressed(id="p1", note="n")),
    "paid": (EventType.PROMISE_PAID, lambda: PromisePaid(id="p1")),
    "released": (EventType.PROMISE_RELEASED, lambda: PromiseReleased(id="p1", reason="r")),
}


def _expected(actions: list[str]):
    """Pure oracle for the p1 promise state machine, independent of SQL."""
    state, progress, exists = None, 0, False
    for a in actions:
        if not exists:
            if a == "made":
                exists, state = True, "open"
            continue
        if state in ("paid", "released"):
            continue
        if a == "progressed":
            progress += 1
        elif a == "paid":
            state = "paid"
        elif a == "released":
            state = "released"
        # a second "made" for an existing id: first-make-wins no-op
    return exists, state, progress


async def _run(actions: list[str]):
    fd, path = tempfile.mkstemp(suffix=".db"); os.close(fd)
    try:
        events = EventStore(path); await events.init()
        proj = Projector(events, path); await proj.init()
        read = ReadStore(path); await read.init()
        for a in actions:
            etype, mk = _EVENT_BY_ACTION[a]
            await events.append(etype, "p1", mk())
        await proj.catch_up()
        incremental = await read.list_promises()
        # rebuild equivalence: fresh projector replaying from zero must agree.
        proj2 = Projector(events, path)
        await proj2.init()
        await proj2._reset_state()
        await proj2.catch_up()
        rebuilt = await read.list_promises()
        await proj2.close()
        await read.close(); await proj.close(); await events.close()
        return incremental, rebuilt
    finally:
        os.unlink(path)


@settings(max_examples=50, deadline=None)
@given(st.lists(st.sampled_from(ACTIONS), max_size=8))
def test_promise_state_machine_holds_for_any_event_sequence(actions):
    incremental, rebuilt = asyncio.run(_run(actions))
    exists, state, progress = _expected(actions)
    assert [(p.id, p.state.value, p.progress_count) for p in incremental] == \
           [(p.id, p.state.value, p.progress_count) for p in rebuilt]
    if not exists:
        assert incremental == []
    else:
        assert len(incremental) == 1
        p = incremental[0]
        assert p.state.value == state and p.progress_count == progress
