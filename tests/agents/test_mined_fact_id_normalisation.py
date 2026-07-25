"""Mined ids are normalised once, at the boundary, not per guard.

`_normalize_id` exists because "canon ids are minted lowercase everywhere, so a
casing mismatch on a citing id is a correctness bug, not an unknown-id case"
(novelizer/agents/intents.py). The commit helpers applied it; the Continuity
Checker's mining guards did not, and compared raw model output. One root, two
opposite failures:

  * a membership check missed, so a real fact was escalated to a human-facing
    retcon flag over a casing artefact the commit helper would have accepted;
  * a dedupe check missed, so the helper normalised and committed a fact that
    was already in canon -- defeating the guard that exists to prevent exactly
    that.

Normalising in the schema means no downstream guard can forget.
"""
from __future__ import annotations
import os
import tempfile

import pytest

from novelizer.agents.continuity_checker import ContinuityChecker
from novelizer.agents.schemas import (
    ContinuityOutput, MinedCausalFact, MinedFactsOutput, MinedSecretFact,
)
from novelizer.canon.committer import Committer
from novelizer.canon.event_store import EventStore
from novelizer.canon.events import (
    CausalEdgeDeclared, EventType, SecretCreated,
)
from novelizer.canon.projector import Projector
from novelizer.canon.read_store import ReadStore
from novelizer.store.models import Chapter, Character


class FakeRunner:
    def __init__(self, out):
        self._out = out

    async def ainvoke(self, inputs):
        return {"structured_response": self._out}


@pytest.fixture
async def stack():
    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    events = EventStore(path)
    await events.init()
    proj = Projector(events, path)
    await proj.init()
    read = ReadStore(path)
    await read.init()
    yield events, proj, read, Committer(events)
    await read.close()
    await proj.close()
    await events.close()
    os.unlink(path)


def test_the_schema_normalises_a_cited_id():
    """The unit rule, independent of any agent."""
    fact = MinedSecretFact(action="uses", id="  A-Secret  ", character_id="Alice",
                            chapter_id="c1", known_id=True)
    assert fact.id == "a-secret"
    assert fact.character_id == "alice"

    causal = MinedCausalFact(cause_chapter_id="CH001", effect_chapter_id=" CH002 ")
    assert (causal.cause_chapter_id, causal.effect_chapter_id) == ("ch001", "ch002")


async def test_a_mined_causal_fact_already_in_canon_is_not_recommitted(stack):
    events, proj, read, committer = stack
    await events.append(EventType.CHAPTER_CREATED, "ch001", Chapter(id="ch001", title="One", prose="p"))
    await events.append(EventType.CHAPTER_CREATED, "ch002", Chapter(id="ch002", title="Two", prose="p"))
    await events.append(EventType.CAUSAL_EDGE_DECLARED, "ch001",
                        CausalEdgeDeclared(cause_chapter_id="ch001", effect_chapter_id="ch002"))
    await proj.catch_up()
    before = len([e for e in await events.events_since(0)
                  if e.event_type == EventType.CAUSAL_EDGE_DECLARED])

    mining_out = MinedFactsOutput(causal_facts=[
        MinedCausalFact(cause_chapter_id="CH001", effect_chapter_id="CH002"),
    ])
    agent = ContinuityChecker(FakeRunner(ContinuityOutput()), FakeRunner(mining_out),
                              read, committer, events)
    await agent.run_once()

    after = len([e for e in await events.events_since(0)
                 if e.event_type == EventType.CAUSAL_EDGE_DECLARED])
    assert after == before, (
        "a differently-cased mined causal fact slipped past the dedupe guard and "
        "was committed again"
    )


async def test_a_mined_secret_fact_with_odd_casing_commits_rather_than_escalating(stack):
    events, proj, read, committer = stack
    await events.append(EventType.CHAPTER_CREATED, "c1", Chapter(id="c1", title="One", prose="p"))
    await events.append(EventType.CHARACTER_CREATED, "alice",
                        Character(id="alice", name="Alice", role="lead"))
    await events.append(EventType.SECRET_CREATED, "a-secret",
                        SecretCreated(id="a-secret", title="A Secret"))
    await proj.catch_up()

    mining_out = MinedFactsOutput(secret_facts=[
        MinedSecretFact(action="uses", id="A-Secret", character_id="Alice",
                        chapter_id="c1", known_id=True),
    ])
    agent = ContinuityChecker(FakeRunner(ContinuityOutput()), FakeRunner(mining_out),
                              read, committer, events)
    await agent.run_once()

    log = await events.events_since(0)
    referenced = [e for e in log if e.event_type == EventType.SECRET_REFERENCED]
    flags = [e for e in log if e.event_type == EventType.FLAG_CREATED]
    assert referenced, "the fact was not committed despite naming a known secret"
    assert not flags, "a casing artefact was escalated to a human-facing flag"
