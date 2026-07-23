from __future__ import annotations

from research_domain.agents import VerifierAgent
from research_domain.corpus import CorpusReader
from research_domain.runtime import ResearchRuntime
from research_domain.schemas import RefutationDraft, VerificationDraft, VerifierOutput

from tests.substrate.postgres_fixture import postgres_dsn  # noqa: F401


class FakeVerifierRunner:
    """Scripted verdict per claim_id, parsed from the prompt's CLAIM_ID line."""

    def __init__(self, verdicts: dict[str, VerificationDraft]):
        self._verdicts = verdicts
        self.calls: list[str] = []

    async def ainvoke(self, inputs: dict) -> dict:
        prompt = inputs["messages"][0]["content"]
        claim_id = next(
            line.split("CLAIM_ID:", 1)[1].strip()
            for line in prompt.splitlines() if line.startswith("CLAIM_ID:")
        )
        self.calls.append(claim_id)
        verdict = self._verdicts.get(claim_id, VerificationDraft(claim_id=claim_id))
        return {"structured_response": VerifierOutput(verdicts=[verdict])}


async def _seed(runtime):
    await runtime.append_events([
        ("claim.proposed", {"claim_id": "c1", "source_id": "a.md", "text": "boils at 100C"}),
    ])


async def test_corroboration_appends_and_dedups(tmp_path, postgres_dsn):
    (tmp_path / "a.md").write_text("x", encoding="utf-8")
    (tmp_path / "b.md").write_text("y", encoding="utf-8")
    runtime = ResearchRuntime(postgres_dsn, stream="ver-corr-stream")
    await runtime.connect()
    try:
        await _seed(runtime)
        runner = FakeVerifierRunner({"c1": VerificationDraft(
            claim_id="c1",
            corroborating_source_ids=["b.md", "b.md", "a.md"])})  # dup + self
        agent = VerifierAgent(runner, runtime, CorpusReader(tmp_path))
        assert await agent.readiness() == 0.6
        await agent.run_once()
        # self-corroboration (a.md) skipped, duplicate collapsed
        assert runtime.corroborators_for("c1") == ["b.md"]
        # verified claim leaves the pending set
        assert await agent.readiness() == 0.0
    finally:
        await runtime.close()


async def test_refutation_mints_counter_claim_and_refuted_event(tmp_path, postgres_dsn):
    (tmp_path / "a.md").write_text("x", encoding="utf-8")
    (tmp_path / "b.md").write_text("y", encoding="utf-8")
    runtime = ResearchRuntime(postgres_dsn, stream="ver-refute-stream")
    await runtime.connect()
    try:
        await _seed(runtime)
        runner = FakeVerifierRunner({"c1": VerificationDraft(
            claim_id="c1",
            refutation=RefutationDraft(source_id="b.md", counter_text="boils at 90C",
                                       reason="direct contradiction"))})
        agent = VerifierAgent(runner, runtime, CorpusReader(tmp_path))
        await agent.run_once()
        refuters = runtime.refuters_for("c1")
        assert len(refuters) == 1
        counter = runtime.get_claim(refuters[0])
        assert counter["source_id"] == "b.md" and counter["text"] == "boils at 90C"
        assert runtime.contradiction_targets() == ["c1"]
    finally:
        await runtime.close()


async def test_inconclusive_verdict_idles_without_blocking_later_claims(tmp_path, postgres_dsn):
    (tmp_path / "a.md").write_text("x", encoding="utf-8")
    (tmp_path / "b.md").write_text("y", encoding="utf-8")
    runtime = ResearchRuntime(postgres_dsn, stream="ver-inconclusive-stream")
    await runtime.connect()
    try:
        await _seed(runtime)
        runner = FakeVerifierRunner({"c2": VerificationDraft(
            claim_id="c2", corroborating_source_ids=["a.md"])})
        agent = VerifierAgent(runner, runtime, CorpusReader(tmp_path))
        await agent.run_once()  # c1 -> empty verdict -> inconclusive
        assert runtime.corroborators_for("c1") == []
        assert await agent.readiness() == 0.0  # inconclusive set drains workable queue
        # a later claim is NOT blocked behind the inconclusive one
        await runtime.append_event(
            "claim.proposed", {"claim_id": "c2", "source_id": "b.md", "text": "later"})
        assert await agent.readiness() == 0.6
        await agent.run_once()
        assert runner.calls == ["c1", "c2"]
        assert runtime.corroborators_for("c2") == ["a.md"]
    finally:
        await runtime.close()
