import os
import tempfile
import pytest
from novelizer.config import Settings
from novelizer.runtime import Runtime
from novelizer.tui.app import NovelizerApp
from novelizer.agents.base import ChapterDraft


class FakeRunner:
    def __init__(self, draft):
        self._draft = draft

    async def ainvoke(self, inputs):
        return {"structured_response": self._draft}


@pytest.mark.asyncio
async def test_feed_renders_authored_chapter():
    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    settings = Settings(db_path=path, author_interval=1, projector_interval=0.1)
    rt = Runtime(settings, runner=FakeRunner(ChapterDraft(title="Live Chapter", prose="It appears.")))
    await rt.start()
    app = NovelizerApp(rt)
    try:
        async with app.run_test() as pilot:
            await pilot.pause(0.6)  # let projector + author + feed workers cycle
            await pilot.pause(0.6)
            assert "Live Chapter" in "\n".join(app.messages)
    finally:
        await rt.close()
        os.unlink(path)
