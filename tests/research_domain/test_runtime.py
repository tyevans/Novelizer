# tests/research_domain/test_runtime.py
import pytest

from research_domain.runtime import ResearchRuntime
from tests.substrate.postgres_fixture import postgres_dsn


@pytest.mark.asyncio
async def test_research_runtime_keeps_all_three_projections_current_across_appends(postgres_dsn):
    runtime = ResearchRuntime(postgres_dsn, stream="research-runtime-test-stream")
    await runtime.connect()
    try:
        await runtime.append_event(
            "claim.proposed", {"claim_id": "claim-1", "source_id": "source-a", "text": "the sky is green"}
        )
        await runtime.append_event(
            "claim.proposed", {"claim_id": "claim-2", "source_id": "source-b", "text": "the sky is blue"}
        )
        assert runtime.get_projection("source_coverage") == {"source-a": 1, "source-b": 1}

        await runtime.append_event(
            "claim.refuted",
            {"claim_id": "claim-2", "target_claim_id": "claim-1", "reason": "source-b directly observed"},
        )
        assert runtime.get_projection("contradiction_map") == {"claim-1": ["claim-2"]}

        await runtime.append_event(
            "claim.proposed", {"claim_id": "claim-3", "source_id": "source-c", "text": "the sky is blue at noon"}
        )
        await runtime.append_event(
            "claim.corrected",
            {"claim_id": "claim-3", "target_claim_id": "claim-2", "reason": "time-of-day qualifier added"},
        )
        assert runtime.get_projection("claim_dependency_graph") == {"claim-2": ["claim-3"]}
        assert runtime.get_projection("source_coverage") == {"source-a": 1, "source-b": 1, "source-c": 1}
    finally:
        await runtime.close()
