from __future__ import annotations
from typing import Any, Callable

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
