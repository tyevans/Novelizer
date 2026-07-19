import os
import tempfile
import pytest
from novelizer.canon.event_store import EventStore
from novelizer.canon.projector import Projector
from novelizer.canon.read_store import ReadStore
from novelizer.canon.events import (
    EventType, InspirationDrawn, InspirationHandConsumed, InspirationHandSuperseded,
    InspirationUptakeRecorded,
)
from novelizer.store.models import HandStatus


@pytest.fixture
async def stack():
    fd, path = tempfile.mkstemp(suffix=".db"); os.close(fd)
    events = EventStore(path); await events.init()
    proj = Projector(events, path); await proj.init()
    read = ReadStore(path); await read.init()
    yield events, proj, read
    await read.close(); await proj.close(); await events.close(); os.unlink(path)


def _drawn(hand_id="h1", seed=42):
    return InspirationDrawn(
        hand_id=hand_id, seed=seed, corpus_version="2026.07", era="modern",
        names=["Doris Kimbrough", "Mateo Rafferty"], professions=["glazier"],
        settings=["salvage yard"], beats=["a debt is called in early"],
    )


async def test_drawn_projects_active_hand(stack):
    events, proj, read = stack
    await events.append(EventType.INSPIRATION_DRAWN, "h1", _drawn())
    await proj.catch_up()
    hand = await read.get_active_hand()
    assert hand is not None and hand.id == "h1"
    assert hand.status == HandStatus.active
    assert hand.seed == 42 and hand.names[0] == "Doris Kimbrough"


async def test_consumed_sets_status_and_chapter(stack):
    events, proj, read = stack
    await events.append(EventType.INSPIRATION_DRAWN, "h1", _drawn())
    await events.append(EventType.INSPIRATION_HAND_CONSUMED, "h1",
                        InspirationHandConsumed(hand_id="h1", chapter_id="c9"))
    await proj.catch_up()
    assert await read.get_active_hand() is None
    hand = await read.get_hand("h1")
    assert hand.status == HandStatus.consumed and hand.consumed_chapter_id == "c9"


async def test_superseded_only_flips_active_hands(stack):
    events, proj, read = stack
    await events.append(EventType.INSPIRATION_DRAWN, "h1", _drawn())
    await events.append(EventType.INSPIRATION_HAND_CONSUMED, "h1",
                        InspirationHandConsumed(hand_id="h1", chapter_id="c1"))
    await events.append(EventType.INSPIRATION_HAND_SUPERSEDED, "h1", InspirationHandSuperseded(hand_id="h1"))
    await proj.catch_up()
    # consumed is absorbing: a late supersede never rewrites history
    assert (await read.get_hand("h1")).status == HandStatus.consumed


async def test_second_drawn_for_same_id_is_noop(stack):
    events, proj, read = stack
    await events.append(EventType.INSPIRATION_DRAWN, "h1", _drawn(seed=1))
    await events.append(EventType.INSPIRATION_DRAWN, "h1", _drawn(seed=999))
    await proj.catch_up()
    assert (await read.get_hand("h1")).seed == 1  # first-mint-wins


async def test_uptake_rows_dedupe_on_replay_key(stack):
    events, proj, read = stack
    await events.append(EventType.INSPIRATION_DRAWN, "h1", _drawn())
    up = InspirationUptakeRecorded(hand_id="h1", kind="names", item="Doris Kimbrough", chapter_id="c1")
    await events.append(EventType.INSPIRATION_UPTAKE_RECORDED, "h1", up)
    await events.append(EventType.INSPIRATION_UPTAKE_RECORDED, "h1", up)
    await proj.catch_up()
    rows = await read.list_uptake("h1")
    assert len(rows) == 1 and rows[0].item == "Doris Kimbrough"


async def test_replay_reproduces_identical_rows(stack):
    events, proj, read = stack
    await events.append(EventType.INSPIRATION_DRAWN, "h1", _drawn())
    await events.append(EventType.INSPIRATION_HAND_CONSUMED, "h1",
                        InspirationHandConsumed(hand_id="h1", chapter_id="c1"))
    await events.append(EventType.INSPIRATION_UPTAKE_RECORDED, "h1",
                        InspirationUptakeRecorded(hand_id="h1", kind="beats",
                                                  item="a debt is called in early", chapter_id="c1"))
    await proj.catch_up()
    before = (await read.list_hands(), await read.list_uptake())
    await proj._reset_state()
    await proj.catch_up()
    after = (await read.list_hands(), await read.list_uptake())
    assert before == after
