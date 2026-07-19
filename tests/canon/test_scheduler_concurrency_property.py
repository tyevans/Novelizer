"""Locked decision 11, concurrency proof obligation 4: concurrent commits from
K fake agents produce a log whose per-aggregate event ordering is still valid
-- no interleaving corruption, no lost or duplicated writes.

This exercises the EventStore/Committer stack directly via asyncio.gather,
simulating the Scheduler's concurrent pool dispatch without needing the full
Scheduler class -- aiosqlite's single-connection serialization is the safety
argument this test is proving.
"""
import asyncio
import os
import tempfile

import pytest
from hypothesis import HealthCheck, given, settings as hyp_settings, strategies as st

from novelizer.canon.event_store import EventStore
from novelizer.canon.committer import Committer
from novelizer.canon.events import EventType, AgentRemark


async def _agent_commits(committer: Committer, agent_name: str, n: int) -> None:
    """Commit n events to this agent's OWN aggregate id, sequentially from the
    agent's point of view -- exactly what one concurrently-dispatched agent's
    run_once() does today (its own commits are sequential; concurrency is
    across agents, not within one)."""
    for i in range(n):
        await committer.commit(
            agent_name, EventType.AGENT_REMARKED, agent_name,
            AgentRemark(agent_name=agent_name, note=f"{agent_name}-{i}"),
        )


@given(k=st.integers(min_value=2, max_value=5), commits_per_agent=st.integers(min_value=1, max_value=5))
@hyp_settings(max_examples=20, deadline=None, suppress_health_check=[HealthCheck.function_scoped_fixture])
async def test_k_concurrent_agents_produce_valid_per_aggregate_ordering(k, commits_per_agent):
    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    events = EventStore(path)
    await events.init()
    committer = Committer(events)
    try:
        agent_names = [f"agent-{i}" for i in range(k)]

        # Simulate the Scheduler's pool dispatch: K agents' commit sequences
        # run concurrently via asyncio.gather, each targeting its own
        # distinct aggregate id.
        await asyncio.gather(*[
            _agent_commits(committer, name, commits_per_agent) for name in agent_names
        ])

        all_events = await events.events_since(0)
        remarks = [e for e in all_events if e.event_type == EventType.AGENT_REMARKED]

        # 1. No lost writes: every agent's aggregate id has exactly
        #    commits_per_agent events.
        by_aggregate: dict[str, list] = {name: [] for name in agent_names}
        for e in remarks:
            by_aggregate[e.aggregate_id].append(e)
        for name in agent_names:
            assert len(by_aggregate[name]) == commits_per_agent, (
                f"{name}: expected {commits_per_agent} events, got {len(by_aggregate[name])}"
            )

        # 2. Per-aggregate ordering preserved: within each agent's own
        #    aggregate id, events replay in the exact order that agent
        #    issued them (by sequence, and by the note's embedded index).
        for name in agent_names:
            agent_events = sorted(by_aggregate[name], key=lambda e: e.sequence)
            notes = [e.payload["note"] for e in agent_events]
            expected = [f"{name}-{i}" for i in range(commits_per_agent)]
            assert notes == expected, f"{name}: out-of-order or corrupted event sequence: {notes}"

        # 3. No duplication, no silent drops: total count matches exactly.
        assert len(remarks) == k * commits_per_agent
    finally:
        await events.close()
        os.unlink(path)
