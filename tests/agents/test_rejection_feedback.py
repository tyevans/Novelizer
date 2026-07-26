"""An agent that files a flag can see when its judgement was thrown out.

A measured fleet window had 17 flag.created against 5 flag.rejected -- a 17%
rejection rate that no filing agent could observe, because `filed_by` was
written at every filing site and read back at none. The write half of the loop
existed; this is the read half.

The sweep derives the roster from AGENT_REGISTRY and each module's own source,
for the reason tests/agents/test_decisiveness.py gives: a hand-listed roster is
how an agent ships unswept. An agent that files flags and never asks what
became of them fails here.
"""
from __future__ import annotations

import importlib
import inspect
import os
import tempfile

import pytest

from novelizer.agents.registry import AGENT_REGISTRY
from novelizer.agents.schemas import WorldEntriesDraft
from novelizer.agents.world_architect import WorldArchitect
from novelizer.canon.committer import Committer
from novelizer.canon.event_store import EventStore
from novelizer.canon.events import EventType
from novelizer.canon.flags import mark_declined
from novelizer.canon.projector import Projector
from novelizer.canon.read_store import ReadStore
from novelizer.store.models import Flag

# The two ways a novelizer agent files a flag: the shared BaseAgent helper, or
# an inline Flag(filed_by=self.name) in its own commit(). Either makes an agent
# a filer, and a filer owes itself the feedback.
_FILING_MARKERS = ("_commit_flag_drafts", "filed_by=self.name")

# Agents that honour their own rejections some other way than
# `_own_rejections_note`, and why -- same shape as DECISIVENESS_EXEMPT
# (tests/agents/test_decisiveness.py): a name here must point at the actual
# mechanism, so the claim is checkable rather than an assertion of good
# intent.
REJECTION_FEEDBACK_EXEMPT: dict[str, str] = {
    "attributor": (
        "Files flags from deterministic parser output, not model judgement, so "
        "there is no prompt for `_own_rejections_note` to feed -- it cannot gate "
        "a re-file the way it does for an LLM-driven filer. Instead it honours "
        "rejections in plain code: Attributor.commit() (novelizer/agents/"
        "attributor.py) loads this agent's own rejected `attribution` flags via "
        "novelizer.canon.flags.own_rejections and drops any draft whose "
        "description matches one, before ever calling _commit_flag_drafts."
    ),
}


def _module(name: str):
    return importlib.import_module(f"novelizer.agents.{name}")


def _source(name: str) -> str:
    return inspect.getsource(_module(name))


FILERS = [
    spec.name for spec in AGENT_REGISTRY
    if any(marker in _source(spec.name) for marker in _FILING_MARKERS)
]


GUIDED_FILERS = [name for name in FILERS if name not in REJECTION_FEEDBACK_EXEMPT]


def test_the_filing_roster_is_derived_and_non_empty():
    assert len(FILERS) >= 7, f"derivation found only {FILERS} -- the marker scan is broken"


@pytest.mark.parametrize("name", GUIDED_FILERS)
def test_every_filing_agent_reads_its_own_rejections(name):
    """A filed_by nobody reads back is the finding this test closes."""
    assert "_own_rejections_note" in _source(name), (
        f"{name} files flags but never asks which of them were rejected"
    )


def test_rejection_feedback_exemptions_are_justified_and_current():
    """Same discipline as DECISIVENESS_EXEMPT: an exemption must name the real
    mechanism, checkable in the module's own source, not merely assert one
    exists."""
    for name, reason in REJECTION_FEEDBACK_EXEMPT.items():
        assert name in FILERS, f"{name} is exempt but no longer a filer -- stale exemption"
        assert len(reason) > 40, f"{name}'s exemption is asserted, not reasoned"
        assert "own_rejections" in _source(name), (
            f"{name} is exempt from _own_rejections_note but its source names no "
            "alternative rejection-handling mechanism"
        )


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


async def test_a_rejected_flag_reaches_its_filer_s_prompt(stack):
    """End to end over the real projection: file, decline, and the next pass of
    the agent that filed it sees the description and the decliner's reason."""
    events, proj, read, committer = stack
    flag = Flag(category="worldbuilding", filed_by="world_architect",
                description="Two entries claim the same capital city")
    await committer.commit("world_architect", EventType.FLAG_CREATED, flag.id, flag)
    declined = mark_declined(flag, by="curator", resolution="not_actionable",
                             reason="both entries name different cities")
    await committer.commit("curator", EventType.FLAG_REJECTED, declined.id, declined)
    await proj.catch_up()

    runner = FakeRunner(WorldEntriesDraft())
    agent = WorldArchitect(runner, read, committer)
    await agent.run_once()

    prompt = runner.calls[0]["messages"][0]["content"]
    assert "Two entries claim the same capital city" in prompt
    assert "both entries name different cities" in prompt


async def test_an_agent_with_no_rejections_sees_no_block(stack):
    """Empty string when there is nothing to say -- the house rule for every
    note helper in novelizer.brain.context."""
    events, proj, read, committer = stack
    runner = FakeRunner(WorldEntriesDraft())
    await WorldArchitect(runner, read, committer).run_once()
    assert "rejected" not in runner.calls[0]["messages"][0]["content"].lower()
