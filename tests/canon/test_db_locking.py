"""Lock hardening for world.db: explicit busy_timeout, IMMEDIATE per-event
transactions in the Projector, and retry-on-locked as a safety net.

Two connections (EventStore, Projector) plus any headless CLI process write to
the same file; WAL allows only one writer at a time, and the stdlib's implicit
5s busy handler turns any longer collision into an unhandled OperationalError.
"""
import os
import sqlite3
import tempfile

import pytest

from novelizer.canon import db
from novelizer.canon.event_store import EventStore
from novelizer.canon.events import EventType
from novelizer.canon.projector import Projector
from novelizer.canon.read_store import ReadStore
from novelizer.store.models import Chapter


@pytest.fixture
def db_path():
    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    yield path
    for suffix in ("", "-wal", "-shm"):
        try:
            os.unlink(path + suffix)
        except FileNotFoundError:
            pass


async def _busy_timeout_ms(conn) -> int:
    cur = await conn.execute("PRAGMA busy_timeout")
    return (await cur.fetchone())[0]


async def test_event_store_sets_explicit_busy_timeout(db_path):
    store = EventStore(db_path)
    await store.init()
    try:
        assert await _busy_timeout_ms(store._conn) == db.BUSY_TIMEOUT_MS
    finally:
        await store.close()


async def test_projector_sets_explicit_busy_timeout(db_path):
    events = EventStore(db_path)
    await events.init()
    projector = Projector(events, db_path)
    await projector.init()
    try:
        assert await _busy_timeout_ms(projector._conn) == db.BUSY_TIMEOUT_MS
    finally:
        await projector.close()
        await events.close()


async def test_read_store_sets_explicit_busy_timeout(db_path):
    store = ReadStore(db_path)
    await store.init()
    try:
        assert await _busy_timeout_ms(store._conn) == db.BUSY_TIMEOUT_MS
    finally:
        await store.close()


async def test_projector_applies_each_event_in_immediate_transaction(db_path):
    """Each event's statements (reads included) run inside one BEGIN IMMEDIATE
    transaction: the write lock is taken up front under busy_timeout instead of
    mid-application, and the application is atomic."""
    events = EventStore(db_path)
    await events.init()
    projector = Projector(events, db_path)
    await projector.init()
    try:
        await events.append(EventType.CHAPTER_CREATED, "c1", Chapter(title="A", prose="a"))
        statements = []
        real_execute = projector._conn.execute

        async def recording_execute(sql, *args, **kwargs):
            statements.append(sql.strip().upper())
            return await real_execute(sql, *args, **kwargs)

        projector._conn.execute = recording_execute
        await projector.catch_up()

        chapter_write = next(
            i for i, s in enumerate(statements) if s.startswith("INSERT OR REPLACE INTO CHAPTERS")
        )
        assert any(
            s.startswith("BEGIN IMMEDIATE") for s in statements[:chapter_write]
        ), f"no BEGIN IMMEDIATE before the projection write; statements: {statements}"
    finally:
        await projector.close()
        await events.close()


async def test_retry_locked_retries_until_success():
    attempts = 0

    async def flaky():
        nonlocal attempts
        attempts += 1
        if attempts < 3:
            raise sqlite3.OperationalError("database is locked")
        return "ok"

    assert await db.retry_locked(flaky, base_delay_s=0.001) == "ok"
    assert attempts == 3


async def test_retry_locked_gives_up_after_max_attempts():
    attempts = 0

    async def always_locked():
        nonlocal attempts
        attempts += 1
        raise sqlite3.OperationalError("database is locked")

    with pytest.raises(sqlite3.OperationalError):
        await db.retry_locked(always_locked, attempts=3, base_delay_s=0.001)
    assert attempts == 3


async def test_retry_locked_propagates_other_operational_errors():
    attempts = 0

    async def broken():
        nonlocal attempts
        attempts += 1
        raise sqlite3.OperationalError("no such table: nope")

    with pytest.raises(sqlite3.OperationalError, match="no such table"):
        await db.retry_locked(broken, base_delay_s=0.001)
    assert attempts == 1


async def test_append_survives_transient_lock_error(db_path):
    """A commit that fails with 'database is locked' (busy wait exceeded) is
    rolled back and retried instead of crashing the caller."""
    store = EventStore(db_path)
    await store.init()
    try:
        real_commit = store._conn.commit
        failures = iter([True])

        async def flaky_commit():
            if next(failures, False):
                raise sqlite3.OperationalError("database is locked")
            await real_commit()

        store._conn.commit = flaky_commit
        stored = await store.append(EventType.CHAPTER_CREATED, "c1", Chapter(title="A", prose="a"))

        store._conn.commit = real_commit
        cur = await store._conn.execute("SELECT COUNT(*) FROM events")
        assert (await cur.fetchone())[0] == 1
        assert stored.payload["title"] == "A"
    finally:
        await store.close()


async def test_concurrent_catch_up_calls_are_serialized(db_path):
    """catch_up runs from both the projector loop and command paths; concurrent
    calls must not interleave transactions on the shared connection or
    double-apply non-idempotent projections (causal_edges is a plain INSERT)."""
    import asyncio

    events = EventStore(db_path)
    await events.init()
    projector = Projector(events, db_path)
    await projector.init()
    try:
        for i in range(5):
            await events.append_raw(
                EventType.CAUSAL_EDGE_DECLARED, f"e{i}",
                {"cause_chapter_id": f"c{i}", "effect_chapter_id": f"c{i + 1}", "note": ""},
            )
        await asyncio.gather(*(projector.catch_up() for _ in range(4)))
        cur = await projector._conn.execute("SELECT COUNT(*) FROM causal_edges")
        assert (await cur.fetchone())[0] == 5
    finally:
        await projector.close()
        await events.close()


async def test_catch_up_survives_transient_lock_error(db_path):
    events = EventStore(db_path)
    await events.init()
    projector = Projector(events, db_path)
    await projector.init()
    try:
        await events.append(EventType.CHAPTER_CREATED, "c1", Chapter(title="A", prose="a"))
        real_commit = projector._conn.commit
        failures = iter([True])

        async def flaky_commit():
            if next(failures, False):
                raise sqlite3.OperationalError("database is locked")
            await real_commit()

        projector._conn.commit = flaky_commit
        await projector.catch_up()

        projector._conn.commit = real_commit
        cur = await projector._conn.execute("SELECT COUNT(*) FROM chapters")
        assert (await cur.fetchone())[0] == 1
    finally:
        await projector.close()
        await events.close()
