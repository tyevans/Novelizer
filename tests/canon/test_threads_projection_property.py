import asyncio
import os
import tempfile
from hypothesis import given, settings, strategies as st
from novelizer.canon.event_store import EventStore
from novelizer.canon.projector import Projector
from novelizer.canon.read_store import ReadStore
from novelizer.canon.events import (
    EventType, ThreadPlanted, ThreadTouched, ThreadPaidOff, ThreadAbandoned,
)
from novelizer.canon.threads import TERMINAL_STATES
from novelizer.store.models import ThreadState

TERMINAL = {ts for ts in ThreadState if ts.value in TERMINAL_STATES}

_ACTION_EVENTS = {
    "touch": (EventType.THREAD_TOUCHED, ThreadTouched, ThreadState.touched),
    "pay_off": (EventType.THREAD_PAID_OFF, ThreadPaidOff, ThreadState.paid_off),
    "abandon": (EventType.THREAD_ABANDONED, ThreadAbandoned, ThreadState.abandoned),
}


def _expected_state(actions: list[str]) -> tuple[ThreadState, int]:
    """Pure re-implementation of the state machine, independent of the
    Projector's SQL, used as the property test's oracle."""
    state = ThreadState.planted
    touch_count = 0
    for action in actions:
        if action == "plant":
            continue  # first-plant-wins: a re-plant of an existing id is always a no-op
        if state in TERMINAL:
            continue  # absorbing: any event after a terminal state is a no-op
        _, _, new_state = _ACTION_EVENTS[action]
        if action == "touch":
            touch_count += 1
        state = new_state
    return state, touch_count


async def _run_sequence(actions: list[str]) -> tuple[ThreadState, int, ThreadState, int]:
    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    try:
        events = EventStore(path)
        await events.init()
        proj = Projector(events, path)
        await proj.init()
        read = ReadStore(path)
        await read.init()

        await events.append(EventType.THREAD_PLANTED, "t1", ThreadPlanted(id="t1", name="The Locket"))
        for action in actions:
            if action == "plant":
                await events.append(EventType.THREAD_PLANTED, "t1", ThreadPlanted(id="t1", name="The Locket"))
                continue
            event_type, payload_cls, _ = _ACTION_EVENTS[action]
            await events.append(event_type, "t1", payload_cls(id="t1"))
        await proj.catch_up()
        record = await read.get_thread("t1")
        incremental_state, incremental_count = record.state, record.touch_count

        # Rebuild equivalence: a fresh projector replaying from zero agrees.
        proj2 = Projector(events, path)
        await proj2.init()
        await proj2._reset_state()
        await proj2.catch_up()
        rebuilt = await read.get_thread("t1")
        rebuilt_state, rebuilt_count = rebuilt.state, rebuilt.touch_count
        await proj2.close()

        await read.close()
        await proj.close()
        await events.close()
        return incremental_state, incremental_count, rebuilt_state, rebuilt_count
    finally:
        os.unlink(path)


@given(st.lists(st.sampled_from(["touch", "pay_off", "abandon", "plant"]), max_size=8))
@settings(max_examples=50, deadline=None)
def test_thread_state_machine_holds_for_any_event_sequence(actions):
    """For any interleaving of touch/pay_off/abandon events following a plant,
    the projected state and touch count match the pure state-machine oracle
    (including absorbing terminal states), and a from-scratch rebuild agrees
    with the incrementally-projected result (replay idempotence)."""
    incremental_state, incremental_count, rebuilt_state, rebuilt_count = asyncio.run(_run_sequence(actions))
    expected_state, expected_count = _expected_state(actions)
    assert incremental_state == expected_state
    assert incremental_count == expected_count
    assert rebuilt_state == expected_state
    assert rebuilt_count == expected_count
