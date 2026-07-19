from __future__ import annotations
from textual.widgets import Static
from novelizer.brain.staleness import STALENESS_THRESHOLD_CHAPTERS, is_thread_stale
from novelizer.store.models import Chapter, ThreadRecord


def thread_board_line(
    thread: ThreadRecord, chapters: list[Chapter], threshold: int = STALENESS_THRESHOLD_CHAPTERS
) -> str:
    marker = "⚠ STALE" if is_thread_stale(thread, chapters, threshold) else thread.state.value
    return f"· {thread.name} (id:{thread.id})  [{marker}]"


class ThreadBoard(Static):
    async def refresh_from(self, read, threshold: int = STALENESS_THRESHOLD_CHAPTERS) -> None:
        threads = await read.list_threads()
        chapters = await read.list_chapters()
        lines = [thread_board_line(t, chapters, threshold) for t in threads]
        self.update("\n".join(lines) or "no threads yet")
