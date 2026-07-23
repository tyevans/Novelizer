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


class AlwaysRefutingRunner:
    """Refutes whatever claim it's asked about, citing the OTHER fixed doc
    with that doc's fixed contradictory text. Never returns an empty
    verdict -- models a worst-case refutation-happy LLM."""

    def __init__(self, runtime: ResearchRuntime, texts: dict[str, str]):
        self._runtime = runtime
        # source_id -> fixed text for that source
        self._texts = texts

    async def ainvoke(self, inputs: dict) -> dict:
        prompt = inputs["messages"][0]["content"]
        claim_id = next(
            line.split("CLAIM_ID:", 1)[1].strip()
            for line in prompt.splitlines() if line.startswith("CLAIM_ID:")
        )
        claim = self._runtime.get_claim(claim_id)
        this_source = claim["source_id"]
        other_source = next(s for s in self._texts if s != this_source)
        verdict = VerificationDraft(
            claim_id=claim_id,
            refutation=RefutationDraft(
                source_id=other_source,
                counter_text=self._texts[other_source],
                reason="mutual contradiction"))
        return {"structured_response": VerifierOutput(verdicts=[verdict])}


async def test_mutual_contradiction_ping_pong_terminates(tmp_path, postgres_dsn):
    (tmp_path / "a.md").write_text("x", encoding="utf-8")
    (tmp_path / "b.md").write_text("y", encoding="utf-8")
    runtime = ResearchRuntime(postgres_dsn, stream="ver-pingpong-stream")
    await runtime.connect()
    try:
        await runtime.catch_up()
        texts = {"a.md": "X is 5", "b.md": "X is 7"}
        await runtime.append_events([
            ("claim.proposed", {"claim_id": "c1", "source_id": "a.md", "text": texts["a.md"],
                                 "origin": "extracted"}),
        ])
        runner = AlwaysRefutingRunner(runtime, texts)
        agent = VerifierAgent(runner, runtime, CorpusReader(tmp_path))

        for _ in range(6):
            if await agent.readiness() == 0.0:
                break
            await agent.run_once()

        assert await agent.readiness() == 0.0
        claims = runtime.list_claims()
        assert len(claims) == 2
        ids = [c["claim_id"] for c in claims]
        c_a, c_b = (ids if runtime.get_claim(ids[0])["source_id"] == "a.md" else ids[::-1])
        assert runtime.refuters_for(c_a) == [c_b]
        assert runtime.refuters_for(c_b) == [c_a]
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
