import pytest
import asyncio
from unittest.mock import AsyncMock, MagicMock
from novelizer.agents.base import BaseAgent, AgentState


async def test_agent_state_initial():
    state = AgentState(agent_name="test")
    assert state.agent_name == "test"
    assert state.paused is False
    assert state.context == {}


async def test_readiness_default():
    """Base agent readiness_check returns 0.0 by default."""
    from novelizer.agents.base import BaseAgent
    store = MagicMock()
    agent = BaseAgent(name="test", store=store, min_interval=0)
    score = await agent.readiness_check()
    assert score == 0.0


async def test_pause_resume():
    store = MagicMock()
    agent = BaseAgent(name="test", store=store, min_interval=0)
    agent.pause()
    assert agent.paused
    agent.resume()
    assert not agent.paused
