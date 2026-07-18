import os
import tempfile
import pytest
from textual.app import App, ComposeResult
from novelizer.canon.event_store import EventStore
from novelizer.canon.projector import Projector
from novelizer.canon.read_store import ReadStore
from novelizer.canon.events import EventType
from novelizer.store.models import Chapter
from novelizer.tui.widgets.browser import StoryBrowser


class _Host(App):
    def __init__(self, read): super().__init__(); self._read = read
    def compose(self) -> ComposeResult:
        yield StoryBrowser("Story", id="browser")
    async def on_mount(self):
        await self.query_one(StoryBrowser).refresh_sections(self._read)


@pytest.mark.asyncio
async def test_browser_lists_sections_and_items():
    fd, path = tempfile.mkstemp(suffix=".db"); os.close(fd)
    events = EventStore(path); await events.init()
    proj = Projector(events, path); await proj.init()
    read = ReadStore(path); await read.init()
    try:
        await events.append(EventType.CHAPTER_CREATED, "c1", Chapter(id="c1", title="One", prose="p"))
        await proj.catch_up()
        app = _Host(read)
        async with app.run_test():
            tree = app.query_one(StoryBrowser)
            labels = [str(n.label) for n in tree.root.children]
            assert any("Chapters" in l for l in labels)
            chapters_node = next(n for n in tree.root.children if "Chapters" in str(n.label))
            assert any("One" in str(c.label) for c in chapters_node.children)
    finally:
        await read.close(); await proj.close(); await events.close(); os.unlink(path)
