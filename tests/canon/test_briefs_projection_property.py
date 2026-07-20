import asyncio
import os
import tempfile

from hypothesis import given, settings
from hypothesis import strategies as st

from novelizer.canon.event_store import EventStore
from novelizer.canon.events import (
    EventType, ChapterBriefDrafted, ChapterBriefSuperseded, ChapterBriefFulfilled,
)
from novelizer.canon.projector import Projector
from novelizer.canon.read_store import ReadStore

ACTIONS = ["drafted", "superseded", "fulfilled"]
_EVENT_BY_ACTION = {
    "drafted": (EventType.CHAPTER_BRIEF_DRAFTED, lambda: ChapterBriefDrafted(brief_id="r1", target_ordinal=3, goal="g")),
    "superseded": (EventType.CHAPTER_BRIEF_SUPERSEDED, lambda: ChapterBriefSuperseded(brief_id="r1", superseded_by_brief_id="r2")),
    "fulfilled": (EventType.CHAPTER_BRIEF_FULFILLED, lambda: ChapterBriefFulfilled(brief_id="r1", chapter_id="c9")),
}


def _expected(actions: list[str]):
    """Pure oracle for the r1 brief state machine, independent of SQL."""
    state, exists = None, False
    for a in actions:
        if not exists:
            if a == "drafted":
                exists, state = True, "open"
            continue
        if state in ("superseded", "fulfilled"):
            continue
        if a == "superseded":
            state = "superseded"
        elif a == "fulfilled":
            state = "fulfilled"
        # a second "drafted" for an existing id: first-draft-wins no-op
    return exists, state


async def _run(actions: list[str]):
    fd, path = tempfile.mkstemp(suffix=".db"); os.close(fd)
    try:
        events = EventStore(path); await events.init()
        proj = Projector(events, path); await proj.init()
        read = ReadStore(path); await read.init()
        for a in actions:
            etype, mk = _EVENT_BY_ACTION[a]
            await events.append(etype, "r1", mk())
        await proj.catch_up()
        incremental = await read.list_briefs()
        # rebuild equivalence: fresh projector replaying from zero must agree.
        proj2 = Projector(events, path)
        await proj2.init()
        await proj2._reset_state()
        await proj2.catch_up()
        rebuilt = await read.list_briefs()
        await proj2.close()
        await read.close(); await proj.close(); await events.close()
        return incremental, rebuilt
    finally:
        os.unlink(path)


@settings(max_examples=50, deadline=None)
@given(st.lists(st.sampled_from(ACTIONS), max_size=8))
def test_brief_state_machine_holds_for_any_event_sequence(actions):
    incremental, rebuilt = asyncio.run(_run(actions))
    exists, state = _expected(actions)
    assert [(b.id, b.status.value) for b in incremental] == \
           [(b.id, b.status.value) for b in rebuilt]
    if not exists:
        assert incremental == []
    else:
        assert len(incremental) == 1
        b = incremental[0]
        assert b.status.value == state
