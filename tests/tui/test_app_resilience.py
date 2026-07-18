import os
import tempfile
import pytest
from novelizer.config import Settings
from novelizer.runtime import Runtime
from novelizer.tui.app import NovelizerApp
from novelizer.canon.events import EventType
from novelizer.store.models import DirectorSignal, SignalKind


class BoomRunner:
    async def ainvoke(self, inputs):
        raise RuntimeError("boom")


@pytest.mark.asyncio
async def test_author_loop_survives_exception_and_feed_keeps_working():
    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    settings = Settings(db_path=path, author_interval=1, projector_interval=0.1)
    rt = Runtime(settings, runner=BoomRunner())
    await rt.start()
    app = NovelizerApp(rt)
    try:
        async with app.run_test() as pilot:
            await pilot.pause(1.3)  # let author loop hit the boom at least once

            # 1. error was surfaced, not swallowed
            assert any("boom" in m and "author" in m for m in app.messages)

            # 2. worker loops are still alive: a subsequently-appended event
            # still flows through the projector/feed loops.
            await rt.events.append(
                EventType.DIRECTOR_SIGNAL_CREATED,
                "s1",
                DirectorSignal(id="s1", kind=SignalKind.seed, body="a storm is coming"),
            )
            await pilot.pause(0.6)
            assert any("a storm is coming" in m for m in app.messages)

            # author loop itself is still iterating (not just projector/feed):
            # further boom errors keep appearing, proving run_once keeps being called.
            boom_count_before = sum(1 for m in app.messages if "boom" in m)
            await pilot.pause(1.3)
            boom_count_after = sum(1 for m in app.messages if "boom" in m)
            assert boom_count_after > boom_count_before
    finally:
        await rt.close()
        os.unlink(path)
