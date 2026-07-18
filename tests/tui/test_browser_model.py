import os
import tempfile
import pytest
from novelizer.canon.event_store import EventStore
from novelizer.canon.projector import Projector
from novelizer.canon.read_store import ReadStore
from novelizer.canon.events import EventType
from novelizer.store.models import Chapter, Character, WorldEntry, RetconRequest
from novelizer.tui.widgets.browser_model import browser_sections, detail_text


@pytest.fixture
async def stack():
    fd, path = tempfile.mkstemp(suffix=".db"); os.close(fd)
    events = EventStore(path); await events.init()
    proj = Projector(events, path); await proj.init()
    read = ReadStore(path); await read.init()
    yield events, proj, read
    await read.close(); await proj.close(); await events.close(); os.unlink(path)


async def test_sections_cover_all_categories(stack):
    events, proj, read = stack
    await events.append(EventType.CHAPTER_CREATED, "c1", Chapter(id="c1", title="One", prose="It began."))
    await events.append(EventType.CHARACTER_CREATED, "ch1", Character(id="ch1", name="Mira", traits="stoic"))
    await events.append(EventType.WORLD_ENTRY_CREATED, "w1", WorldEntry(id="w1", title="Brinemarsh", body="salt"))
    await events.append(EventType.RETCON_REQUEST_CREATED, "r1", RetconRequest(id="r1", description="scar mismatch", conflicting_entry_ids=[], proposed_resolution="left hand"))
    await proj.catch_up()
    secs = {s["key"]: s for s in await browser_sections(read)}
    assert [s for s in secs] == ["chapters", "characters", "world", "retcons"] or set(secs) == {"chapters","characters","world","retcons"}
    assert secs["chapters"]["items"][0]["label"].startswith("One")
    assert "Mira" in secs["characters"]["items"][0]["label"]
    assert "Brinemarsh" in secs["world"]["items"][0]["label"]
    assert "scar mismatch" in secs["retcons"]["items"][0]["label"]


async def test_detail_text_for_chapter_and_character(stack):
    events, proj, read = stack
    await events.append(EventType.CHAPTER_CREATED, "c1", Chapter(id="c1", title="One", prose="It began in salt."))
    await events.append(EventType.CHARACTER_CREATED, "ch1", Character(id="ch1", name="Mira", traits="stoic", arc_status="wary"))
    await proj.catch_up()
    assert "It began in salt." in await detail_text(read, "chapters", "c1")
    d = await detail_text(read, "characters", "ch1")
    assert "Mira" in d and "wary" in d
    assert await detail_text(read, "chapters", "nope") == ""
