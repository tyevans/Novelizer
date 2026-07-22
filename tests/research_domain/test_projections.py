import pytest

from research_domain.projections import (
    build_source_coverage_catalog,
    build_contradiction_map_catalog,
    build_claim_dependency_catalog,
)


class _FakeClaimEvent:
    def __init__(self, source_id: str) -> None:
        self.payload = {"source_id": source_id}


@pytest.mark.asyncio
async def test_invalidating_a_source_and_recomputing_returns_its_count():
    counts = {"source-a": 3, "source-b": 7}
    catalog = build_source_coverage_catalog(lambda source_id: counts[source_id])
    catalog.invalidate("source_coverage", _FakeClaimEvent(source_id="source-a"))
    result = await catalog.recompute_dirty("source_coverage")
    assert result == {"source-a": 3}


@pytest.mark.asyncio
async def test_multiple_sources_invalidated_all_recompute():
    counts = {"source-a": 3, "source-b": 7}
    catalog = build_source_coverage_catalog(lambda source_id: counts[source_id])
    catalog.invalidate("source_coverage", _FakeClaimEvent(source_id="source-a"))
    catalog.invalidate("source_coverage", _FakeClaimEvent(source_id="source-b"))
    result = await catalog.recompute_dirty("source_coverage")
    assert result == {"source-a": 3, "source-b": 7}


class _FakeTargetEvent:
    def __init__(self, target_claim_id: str) -> None:
        self.payload = {"target_claim_id": target_claim_id}


@pytest.mark.asyncio
async def test_contradiction_map_returns_refuting_claims_for_target():
    edges = {"claim-1": ["claim-2", "claim-3"]}
    catalog = build_contradiction_map_catalog(lambda claim_id: edges[claim_id])
    catalog.invalidate("contradiction_map", _FakeTargetEvent(target_claim_id="claim-1"))
    result = await catalog.recompute_dirty("contradiction_map")
    assert result == {"claim-1": ["claim-2", "claim-3"]}


@pytest.mark.asyncio
async def test_contradiction_map_multiple_targets_invalidated_all_recompute():
    edges = {"claim-1": ["claim-2"], "claim-5": ["claim-6"]}
    catalog = build_contradiction_map_catalog(lambda claim_id: edges[claim_id])
    catalog.invalidate("contradiction_map", _FakeTargetEvent(target_claim_id="claim-1"))
    catalog.invalidate("contradiction_map", _FakeTargetEvent(target_claim_id="claim-5"))
    result = await catalog.recompute_dirty("contradiction_map")
    assert result == {"claim-1": ["claim-2"], "claim-5": ["claim-6"]}


@pytest.mark.asyncio
async def test_contradiction_map_returns_empty_list_for_claim_with_no_refuters():
    edges = {"claim-1": []}
    catalog = build_contradiction_map_catalog(lambda claim_id: edges[claim_id])
    catalog.invalidate("contradiction_map", _FakeTargetEvent(target_claim_id="claim-1"))
    result = await catalog.recompute_dirty("contradiction_map")
    assert result == {"claim-1": []}


@pytest.mark.asyncio
async def test_claim_dependency_graph_returns_superseding_claims_for_target():
    edges = {"claim-1": ["claim-4"]}
    catalog = build_claim_dependency_catalog(lambda claim_id: edges[claim_id])
    catalog.invalidate("claim_dependency_graph", _FakeTargetEvent(target_claim_id="claim-1"))
    result = await catalog.recompute_dirty("claim_dependency_graph")
    assert result == {"claim-1": ["claim-4"]}


@pytest.mark.asyncio
async def test_claim_dependency_graph_and_contradiction_map_are_independent_catalogs():
    contradiction_edges = {"claim-1": ["claim-2"]}
    dependency_edges = {"claim-1": ["claim-9"]}
    contradiction_catalog = build_contradiction_map_catalog(lambda cid: contradiction_edges[cid])
    dependency_catalog = build_claim_dependency_catalog(lambda cid: dependency_edges[cid])
    contradiction_catalog.invalidate("contradiction_map", _FakeTargetEvent(target_claim_id="claim-1"))
    dependency_catalog.invalidate("claim_dependency_graph", _FakeTargetEvent(target_claim_id="claim-1"))
    contradiction_result = await contradiction_catalog.recompute_dirty("contradiction_map")
    dependency_result = await dependency_catalog.recompute_dirty("claim_dependency_graph")
    assert contradiction_result == {"claim-1": ["claim-2"]}
    assert dependency_result == {"claim-1": ["claim-9"]}
