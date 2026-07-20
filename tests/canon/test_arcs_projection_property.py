import asyncio
import os
import tempfile
import uuid

from hypothesis import given, settings
from hypothesis import strategies as st

from novelizer.canon.event_store import EventStore
from novelizer.canon.events import (
    EventType, ArcDeclared, ArcPivotPlanned, ArcAdvanced, ArcResolved,
)
from novelizer.canon.projector import Projector
from novelizer.canon.read_store import ReadStore

ACTIONS = ["declared", "pivot", "advanced", "resolved"]
CHARACTER_ID = "c1"


def _expected(actions: list[str]):
    """Pure oracle for character c1's arc history, independent of SQL.

    Each "declared" mints a new arc id and deactivates any prior arc for
    c1 (the new one becomes the sole active arc). advanced/resolved/pivot
    apply to the most recently declared arc id. Returns a dict of
    arc_id -> {active, resolved, advance_count, outcome} plus the ordered
    list of declared arc ids (for identity/order assertions).
    """
    arcs: dict[str, dict] = {}
    order: list[str] = []
    current_id = None
    for a in actions:
        if a == "declared":
            new_id = f"arc-{len(order)}"
            if current_id is not None:
                arcs[current_id]["active"] = False
            arcs[new_id] = {
                "active": True, "resolved": False, "advance_count": 0, "outcome": "",
            }
            order.append(new_id)
            current_id = new_id
        elif current_id is None:
            continue  # no arc declared yet -- no-op
        elif a == "pivot":
            pass  # pivots don't affect this oracle's tracked fields
        elif a == "advanced":
            if not arcs[current_id]["resolved"]:
                arcs[current_id]["advance_count"] += 1
        elif a == "resolved":
            if not arcs[current_id]["resolved"]:
                arcs[current_id]["resolved"] = True
                arcs[current_id]["outcome"] = "truth_embraced"
    return arcs, order


async def _run(actions: list[str]):
    fd, path = tempfile.mkstemp(suffix=".db"); os.close(fd)
    try:
        events = EventStore(path); await events.init()
        proj = Projector(events, path); await proj.init()
        read = ReadStore(path); await read.init()

        current_id = None
        n = 0
        for a in actions:
            if a == "declared":
                new_id = f"arc-{n}"; n += 1
                await events.append(EventType.ARC_DECLARED, new_id, ArcDeclared(
                    arc_id=new_id, character_id=CHARACTER_ID, arc_type="positive",
                ))
                current_id = new_id
            elif current_id is None:
                continue
            elif a == "pivot":
                await events.append(EventType.ARC_PIVOT_PLANNED, current_id, ArcPivotPlanned(
                    arc_id=current_id, beat_id="b1", description="pivot",
                ))
            elif a == "advanced":
                await events.append(EventType.ARC_ADVANCED, current_id, ArcAdvanced(
                    arc_id=current_id, chapter_id="ch1", note="n",
                ))
            elif a == "resolved":
                await events.append(EventType.ARC_RESOLVED, current_id, ArcResolved(
                    arc_id=current_id, chapter_id="ch2", outcome="truth_embraced",
                ))

        await proj.catch_up()
        incremental = await read.list_arcs()

        proj2 = Projector(events, path)
        await proj2.init()
        await proj2._reset_state()
        await proj2.catch_up()
        rebuilt = await read.list_arcs()
        await proj2.close()
        await read.close(); await proj.close(); await events.close()
        return incremental, rebuilt
    finally:
        os.unlink(path)


@settings(max_examples=50, deadline=None)
@given(st.lists(st.sampled_from(ACTIONS), max_size=8))
def test_arc_state_machine_holds_for_any_event_sequence(actions):
    incremental, rebuilt = asyncio.run(_run(actions))

    assert [(a.id, a.active, a.resolved, a.advance_count, a.outcome) for a in incremental] == \
           [(a.id, a.active, a.resolved, a.advance_count, a.outcome) for a in rebuilt]

    expected_arcs, order = _expected(actions)

    assert {a.id for a in incremental} == set(order)

    active_arcs = [a for a in incremental if a.active]
    if order:
        assert len(active_arcs) == 1
    else:
        assert active_arcs == []

    for a in incremental:
        exp = expected_arcs[a.id]
        assert a.active == exp["active"]
        assert a.resolved == exp["resolved"]
        assert a.advance_count == exp["advance_count"]
        assert a.outcome == exp["outcome"]
