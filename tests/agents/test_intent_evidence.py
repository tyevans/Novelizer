"""Citing intents carry the evidence that grounds them.

`response_format` biases a model to emit its final structure early and skip the
tool loop. An evidence field on the intents that cite existing canon is the
structural counter-pressure: the field cannot be filled without having read
something. What the agent cites is recorded on the event, so the grounding for
every claim stays in the log alongside the claim.

See docs/agent-prompting/proposal-fleet-shared.md §2.7.
"""
from __future__ import annotations

import logging

import pytest

from novelizer.agents import intents as intent_helpers
from novelizer.agents.schemas import CausalIntent, SecretCitation, ThreadIntent
from novelizer.canon.events import EventType


class FakeCommitter:
    def __init__(self):
        self.commits = []

    async def commit(self, agent, event_type, aggregate_id, payload):
        self.commits.append((event_type, aggregate_id, payload))


class TestSchemaAcceptsEvidence:
    def test_thread_intent_evidence_defaults_empty(self):
        """Optional at the type level: a required field would raise the
        constraint tax it is meant to reduce."""
        assert ThreadIntent(action="touch", id="t1").evidence == ""

    def test_each_citing_intent_accepts_evidence(self):
        assert ThreadIntent(action="touch", id="t1", evidence="ch003").evidence == "ch003"
        assert SecretCitation(action="learn", id="s1", character_id="mara",
                               evidence="chapters/003-x.md").evidence == "chapters/003-x.md"
        assert CausalIntent(cause_chapter_id="c1", effect_chapter_id="c2",
                            evidence="ch001").evidence == "ch001"


class TestEvidenceIsRecordedOnTheEvent:
    async def test_thread_touch_carries_evidence_onto_the_payload(self):
        committer = FakeCommitter()
        await intent_helpers.commit_thread_intents(
            committer, "author",
            [ThreadIntent(action="touch", id="t1", note="n", evidence="ch003")],
            {"t1"}, chapter_id="c9",
        )
        event_type, _, payload = committer.commits[0]
        assert event_type == EventType.THREAD_TOUCHED
        assert payload.evidence == "ch003"

    async def test_knowledge_learn_carries_evidence_onto_the_payload(self):
        committer = FakeCommitter()
        await intent_helpers.commit_secret_citations(
            committer, "character_keeper",
            [SecretCitation(action="learn", id="s1", character_id="mara", evidence="ch004")],
            {"s1"}, chapter_id="c9", allowed_actions=frozenset({"learn"}),
        )
        _, _, payload = committer.commits[0]
        assert payload.evidence == "ch004"

    async def test_causal_edge_carries_evidence_onto_the_payload(self):
        committer = FakeCommitter()
        await intent_helpers.commit_causal_intents(
            committer, "author",
            [CausalIntent(cause_chapter_id="c1", effect_chapter_id="c2", evidence="ch001")],
            {"c1", "c2"},
        )
        _, _, payload = committer.commits[0]
        assert payload.evidence == "ch001"

    async def test_minting_actions_need_no_evidence(self):
        """A plant plants something new — there is no prior canon to cite."""
        committer = FakeCommitter()
        await intent_helpers.commit_thread_intents(
            committer, "author", [ThreadIntent(action="plant", name="A new thread")], set(),
        )
        assert committer.commits[0][0] == EventType.THREAD_PLANTED


class TestUngroundedCitingIntentsAreVisible:
    async def test_citing_intent_without_evidence_still_commits(self, caplog):
        """Warn, don't drop: silently discarding a real narrative beat is worse
        than recording one that is under-cited, and the log makes the gap
        measurable before any drop policy is considered."""
        committer = FakeCommitter()
        with caplog.at_level(logging.WARNING):
            await intent_helpers.commit_thread_intents(
                committer, "author", [ThreadIntent(action="touch", id="t1")], {"t1"},
            )
        assert len(committer.commits) == 1
        assert "evidence" in caplog.text
        assert "touch" in caplog.text

    async def test_no_warning_when_evidence_present(self, caplog):
        committer = FakeCommitter()
        with caplog.at_level(logging.WARNING):
            await intent_helpers.commit_thread_intents(
                committer, "author",
                [ThreadIntent(action="touch", id="t1", evidence="ch002")], {"t1"},
            )
        assert "evidence" not in caplog.text

    @pytest.mark.parametrize("action", ["learn", "reveal", "uses"])
    async def test_every_citing_knowledge_action_is_checked(self, action, caplog):
        committer = FakeCommitter()
        kwargs = {"character_id": "mara"} if action in ("learn", "uses") else {}
        with caplog.at_level(logging.WARNING):
            await intent_helpers.commit_secret_citations(
                committer, "author", [SecretCitation(action=action, id="s1", **kwargs)],
                {"s1"}, allowed_actions=frozenset({"plant", "learn", "reveal", "uses"}),
            )
        assert "evidence" in caplog.text


class TestReplayCompatibility:
    def test_payloads_deserialize_without_evidence(self):
        """Events written before this field must still replay."""
        from novelizer.canon.events import CausalEdgeDeclared, SecretLearned, ThreadTouched

        assert ThreadTouched.model_validate({"id": "t1"}).evidence == ""
        assert SecretLearned.model_validate({"id": "s1", "character_id": "m"}).evidence == ""
        assert CausalEdgeDeclared.model_validate(
            {"cause_chapter_id": "c1", "effect_chapter_id": "c2"}
        ).evidence == ""
