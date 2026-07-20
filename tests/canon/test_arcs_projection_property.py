import asyncio
import os
import tempfile

from hypothesis import given, settings
from hypothesis import strategies as st

from novelizer.canon.event_store import EventStore
from novelizer.canon.events import (
    EventType, ArcDeclared, ArcPivotPlanned, ArcAdvanced, ArcResolved,
)
from novelizer.canon.projector import Projector
from novelizer.canon.read_store import ReadStore

ACTION_KINDS = ["declared", "pivot", "advanced", "resolved"]
CHARACTER_IDS = ["c1", "c2"]

ACTIONS = st.tuples(st.sampled_from(CHARACTER_IDS), st.sampled_from(ACTION_KINDS))


def _expected(actions: list[tuple[str, str]]):
    """Pure oracle for c1's and c2's arc histories, independent of SQL and
    of each other -- each character's declared/pivot/advanced/resolved
    stream is tracked in its own chain, keyed by deterministic arc ids
    ("arc-{character}-{n}") so interleaving the two characters' actions
    cannot cross-contaminate their state.

    Each "declared" mints a new arc id for that character and deactivates
    any prior arc for that character (the new one becomes that character's
    sole active arc). advanced/resolved/pivot apply to the most recently
    declared arc id for that character. Returns arcs (arc_id -> state) and,
    per character, the ordered list of declared arc ids.
    """
    arcs: dict[str, dict] = {}
    order_by_character: dict[str, list[str]] = {c: [] for c in CHARACTER_IDS}
    current_id_by_character: dict[str, str | None] = {c: None for c in CHARACTER_IDS}

    for character_id, a in actions:
        order = order_by_character[character_id]
        current_id = current_id_by_character[character_id]
        if a == "declared":
            new_id = f"arc-{character_id}-{len(order)}"
            if current_id is not None:
                arcs[current_id]["active"] = False
            arcs[new_id] = {
                "active": True, "resolved": False, "advance_count": 0, "outcome": "",
                "character_id": character_id,
            }
            order.append(new_id)
            current_id_by_character[character_id] = new_id
        elif current_id is None:
            continue  # no arc declared yet for this character -- no-op
        elif a == "pivot":
            pass  # pivots don't affect this oracle's tracked fields
        elif a == "advanced":
            if not arcs[current_id]["resolved"]:
                arcs[current_id]["advance_count"] += 1
        elif a == "resolved":
            if not arcs[current_id]["resolved"]:
                arcs[current_id]["resolved"] = True
                arcs[current_id]["outcome"] = "truth_embraced"

    return arcs, order_by_character


async def _run(actions: list[tuple[str, str]]):
    fd, path = tempfile.mkstemp(suffix=".db"); os.close(fd)
    try:
        events = EventStore(path); await events.init()
        proj = Projector(events, path); await proj.init()
        read = ReadStore(path); await read.init()

        current_id_by_character: dict[str, str | None] = {c: None for c in CHARACTER_IDS}
        n_by_character: dict[str, int] = {c: 0 for c in CHARACTER_IDS}
        for character_id, a in actions:
            current_id = current_id_by_character[character_id]
            if a == "declared":
                n = n_by_character[character_id]
                new_id = f"arc-{character_id}-{n}"
                n_by_character[character_id] = n + 1
                await events.append(EventType.ARC_DECLARED, new_id, ArcDeclared(
                    arc_id=new_id, character_id=character_id, arc_type="positive",
                ))
                current_id_by_character[character_id] = new_id
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
@given(st.lists(ACTIONS, max_size=12))
def test_arc_state_machine_holds_for_any_event_sequence(actions):
    incremental, rebuilt = asyncio.run(_run(actions))

    assert [(a.id, a.active, a.resolved, a.advance_count, a.outcome) for a in incremental] == \
           [(a.id, a.active, a.resolved, a.advance_count, a.outcome) for a in rebuilt]

    expected_arcs, order_by_character = _expected(actions)
    all_order = [arc_id for character_id in CHARACTER_IDS for arc_id in order_by_character[character_id]]

    assert {a.id for a in incremental} == set(all_order)

    for character_id in CHARACTER_IDS:
        active_arcs = [a for a in incremental if a.character_id == character_id and a.active]
        if order_by_character[character_id]:
            assert len(active_arcs) == 1
        else:
            assert active_arcs == []

    for a in incremental:
        exp = expected_arcs[a.id]
        assert a.character_id == exp["character_id"]
        assert a.active == exp["active"]
        assert a.resolved == exp["resolved"]
        assert a.advance_count == exp["advance_count"]
        assert a.outcome == exp["outcome"]
