import os
import tempfile
import pytest
from novelizer.canon.event_store import EventStore
from novelizer.canon.projector import Projector
from novelizer.canon.read_store import ReadStore
from novelizer.canon.events import EventType
from novelizer.canon.autonomy import Proposal, ProposalStatus
from novelizer.canon.proposal_service import ProposalService
from novelizer.store.models import Chapter


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
    yield events, proj, read
    await read.close()
    await proj.close()
    await events.close()
    os.unlink(path)


async def test_approve_appends_target_event_and_marks_approved(stack):
    events, proj, read = stack
    ch = Chapter(id="c1", title="One", prose="p")
    proposal = Proposal(proposing_agent="author", target_event_type=EventType.CHAPTER_CREATED,
                         target_aggregate_id="c1", payload=ch.model_dump(mode="json"))
    await events.append(EventType.PROPOSAL_CREATED, proposal.id, proposal)
    await proj.catch_up()
    service = ProposalService(events)
    await service.approve(proposal)
    await proj.catch_up()
    chapters = await read.list_chapters()
    assert len(chapters) == 1 and chapters[0].title == "One"
    props = await read.list_proposals(status="approved")
    assert len(props) == 1 and props[0].id == proposal.id


async def test_reject_marks_rejected_without_target_event(stack):
    events, proj, read = stack
    proposal = Proposal(proposing_agent="editor", target_event_type=EventType.CHAPTER_STATUS_CHANGED,
                         target_aggregate_id="c1", payload={"id": "c1", "editorial_status": "reviewed"})
    await events.append(EventType.PROPOSAL_CREATED, proposal.id, proposal)
    await proj.catch_up()
    service = ProposalService(events)
    await service.reject(proposal)
    await proj.catch_up()
    assert await read.list_chapters() == []
    props = await read.list_proposals(status="rejected")
    assert len(props) == 1 and props[0].id == proposal.id
