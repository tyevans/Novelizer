import pytest
from unittest.mock import AsyncMock, MagicMock
from novelizer.agents.retconner import Retconner
from novelizer.agents.base import AgentState
from novelizer.store.models import RetconRequest, RetconStatus, WorldEntry


@pytest.fixture
def store():
    s = MagicMock()
    s.list_retcon_requests = AsyncMock(return_value=[])
    s.list_world_entries = AsyncMock(return_value=[])
    s.list_characters = AsyncMock(return_value=[])
    s.save_world_entry = AsyncMock()
    s.resolve_retcon = AsyncMock()
    s.supersede_world_entry = AsyncMock()
    s.db = MagicMock()
    s.db.count_open_retcons = AsyncMock(return_value=0)
    return s


async def test_readiness_zero_when_no_retcons(store):
    store.db.count_open_retcons = AsyncMock(return_value=0)
    agent = Retconner(store=store, min_interval=0)
    score = await agent.readiness_check()
    assert score == 0.0


async def test_readiness_nonzero_with_retcons(store):
    store.db.count_open_retcons = AsyncMock(return_value=3)
    agent = Retconner(store=store, min_interval=0)
    score = await agent.readiness_check()
    assert score > 0.0


async def test_poll_fetches_oldest_open_retcon(store):
    req = RetconRequest(
        description="Conflict", conflicting_entry_ids=["x"], proposed_resolution="Remove x."
    )
    store.list_retcon_requests = AsyncMock(return_value=[req])
    agent = Retconner(store=store, min_interval=0)
    state = AgentState(agent_name="retconner")
    await agent.poll(state)
    assert state.context["target_retcon"] is req


async def test_commit_resolves_retcon(store):
    req = RetconRequest(
        description="Conflict", conflicting_entry_ids=["x"], proposed_resolution="Remove x."
    )
    agent = Retconner(store=store, min_interval=0)
    state = AgentState(agent_name="retconner")
    state.context["target_retcon"] = req
    state.context["amended_entries"] = []
    await agent.commit(state)
    store.resolve_retcon.assert_awaited_once_with(req.id, "retconner")
