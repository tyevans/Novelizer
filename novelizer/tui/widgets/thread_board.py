from __future__ import annotations
from textual.widgets import Static
from novelizer.brain.staleness import is_thread_stale
from novelizer.store.models import Chapter, ThreadRecord


def thread_board_line(thread: ThreadRecord, chapters: list[Chapter]) -> str:
    marker = "⚠ STALE" if is_thread_stale(thread, chapters) else thread.state.value
    return f"· {thread.name} (id:{thread.id})  [{marker}]"


class ThreadBoard(Static):
    async def refresh_from(self, read) -> None:
        threads = await read.list_threads()
        chapters = await read.list_chapters()
        lines = [thread_board_line(t, chapters) for t in threads]
        self.update("\n".join(lines) or "no threads yet")
