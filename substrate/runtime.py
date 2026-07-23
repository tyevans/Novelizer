# substrate/runtime.py
from __future__ import annotations
from dataclasses import dataclass

from substrate.postgres.events import PostgresEventStore
from substrate.projection import ProjectionCatalog


class _EventView:
    """Wraps a read_stream() row's payload so it satisfies the `.payload`
    attribute access every ProjectionSpec.invalidation_key lambda expects."""

    def __init__(self, payload: dict) -> None:
        self.payload = payload


@dataclass(frozen=True)
class _ProjectionRegistration:
    catalog: ProjectionCatalog
    projection_name: str
    event_types: frozenset[str]


class RuntimeBase:
    """Storage-agnostic lifecycle: connect an event store, replay its stream
    into registered projections, expose read access, close cleanly.

    Domain runtimes subclass this and register their own ProjectionCatalogs
    against the event types that should invalidate them.
    """

    def __init__(self, event_store: PostgresEventStore, stream: str) -> None:
        self._event_store = event_store
        self._stream = stream
        self._registrations: list[_ProjectionRegistration] = []
        self._results: dict[str, dict] = {}

    def register_projection(
        self, catalog: ProjectionCatalog, projection_name: str, event_types: set[str]
    ) -> None:
        self._registrations.append(
            _ProjectionRegistration(catalog, projection_name, frozenset(event_types))
        )

    async def connect(self) -> None:
        await self._event_store.connect()

    async def append(self, event_type: str, payload: dict, **kwargs) -> int:
        return await self._event_store.append(self._stream, event_type, payload, **kwargs)

    async def catch_up(self) -> None:
        events = await self._event_store.read_stream(self._stream)
        for registration in self._registrations:
            for event in events:
                if event["event_type"] in registration.event_types:
                    registration.catalog.invalidate(
                        registration.projection_name, _EventView(event["payload"])
                    )
            self._results[registration.projection_name] = await registration.catalog.recompute_dirty(
                registration.projection_name
            )

    def get_projection(self, projection_name: str) -> dict:
        return self._results.get(projection_name, {})

    async def close(self) -> None:
        await self._event_store.close()
