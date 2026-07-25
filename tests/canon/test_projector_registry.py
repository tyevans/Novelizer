"""The Projector dispatches through an open registry, not a closed if/elif chain.

These are structural tests: they pin the Open/Closed seam that lets a new event
type be projected by *registering* a handler rather than by editing one 600-line
method, and they pin the schema as the single source of truth for which tables
exist (so a table added to the schema can never be forgotten by the rebuild
path).
"""
from __future__ import annotations
import os
import tempfile
import pytest
from novelizer.canon import projections
from novelizer.canon.event_store import EventStore
from novelizer.canon.events import EventType
from novelizer.canon.projector import Projector, PROJECTION_TABLES, projects


@pytest.fixture
async def wired():
    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    events = EventStore(path)
    await events.init()
    proj = Projector(events, path)
    await proj.init()
    yield events, proj, path
    await proj.close()
    await events.close()
    os.unlink(path)


def test_every_projected_event_type_has_a_registered_handler():
    """The registry is the dispatch table -- no arm hides in a conditional."""
    assert projections.HANDLERS, "no projection handlers registered"
    # EventType is a namespace of string constants, not an Enum, so a key is
    # valid iff it is one of those declared constant values.
    known = {
        v for k, v in vars(EventType).items()
        if not k.startswith("_") and isinstance(v, str)
    }
    for event_type, handler in projections.HANDLERS.items():
        assert isinstance(event_type, str)
        assert event_type in known, f"{event_type} is not declared on EventType"
        assert callable(handler)


def test_reset_clears_every_table_declared_in_the_schema():
    """PROJECTION_TABLES is derived from the schema, not hand-maintained.

    A hand-written list silently skips any table added to the schema later,
    leaving stale rows behind on rebuild.
    """
    from novelizer.canon.projector import _CREATE

    declared = {
        line.split("CREATE TABLE IF NOT EXISTS ")[1].split("(")[0].strip()
        for line in _CREATE.splitlines()
        if "CREATE TABLE IF NOT EXISTS" in line
    }
    # projector_state holds the replay position and is reset separately.
    assert declared - {"projector_state"} == set(PROJECTION_TABLES)


async def test_reset_leaves_no_rows_in_any_projection_table(wired):
    events, proj, _ = wired
    for table in PROJECTION_TABLES:
        cols = [
            r[1] for r in await (await proj._conn.execute(f"PRAGMA table_info({table})")).fetchall()
        ]
        placeholders = ",".join("?" for _ in cols)
        await proj._conn.execute(
            f"INSERT INTO {table} ({','.join(cols)}) VALUES ({placeholders})",
            tuple("x" for _ in cols),
        )
    await proj._conn.commit()
    await proj._reset_state()
    for table in PROJECTION_TABLES:
        cur = await proj._conn.execute(f"SELECT COUNT(*) FROM {table}")
        assert (await cur.fetchone())[0] == 0, f"{table} still has rows after reset"


async def test_a_new_event_type_projects_without_editing_the_projector(wired):
    """OCP: extension happens by registration, in a caller's own module."""
    events, proj, _ = wired
    seen: list[str] = []

    sentinel = EventType.AUTONOMY_CHANGED  # any type; we re-register temporarily
    original = projections.HANDLERS.pop(sentinel, None)
    try:

        @projects(sentinel)
        async def _handler(ctx) -> None:  # pragma: no cover - asserted via `seen`
            seen.append(ctx.payload["marker"])

        await events.append_raw(sentinel, "agg", {"marker": "hello"})
        await proj.catch_up()
        assert seen == ["hello"]
    finally:
        if original is None:
            projections.HANDLERS.pop(sentinel, None)
        else:
            projections.HANDLERS[sentinel] = original


def test_registering_a_second_handler_for_one_event_type_is_refused():
    """Two handlers for one type means one silently never runs.

    A dict would happily let the second registration win; the registry raises
    instead, so an accidental double-registration fails at import time rather
    than dropping projections at runtime.
    """
    with pytest.raises(ValueError, match="already projected by"):

        @projects(EventType.CHAPTER_CREATED)
        async def _shadow(ctx) -> None:  # pragma: no cover - never registered
            raise AssertionError("must not be registered")


async def test_unknown_event_type_is_a_no_op(wired):
    """An event with no registered handler advances the position, silently."""
    events, proj, _ = wired
    await events.append_raw(EventType.CHAPTER_CREATED, "c1", {"id": "c1", "title": "t", "prose": "p"})
    before = await proj.catch_up()
    original = projections.HANDLERS.pop(EventType.CHAPTER_CREATED)
    try:
        await events.append_raw(EventType.CHAPTER_CREATED, "c2", {"id": "c2"})
        after = await proj.catch_up()
        assert after > before  # position advanced, no exception
    finally:
        projections.HANDLERS[EventType.CHAPTER_CREATED] = original
