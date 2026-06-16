import pytest
from unittest.mock import AsyncMock, MagicMock
from novelizer.agents.editor import Editor
from novelizer.agents.base import AgentState
from novelizer.store.models import Chapter, EditorialStatus


@pytest.fixture
def store():
    s = MagicMock()
    s.list_chapters = AsyncMock(return_value=[])
    s.save_chapter = AsyncMock()
    s.save_director_signal = AsyncMock()
    s.db = MagicMock()
    s.db.count_draft_chapters = AsyncMock(return_value=0)
    return s


async def test_readiness_zero_when_no_drafts(store):
    store.db.count_draft_chapters = AsyncMock(return_value=0)
    agent = Editor(store=store, min_interval=0)
    score = await agent.readiness_check()
    assert score == 0.0


async def test_readiness_nonzero_with_drafts(store):
    store.db.count_draft_chapters = AsyncMock(return_value=2)
    agent = Editor(store=store, min_interval=0)
    score = await agent.readiness_check()
    assert score > 0.0


async def test_poll_fetches_oldest_draft(store):
    ch1 = Chapter(title="Ch 1", prose="First.")
    ch2 = Chapter(title="Ch 2", prose="Second.")
    store.list_chapters = AsyncMock(return_value=[ch1, ch2])
    agent = Editor(store=store, min_interval=0)
    state = AgentState(agent_name="editor")
    await agent.poll(state)
    assert state.context["target_chapter"].title == "Ch 1"


async def test_commit_promotes_to_reviewed(store):
    ch = Chapter(title="Ch 1", prose="Good prose.")
    agent = Editor(store=store, min_interval=0)
    state = AgentState(agent_name="editor")
    state.context["target_chapter"] = ch
    state.context["verdict"] = "approve"
    state.context["notes"] = ""
    await agent.commit(state)
    saved = store.save_chapter.call_args[0][0]
    assert saved.editorial_status == EditorialStatus.reviewed
