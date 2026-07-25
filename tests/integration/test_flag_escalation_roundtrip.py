"""Full-stack integration test for the flag severity/escalation round trip.

Exercises the event/agent layer end to end (no TUI): a flag is filed, Triage
assesses it, and either a critical verdict or three repeated Retconner
declines drives escalation — followed, in the critical case, by the owning
agent (Retconner) resolving the flag and auto-clearing the escalation.

This stitches together the already-proven fixture/call patterns from
tests/agents/test_triage.py and tests/agents/test_retconner.py: Triage is
driven via `run_once()` with a `FakeRunner` returning a structured
`TriageVerdict`, and Retconner's success path is driven via its real
`commit()` method (there is no `_resolve` method - the amend/resolve/
auto-clear logic all lives inside `commit()`), while its failure path is
driven via `_decline()`, exactly as those agents' own unit tests do.
"""
import os
import tempfile

import pytest

from novelizer.canon.event_store import EventStore
from novelizer.canon.projector import Projector
from novelizer.canon.read_store import ReadStore
from novelizer.canon.committer import Committer
from novelizer.canon.events import EventType
from novelizer.canon.flags import FAILURE_ESCALATION_THRESHOLD
from novelizer.agents.triage import Triage
from novelizer.agents.retconner import Retconner
from novelizer.agents.schemas import TriageVerdict, RetconAmendments
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


async def test_critical_flag_escalates_then_resolves_and_clears(stack):
    events, proj, read, committer = stack

    # A sibling agent files a contradiction flag - owned by Retconner.
    flag = Flag(id="f1", category="contradiction", description="two suns",
                related_entry_ids=[], proposed_resolution="", filed_by="continuity_checker")
    await events.append(EventType.FLAG_CREATED, flag.id, flag)
    await proj.catch_up()

    # Triage assesses it as real and critical -> escalates immediately, but
    # (per Triage.commit) leaves it open for the owning agent's own poll.
    triage_out = TriageVerdict(verdict="real", severity="critical")
    triage = Triage(FakeRunner(triage_out), read, committer)
    await triage.run_once()
    await proj.catch_up()

    escalated = await read.list_flags(escalated=True)
    assert len(escalated) == 1
    assert escalated[0].id == "f1"
    assert escalated[0].severity == "critical"
    still_open = await read.list_flags(category="contradiction", status=FlagStatus.open)
    assert len(still_open) == 1 and still_open[0].id == "f1"

    # Retconner (the owning agent) later picks it up and resolves it via its
    # real success path: commit() with resolution="amend" (the default).
    current = still_open[0]
    retcon_out = RetconAmendments(feed_note="fixed it")
    retconner = Retconner(FakeRunner(retcon_out), read, committer)
    await retconner.commit(retcon_out, {"target": current, "world": []})
    await proj.catch_up()

    # Resolving an escalated flag auto-clears the escalation (commit()'s
    # `if resolved.escalated:` branch emits FLAG_ESCALATION_CLEARED).
    remaining = await read.list_flags(escalated=True)
    assert remaining == []
    resolved = await read.list_flags(category="contradiction", status=FlagStatus.resolved)
    assert len(resolved) == 1
    assert resolved[0].id == "f1"
    assert resolved[0].escalated is False
    assert resolved[0].escalation_cleared_by == "agent"


async def test_repeated_failure_escalates_minor_flag(stack):
    events, proj, read, committer = stack

    # A minor flag that never gets marked critical by Triage should still
    # escalate purely from the owning agent (Retconner) declining it
    # repeatedly - Retconner._decline's own failed_attempts/threshold path.
    flag = Flag(id="f2", category="contradiction", description="scar mismatch",
                severity="minor", related_entry_ids=[], proposed_resolution="",
                filed_by="continuity_checker")
    await events.append(EventType.FLAG_CREATED, flag.id, flag)
    await proj.catch_up()

    retconner = Retconner(FakeRunner(RetconAmendments()), read, committer)
    for _ in range(FAILURE_ESCALATION_THRESHOLD):
        current = (await read.list_flags(category="contradiction"))[0]
        assert current.status == FlagStatus.open
        await retconner._decline(current, "cannot_reproduce", "no evidence")
        await proj.catch_up()
        # _decline rejects the flag; re-file it as open so the next decline
        # in this loop targets a fresh attempt against the same flag id -
        # test scaffolding standing in for a live re-poll cycle, not a real
        # system re-open mechanic.
        rejected = (await read.list_flags(category="contradiction", status=FlagStatus.rejected))[0]
        if rejected.status == FlagStatus.rejected:
            reopened = rejected.model_copy(update={"status": FlagStatus.open})
            await events.append(EventType.FLAG_CREATED, reopened.id, reopened)
            await proj.catch_up()

    escalated = await read.list_flags(escalated=True)
    assert len(escalated) == 1
    assert escalated[0].id == "f2"
    assert escalated[0].failed_attempts == FAILURE_ESCALATION_THRESHOLD
    assert escalated[0].severity == "minor"
