from __future__ import annotations
from dataclasses import dataclass
from enum import StrEnum


class GatingTier(StrEnum):
    always = "always"
    never = "never"
    tiered = "tiered"


@dataclass(frozen=True)
class EventTypeSpec:
    name: str
    gating_tier: GatingTier
    tier_level: str | None = None


class EventTypeRegistry:
    def __init__(self) -> None:
        self._specs: dict[str, EventTypeSpec] = {}

    def register(self, spec: EventTypeSpec) -> None:
        if spec.name in self._specs:
            raise ValueError(f"event type already registered: {spec.name}")
        self._specs[spec.name] = spec

    def get(self, name: str) -> EventTypeSpec:
        return self._specs[name]

    def all(self) -> list[EventTypeSpec]:
        return list(self._specs.values())
