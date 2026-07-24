"""FlagLabeler: a quick post-filing LLM pass that gives every open flag a short
title and one-sentence summary via a narrow FLAG_LABELED event, so the flags/
escalations UI no longer has to fake a title from a raw description prefix."""
import os
import tempfile
import pytest
from novelizer.canon.event_store import EventStore
from novelizer.canon.projector import Projector
from novelizer.canon.read_store import ReadStore
from novelizer.canon.committer import Committer
from novelizer.canon.events import EventType
from novelizer.agents.flaglabeler import FlagLabeler, MAX_TITLE_CHARS
from novelizer.agents.schemas import FlagLabel
from novelizer.store.models import Flag, FlagStatus


class FakeRunner:
    def __init__(self, out):
        self._out = out
        self.calls = []

    async def ainvoke(self, inputs):
        self.calls.append(inputs)
        return {"structured_response": self._out}


@pytest.fixture
async def stack():
    fd, path = tempfile.mkstemp(suffix=".db"); os.close(fd)
    events = EventStore(path); await events.init()
    proj = Projector(events, path); await proj.init()
    read = ReadStore(path); await read.init()
    yield events, proj, read, Committer(events)
    await read.close(); await proj.close(); await events.close(); os.unlink(path)


async def _file_flag(events, proj, **kw):
    kw.setdefault("category", "pacing")
    kw.setdefault("description", "the second act sags for three chapters running")
    flag = Flag(**kw)
    await events.append(EventType.FLAG_CREATED, flag.id, flag)
    await proj.catch_up()
    return flag


async def test_labels_untitled_open_flag(stack):
    events, proj, read, committer = stack
    filed = await _file_flag(events, proj, id="f1", filed_by="structure_analyst")
    runner = FakeRunner(FlagLabel(title="Sagging second act", summary="The middle act loses momentum."))
    agent = FlagLabeler(runner, read, committer)

    await agent.run_once()
    await proj.catch_up()

    flag = (await read.list_flags(status=FlagStatus.open))[0]
    assert flag.id == "f1"
    assert flag.title == "Sagging second act"
    assert flag.summary == "The middle act loses momentum."
    # untouched fields survive the re-commit
    assert flag.category == "pacing"
    assert flag.filed_by == "structure_analyst"
    assert flag.description == filed.description
    assert len(runner.calls) == 1


async def test_skips_already_titled_flag(stack):
    events, proj, read, committer = stack
    await _file_flag(events, proj, id="f1", title="Already labelled")
    runner = FakeRunner(FlagLabel(title="x", summary="y"))
    agent = FlagLabeler(runner, read, committer)

    await agent.run_once()

    assert runner.calls == []  # no LLM pass for a flag that already has a title
    log = await events.events_since(0, event_types=[EventType.FLAG_LABELED])
    assert log == []  # nothing labelled


async def test_idempotent_after_labeling(stack):
    events, proj, read, committer = stack
    await _file_flag(events, proj, id="f1")
    runner = FakeRunner(FlagLabel(title="Sagging second act", summary="Momentum dips."))
    agent = FlagLabeler(runner, read, committer)

    await agent.run_once()
    await proj.catch_up()
    calls_after_first = len(runner.calls)

    await agent.run_once()  # flag now titled: nothing left to label
    assert len(runner.calls) == calls_after_first
    labels = await events.events_since(0, event_types=[EventType.FLAG_LABELED])
    assert len(labels) == 1  # labelled exactly once, never again
    created = await events.events_since(0, event_types=[EventType.FLAG_CREATED])
    assert len(created) == 1  # the flag was never re-created


async def test_only_open_flags_are_labelled(stack):
    events, proj, read, committer = stack
    resolved = Flag(id="f1", category="pacing", description="was a problem", status=FlagStatus.rejected)
    await events.append(EventType.FLAG_REJECTED, resolved.id, resolved)
    await proj.catch_up()
    runner = FakeRunner(FlagLabel(title="x", summary="y"))
    agent = FlagLabeler(runner, read, committer)

    await agent.run_once()

    assert runner.calls == []  # closed flags are not labelled


async def test_long_title_is_capped(stack):
    events, proj, read, committer = stack
    await _file_flag(events, proj, id="f1")
    runner = FakeRunner(FlagLabel(title="word " * 100, summary="a summary"))
    agent = FlagLabeler(runner, read, committer)

    await agent.run_once()
    await proj.catch_up()

    flag = (await read.list_flags(status=FlagStatus.open))[0]
    assert len(flag.title) <= MAX_TITLE_CHARS


async def test_readiness_zero_when_all_titled(stack):
    events, proj, read, committer = stack
    await _file_flag(events, proj, id="f1", title="labelled")
    agent = FlagLabeler(FakeRunner(FlagLabel(title="x", summary="y")), read, committer)
    assert await agent.readiness() == 0.0


async def test_readiness_positive_when_untitled_pending(stack):
    events, proj, read, committer = stack
    await _file_flag(events, proj, id="f1")
    agent = FlagLabeler(FakeRunner(FlagLabel(title="x", summary="y")), read, committer)
    assert await agent.readiness() > 0.0
