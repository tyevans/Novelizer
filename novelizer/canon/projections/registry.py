"""The projection dispatch table and the context handlers receive.

Dispatch is a mapping from event type to handler, populated by the `@projects`
decorator at import time. The Projector owns transactions, retries and the
replay position; it does not own the knowledge of what any single event means.
Adding an event type means adding a handler in the module that owns that
aggregate -- no existing code changes.
"""
from __future__ import annotations
import json
from dataclasses import dataclass
from typing import Any, Awaitable, Callable

import aiosqlite

from novelizer.canon.events import StoredEvent


@dataclass(frozen=True)
class ProjectionContext:
    """Everything a handler needs to fold one event into the read model."""

    conn: aiosqlite.Connection
    event: StoredEvent

    @property
    def payload(self) -> dict[str, Any]:
        return self.event.payload

    @property
    def event_type(self) -> str:
        return self.event.event_type

    @property
    def aggregate_id(self) -> str:
        return self.event.aggregate_id

    @property
    def data(self) -> str:
        """The payload as stored JSON, for tables that keep the raw blob."""
        return json.dumps(self.event.payload)

    async def fetch_one(self, sql: str, params: tuple = ()) -> tuple | None:
        cur = await self.conn.execute(sql, params)
        return await cur.fetchone()

    async def fetch_all(self, sql: str, params: tuple = ()) -> list[tuple]:
        cur = await self.conn.execute(sql, params)
        return list(await cur.fetchall())

    async def execute(self, sql: str, params: tuple = ()) -> None:
        await self.conn.execute(sql, params)


Handler = Callable[[ProjectionContext], Awaitable[None]]

HANDLERS: dict[str, Handler] = {}


def projects(*event_types: str) -> Callable[[Handler], Handler]:
    """Register `fn` as the projection for each of `event_types`.

    Registering a type that already has a handler is an error: two handlers for
    one event type means one of them silently never runs, which is the exact
    class of bug a registry is supposed to make impossible.
    """

    def decorate(fn: Handler) -> Handler:
        for event_type in event_types:
            existing = HANDLERS.get(event_type)
            if existing is not None and existing is not fn:
                raise ValueError(
                    f"{event_type} already projected by {existing.__module__}."
                    f"{existing.__qualname__}; refusing to shadow it with "
                    f"{fn.__module__}.{fn.__qualname__}"
                )
            HANDLERS[event_type] = fn
        return fn

    return decorate


def upsert(table: str, columns: str, values: str) -> str:
    """Build an upsert that preserves the row's rowid.

    INSERT OR REPLACE deletes the conflicting row and inserts a new one, which
    hands that row a fresh (max+1) rowid. Every projection read recovers
    authored order from rowid (see ReadStore.list_chapters), so a REPLACE moved
    any updated row to the end -- revising chapter 1 relocated it to the back of
    the book, and with it the chapter_order that paradox detection, staleness,
    context indexes and export all derive. ON CONFLICT DO UPDATE mutates the row
    in place, so insertion order is stable for the row's whole life.
    """
    sets = ", ".join(
        f"{c}=excluded.{c}" for c in (c.strip() for c in columns.split(",")) if c != "id"
    )
    return f"INSERT INTO {table} ({columns}) VALUES ({values}) ON CONFLICT(id) DO UPDATE SET {sets}"


async def load_record(ctx: ProjectionContext, table: str, model_cls, record_id: str):
    """Read one record's validated model, or None when the row is absent.

    An absent row is the normal "cites an id we never projected" case: handlers
    treat it as a no-op rather than an error, because the event remains a fact
    in the log either way.
    """
    row = await ctx.fetch_one(f"SELECT data FROM {table} WHERE id=?", (record_id,))
    if row is None:
        return None
    return model_cls.model_validate_json(row[0])


async def row_exists(ctx: ProjectionContext, table: str, record_id: str) -> bool:
    return await ctx.fetch_one(f"SELECT 1 FROM {table} WHERE id=?", (record_id,)) is not None
