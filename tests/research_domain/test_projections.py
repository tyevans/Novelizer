from research_domain.projections import build_source_coverage_catalog


class _FakeClaimEvent:
    def __init__(self, source_id: str) -> None:
        self.payload = {"source_id": source_id}


def test_invalidating_a_source_and_recomputing_returns_its_count():
    counts = {"source-a": 3, "source-b": 7}
    catalog = build_source_coverage_catalog(lambda source_id: counts[source_id])
    catalog.invalidate("source_coverage", _FakeClaimEvent(source_id="source-a"))
    result = catalog.recompute_dirty("source_coverage")
    assert result == {"source-a": 3}


def test_multiple_sources_invalidated_all_recompute():
    counts = {"source-a": 3, "source-b": 7}
    catalog = build_source_coverage_catalog(lambda source_id: counts[source_id])
    catalog.invalidate("source_coverage", _FakeClaimEvent(source_id="source-a"))
    catalog.invalidate("source_coverage", _FakeClaimEvent(source_id="source-b"))
    result = catalog.recompute_dirty("source_coverage")
    assert result == {"source-a": 3, "source-b": 7}
