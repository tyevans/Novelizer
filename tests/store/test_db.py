import pytest
import tempfile
import os
from novelizer.store.db import WorldDB
from novelizer.store.models import (
    WorldEntry, Character, Event, Chapter,
    RetconRequest, DirectorSignal, SignalKind,
    CanonStatus, EditorialStatus, RetconStatus,
)


@pytest.fixture
async def db():
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
        path = f.name
    d = WorldDB(path)
    await d.init()
    yield d
    await d.close()
    os.unlink(path)


async def test_world_entry_roundtrip(db):
    entry = WorldEntry(title="The Ashfields", body="A blasted plain.")
    await db.save_world_entry(entry)
    results = await db.list_world_entries()
    assert len(results) == 1
    assert results[0].title == "The Ashfields"


async def test_character_roundtrip(db):
    char = Character(name="Maren", traits="Brave, reckless")
    await db.save_character(char)
    results = await db.list_characters()
    assert len(results) == 1
    assert results[0].name == "Maren"


async def test_chapter_roundtrip(db):
    ch = Chapter(title="Ch 1", prose="She ran.")
    await db.save_chapter(ch)
    results = await db.list_chapters()
    assert len(results) == 1
    assert results[0].prose == "She ran."


async def test_retcon_request_roundtrip(db):
    req = RetconRequest(
        description="Conflict",
        conflicting_entry_ids=["x", "y"],
        proposed_resolution="Remove x.",
    )
    await db.save_retcon_request(req)
    results = await db.list_retcon_requests(status=RetconStatus.open)
    assert len(results) == 1


async def test_director_signal_consume(db):
    sig = DirectorSignal(kind=SignalKind.seed, body="Empire falls.")
    await db.save_director_signal(sig)
    pending = await db.list_unconsumed_signals()
    assert len(pending) == 1
    await db.mark_signal_consumed(sig.id)
    pending = await db.list_unconsumed_signals()
    assert len(pending) == 0


async def test_superseded_entries_excluded(db):
    old = WorldEntry(title="Old North", body="version 1")
    await db.save_world_entry(old)
    new = WorldEntry(title="New North", body="version 2", supersedes_id=old.id)
    await db.save_world_entry(new)
    await db.mark_superseded(old.id)
    results = await db.list_world_entries()
    assert len(results) == 1
    assert results[0].title == "New North"
