from __future__ import annotations
import asyncio
from textual.app import App, ComposeResult
from textual.widgets import Header, Footer, RichLog
from novelizer.canon.events import StoredEvent, EventType

_LABELS = {
    EventType.CHAPTER_CREATED: "Author",
    EventType.WORLD_ENTRY_CREATED: "Architect",
    EventType.CHARACTER_CREATED: "Keeper",
    EventType.DIRECTOR_SIGNAL_CREATED: "Director",
}


def format_event(ev: StoredEvent) -> str:
    who = _LABELS.get(ev.event_type, "System")
    p = ev.payload
    if ev.event_type == EventType.CHAPTER_CREATED:
        detail = f"new chapter: {p.get('title', '')}"
    elif ev.event_type == EventType.WORLD_ENTRY_CREATED:
        detail = f"lore: {p.get('title', '')}"
    elif ev.event_type == EventType.DIRECTOR_SIGNAL_CREATED:
        detail = f"signal: {p.get('body', '')}"
    else:
        detail = ev.event_type
    return f"◆ {who} — {detail}"


class NovelizerApp(App):
    TITLE = "Novelizer — Mission Control"

    def __init__(self, runtime) -> None:
        super().__init__()
        self.runtime = runtime
        self._last_seq = 0

    def compose(self) -> ComposeResult:
        yield Header()
        yield RichLog(highlight=False, markup=False, id="feed")
        yield Footer()

    async def on_mount(self) -> None:
        self.run_worker(self._projector_loop(), exclusive=False)
        self.run_worker(self._author_loop(), exclusive=False)
        self.run_worker(self._feed_loop(), exclusive=False)

    async def _projector_loop(self) -> None:
        while True:
            await self.runtime.projector.catch_up()
            await asyncio.sleep(self.runtime.settings.projector_interval)

    async def _author_loop(self) -> None:
        while True:
            await self.runtime.author.run_once()
            await asyncio.sleep(self.runtime.author.interval)

    async def _feed_loop(self) -> None:
        log = self.query_one("#feed", RichLog)
        while True:
            events = await self.runtime.events.events_since(self._last_seq)
            for ev in events:
                log.write(format_event(ev))
                self._last_seq = ev.sequence
            await asyncio.sleep(0.3)
