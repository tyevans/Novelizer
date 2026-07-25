"""Mined thread facts must not resurrect a finished thread.

`commit_thread_intents` documents its contract explicitly: the id set it is
given is the thread's *non-terminal* ids, and an intent citing a terminal one is
"dropped with a logged warning and no event is committed"
(novelizer/agents/intents.py). Author, Editor and the chat service all filter by
TERMINAL_STATES before calling it. The Continuity Checker's mining path did not
-- it passed every thread, terminal included -- so the guard could never fire
for mined facts, even though the promise set built in the same method a hundred
lines below *is* filtered by TERMINAL_PROMISE_STATES.

The result was a phantom fact: a real thread.paid_off or thread.touched event
appended to the immutable log against an already-finished thread, which the
projection then no-ops (first-terminal-wins). Permanently in the log and in the
feed, absent from the read model -- the worst place for a disagreement to live.
"""
from __future__ import annotations
import os
import tempfile

import pytest

from novelizer.agents.continuity_checker import ContinuityChecker
from novelizer.agents.schemas import ContinuityOutput, MinedFactsOutput, MinedThreadFact
from novelizer.canon.committer import Committer
from novelizer.canon.event_store import EventStore
from novelizer.canon.events import EventType, ThreadPaidOff, ThreadPlanted
from novelizer.canon.projector import Projector
from novelizer.canon.read_store import ReadStore
from novelizer.store.models import Chapter


class FakeRunner:
    def __init__(self, out):
        self._out = out
        self.calls = []

    async def ainvoke(self, inputs):
        self.calls.append(inputs)
        return {"structured_response": self._out}


@pytest.fixture
async def stack():
    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    events = EventStore(path)
    await events.init()
    proj = Projector(events, path)
    await proj.init()
    read = ReadStore(path)
    await read.init()
    yield events, proj, read, Committer(events)
    await read.close()
    await proj.close()
    await events.close()
    os.unlink(path)


async def _paid_off_thread(events, proj):
    await events.append(EventType.THREAD_PLANTED, "the-lost-heir",
                        ThreadPlanted(id="the-lost-heir", name="The Lost Heir"))
    await events.append(EventType.CHAPTER_CREATED, "c1", Chapter(id="c1", title="One", prose="p"))
    await events.append(EventType.THREAD_PAID_OFF, "the-lost-heir",
                        ThreadPaidOff(id="the-lost-heir", chapter_id="c1"))
    # A later chapter, so the (thread, chapter) touch-dedupe cannot mask the
    # terminal-state guard we are actually testing.
    await events.append(EventType.CHAPTER_CREATED, "c2", Chapter(id="c2", title="Two", prose="p"))
    await proj.catch_up()


@pytest.mark.parametrize("action, event_type", [
    ("paid_off", EventType.THREAD_PAID_OFF),
    ("touch", EventType.THREAD_TOUCHED),
])
async def test_mined_fact_citing_a_terminal_thread_commits_nothing(stack, action, event_type):
    events, proj, read, committer = stack
    await _paid_off_thread(events, proj)
    before = len([e for e in await events.events_since(0) if e.event_type == event_type])

    mining_out = MinedFactsOutput(thread_facts=[
        MinedThreadFact(action=action, id="the-lost-heir", chapter_id="c2"),
    ])
    agent = ContinuityChecker(FakeRunner(ContinuityOutput()), FakeRunner(mining_out),
                              read, committer, events)
    await agent.run_once()

    after = len([e for e in await events.events_since(0) if e.event_type == event_type])
    assert after == before, (
        f"a mined {action!r} fact against a paid-off thread appended a {event_type} the "
        "projection can only no-op -- a phantom fact in the log"
    )


async def test_a_live_thread_still_accepts_a_mined_touch(stack):
    """The filter must not silence mining for threads that are still open."""
    events, proj, read, committer = stack
    await events.append(EventType.THREAD_PLANTED, "the-lost-heir",
                        ThreadPlanted(id="the-lost-heir", name="The Lost Heir"))
    await events.append(EventType.CHAPTER_CREATED, "c1", Chapter(id="c1", title="One", prose="p"))
    await proj.catch_up()

    mining_out = MinedFactsOutput(thread_facts=[
        MinedThreadFact(action="touch", id="the-lost-heir", chapter_id="c1"),
    ])
    agent = ContinuityChecker(FakeRunner(ContinuityOutput()), FakeRunner(mining_out),
                              read, committer, events)
    await agent.run_once()

    touches = [e for e in await events.events_since(0) if e.event_type == EventType.THREAD_TOUCHED]
    assert len(touches) == 1
