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
    EventType.RETCON_REQUEST_CREATED: "Continuity",
    EventType.CHAPTER_STATUS_CHANGED: "Editor",
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
    elif ev.event_type == EventType.RETCON_REQUEST_CREATED:
        detail = f"retcon: {p.get('description', '')}"
    elif ev.event_type == EventType.CHAPTER_STATUS_CHANGED:
        detail = f"chapter reviewed: {p.get('title', '')}"
    else:
        detail = ev.event_type
    return f"◆ {who} — {detail}"


class NovelizerApp(App):
    TITLE = "Novelizer — Mission Control"

    def __init__(self, runtime) -> None:
        super().__init__()
        self.runtime = runtime
        self._last_seq = 0
        self.messages: list[str] = []

    def compose(self) -> ComposeResult:
        yield Header()
        yield RichLog(highlight=False, markup=False, id="feed")
        yield Footer()

    async def on_mount(self) -> None:
        self.run_worker(self._projector_loop(), exclusive=False)
        self.run_worker(self._scheduler_loop(), exclusive=False)
        self.run_worker(self._feed_loop(), exclusive=False)

    def _report_worker_error(self, worker_name: str, e: Exception) -> None:
        line = f"⚠ {worker_name} error: {e}"
        try:
            log = self.query_one("#feed", RichLog)
            log.write(line)
        except Exception:
            pass
        self.messages.append(line)

    async def _projector_loop(self) -> None:
        while True:
            try:
                await self.runtime.projector.catch_up()
            except Exception as e:
                self._report_worker_error("projector", e)
            await asyncio.sleep(self.runtime.settings.projector_interval)

    async def _scheduler_loop(self) -> None:
        while True:
            try:
                await self.runtime.scheduler.tick()
            except Exception as e:
                self._report_worker_error("scheduler", e)
            await asyncio.sleep(self.runtime.settings.projector_interval)

    async def _feed_loop(self) -> None:
        log = self.query_one("#feed", RichLog)
        while True:
            try:
                events = await self.runtime.events.events_since(self._last_seq)
                for ev in events:
                    rendered = format_event(ev)
                    log.write(rendered)
                    self.messages.append(rendered)
                    self._last_seq = ev.sequence
            except Exception as e:
                self._report_worker_error("feed", e)
            await asyncio.sleep(0.3)
