"""The four agents that drive flag lifecycle transitions must route through the
Flag aggregate rather than hand-rolling the rules. See novelizer/canon/flags.py."""
import os
import tempfile
import pytest
from novelizer.canon.event_store import EventStore
from novelizer.canon.projector import Projector
from novelizer.canon.read_store import ReadStore
from novelizer.canon.committer import Committer
from novelizer.canon.events import EventType
from novelizer.agents.curator import Curator
from novelizer.agents.retconner import Retconner
from novelizer.agents.triage import Triage
from novelizer.agents.schemas import TriageVerdict
from novelizer.store.models import Flag, FlagStatus


class FakeRunner:
    def __init__(self, out):
        self._out = out
        self.calls = []

    async def ainvoke(self, inputs):
        self.calls.append(inputs)
        return {"structured_response": self._out}


@pytest.fixture
async def stack():
    fd, path = tempfile.mkstemp(suffix=".db"); os.close(fd)
    events = EventStore(path); await events.init()
    proj = Projector(events, path); await proj.init()
    read = ReadStore(path); await read.init()
    yield events, proj, read, Committer(events)
    await read.close(); await proj.close(); await events.close(); os.unlink(path)


async def test_critical_verdict_on_a_closed_flag_does_not_escalate(stack):
    # `commit` is reachable with a stale target -- the flag can be decided by
    # its owning agent between poll and commit. Escalating a decided flag would
    # put a permanently-escalated phantom on the human's queue.
    events, proj, read, committer = stack
    flag = Flag(id="f1", category="contradiction", description="two suns",
                status=FlagStatus.resolved, filed_by="continuity_checker")
    await events.append(EventType.FLAG_CREATED, "f1", flag)
    await proj.catch_up()
    out = TriageVerdict(verdict="real", severity="critical")
    agent = Triage(FakeRunner(out), read, committer)
    await agent.commit(out, {"target": flag})
    await proj.catch_up()
    assert await read.list_flags(escalated=True) == []


@pytest.mark.parametrize("agent_cls", [Curator, Retconner, Triage])
def test_agents_do_not_define_their_own_escalation_threshold(agent_cls):
    # The threshold lives in novelizer.canon.flags. The Curator and the
    # Retconner each used to carry a copy of the literal, free to drift.
    assert not hasattr(agent_cls, "_FAILURE_ESCALATION_THRESHOLD")
