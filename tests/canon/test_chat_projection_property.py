import asyncio
import os
import tempfile
from hypothesis import given, settings, strategies as st
from novelizer.canon.event_store import EventStore
from novelizer.canon.projector import Projector
from novelizer.canon.read_store import ReadStore
from novelizer.canon.events import EventType, ChatUserMessaged, ChatAgentReplied

AGENTS = ["author", "editor", "character_keeper"]

message_strategy = st.lists(
    st.tuples(st.sampled_from(AGENTS), st.sampled_from(["user", "agent"]), st.text(max_size=40)),
    max_size=30,
)


async def _project(seq: list[tuple[str, str, str]]) -> dict[str, list[tuple[str, str]]]:
    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    try:
        events = EventStore(path)
        await events.init()
        proj = Projector(events, path)
        await proj.init()
        read = ReadStore(path)
        await read.init()
        for i, (agent, role, text) in enumerate(seq):
            mid = f"m{i}"
            if role == "user":
                await events.append(
                    EventType.CHAT_USER_MESSAGED, agent,
                    ChatUserMessaged(message_id=mid, agent_name=agent, text=text),
                )
            else:
                await events.append(
                    EventType.CHAT_AGENT_REPLIED, agent,
                    ChatAgentReplied(message_id=mid, agent_name=agent, text=text),
                )
        await proj.catch_up()
        out = {}
        for agent in AGENTS:
            msgs = await read.list_chat_messages(agent)
            out[agent] = [(m.role, m.message_id) for m in msgs]
        await read.close()
        await proj.close()
        await events.close()
        return out
    finally:
        os.unlink(path)


@settings(max_examples=25, deadline=None)
@given(message_strategy)
def test_any_interleaving_projects_per_agent_transcripts_in_order(seq):
    """Per-agent transcript == that agent's subsequence of the log, order preserved."""
    projected = asyncio.run(_project(seq))
    for agent in AGENTS:
        expected = [(role, f"m{i}") for i, (a, role, _t) in enumerate(seq) if a == agent]
        assert projected[agent] == expected
