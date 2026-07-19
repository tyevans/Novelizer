import os
import tempfile
import pytest
from novelizer.canon.event_store import EventStore
from novelizer.canon.projector import Projector
from novelizer.canon.read_store import ReadStore
from novelizer.canon.events import EventType, ThemeIntroduced
from novelizer.store.models import Chapter, Character, WorldEntry, RetconRequest, RetconStatus
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
    secs = await browser_sections(read)
    assert [s["key"] for s in secs] == ["chapters", "characters", "world", "retcons", "themes"]
    assert secs[0]["items"][0]["label"].startswith("One")
    assert "Mira" in secs[1]["items"][0]["label"]
    assert "Brinemarsh" in secs[2]["items"][0]["label"]
    assert "scar mismatch" in secs[3]["items"][0]["label"]


async def test_browser_sections_includes_themes(stack):
    events, proj, read = stack
    await events.append(EventType.THEME_INTRODUCED, "loss", ThemeIntroduced(id="loss", title="Loss of Innocence"))
    await proj.catch_up()
    secs = await browser_sections(read)
    themes_section = [s for s in secs if s["key"] == "themes"][0]
    assert themes_section["label"] == "Themes (1)"
    assert themes_section["items"][0]["id"] == "loss"
    assert "Loss of Innocence" in themes_section["items"][0]["label"]


async def test_detail_text_renders_theme(stack):
    events, proj, read = stack
    await events.append(EventType.THEME_INTRODUCED, "loss", ThemeIntroduced(id="loss", title="Loss of Innocence"))
    await proj.catch_up()
    d = await detail_text(read, "themes", "loss")
    assert "Loss of Innocence" in d


async def test_detail_text_for_chapter_and_character(stack):
    events, proj, read = stack
    await events.append(EventType.CHAPTER_CREATED, "c1", Chapter(id="c1", title="One", prose="It began in salt."))
    await events.append(EventType.CHARACTER_CREATED, "ch1", Character(id="ch1", name="Mira", traits="stoic", arc_status="wary"))
    await events.append(EventType.WORLD_ENTRY_CREATED, "w1", WorldEntry(id="w1", title="Brinemarsh", body="salt"))
    await events.append(EventType.RETCON_REQUEST_CREATED, "r1", RetconRequest(id="r1", description="scar mismatch", conflicting_entry_ids=[], proposed_resolution="left hand"))
    await proj.catch_up()
    assert "It began in salt." in await detail_text(read, "chapters", "c1")
    d = await detail_text(read, "characters", "ch1")
    assert "Mira" in d and "wary" in d
    assert await detail_text(read, "chapters", "nope") == ""
    assert await detail_text(read, "characters", "nope") == ""
    assert await detail_text(read, "world", "nope") == ""
    assert await detail_text(read, "retcons", "nope") == ""


async def test_retcon_section_only_open(stack):
    events, proj, read = stack
    # Create one open retcon
    r1 = RetconRequest(id="r1", description="open change", conflicting_entry_ids=[], proposed_resolution="fix it")
    await events.append(EventType.RETCON_REQUEST_CREATED, "r1", r1)
    # Create one resolved retcon (append created then resolved)
    r2 = RetconRequest(id="r2", description="resolved change", conflicting_entry_ids=[], proposed_resolution="fix it")
    await events.append(EventType.RETCON_REQUEST_CREATED, "r2", r2)
    r2_resolved = r2.model_copy(update={"status": RetconStatus.resolved})
    await events.append(EventType.RETCON_REQUEST_RESOLVED, "r2", r2_resolved)
    await proj.catch_up()
    secs = await browser_sections(read)
    retcons_section = [s for s in secs if s["key"] == "retcons"][0]
    assert len(retcons_section["items"]) == 1
    assert retcons_section["items"][0]["id"] == "r1"


async def test_detail_text_for_character_includes_voice_card_when_present(stack):
    events, proj, read = stack
    await events.append(
        EventType.CHARACTER_CREATED, "ch1",
        Character(id="ch1", name="Mira", traits="stoic", arc_status="wary",
                  voice="Speaks in short, clipped sentences."),
    )
    await proj.catch_up()
    d = await detail_text(read, "characters", "ch1")
    assert "Voice: Speaks in short, clipped sentences." in d


async def test_detail_text_for_character_omits_voice_line_when_absent(stack):
    events, proj, read = stack
    await events.append(EventType.CHARACTER_CREATED, "ch1", Character(id="ch1", name="Mira", traits="stoic"))
    await proj.catch_up()
    d = await detail_text(read, "characters", "ch1")
    assert "Voice:" not in d
