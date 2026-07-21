import os
import tempfile
import pytest
from novelizer.canon.event_store import EventStore
from novelizer.canon.projector import Projector
from novelizer.canon.read_store import ReadStore
from novelizer.canon.events import EventType, ThemeIntroduced, ThreadPlanted, ThreadTouched
from novelizer.store.models import (
    Chapter, Character, EditorialStatus, Flag, FlagStatus, WorldEntry,
)
from novelizer.tui.widgets.browser_model import (
    STATUS_DOTS,
    browser_sections,
    detail_view,
    word_count,
)

THRESHOLD = 3  # tests pin the explicit keyword; production passes settings


@pytest.fixture
async def stack():
    fd, path = tempfile.mkstemp(suffix=".db"); os.close(fd)
    events = EventStore(path); await events.init()
    proj = Projector(events, path); await proj.init()
    read = ReadStore(path); await read.init()
    yield events, proj, read
    await read.close(); await proj.close(); await events.close(); os.unlink(path)


async def test_sections_cover_all_categories_including_threads(stack):
    events, proj, read = stack
    await events.append(EventType.CHAPTER_CREATED, "c1", Chapter(id="c1", title="One", prose="It began."))
    await events.append(EventType.CHARACTER_CREATED, "ch1", Character(id="ch1", name="Mira", traits="stoic"))
    await events.append(EventType.WORLD_ENTRY_CREATED, "w1", WorldEntry(id="w1", title="Brinemarsh", body="salt"))
    await events.append(EventType.FLAG_CREATED, "r1", Flag(id="r1", category="contradiction", description="scar mismatch", related_entry_ids=[], proposed_resolution="left hand"))
    await events.append(EventType.THREAD_PLANTED, "the-locket", ThreadPlanted(id="the-locket", name="The Locket"))
    await proj.catch_up()
    secs = await browser_sections(read, staleness_threshold=THRESHOLD)
    assert [s["key"] for s in secs] == ["chapters", "characters", "world", "flags", "threads", "themes"]
    assert "Mira" in secs[1]["items"][0]["label"]
    assert "Brinemarsh" in secs[2]["items"][0]["label"]
    assert "scar mismatch" in secs[3]["items"][0]["label"]
    assert "The Locket" in secs[4]["items"][0]["label"]
    assert "the-locket" not in secs[4]["items"][0]["label"]   # no slugs anywhere


def test_status_dots_cover_the_real_editorial_statuses():
    # Real enum values are draft/reviewed/final — the spec sketch's
    # approved/draft/revising names map by pipeline position.
    assert STATUS_DOTS == {
        EditorialStatus.draft: "◌",
        EditorialStatus.reviewed: "◐",
        EditorialStatus.final: "●",
    }


async def test_chapter_rows_show_status_dot_not_enum_text(stack):
    events, proj, read = stack
    await events.append(EventType.CHAPTER_CREATED, "c1",
                        Chapter(id="c1", title="One", prose="p"))
    await events.append(EventType.CHAPTER_CREATED, "c2",
                        Chapter(id="c2", title="Two", prose="p", editorial_status=EditorialStatus.final))
    await proj.catch_up()
    secs = await browser_sections(read, staleness_threshold=THRESHOLD)
    chapter_labels = [i["label"] for i in secs[0]["items"]]
    assert chapter_labels == ["◌ One", "● Two"]
    assert not any("EditorialStatus" in l or "[" in l for l in chapter_labels)


async def test_flags_label_gains_alarm_mark_only_when_stale(stack):
    events, proj, read = stack
    secs = await browser_sections(read, staleness_threshold=THRESHOLD)
    assert [s for s in secs if s["key"] == "flags"][0]["label"] == "Flags (0)"
    await events.append(EventType.FLAG_CREATED, "r1",
                        Flag(id="r1", category="contradiction", description="open change",
                             related_entry_ids=[], proposed_resolution="fix"))
    r2 = Flag(id="r2", category="pacing", description="stale change", related_entry_ids=[], proposed_resolution="fix")
    await events.append(EventType.FLAG_CREATED, "r2", r2)
    await events.append(EventType.FLAG_REJECTED, "r2", r2.model_copy(update={"status": "stale"}))
    await proj.catch_up()
    secs = await browser_sections(read, staleness_threshold=THRESHOLD)
    flags = [s for s in secs if s["key"] == "flags"][0]
    assert flags["label"] == "Flags (1) ⚠"
    assert len(flags["items"]) == 1 and flags["items"][0]["id"] == "r1"


async def test_threads_label_counts_open_and_stale_via_explicit_threshold(stack):
    events, proj, read = stack
    await events.append(EventType.THREAD_PLANTED, "t1", ThreadPlanted(id="t1", name="Stale One"))
    for i in range(3):
        await events.append(EventType.CHAPTER_CREATED, f"c{i}", Chapter(id=f"c{i}", title=f"Ch{i}", prose="p"))
    await events.append(EventType.THREAD_PLANTED, "t2", ThreadPlanted(id="t2", name="Fresh Two"))
    await events.append(EventType.THREAD_TOUCHED, "t2", ThreadTouched(id="t2", chapter_id="c2"))
    await proj.catch_up()
    secs = await browser_sections(read, staleness_threshold=THRESHOLD)
    threads = [s for s in secs if s["key"] == "threads"][0]
    assert threads["label"] == "Threads (2 · 1 stale)"
    labels = [i["label"] for i in threads["items"]]
    assert "⚠ Stale One · stale" in labels
    assert any(l.startswith("· Fresh Two") for l in labels)
    # the SAME data with a looser threshold is quiet — staleness is a
    # parameter fed from settings, never re-typed
    loose = await browser_sections(read, staleness_threshold=99)
    assert [s for s in loose if s["key"] == "threads"][0]["label"] == "Threads (2)"


