"""Attributor: deterministic parse/resolve/commit of the Author's inline
speaker markup into chapter.attributed. The model is called only to repair
markup the parser flags, and a failed repair still commits what was parsed."""
import os
import tempfile
import pytest
from novelizer.agents.attributor import Attributor
from novelizer.agents.schemas import RepairedMarkup
from novelizer.canon.committer import Committer
from novelizer.canon.event_store import EventStore
from novelizer.canon.events import ChapterRevised, EventType
from novelizer.canon.flags import mark_declined
from novelizer.canon.projector import Projector
from novelizer.canon.read_store import ReadStore
from novelizer.store.models import Chapter, Character, Flag, FlagStatus


class _Runner:
    """The Attributor must not call the model on well-formed markup."""

    def __init__(self):
        self.calls = 0

    async def ainvoke(self, _payload):
        self.calls += 1
        return {"structured_response": None}


@pytest.fixture
async def stack():
    fd, path = tempfile.mkstemp(suffix=".db"); os.close(fd)
    events = EventStore(path); await events.init()
    proj = Projector(events, path); await proj.init()
    read = ReadStore(path); await read.init()
    yield events, proj, read, Committer(events)
    await read.close(); await proj.close(); await events.close(); os.unlink(path)


@pytest.mark.asyncio
async def test_attributes_a_clean_chapter_without_calling_the_model(stack):
    events, proj, read, committer = stack
    await events.append(EventType.CHAPTER_CREATED, "ch1",
                        Chapter(id="ch1", title="One",
                                prose='He waited. <speech char="Mira">"Twenty."</speech>'))
    await events.append(EventType.CHARACTER_CREATED, "mira",
                        Character(id="mira", name="Mira", aliases=[]))
    await proj.catch_up()
    runner = _Runner()
    agent = Attributor(runner, read, committer, events)

    await agent.run_once()
    await proj.catch_up()

    log = await events.events_since(0, event_types=[EventType.CHAPTER_ATTRIBUTED])
    assert len(log) == 1
    payload = log[0].payload
    assert payload["prose"] == 'He waited. "Twenty."'
    assert [s["kind"] for s in payload["segments"]] == ["narration", "speech"]
    assert payload["segments"][1]["character_id"] == "mira"
    assert runner.calls == 0


@pytest.mark.asyncio
async def test_unresolvable_speaker_gets_a_null_id_and_raises_a_flag(stack):
    events, proj, read, committer = stack
    await events.append(EventType.CHAPTER_CREATED, "ch1",
                        Chapter(id="ch1", title="One", prose='<speech char="Nobody">"Hi."</speech>'))
    await events.append(EventType.CHARACTER_CREATED, "mira",
                        Character(id="mira", name="Mira", aliases=[]))
    await proj.catch_up()
    agent = Attributor(_Runner(), read, committer, events)

    await agent.run_once()
    await proj.catch_up()

    log = await events.events_since(0, event_types=[EventType.CHAPTER_ATTRIBUTED])
    payload = log[0].payload
    assert payload["segments"][0]["character_id"] is None
    assert payload["segments"][0]["character_name"] == "Nobody"
    flags = await events.events_since(0, event_types=[EventType.FLAG_CREATED])
    assert flags, "unresolved speaker must be flagged"


@pytest.mark.asyncio
async def test_malformed_markup_still_commits_and_flags(stack):
    events, proj, read, committer = stack
    await events.append(EventType.CHAPTER_CREATED, "ch1",
                        Chapter(id="ch1", title="One", prose='<speech char="Mira">"Hi."'))
    await proj.catch_up()
    agent = Attributor(_Runner(), read, committer, events)

    await agent.run_once()
    await proj.catch_up()

    attributed = await events.events_since(0, event_types=[EventType.CHAPTER_ATTRIBUTED])
    assert attributed, "a malformed chapter must not block"
    flags = await events.events_since(0, event_types=[EventType.FLAG_CREATED])
    assert flags


class _RepairingRunner:
    """Simulates a real deep-agent runner: response_format=RepairedMarkup, so a
    successful call returns a structured_response of that model -- the shape
    create_deep_agent actually produces (verified against
    deepagents/middleware/subagents.py), not the shape the code under test
    assumes."""

    def __init__(self, repaired: str):
        self._repaired = repaired
        self.calls = 0

    async def ainvoke(self, _payload):
        self.calls += 1
        return {"structured_response": RepairedMarkup(prose=self._repaired)}


