import asyncio
import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from novelizer.scheduler import Scheduler
from novelizer.agents.base import BaseAgent, AgentState


def make_agent(name, interval=0, readiness=0.5):
    store = MagicMock()
    store.list_unconsumed_signals = AsyncMock(return_value=[])
    agent = MagicMock(spec=BaseAgent)
    agent.name = name
    agent.paused = False
    agent.min_interval = interval
    agent.ready_for_interval = MagicMock(return_value=True)
    agent.readiness_check = AsyncMock(return_value=readiness)
    agent.run_once = AsyncMock()
    return agent


async def test_scheduler_runs_highest_readiness_agent():
    store = MagicMock()
    store.list_unconsumed_signals = AsyncMock(return_value=[])

    low = make_agent("low", readiness=0.1)
    high = make_agent("high", readiness=0.9)

    scheduler = Scheduler(agents=[low, high], store=store)

    # Run one tick
    await scheduler.tick()

    high.run_once.assert_awaited_once()
    low.run_once.assert_not_awaited()


async def test_scheduler_skips_paused_agents():
    store = MagicMock()
    store.list_unconsumed_signals = AsyncMock(return_value=[])

    paused = make_agent("paused", readiness=1.0)
    paused.paused = True
    active = make_agent("active", readiness=0.5)

    scheduler = Scheduler(agents=[paused, active], store=store)
    await scheduler.tick()

    paused.run_once.assert_not_awaited()
    active.run_once.assert_awaited_once()


async def test_scheduler_skips_agents_not_ready_for_interval():
    store = MagicMock()
    store.list_unconsumed_signals = AsyncMock(return_value=[])

    not_ready = make_agent("not_ready", readiness=1.0)
    not_ready.ready_for_interval = MagicMock(return_value=False)
    ready = make_agent("ready", readiness=0.3)

    scheduler = Scheduler(agents=[not_ready, ready], store=store)
    await scheduler.tick()

    not_ready.run_once.assert_not_awaited()
    ready.run_once.assert_awaited_once()


async def test_scheduler_override_signal_boosts_agent():
    from novelizer.store.models import DirectorSignal, SignalKind

    override_sig = DirectorSignal(kind=SignalKind.override, body="go", target_agent="low")

    store = MagicMock()
    store.list_unconsumed_signals = AsyncMock(return_value=[override_sig])

    low = make_agent("low", readiness=0.1)
    high = make_agent("high", readiness=0.9)

    scheduler = Scheduler(agents=[low, high], store=store)
    await scheduler.tick()

    # Low agent should run because of override, not high
    low.run_once.assert_awaited_once()
    high.run_once.assert_not_awaited()


async def test_tick_noop_when_no_eligible_agents():
    store = MagicMock()
    store.list_unconsumed_signals = AsyncMock(return_value=[])

    agent = make_agent("only", readiness=0.5)
    agent.ready_for_interval = MagicMock(return_value=False)

    scheduler = Scheduler(agents=[agent], store=store)
    # Should not raise
    await scheduler.tick()
    agent.run_once.assert_not_awaited()
