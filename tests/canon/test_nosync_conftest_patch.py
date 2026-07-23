"""Regression tests for the tests/conftest.py synchronous=OFF monkeypatch.

conftest.py replaces novelizer.canon.db.connect so test databases skip fsync
(the measured 170s -> 32s suite win). The patch only holds while every store
resolves `db.connect` as a module attribute at call time; a refactor to
`from novelizer.canon.db import connect`, or a rename of db.connect, would
silently bypass it and regress the suite to fsync-bound with zero failing
tests. These tests pin the patch by opening a connection through each
production store path and asserting PRAGMA synchronous is actually 0 (OFF).
"""
import os
import tempfile

import pytest

from novelizer.canon.event_store import EventStore
from novelizer.canon.projector import Projector
from novelizer.canon.read_store import ReadStore
from novelizer.store.kg_store import KGStore


@pytest.fixture
def db_path():
    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    yield path
    os.unlink(path)


async def _synchronous_pragma(conn) -> int:
    cur = await conn.execute("PRAGMA synchronous")
    (value,) = await cur.fetchone()
    return value


async def test_event_store_connection_has_synchronous_off(db_path):
    store = EventStore(db_path)
    await store.init()
    try:
        assert await _synchronous_pragma(store._conn) == 0
    finally:
        await store.close()


async def test_read_store_connection_has_synchronous_off(db_path):
    store = ReadStore(db_path)
    await store.init()
    try:
        assert await _synchronous_pragma(store._conn) == 0
    finally:
        await store.close()


async def test_projector_connection_has_synchronous_off(db_path):
    events = EventStore(db_path)
    await events.init()
    projector = Projector(events, db_path)
    await projector.init()
    try:
        assert await _synchronous_pragma(projector._conn) == 0
    finally:
        await projector.close()
        await events.close()


async def test_kg_store_connection_has_synchronous_off(db_path):
    store = KGStore(db_path)
    await store.init()
    try:
        assert await _synchronous_pragma(store._conn) == 0
    finally:
        await store.close()