def test_word_count_is_computed_from_prose():
    assert word_count("") == 0
    assert word_count("It began in salt.") == 4


async def test_detail_view_chapter_typography_title_meta_prose(stack):
    events, proj, read = stack
    prose = "It began in salt.\n\nAnd it ended there."
    await events.append(EventType.CHAPTER_CREATED, "c1",
                        Chapter(id="c1", title="One", prose=prose, editorial_status=EditorialStatus.final))
    await proj.catch_up()
    view = await detail_view(read, "chapters", "c1")
    assert view.title == "One"
    lines = view.body.plain.splitlines()
    assert lines[0] == "One"
    assert lines[1] == "final · 8 words"
    assert "It began in salt." in view.body.plain
    assert "And it ended there." in view.body.plain          # paragraphs preserved
    styles = [(view.body.plain[s.start:s.end], str(s.style)) for s in view.body.spans]
    assert ("One", "bold") in styles
    assert ("final · 8 words", "dim") in styles


async def test_detail_view_character_fields_and_voice(stack):
    events, proj, read = stack
    await events.append(EventType.CHARACTER_CREATED, "ch1",
                        Character(id="ch1", name="Mira", traits="stoic", arc_status="wary",
                                  voice="Speaks in short, clipped sentences.", backstory="Born at sea."))
    await proj.catch_up()
    view = await detail_view(read, "characters", "ch1")
    assert view.title == "Mira"
    d = view.body.plain
    assert "Mira" in d and "Traits: stoic" in d and "Arc: wary" in d
    assert "Voice: Speaks in short, clipped sentences." in d
    assert "Born at sea." in d


async def test_detail_view_character_omits_empty_fields(stack):
    events, proj, read = stack
    await events.append(EventType.CHARACTER_CREATED, "ch1", Character(id="ch1", name="Mira", traits="stoic"))
    await proj.catch_up()
    d = (await detail_view(read, "characters", "ch1")).body.plain
    assert "Voice:" not in d and "Motivations:" not in d and "Arc:" not in d


async def test_detail_view_thread_names_state_and_last_touch_no_ids(stack):
    events, proj, read = stack
    await events.append(EventType.CHAPTER_CREATED, "c1", Chapter(id="c1", title="The Gift", prose="p"))
    await events.append(EventType.THREAD_PLANTED, "t1", ThreadPlanted(id="t1", name="The Locket"))
    await events.append(EventType.THREAD_TOUCHED, "t1",
                        ThreadTouched(id="t1", chapter_id="c1", note="left at the tideline"))
    await proj.catch_up()
    view = await detail_view(read, "threads", "t1")
    assert view.title == "The Locket"
    d = view.body.plain
    assert 'touched' in d and 'last touch: ch 1 "The Gift"' in d
    assert "left at the tideline" in d
    assert "t1" not in d and "c1" not in d


async def test_detail_view_thread_unknown_chapter_shows_dash_not_id(stack):
    events, proj, read = stack
    await events.append(EventType.THREAD_PLANTED, "t1", ThreadPlanted(id="t1", name="The Locket"))
    await proj.catch_up()
    d = (await detail_view(read, "threads", "t1")).body.plain
    assert "last touch: —" in d


async def test_detail_view_theme_world_and_retcon(stack):
    events, proj, read = stack
    await events.append(EventType.THEME_INTRODUCED, "loss", ThemeIntroduced(id="loss", title="Loss of Innocence"))
    await events.append(EventType.WORLD_ENTRY_CREATED, "w1", WorldEntry(id="w1", title="Brinemarsh", body="salt"))
    await events.append(EventType.FLAG_CREATED, "r1",
                        Flag(id="r1", category="contradiction", description="scar mismatch",
                             related_entry_ids=[], proposed_resolution="left hand"))
    await proj.catch_up()
    theme = await detail_view(read, "themes", "loss")
    assert theme.title == "Loss of Innocence" and "touched 0x" in theme.body.plain
    world = await detail_view(read, "world", "w1")
    assert world.title == "Brinemarsh" and "salt" in world.body.plain
    flag = await detail_view(read, "flags", "r1")
    assert flag.title == "scar mismatch" and "Proposed: left hand" in flag.body.plain


async def test_detail_view_not_found_returns_none(stack):
    events, proj, read = stack
    for section in ("chapters", "characters", "world", "flags", "threads", "themes", "nope"):
        assert await detail_view(read, section, "ghost") is None


async def test_detail_view_empty_title_is_distinct_from_not_found(stack):
    # A record that exists but has an empty title must NOT look like not-found.
    events, proj, read = stack
    await events.append(EventType.CHAPTER_CREATED, "c1", Chapter(id="c1", title="", prose="p"))
    await proj.catch_up()
    view = await detail_view(read, "chapters", "c1")
    assert view is not None
    assert view.title == ""
