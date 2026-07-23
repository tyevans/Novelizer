"""End-to-end proving ground: extractor -> verifier -> retractor driven by
agent_kit.Scheduler over a corpus with a planted contradiction, no LLM
anywhere. Asserts the projections converge to the expected final state."""
from __future__ import annotations

from agent_kit import Scheduler

from research_domain.agents import ExtractorAgent, RetractorAgent, VerifierAgent
from research_domain.corpus import CorpusReader
from research_domain.runtime import ResearchRuntime
from research_domain.schemas import (
    ClaimDraft,
    CorrectionDraft,
    ExtractorOutput,
    RefutationDraft,
    RetractorOutput,
    VerificationDraft,
    VerifierOutput,
)

from tests.substrate.postgres_fixture import postgres_dsn  # noqa: F401

BOIL_A = "water boils at 100C at sea level"
BOIL_B = "water boils at 90C everywhere"
GRAVITY = "objects fall at 9.8 m/s^2"


class FakeExtractorRunner:
    async def ainvoke(self, inputs: dict) -> dict:
        prompt = inputs["messages"][0]["content"]
        source_id = next(
            line.split("SOURCE_ID:", 1)[1].strip()
            for line in prompt.splitlines() if line.startswith("SOURCE_ID:"))
        by_source = {
            "boiling_a.md": [BOIL_A],
            "boiling_b.md": [BOIL_B],
            "gravity.md": [GRAVITY],
        }
        return {"structured_response": ExtractorOutput(
            claims=[ClaimDraft(text=t) for t in by_source.get(source_id, [])])}


class FakeVerifierRunner:
    """Corroborates gravity from boiling_a (scripted); refutes the 100C
    claim from boiling_b; everything else inconclusive."""

    def __init__(self, runtime: ResearchRuntime):
        self._runtime = runtime

    async def ainvoke(self, inputs: dict) -> dict:
        prompt = inputs["messages"][0]["content"]
        claim_id = next(
            line.split("CLAIM_ID:", 1)[1].strip()
            for line in prompt.splitlines() if line.startswith("CLAIM_ID:"))
        claim = self._runtime.get_claim(claim_id)
        if claim and claim["text"] == BOIL_A:
            verdict = VerificationDraft(
                claim_id=claim_id,
                refutation=RefutationDraft(
                    source_id="boiling_b.md", counter_text=BOIL_B,
                    reason="boiling_b.md asserts a different boiling point"))
        elif claim and claim["text"] == GRAVITY:
            verdict = VerificationDraft(
                claim_id=claim_id, corroborating_source_ids=["boiling_a.md"])
        else:
            verdict = VerificationDraft(claim_id=claim_id)
        return {"structured_response": VerifierOutput(verdicts=[verdict])}


class FakeRetractorRunner:
    """Always sides with the refuter (the planted resolution)."""

    def __init__(self, runtime: ResearchRuntime):
        self._runtime = runtime

    async def ainvoke(self, inputs: dict) -> dict:
        prompt = inputs["messages"][0]["content"]
        target = next(
            line.split("TARGET_CLAIM_ID:", 1)[1].strip()
            for line in prompt.splitlines() if line.startswith("TARGET_CLAIM_ID:"))
        refuters = self._runtime.refuters_for(target)
        correction = CorrectionDraft(
            superseding_claim_id=refuters[0], target_claim_id=target,
            reason="refuting source is more specific")
        return {"structured_response": RetractorOutput(corrections=[correction])}


async def test_trio_converges_on_planted_contradiction(tmp_path, postgres_dsn):
    (tmp_path / "boiling_a.md").write_text(BOIL_A, encoding="utf-8")
    (tmp_path / "boiling_b.md").write_text(BOIL_B, encoding="utf-8")
    (tmp_path / "gravity.md").write_text(GRAVITY, encoding="utf-8")

    runtime = ResearchRuntime(postgres_dsn, stream="pipeline-stream")
    await runtime.connect()
    try:
        await runtime.catch_up()
        corpus = CorpusReader(tmp_path)
        agents = [
            ExtractorAgent(FakeExtractorRunner(), runtime, corpus, interval=1),
            VerifierAgent(FakeVerifierRunner(runtime), runtime, corpus, interval=1),
            RetractorAgent(FakeRetractorRunner(runtime), runtime, interval=1),
        ]
        fake_now = [1000.0]
        sched = Scheduler(agents, clock=lambda: fake_now[0], max_concurrent_agents=2)

        def _gravity_corroborated() -> bool:
            claim = next(
                (c for c in runtime.list_claims() if c["text"] == GRAVITY), None)
            return bool(claim and runtime.corroborators_for(claim["claim_id"]))

        for _ in range(40):  # generous tick budget; loop exits early when done
            await sched.tick()
            await sched.drain_in_flight()
            fake_now[0] += 10.0
            done = (
                len(runtime.claimed_source_ids()) >= 3
                and runtime.get_projection("claim_dependency_graph")
                and _gravity_corroborated()
            )
            if done:
                break

        # every doc extracted
        assert runtime.claimed_source_ids() == {"boiling_a.md", "boiling_b.md", "gravity.md"}
        # the contradiction was found and resolved
        [(target, superseders)] = runtime.get_projection("claim_dependency_graph").items()
        assert runtime.get_claim(target)["text"] == BOIL_A
        assert len(superseders) == 1
        assert runtime.get_claim(superseders[0])["text"] == BOIL_B
        # gravity got corroborated
        gravity_claim = next(c for c in runtime.list_claims() if c["text"] == GRAVITY)
        assert runtime.corroborators_for(gravity_claim["claim_id"]) == ["boiling_a.md"]
        # no agent has anything left to do
        for agent in agents:
            assert await agent.readiness() == 0.0
        # scheduler bookkeeping saw every agent complete at least once
        status = {s["name"]: s for s in sched.status()}
        assert all(status[n]["run_count"] >= 1 for n in ("extractor", "verifier", "retractor"))
        assert all(status[n]["last_error"] is None for n in ("extractor", "verifier", "retractor"))
    finally:
        await runtime.close()
