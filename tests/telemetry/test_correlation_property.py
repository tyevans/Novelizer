# tests/telemetry/test_correlation_property.py
"""Spec invariant: every domain event committed during a run carries exactly
that run's run_id, and no run_id appears in the domain log that the telemetry
log doesn't know. Sequential interleavings only — the scheduler runs agents
one at a time by design."""
import os
import tempfile
from hypothesis import given, settings, strategies as st
from novelizer.canon.event_store import EventStore
from novelizer.canon.events import EventType, AgentRemark
from novelizer.canon.committer import Committer
from novelizer.agents.base import BaseAgent
from novelizer.telemetry.bus import TelemetryBus
from novelizer.telemetry.recorder import TelemetryRecorder
from novelizer.telemetry.events import TelemetryEventType


class CommitsN(BaseAgent):
    def __init__(self, committer, name, n):
        super().__init__(runner=None, read_store=None, committer=committer, interval=0, name=name)
        self._n = n

    async def _run(self):
        for i in range(self._n):
            await self._committer.commit(
                self.name, EventType.AGENT_REMARKED, self.name,
                AgentRemark(agent_name=self.name, note=f"note {i}"))


@settings(deadline=None, max_examples=25)
@given(runs=st.lists(
    st.tuples(st.sampled_from(["author", "editor", "retconner"]),
              st.integers(min_value=0, max_value=4)),
    min_size=1, max_size=6))
async def test_every_domain_event_carries_its_own_runs_id(runs):
    fd, dpath = tempfile.mkstemp(suffix=".db"); os.close(fd)
    fd, tpath = tempfile.mkstemp(suffix=".db"); os.close(fd)
    domain = EventStore(dpath); await domain.init()
    tel_store = EventStore(tpath); await tel_store.init()
    recorder = TelemetryRecorder(tel_store, TelemetryBus())
    committer = Committer(domain)
    try:
        for name, n in runs:
            agent = CommitsN(committer, name, n)
            agent.telemetry = recorder
            await agent.run_once()

        tel = await tel_store.events_since(0)
        started = [e for e in tel if e.event_type == TelemetryEventType.AGENT_RUN_STARTED]
        run_ids_in_order = [e.payload["run_id"] for e in started]
        assert len(run_ids_in_order) == len(runs)

        dom = await domain.events_since(0)
        # Domain events, in commit order, must group by run in run order with
        # exactly the declared counts — and cite exactly that run's id.
        expected = [rid for rid, (_, n) in zip(run_ids_in_order, runs) for _ in range(n)]
        assert [e.run_id for e in dom] == expected
        # No domain run_id the telemetry log doesn't know.
        assert {e.run_id for e in dom} <= set(run_ids_in_order)
    finally:
        await domain.close(); await tel_store.close()
        os.unlink(dpath); os.unlink(tpath)
