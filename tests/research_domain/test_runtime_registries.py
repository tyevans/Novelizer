from __future__ import annotations
import asyncio

import pytest

from research_domain.runtime import ResearchRuntime

from tests.substrate.postgres_fixture import postgres_dsn  # noqa: F401


@pytest.mark.asyncio
async def test_claims_and_corroborator_registries_refresh(postgres_dsn):
    runtime = ResearchRuntime(postgres_dsn, stream="registry-test-stream")
    await runtime.connect()
    try:
        await runtime.append_events([
            ("claim.proposed", {"claim_id": "c1", "source_id": "a.md", "text": "The sky is blue."}),
            ("claim.proposed", {"claim_id": "c2", "source_id": "b.md", "text": "The sky is green."}),
            ("source.corroborated", {"source_id": "b.md", "claim_id": "c1"}),
            ("claim.refuted", {"claim_id": "c2", "target_claim_id": "c1", "reason": "colors differ"}),
        ])
        claims = runtime.list_claims()
        assert [c["claim_id"] for c in claims] == ["c1", "c2"]
        assert runtime.get_claim("c1")["text"] == "The sky is blue."
        assert runtime.get_claim("missing") is None
        assert runtime.claimed_source_ids() == {"a.md", "b.md"}
        assert runtime.corroborators_for("c1") == ["b.md"]
        assert runtime.corroborators_for("c2") == []
        assert runtime.refuters_for("c1") == ["c2"]
        assert runtime.contradiction_targets() == ["c1"]
        assert runtime.superseders_for("c1") == []
    finally:
        await runtime.close()


@pytest.mark.asyncio
async def test_append_events_batches_with_single_visible_catchup(postgres_dsn):
    runtime = ResearchRuntime(postgres_dsn, stream="registry-batch-stream")
    await runtime.connect()
    try:
        await runtime.append_events([
            ("claim.proposed", {"claim_id": "c1", "source_id": "a.md", "text": "t1"}),
            ("claim.proposed", {"claim_id": "c2", "source_id": "a.md", "text": "t2"}),
        ])
        assert runtime.get_projection("source_coverage") == {"a.md": 2}
    finally:
        await runtime.close()


@pytest.mark.asyncio
async def test_concurrent_appends_serialize_without_corruption(postgres_dsn):
    runtime = ResearchRuntime(postgres_dsn, stream="registry-lock-stream")
    await runtime.connect()
    try:
        async def writer(i: int):
            await runtime.append_event(
                "claim.proposed",
                {"claim_id": f"c{i}", "source_id": f"s{i}.md", "text": f"t{i}"})
        await asyncio.gather(*(writer(i) for i in range(6)))
        assert len(runtime.list_claims()) == 6
        assert runtime.claimed_source_ids() == {f"s{i}.md" for i in range(6)}
    finally:
        await runtime.close()
