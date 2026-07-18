import os
import tempfile
import pytest
from novelizer.settings import EffectiveSettings as Settings
from novelizer.runtime import Runtime
from novelizer.tui.app import NovelizerApp
from novelizer.agents.schemas import (
    WorldEntriesDraft, WorldEntryDraft, KeeperOutput, EditorVerdict,
    ContinuityOutput, RetconAmendments, StructureAnalystOutput,
)
from novelizer.agents.base import ChapterDraft


class _R:
    def __init__(self, out):
        self._out = out

    async def ainvoke(self, inputs):
        return {"structured_response": self._out}


def _room_runners():
    return {
        "world_architect": _R(WorldEntriesDraft(entries=[WorldEntryDraft(title="Brinemarsh", body="salt")])),
        "author": _R(ChapterDraft(title="Live Chapter", prose="It appears.")),
        "character_keeper": _R(KeeperOutput()),
        "editor": _R(EditorVerdict(verdict="approve", notes="ok")),
        "continuity_checker": _R(ContinuityOutput()),
        "retconner": _R(RetconAmendments()),
        "structure_analyst": _R(StructureAnalystOutput()),
    }


@pytest.mark.asyncio
async def test_feed_renders_authored_chapter():
    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    settings = Settings(
        db_path=path,
        author_interval=1,
        default_agent_interval=1,
        continuity_interval=1,
        projector_interval=0.1,
    )
    rt = Runtime(settings, runners=_room_runners())
    await rt.start()
    app = NovelizerApp(rt)
    try:
        async with app.run_test() as pilot:
            await pilot.pause(0.6)  # let projector + scheduler + feed workers cycle
            await pilot.pause(0.6)
            await pilot.pause(0.6)
            assert "Live Chapter" in "\n".join(app.messages)
    finally:
        await rt.close()
        os.unlink(path)
