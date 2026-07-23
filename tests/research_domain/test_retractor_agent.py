from __future__ import annotations

from research_domain.agents import RetractorAgent
from research_domain.runtime import ResearchRuntime
from research_domain.schemas import CorrectionDraft, RetractorOutput

from tests.substrate.postgres_fixture import postgres_dsn  # noqa: F401


class FakeRetractorRunner:
    def __init__(self, corrections: dict[str, CorrectionDraft | None]):
        self._corrections = corrections
        self.calls: list[str] = []

    async def ainvoke(self, inputs: dict) -> dict:
        prompt = inputs["messages"][0]["content"]
        target = next(
            line.split("TARGET_CLAIM_ID:", 1)[1].strip()
            for line in prompt.splitlines() if line.startswith("TARGET_CLAIM_ID:")
        )
        self.calls.append(target)
        c = self._corrections.get(target)
        return {"structured_response": RetractorOutput(corrections=[c] if c else [])}


async def _seed_contradiction(runtime):
    await runtime.append_events([
        ("claim.proposed", {"claim_id": "c1", "source_id": "a.md", "text": "100C"}),
        ("claim.proposed", {"claim_id": "c2", "source_id": "b.md", "text": "90C"}),
        ("claim.refuted", {"claim_id": "c2", "target_claim_id": "c1", "reason": "differs"}),
    ])


async def test_correction_appended_for_valid_superseder(postgres_dsn):
    runtime = ResearchRuntime(postgres_dsn, stream="ret-valid-stream")
    await runtime.connect()
    try:
        await _seed_contradiction(runtime)
        runner = FakeRetractorRunner({"c1": CorrectionDraft(
            superseding_claim_id="c2", target_claim_id="c1", reason="b.md is newer")})
        agent = RetractorAgent(runner, runtime)
        assert await agent.readiness() == 0.5
        await agent.run_once()
        assert runtime.superseders_for("c1") == ["c2"]
        assert runtime.get_projection("claim_dependency_graph") == {"c1": ["c2"]}
        # resolved contradiction leaves pending -> readiness 0
        assert await agent.readiness() == 0.0
    finally:
        await runtime.close()


async def test_invalid_superseder_rejected(postgres_dsn):
    runtime = ResearchRuntime(postgres_dsn, stream="ret-invalid-stream")
    await runtime.connect()
    try:
        await _seed_contradiction(runtime)
        # c9 is not a refuter of c1 -> must be dropped at commit validation
        runner = FakeRetractorRunner({"c1": CorrectionDraft(
            superseding_claim_id="c9", target_claim_id="c1", reason="bogus")})
        agent = RetractorAgent(runner, runtime)
        await agent.run_once()
        assert runtime.superseders_for("c1") == []
        assert await agent.readiness() == 0.0  # fruitless run -> target leaves workable queue
    finally:
        await runtime.close()


async def test_original_stands_verdict_idles_without_blocking_later_targets(postgres_dsn):
    runtime = ResearchRuntime(postgres_dsn, stream="ret-stands-stream")
    await runtime.connect()
    try:
        await _seed_contradiction(runtime)
        runner = FakeRetractorRunner({
            "c1": None,  # model says original stands
            "c3": CorrectionDraft(
                superseding_claim_id="c4", target_claim_id="c3", reason="newer"),
        })
        agent = RetractorAgent(runner, runtime)
        await agent.run_once()
        assert runtime.superseders_for("c1") == []
        assert await agent.readiness() == 0.0  # stood target leaves workable queue
        # a later contradiction is NOT blocked behind the stood one
        await runtime.append_events([
            ("claim.proposed", {"claim_id": "c3", "source_id": "c.md", "text": "80C"}),
            ("claim.proposed", {"claim_id": "c4", "source_id": "d.md", "text": "85C"}),
            ("claim.refuted", {"claim_id": "c4", "target_claim_id": "c3", "reason": "x"}),
        ])
        assert await agent.readiness() == 0.5
        await agent.run_once()
        assert runner.calls == ["c1", "c3"]
        assert runtime.superseders_for("c3") == ["c4"]
    finally:
        await runtime.close()
