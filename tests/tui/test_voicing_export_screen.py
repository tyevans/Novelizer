import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from novelizer.canon.events import AttributedSegment
from novelizer.store.models import Chapter


class _FakeRead:
    def __init__(self, chapters, segments):
        self._chapters = chapters
        self._segments = segments

    async def list_chapters(self, status=None):
        return list(self._chapters)

    async def list_speech_segments(self, chapter_id=None):
        if chapter_id is None:
            return list(self._segments)
        return [s for s in self._segments if s.chapter_id == chapter_id]


@pytest.fixture
def voicing_runtime():
    """A minimal stand-in for Runtime: just enough of .settings and .read for
    VoicingExportScreen.write_export(), which holds all the logic and no
    widgets -- deliberately kept off the real app harness (TUI tests here are
    load-flaky, see docs/TESTING-TUI.md)."""

    def _make(tmp_path, *, chapters, segments, story_title=None):
        settings = SimpleNamespace(
            db_path=str(tmp_path / "story.db"),
            story_title=story_title,
        )
        return SimpleNamespace(settings=settings, read=_FakeRead(chapters, segments))

    return _make


@pytest.mark.asyncio
async def test_writes_a_json_document_under_export(tmp_path, voicing_runtime):
    runtime = voicing_runtime(
        tmp_path,
        chapters=[Chapter(id="ch1", title="One", prose='He. "Hi."')],
        segments=[AttributedSegment(chapter_id="ch1", index=0, kind="speech", character_id="mira",
                                    character_name="Mira", start_offset=4,
                                    end_offset=9, text='"Hi."')],
    )
    from novelizer.tui.voicing_export_screen import VoicingExportScreen

    screen = VoicingExportScreen(runtime)
    screen.title_value = "Book"
    screen.chunk_by = "segment"
    path = await screen.write_export()

    assert path.parent == tmp_path / "export"
    assert path.suffix == ".json"
    data = json.loads(Path(path).read_text())
    assert data["chunks"][0]["character_name"] == "Mira"


@pytest.mark.asyncio
async def test_annotated_format_writes_marked_prose(tmp_path, voicing_runtime):
    runtime = voicing_runtime(
        tmp_path,
        chapters=[Chapter(id="ch1", title="One", prose='He. "Hi."')],
        segments=[
            AttributedSegment(chapter_id="ch1", index=0, kind="narration", character_id=None,
                              character_name="", start_offset=0, end_offset=4, text="He. "),
            AttributedSegment(chapter_id="ch1", index=1, kind="speech", character_id="mira",
                              character_name="Mira", start_offset=4, end_offset=9, text='"Hi."'),
        ],
    )
    from novelizer.tui.voicing_export_screen import VoicingExportScreen

    screen = VoicingExportScreen(runtime)
    screen.export_format = "annotated"
    screen.chunk_by = "segment"
    path = await screen.write_export()

    assert path.suffix == ".txt"
    assert Path(path).read_text() == 'He. <speech char="Mira">"Hi."</speech>'


@pytest.mark.asyncio
async def test_annotated_format_rejects_chapter_chunking(tmp_path, voicing_runtime):
    runtime = voicing_runtime(
        tmp_path,
        chapters=[Chapter(id="ch1", title="One", prose="x")],
        segments=[AttributedSegment(chapter_id="ch1", index=0, kind="narration",
                                    character_id=None, character_name="",
                                    start_offset=0, end_offset=1, text="x")],
    )
    from novelizer.tui.voicing_export_screen import VoicingExportScreen

    screen = VoicingExportScreen(runtime)
    screen.export_format = "annotated"
    screen.chunk_by = "chapter"
    assert await screen.write_export() is None
    assert "per-chapter" in screen._error


@pytest.mark.asyncio
async def test_reports_an_error_when_no_segments_exist(tmp_path, voicing_runtime):
    runtime = voicing_runtime(tmp_path, chapters=[], segments=[])
    from novelizer.tui.voicing_export_screen import VoicingExportScreen

    screen = VoicingExportScreen(runtime)
    assert await screen.write_export() is None
    assert "no attributed" in screen._error.lower()
