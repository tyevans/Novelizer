"""The Retconner can decline, and it stays in its lane.

Two defects this covers:
  * Every non-None result was marked RESOLVED, so "repaired", "already fine"
    and "this request is nonsense" were indistinguishable in the log --
    while RETCON_REQUEST_REJECTED existed unused.
  * Voice-drift retcons carry CHARACTER ids in conflicting_entry_ids, but
    poll() only loads world entries and commit() always built a WorldEntry,
    producing an entry that supersedes nothing.

See docs/agent-prompting/proposal-retconner.md §1, §4.
"""
from __future__ import annotations

import os
import tempfile

import pytest

from novelizer.agents.retconner import Retconner
from novelizer.agents.schemas import RetconAmendments
from novelizer.canon.committer import Committer
from novelizer.canon.event_store import EventStore
from novelizer.canon.events import EventType
from novelizer.canon.projector import Projector
from novelizer.canon.read_store import ReadStore
from novelizer.store.models import Character, RetconRequest, RetconStatus, WorldEntry


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


async def _file_request(events, proj, **kw):
    req = RetconRequest(
        id=kw.pop("id", "r1"),
        description=kw.pop("description", "two suns vs one sun"),
        conflicting_entry_ids=kw.pop("conflicting_entry_ids", ["w1"]),
        proposed_resolution=kw.pop("proposed_resolution", "make it one sun"),
        **kw,
    )
    await events.append(EventType.RETCON_REQUEST_CREATED, req.id, req)
    await proj.catch_up()
    return req


class TestDeclinePath:
    async def test_already_consistent_rejects_without_amending(self, stack):
        """The report was captured on an earlier pass; the paradox may already
        be gone. Recording that as 'resolved' would claim a repair that never
        happened."""
        events, proj, read, committer = stack
        await events.append(EventType.WORLD_ENTRY_CREATED, "w1", WorldEntry(id="w1", title="Sky", body="One sun."))
        await _file_request(events, proj)
        out = RetconAmendments(resolution="already_consistent", reason="live canon says one sun")
        r = Retconner(FakeRunner(out), read, committer)
        await r.run_once()
        await proj.catch_up()
        assert await read.list_world_entries() == [
            e for e in await read.list_world_entries() if e.id == "w1"
        ]
        assert len(await read.list_world_entries()) == 1
        assert await read.list_retcon_requests(status=RetconStatus.open) == []

    async def test_amend_still_supersedes_and_resolves(self, stack):
        events, proj, read, committer = stack
        await events.append(EventType.WORLD_ENTRY_CREATED, "w1", WorldEntry(id="w1", title="Sky", body="Two suns."))
        await _file_request(events, proj)
        out = RetconAmendments(
            resolution="amend",
            amended_entries=[
                {"title": "Sky", "body": "One sun.", "domain": "physical", "tags": [], "supersedes_id": "w1"}
            ],
        )
        r = Retconner(FakeRunner(out), read, committer)
        await r.run_once()
        await proj.catch_up()
        bodies = [e.body for e in await read.list_world_entries()]
        assert "One sun." in bodies
        assert await read.list_retcon_requests(status=RetconStatus.open) == []

    async def test_default_resolution_is_amend_for_back_compat(self):
        assert RetconAmendments().resolution == "amend"


class TestLaneGuard:
    async def test_character_only_request_is_declined_without_an_llm_call(self, stack):
        """A voice-drift retcon names character ids. Building a WorldEntry for
        it produces an orphan that supersedes nothing, so it never reaches the
        model."""
        events, proj, read, committer = stack
        await events.append(EventType.CHARACTER_CREATED, "mara", Character(id="mara", name="Mara"))
        await _file_request(events, proj, conflicting_entry_ids=["mara"])
        runner = FakeRunner(RetconAmendments())
        r = Retconner(runner, read, committer)
        await r.run_once()
        await proj.catch_up()
        assert runner.calls == []
        assert await read.list_world_entries() == []
        assert await read.list_retcon_requests(status=RetconStatus.open) == []

    async def test_request_naming_a_world_entry_still_runs(self, stack):
        events, proj, read, committer = stack
        await events.append(EventType.WORLD_ENTRY_CREATED, "w1", WorldEntry(id="w1", title="Sky", body="Two suns."))
        await _file_request(events, proj, conflicting_entry_ids=["w1"])
        runner = FakeRunner(RetconAmendments(resolution="already_consistent", reason="fine"))
        r = Retconner(runner, read, committer)
        await r.run_once()
        assert len(runner.calls) == 1
