from __future__ import annotations
from typing import Optional
from novelizer.config import Settings
from novelizer.canon.event_store import EventStore
from novelizer.canon.projector import Projector
from novelizer.canon.read_store import ReadStore
from novelizer.agents.author import Author, build_author_runner


class Runtime:
    def __init__(self, settings: Settings, runner=None) -> None:
        self.settings = settings
        self.events = EventStore(settings.db_path)
        self.projector = Projector(self.events, settings.db_path)
        self.read = ReadStore(settings.db_path)
        self._runner = runner
        self.author: Optional[Author] = None

    async def start(self) -> None:
        await self.events.init()
        await self.projector.init()
        await self.read.init()
        await self.projector.catch_up()
        runner = self._runner or build_author_runner(self.settings)
        self.author = Author(runner, self.read, self.events, interval=self.settings.author_interval)

    async def close(self) -> None:
        await self.read.close()
        await self.projector.close()
        await self.events.close()
