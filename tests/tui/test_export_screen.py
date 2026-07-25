import os
import tempfile
import shutil
from pathlib import Path

import pytest
from novelizer.settings import EffectiveSettings as Settings
from novelizer.runtime import Runtime
from novelizer.store.models import Chapter, EditorialStatus
from novelizer.canon.events import EventType
from novelizer.tui.app import NovelizerApp
from novelizer.tui.export_screen import ExportScreen
from novelizer.agents.schemas import SummarizerOutput
from tests.tui.conftest import stub_runners


class _R:
    async def ainvoke(self, inputs):
        return {"structured_response": SummarizerOutput(gist="g", summary="s")}


async def _app_with_chapters():
    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    settings = Settings(db_path=path, projector_interval=0.1, story_title="The Drowned Bell")
    rt = Runtime(settings, runners=stub_runners(**{"summarizer": _R()}))
    await rt.start()
    ch = Chapter(title="Ch One", prose="Some prose.", editorial_status=EditorialStatus.final)
    await rt.committer.commit("author", EventType.CHAPTER_CREATED, ch.id, ch)
    await rt.projector.catch_up()
    return NovelizerApp(rt), rt, path


@pytest.mark.asyncio
async def test_export_screen_writes_epub_and_reports_path():
    app, rt, db_path = await _app_with_chapters()
    try:
        async with app.run_test() as pilot:
            await app.push_screen(ExportScreen(rt))
            await pilot.pause()
            screen = app.screen
            assert isinstance(screen, ExportScreen)
            screen.title_value = "The Drowned Bell"
            screen.author_value = "A. Author"
            screen.status_value = "final"
            await screen.do_export()
            await pilot.pause()

        story_root = Path(db_path).parent
        export_dir = story_root / "export"
        files = list(export_dir.glob("*.epub"))
        assert len(files) == 1
        assert files[0].stat().st_size > 0
    finally:
        await rt.close()
        os.unlink(db_path)
        export_dir = Path(db_path).parent / "export"
        if export_dir.exists():
            shutil.rmtree(export_dir)
