from __future__ import annotations
from typing import Callable

from substrate.projection import ProjectionCatalog, ProjectionSpec


def build_source_coverage_catalog(count_claims_for_source: Callable[[str], int]) -> ProjectionCatalog:
    catalog = ProjectionCatalog()
    catalog.register(
        ProjectionSpec(
            name="source_coverage",
            invalidation_key=lambda event: event.payload["source_id"],
            recompute=count_claims_for_source,
        )
    )
    return catalog


def build_contradiction_map_catalog(
    refuting_claims_for_target: Callable[[str], list[str]]
) -> ProjectionCatalog:
    catalog = ProjectionCatalog()
    catalog.register(
        ProjectionSpec(
            name="contradiction_map",
            invalidation_key=lambda event: event.payload["target_claim_id"],
            recompute=refuting_claims_for_target,
        )
    )
    return catalog


def build_claim_dependency_catalog(
    superseding_claims_for_target: Callable[[str], list[str]]
) -> ProjectionCatalog:
    catalog = ProjectionCatalog()
    catalog.register(
        ProjectionSpec(
            name="claim_dependency_graph",
            invalidation_key=lambda event: event.payload["target_claim_id"],
            recompute=superseding_claims_for_target,
        )
    )
    return catalog
