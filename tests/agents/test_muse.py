import os
import tempfile
import pytest
from novelizer.canon.event_store import EventStore
from novelizer.canon.projector import Projector
from novelizer.canon.read_store import ReadStore
from novelizer.canon.committer import Committer
from novelizer.canon.events import EventType, InspirationHandConsumed
from novelizer.agents.muse import Muse, _exclusion_window


@pytest.fixture
async def stack():
    fd, path = tempfile.mkstemp(suffix=".db"); os.close(fd)
    events = EventStore(path); await events.init()
    proj = Projector(events, path); await proj.init()
    read = ReadStore(path); await read.init()
    yield events, proj, read, Committer(events)
    await read.close(); await proj.close(); await events.close(); os.unlink(path)


async def test_run_once_deals_a_hand_when_none_active(stack):
    events, proj, read, committer = stack
    muse = Muse(read, committer)
    await muse.run_once()
    await proj.catch_up()
    hand = await read.get_active_hand()
    assert hand is not None
    assert len(hand.names) == 5 and len(hand.beats) == 2
    assert hand.era == "modern"


async def test_run_once_skips_when_hand_already_active(stack):
    events, proj, read, committer = stack
    muse = Muse(read, committer)
    await muse.run_once()
    await proj.catch_up()
    first = await read.get_active_hand()
    await muse.run_once()
    await proj.catch_up()
    assert [h.id for h in await read.list_hands()] == [first.id]


async def test_new_hand_excludes_recent_hand_items(stack):
    events, proj, read, committer = stack
    muse = Muse(read, committer)
    await muse.run_once()
    await proj.catch_up()
    first = await read.get_active_hand()
    await events.append(EventType.INSPIRATION_HAND_CONSUMED, first.id,
                        InspirationHandConsumed(hand_id=first.id, chapter_id="c1"))
    await proj.catch_up()
    await muse.run_once()
    await proj.catch_up()
    second = await read.get_active_hand()
    assert second.id != first.id
    for kind in ("professions", "settings", "beats"):
        assert not (set(getattr(second, kind)) & set(getattr(first, kind)))


async def test_readiness_reflects_hand_presence(stack):
    events, proj, read, committer = stack
    muse = Muse(read, committer)
    assert await muse.readiness() == 0.9
    await muse.run_once()
    await proj.catch_up()
    assert await muse.readiness() == 0.0


async def test_era_setting_is_respected(stack):
    events, proj, read, committer = stack
    muse = Muse(read, committer, era="victorian")
    await muse.run_once()
    await proj.catch_up()
    assert (await read.get_active_hand()).era == "victorian"


class _FakeHand:
    def __init__(self, tag):
        self.names = [f"{tag}-name"]
        self.professions = [f"{tag}-prof"]
        self.settings = [f"{tag}-setting"]
        self.beats = [f"{tag}-beat"]


def test_exclusion_window_zero_excludes_nothing():
    # Regression for the `hands[-0:]` slice-inversion bug: n <= 0 must mean
    # an empty exclusion window, not "exclude everything" (which is what a
    # naive `hands[-self._exclusion_hands:]` slice does when the count is 0).
    hands = [_FakeHand("a"), _FakeHand("b"), _FakeHand("c")]
    assert _exclusion_window(hands, 0) == set()


def test_exclusion_window_positive_n_includes_last_n_hands():
    hands = [_FakeHand("a"), _FakeHand("b"), _FakeHand("c")]
    window = _exclusion_window(hands, 2)
    assert "b-name" in window and "c-name" in window
    assert "a-name" not in window


async def test_zero_exclusion_hands_allows_repeat_after_consuming(stack):
    events, proj, read, committer = stack
    muse = Muse(read, committer, exclusion_hands=0)
    first = await muse.deal_fresh_hand()
    await proj.catch_up()
    await events.append(EventType.INSPIRATION_HAND_CONSUMED, first.hand_id,
                        InspirationHandConsumed(hand_id=first.hand_id, chapter_id="c1"))
    await proj.catch_up()
    second = await muse.deal_fresh_hand()
    await proj.catch_up()
    assert second is not None
    assert len(second.names) == 5
