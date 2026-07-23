# research_domain/runtime.py
from __future__ import annotations

from substrate import PostgresEventStore, RuntimeBase
from research_domain.projections import (
    build_claim_dependency_catalog,
    build_contradiction_map_catalog,
    build_source_coverage_catalog,
)


class ResearchRuntime(RuntimeBase):
    """Wires the three research_domain projections to a Postgres-backed
    RuntimeBase. Lookup dicts are refreshed from the full event stream on
    every catch_up() so the catalogs' recompute closures always see current
    data -- see the class-level note on _refresh_lookup_dicts."""

    def __init__(self, dsn: str, stream: str = "research-stream") -> None:
        super().__init__(PostgresEventStore(dsn), stream)

        # These dicts are mutated in place (never rebound) by
        # _refresh_lookup_dicts, so the closures below -- captured once here
        # and handed to the projection catalogs -- always see current data.
        self._counts_by_source: dict[str, int] = {}
        self._refuters_by_target: dict[str, list[str]] = {}
        self._superseders_by_target: dict[str, list[str]] = {}

        source_coverage = build_source_coverage_catalog(
            lambda source_id: self._counts_by_source[source_id]
        )
        contradiction_map = build_contradiction_map_catalog(
            lambda target_claim_id: self._refuters_by_target[target_claim_id]
        )
        claim_dependency_graph = build_claim_dependency_catalog(
            lambda target_claim_id: self._superseders_by_target[target_claim_id]
        )

        self.register_projection(source_coverage, "source_coverage", {"claim.proposed"})
        self.register_projection(contradiction_map, "contradiction_map", {"claim.refuted"})
        self.register_projection(
            claim_dependency_graph, "claim_dependency_graph", {"claim.corrected"}
        )

    async def _refresh_lookup_dicts(self) -> None:
        events = await self._event_store.read_stream(self._stream)
        self._counts_by_source.clear()
        self._refuters_by_target.clear()
        self._superseders_by_target.clear()
        for event in events:
            if event["event_type"] == "claim.proposed":
                source_id = event["payload"]["source_id"]
                self._counts_by_source[source_id] = self._counts_by_source.get(source_id, 0) + 1
            elif event["event_type"] == "claim.refuted":
                target_id = event["payload"]["target_claim_id"]
                self._refuters_by_target.setdefault(target_id, []).append(event["payload"]["claim_id"])
            elif event["event_type"] == "claim.corrected":
                target_id = event["payload"]["target_claim_id"]
                self._superseders_by_target.setdefault(target_id, []).append(event["payload"]["claim_id"])

    async def catch_up(self) -> None:
        await self._refresh_lookup_dicts()
        await super().catch_up()

    async def append_event(self, event_type: str, payload: dict) -> None:
        await self.append(event_type, payload)
        await self.catch_up()
