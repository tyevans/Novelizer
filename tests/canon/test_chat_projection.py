import os
import tempfile
import pytest
from novelizer.canon.event_store import EventStore
from novelizer.canon.projector import Projector
from novelizer.canon.read_store import ReadStore
from novelizer.canon.events import EventType, ChatUserMessaged, ChatAgentReplied
from novelizer.canon.policy import _NEVER_GATED


@pytest.fixture
def db_path():
    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    yield path
    os.unlink(path)


async def _stores(path):
    events = EventStore(path)
    await events.init()
    proj = Projector(events, path)
    await proj.init()
    read = ReadStore(path)
    await read.init()
    return events, proj, read


@pytest.mark.asyncio
async def test_chat_events_project_to_ordered_transcript(db_path):
    events, proj, read = await _stores(db_path)
    try:
        await events.append(
            EventType.CHAT_USER_MESSAGED, "author",
            ChatUserMessaged(message_id="m1", agent_name="author", text="what if the mine collapse was deliberate?"),
        )
        await events.append(
            EventType.CHAT_AGENT_REPLIED, "author",
            ChatAgentReplied(message_id="m2", agent_name="author", text="Then ch. 3 reads as foreshadowing.", replying_to="m1"),
        )
        await events.append(
            EventType.CHAT_USER_MESSAGED, "editor",
            ChatUserMessaged(message_id="m3", agent_name="editor", text="too slow?"),
        )
        await proj.catch_up()
        msgs = await read.list_chat_messages("author")
        assert [(m.role, m.message_id) for m in msgs] == [("user", "m1"), ("agent", "m2")]
        assert msgs[0].text.startswith("what if")
        assert await read.list_chat_conversations() == ["author", "editor"]
    finally:
        await read.close()
        await proj.close()
        await events.close()


@pytest.mark.asyncio
async def test_chat_projection_is_idempotent_per_message_id(db_path):
    events, proj, read = await _stores(db_path)
    try:
        await events.append(
            EventType.CHAT_USER_MESSAGED, "author",
            ChatUserMessaged(message_id="m1", agent_name="author", text="hello"),
        )
        await proj.catch_up()
        await proj._reset_state()
        await proj.catch_up()
        msgs = await read.list_chat_messages("author")
        assert len(msgs) == 1
    finally:
        await read.close()
        await proj.close()
        await events.close()


def test_chat_events_are_never_gated():
    assert EventType.CHAT_USER_MESSAGED in _NEVER_GATED
    assert EventType.CHAT_AGENT_REPLIED in _NEVER_GATED