@pytest.mark.asyncio
async def test_successful_repair_is_reparsed_and_adopted(stack):
    """The reparse-and-adopt branch: malformed markup goes in, the repair call
    returns corrected markup, and the committed payload reflects the REPAIRED
    parse (both spans resolved) rather than the degraded one (a bare unclosed
    tag with no span at all)."""
    events, proj, read, committer = stack
    await events.append(EventType.CHAPTER_CREATED, "ch1",
                        Chapter(id="ch1", title="One", prose='<speech char="Mira">"Hi."'))
    await events.append(EventType.CHARACTER_CREATED, "mira",
                        Character(id="mira", name="Mira", aliases=[]))
    await proj.catch_up()
    runner = _RepairingRunner('<speech char="Mira">"Hi."</speech>')
    agent = Attributor(runner, read, committer, events)

    await agent.run_once()
    await proj.catch_up()

    log = await events.events_since(0, event_types=[EventType.CHAPTER_ATTRIBUTED])
    payload = log[0].payload
    assert runner.calls == 1
    assert payload["prose"] == '"Hi."'
    assert [s["kind"] for s in payload["segments"]] == ["speech"]
    assert payload["segments"][0]["character_id"] == "mira"
    assert payload["problems"] == []
    flags = await events.events_since(0, event_types=[EventType.FLAG_CREATED])
    assert flags == [], "a successful repair leaves nothing to flag"


@pytest.mark.asyncio
async def test_a_rejected_flag_is_not_refiled_but_a_new_one_still_is(stack):
    """The code-side equivalent of _own_rejections_note: this agent files in
    plain code, so a prompt note can't gate a re-file -- honouring rejections
    has to happen in commit() itself, or the same finding re-files forever on
    every re-attribution."""
    events, proj, read, committer = stack
    await events.append(EventType.CHAPTER_CREATED, "ch1",
                        Chapter(id="ch1", title="One", prose='<speech char="Nobody">"Hi."</speech>'))
    await proj.catch_up()
    agent = Attributor(_Runner(), read, committer, events)
    await agent.run_once()
    await proj.catch_up()

    filed = (await read.list_flags(category="attribution", status=FlagStatus.open))
    assert len(filed) == 1
    rejected_description = filed[0].description
    declined = mark_declined(filed[0], by="triage", resolution="not_actionable",
                             reason="Nobody is an intentional unnamed extra")
    await committer.commit("triage", EventType.FLAG_REJECTED, declined.id, declined)
    await proj.catch_up()

    # Revise the chapter: the same unresolved speaker persists, plus a
    # genuinely new one.
    await events.append(EventType.CHAPTER_REVISED, "ch1",
                        ChapterRevised(chapter_id="ch1",
                                       prose='<speech char="Nobody">"Hi."</speech> '
                                             '<speech char="AlsoUnknown">"Bye."</speech>'))
    await proj.catch_up()
    await agent.run_once()
    await proj.catch_up()

    open_flags = await read.list_flags(category="attribution", status=FlagStatus.open)
    descriptions = {f.description for f in open_flags}
    assert rejected_description not in descriptions, "a rejected finding must not be re-filed"
    assert any("AlsoUnknown" in d for d in descriptions), "a genuinely new problem must still be flagged"


@pytest.mark.asyncio
async def test_already_attributed_chapters_are_skipped(stack):
    events, proj, read, committer = stack
    await events.append(EventType.CHAPTER_CREATED, "ch1",
                        Chapter(id="ch1", title="One", prose="Plain."))
    await proj.catch_up()
    agent = Attributor(_Runner(), read, committer, events)

    await agent.run_once()
    await proj.catch_up()
    first = len(await events.events_since(0, event_types=[EventType.CHAPTER_ATTRIBUTED]))

    await agent.run_once()
    await proj.catch_up()
    assert len(await events.events_since(0, event_types=[EventType.CHAPTER_ATTRIBUTED])) == first


@pytest.mark.asyncio
async def test_revision_triggers_reattribution(stack):
    events, proj, read, committer = stack
    await events.append(EventType.CHAPTER_CREATED, "ch1",
                        Chapter(id="ch1", title="One", prose="old prose"))
    await proj.catch_up()
    agent = Attributor(_Runner(), read, committer, events)
    await agent.run_once()

    await events.append(EventType.CHAPTER_REVISED, "ch1",
                        ChapterRevised(chapter_id="ch1", prose="new prose"))
    await proj.catch_up()
    await agent.run_once()

    log = await events.events_since(0, event_types=[EventType.CHAPTER_ATTRIBUTED])
    assert len(log) == 2


@pytest.mark.asyncio
async def test_readiness_is_backlog_proportional(stack):
    events, proj, read, committer = stack
    for i in range(3):
        await events.append(EventType.CHAPTER_CREATED, f"ch{i}",
                            Chapter(id=f"ch{i}", title=str(i), prose="Plain."))
    await proj.catch_up()
    agent = Attributor(_Runner(), read, committer, events)

    assert await agent.readiness() > 0
    await agent.run_once()
    assert await agent.readiness() == 0.0
