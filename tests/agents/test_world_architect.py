import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from novelizer.agents.world_architect import WorldArchitect
from novelizer.agents.base import AgentState
from novelizer.store.models import WorldEntry, Domain


@pytest.fixture
def store():
    s = MagicMock()
    s.list_world_entries = AsyncMock(return_value=[])
    s.save_world_entry = AsyncMock()
    return s


async def test_readiness_no_entries(store):
    agent = WorldArchitect(store=store, min_interval=0)
    store.db = MagicMock()
    store.db.count_world_entries = AsyncMock(return_value=0)
    score = await agent.readiness_check()
    assert score == 1.0


async def test_readiness_many_entries(store):
    agent = WorldArchitect(store=store, min_interval=0)
    store.db = MagicMock()
    store.db.count_world_entries = AsyncMock(return_value=100)
    score = await agent.readiness_check()
    assert 0.0 < score < 1.0


async def test_poll_populates_context(store):
    agent = WorldArchitect(store=store, min_interval=0)
    store.list_world_entries = AsyncMock(return_value=[
        WorldEntry(title="The North", body="Cold.")
    ])
    state = AgentState(agent_name="world_architect")
    await agent.poll(state)
    assert "existing_entries" in state.context
    assert len(state.context["existing_entries"]) == 1


async def test_commit_saves_entries(store):
    agent = WorldArchitect(store=store, min_interval=0)
    new_entry = WorldEntry(title="The Deep", body="Ancient caves.")
    state = AgentState(agent_name="world_architect")
    state.context["new_entries"] = [new_entry]
    await agent.commit(state)
    store.save_world_entry.assert_awaited_once_with(new_entry)
