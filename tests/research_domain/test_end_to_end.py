# tests/research_domain/test_end_to_end.py
import pytest

from substrate.postgres.events import PostgresEventStore
from substrate.policy import is_gated
from research_domain.event_types import build_research_registry, RESEARCH_TIER_ORDER
from research_domain.projections import build_source_coverage_catalog
from tests.substrate.postgres_fixture import postgres_dsn


class _ClaimEvent:
    def __init__(self, source_id: str) -> None:
        self.payload = {"source_id": source_id}


@pytest.mark.asyncio
async def test_research_domain_composes_events_gating_and_projection(postgres_dsn):
    store = PostgresEventStore(postgres_dsn)
    await store.connect()
    try:
        await store.append("research-stream", "claim.proposed", {"source_id": "source-a", "text": "x"})
        await store.append("research-stream", "claim.proposed", {"source_id": "source-a", "text": "y"})
        await store.append("research-stream", "claim.proposed", {"source_id": "source-b", "text": "z"})
        await store.append("research-stream", "source.corroborated", {"source_id": "source-a"})

        registry = build_research_registry()
        # A Scout's claim.proposed event is auto-runnable once the "auto" tier is active.
        assert is_gated("claim.proposed", registry, RESEARCH_TIER_ORDER, current_tier_index=0) is True
        # A corroboration is never gated -- pure additive evidence.
        assert is_gated("source.corroborated", registry, RESEARCH_TIER_ORDER, current_tier_index=1) is False

        # ProjectionCatalog.recompute (per its M1 interface) is a synchronous
        # callable, so the async count query is run once up front and the
        # catalog's recompute function is a plain dict lookup over the result
        # -- not an async call back into Postgres from inside recompute.
        rows = await store.read_stream("research-stream")
        counts_by_source: dict[str, int] = {}
        for r in rows:
            if r["event_type"] == "claim.proposed":
                sid = r["payload"]["source_id"]
                counts_by_source[sid] = counts_by_source.get(sid, 0) + 1

        catalog = build_source_coverage_catalog(lambda source_id: counts_by_source[source_id])
        catalog.invalidate("source_coverage", _ClaimEvent(source_id="source-a"))
        catalog.invalidate("source_coverage", _ClaimEvent(source_id="source-b"))
        result = await catalog.recompute_dirty("source_coverage")
        assert result == {"source-a": 2, "source-b": 1}
    finally:
        await store.close()


from research_domain.events import ClaimProposed, ClaimRefuted, ClaimCorrected
from research_domain.projections import (
    build_contradiction_map_catalog,
    build_claim_dependency_catalog,
)


class _TargetClaimEvent:
    def __init__(self, target_claim_id: str) -> None:
        self.payload = {"target_claim_id": target_claim_id}


@pytest.mark.asyncio
async def test_contradiction_map_and_dependency_graph_recompute_from_real_postgres_events(postgres_dsn):
    store = PostgresEventStore(postgres_dsn)
    await store.connect()
    try:
        await store.append(
            "research-stream", "claim.proposed",
            ClaimProposed(claim_id="claim-1", source_id="source-a", text="the sky is green").model_dump(),
        )
        await store.append(
            "research-stream", "claim.proposed",
            ClaimProposed(claim_id="claim-2", source_id="source-b", text="the sky is blue").model_dump(),
        )
        await store.append(
            "research-stream", "claim.refuted",
            ClaimRefuted(claim_id="claim-2", target_claim_id="claim-1", reason="source-b directly observed").model_dump(),
        )
        await store.append(
            "research-stream", "claim.proposed",
            ClaimProposed(claim_id="claim-3", source_id="source-c", text="the sky is blue at noon").model_dump(),
        )
        await store.append(
            "research-stream", "claim.corrected",
            ClaimCorrected(claim_id="claim-3", target_claim_id="claim-2", reason="time-of-day qualifier added").model_dump(),
        )

        rows = await store.read_stream("research-stream")

        refuters_by_target: dict[str, list[str]] = {}
        superseders_by_target: dict[str, list[str]] = {}
        for r in rows:
            if r["event_type"] == "claim.refuted":
                tid = r["payload"]["target_claim_id"]
                refuters_by_target.setdefault(tid, []).append(r["payload"]["claim_id"])
            elif r["event_type"] == "claim.corrected":
                tid = r["payload"]["target_claim_id"]
                superseders_by_target.setdefault(tid, []).append(r["payload"]["claim_id"])

        contradiction_catalog = build_contradiction_map_catalog(lambda cid: refuters_by_target[cid])
        contradiction_catalog.invalidate("contradiction_map", _TargetClaimEvent(target_claim_id="claim-1"))
        contradiction_result = await contradiction_catalog.recompute_dirty("contradiction_map")
        assert contradiction_result == {"claim-1": ["claim-2"]}

        dependency_catalog = build_claim_dependency_catalog(lambda cid: superseders_by_target[cid])
        dependency_catalog.invalidate("claim_dependency_graph", _TargetClaimEvent(target_claim_id="claim-2"))
        dependency_result = await dependency_catalog.recompute_dirty("claim_dependency_graph")
        assert dependency_result == {"claim-2": ["claim-3"]}
    finally:
        await store.close()
