import pytest
from unittest.mock import AsyncMock, MagicMock
from novelizer.agents.continuity_checker import ContinuityChecker
from novelizer.agents.base import AgentState
from novelizer.store.models import WorldEntry, RetconRequest


@pytest.fixture
def store():
    s = MagicMock()
    s.list_world_entries = AsyncMock(return_value=[])
    s.list_characters = AsyncMock(return_value=[])
    s.list_chapters = AsyncMock(return_value=[])
    s.save_retcon_request = AsyncMock()
    s.db = MagicMock()
    s.db.count_open_retcons = AsyncMock(return_value=0)
    return s


async def test_readiness_drops_when_many_open_retcons(store):
    store.db.count_open_retcons = AsyncMock(return_value=5)
    agent = ContinuityChecker(store=store, min_interval=0)
    score = await agent.readiness_check()
    assert score < 0.5


async def test_commit_saves_retcon_requests(store):
    agent = ContinuityChecker(store=store, min_interval=0)
    req = RetconRequest(
        description="Contradiction",
        conflicting_entry_ids=["a", "b"],
        proposed_resolution="Remove a.",
    )
    state = AgentState(agent_name="continuity_checker")
    state.context["retcon_requests"] = [req]
    await agent.commit(state)
    store.save_retcon_request.assert_awaited_once_with(req)
