import os
import tempfile
import pytest
from novelizer.settings import EffectiveSettings as Settings
from novelizer.runtime import Runtime
from novelizer.tui.app import NovelizerApp
from novelizer.agents.schemas import (
    WorldEntriesDraft, WorldEntryDraft, KeeperOutput, EditorVerdict,
    ContinuityOutput, RetconAmendments, StructureAnalystOutput, SummarizerOutput,
)
from novelizer.agents.base import ChapterDraft
from tests.tui.conftest import stub_runners

_AGENTS = ["world_architect", "character_keeper", "author", "editor",
           "continuity_checker", "retconner", "structure_analyst", "summarizer"]


class _R:
    def __init__(self, out): self._out = out
    async def ainvoke(self, inputs): return {"structured_response": self._out}


def _runners():
    return {
        "world_architect": _R(WorldEntriesDraft(entries=[WorldEntryDraft(title="Brinemarsh", body="salt")])),
        "author": _R(ChapterDraft(title="Chapter One", prose="It began.")),
        "character_keeper": _R(KeeperOutput()),
        "editor": _R(EditorVerdict(verdict="approve", notes="ok")),
        "continuity_checker": _R(ContinuityOutput()),
        "retconner": _R(RetconAmendments()),
        "structure_analyst": _R(StructureAnalystOutput()),
        "summarizer": _R(SummarizerOutput(gist="g", summary="s")),
    }


async def _quiet_runtime(path):
    settings = Settings(db_path=path, projector_interval=0.1,
                        author_interval=100, default_agent_interval=100, continuity_interval=100)
    rt = Runtime(settings, runners=stub_runners(**_runners()))
    await rt.start()
    for name in _AGENTS:
        rt.scheduler.pause_agent(name)
    return rt


@pytest.mark.asyncio
async def test_empty_log_shows_director_welcome_block():
    fd, path = tempfile.mkstemp(suffix=".db"); os.close(fd)
    rt = await _quiet_runtime(path)
    app = NovelizerApp(rt)
    try:
        async with app.run_test() as pilot:
            await pilot.pause(0.5)
            joined = "\n".join(app.messages)
            assert "★ The room is assembled: Author, Editor, Architect, Keeper, Continuity, Retconner, Analyst." in joined
            assert ":seed a lighthouse keeper who taxes the tide" in joined
            assert all(isinstance(m, str) for m in app.messages)
    finally:
        await rt.close(); os.unlink(path)


@pytest.mark.asyncio
async def test_chapter_created_writes_numbered_rule_lines_and_no_welcome():
    from novelizer.canon.events import EventType
    from novelizer.store.models import Chapter

    fd, path = tempfile.mkstemp(suffix=".db"); os.close(fd)
    rt = await _quiet_runtime(path)
    await rt.events.append(EventType.CHAPTER_CREATED, "c1", Chapter(id="c1", title="One", prose="p"))
    await rt.events.append(EventType.CHAPTER_CREATED, "c2", Chapter(id="c2", title="Two", prose="p"))
    app = NovelizerApp(rt)
    try:
        async with app.run_test() as pilot:
            await pilot.pause(0.6)
            joined = "\n".join(app.messages)
            assert "── ch 1 · One ──" in joined
            assert "── ch 2 · Two ──" in joined
            assert 'drafted "Two"' in joined       # the event line itself still renders
            assert "★ The room is assembled" not in joined  # log wasn't empty
            assert all(isinstance(m, str) for m in app.messages)
    finally:
        await rt.close(); os.unlink(path)
