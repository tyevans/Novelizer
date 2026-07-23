# research_domain/runtime.py
from __future__ import annotations

import asyncio

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
    data -- see the class-level note on _refresh_lookup_dicts.

    All mutation paths (catch_up, append_event, append_events) are
    serialized under one asyncio.Lock: multiple agents share a single
    runtime instance under a concurrency-2 scheduler, and
    _refresh_lookup_dicts clears shared dicts mid-refresh — the lock keeps
    an agent from reading half-cleared state through another's refresh."""

    def __init__(self, dsn: str, stream: str = "research-stream") -> None:
        super().__init__(PostgresEventStore(dsn), stream)

        self._refresh_lock = asyncio.Lock()

        # These dicts are mutated in place (never rebound) by
        # _refresh_lookup_dicts, so the closures below -- captured once here
        # and handed to the projection catalogs -- always see current data.
        self._counts_by_source: dict[str, int] = {}
        self._refuters_by_target: dict[str, list[str]] = {}
        self._superseders_by_target: dict[str, list[str]] = {}
        self._claims_by_id: dict[str, dict] = {}
        self._corroborators_by_claim: dict[str, list[str]] = {}
        self._extracted_sources: set[str] = set()

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

    # --- read accessors (plain reads of the last-refreshed state) ---------

    def list_claims(self) -> list[dict]:
        return list(self._claims_by_id.values())

    def get_claim(self, claim_id: str) -> dict | None:
        return self._claims_by_id.get(claim_id)

    def claimed_source_ids(self) -> set[str]:
        return set(self._counts_by_source)

    def extracted_source_ids(self) -> set[str]:
        return set(self._extracted_sources)

    def corroborators_for(self, claim_id: str) -> list[str]:
        return list(self._corroborators_by_claim.get(claim_id, []))

    def refuters_for(self, claim_id: str) -> list[str]:
        return list(self._refuters_by_target.get(claim_id, []))

    def superseders_for(self, claim_id: str) -> list[str]:
        return list(self._superseders_by_target.get(claim_id, []))

    def contradiction_targets(self) -> list[str]:
        return list(self._refuters_by_target)

    # --- refresh / mutation paths -----------------------------------------

    async def _refresh_lookup_dicts(self) -> None:
        events = await self._event_store.read_stream(self._stream)
        self._counts_by_source.clear()
        self._refuters_by_target.clear()
        self._superseders_by_target.clear()
        self._claims_by_id.clear()
        self._corroborators_by_claim.clear()
        self._extracted_sources.clear()
        for event in events:
            payload = event["payload"]
            if event["event_type"] == "claim.proposed":
                source_id = payload["source_id"]
                self._counts_by_source[source_id] = self._counts_by_source.get(source_id, 0) + 1
                self._claims_by_id[payload["claim_id"]] = {
                    "claim_id": payload["claim_id"],
                    "source_id": source_id,
                    "text": payload["text"],
                }
                if payload.get("origin", "extracted") == "extracted":
                    self._extracted_sources.add(source_id)
            elif event["event_type"] == "source.corroborated":
                self._corroborators_by_claim.setdefault(payload["claim_id"], []).append(
                    payload["source_id"]
                )
            elif event["event_type"] == "claim.refuted":
                target_id = payload["target_claim_id"]
                self._refuters_by_target.setdefault(target_id, []).append(payload["claim_id"])
            elif event["event_type"] == "claim.corrected":
                target_id = payload["target_claim_id"]
                self._superseders_by_target.setdefault(target_id, []).append(payload["claim_id"])

    async def _catch_up_inner(self) -> None:
        await self._refresh_lookup_dicts()
        await super().catch_up()

    async def catch_up(self) -> None:
        async with self._refresh_lock:
            await self._catch_up_inner()

    async def append_event(self, event_type: str, payload: dict) -> None:
        async with self._refresh_lock:
            await self.append(event_type, payload)
            await self._catch_up_inner()

    async def append_events(self, events: list[tuple[str, dict]]) -> None:
        """Batch append + one catch_up: the agent commit path — several
        events land atomically-enough (one refresh) under the lock."""
        async with self._refresh_lock:
            for event_type, payload in events:
                await self.append(event_type, payload)
            await self._catch_up_inner()
