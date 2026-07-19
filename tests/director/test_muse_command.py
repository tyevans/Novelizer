import os
import tempfile
import pytest
from novelizer.director.commands import dispatch
from novelizer.runtime import Runtime
from novelizer.settings import EffectiveSettings
from novelizer.store.models import HandStatus


@pytest.fixture
async def runtime():
    tmp = tempfile.mkdtemp()
    settings = EffectiveSettings(db_path=os.path.join(tmp, "world.db"),
                                 chroma_path=os.path.join(tmp, "chroma"))
    rt = Runtime(settings, runners={})
    await rt.start()
    yield rt
    await rt.close()


async def test_muse_status_before_any_hand(runtime):
    out = await dispatch(runtime, ":muse")
    assert "No active hand" in out


async def test_muse_reroll_supersedes_and_redeals(runtime):
    first = await runtime.muse.deal_fresh_hand()
    await runtime.projector.catch_up()
    out = await dispatch(runtime, ":muse reroll")
    await runtime.projector.catch_up()
    assert "Rerolled" in out
    assert (await runtime.read.get_hand(first.hand_id)).status == HandStatus.superseded
    active = await runtime.read.get_active_hand()
    assert active is not None and active.id != first.hand_id


async def test_muse_status_shows_active_hand(runtime):
    hand = await runtime.muse.deal_fresh_hand()
    await runtime.projector.catch_up()
    out = await dispatch(runtime, ":muse")
    assert hand.names[0] in out
