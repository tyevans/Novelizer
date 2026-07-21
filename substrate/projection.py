from __future__ import annotations
from dataclasses import dataclass
from typing import Any, Callable


@dataclass(frozen=True)
class ProjectionSpec:
    name: str
    invalidation_key: Callable[[Any], str]
    recompute: Callable[[str], Any]


class ProjectionCatalog:
    def __init__(self) -> None:
        self._specs: dict[str, ProjectionSpec] = {}
        self._dirty: dict[str, set[str]] = {}

    def register(self, spec: ProjectionSpec) -> None:
        self._specs[spec.name] = spec
        self._dirty[spec.name] = set()

    def invalidate(self, projection_name: str, source_event: Any) -> None:
        spec = self._specs[projection_name]
        key = spec.invalidation_key(source_event)
        self._dirty[projection_name].add(key)

    def recompute_dirty(self, projection_name: str) -> dict[str, Any]:
        spec = self._specs[projection_name]
        keys = self._dirty[projection_name]
        result = {key: spec.recompute(key) for key in keys}
        self._dirty[projection_name] = set()
        return result
