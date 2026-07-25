"""Generation-keyed memoisation for values derived from the whole read model.

The canon virtual filesystem backends each build a full snapshot of canon --
every chapter, character, world entry, thread, secret and theme, plus the
rendered path index -- and they did it on every single backend call. Measured
on stories/death-becomes-her: 3,599 tool calls against 117 canon events, with
only 43 distinct replay generations ever current while a call was in flight.
98.8% of those builds re-derived a view that had not changed, against a SQLite
file the projector is writing to and thirteen agents are reading from at once.

The key is the projector's replay cursor (`ReadStore.projection_generation`),
not a timer and not a manual invalidate() the backends would have to remember
to call. Canon is an append-only log, the cursor moves in the same transaction
as each projection write, and nothing can change a projection without moving
it -- so the cursor is a complete change stamp, and a stale entry is not
representable rather than merely unlikely.
"""
from __future__ import annotations

from typing import Awaitable, Callable, Generic, TypeVar

T = TypeVar("T")


class GenerationCache(Generic[T]):
    """Memoises one derived value, invalidated by the projection generation.

    Not an LRU and deliberately holds exactly one entry: the value is a view of
    ALL of canon, so a second entry could only ever be a superseded generation
    of the same thing, and keeping it would be a memory leak that grows with
    the length of the run rather than a cache.
    """

    def __init__(self, read_store, build: Callable[[], Awaitable[T]]) -> None:
        self._read = read_store
        self._build = build
        self._generation: int | None = None
        self._value: T | None = None

    async def get(self) -> T:
        """Return the value for the current generation, building if needed.

        The generation is read BEFORE and AFTER the build, and the result is
        only stored when it did not move. A build is several separate awaits
        and is not one transaction, so one that straddled a commit holds a mix
        of two generations; storing that under the older stamp would serve
        provably wrong content to every later call at that generation. Dropping
        it costs one rebuild on the next call and keeps the guarantee absolute.

        Concurrent callers may each build -- there is no lock, because every
        build produces a correct value for the generation it verified, so the
        only cost of a race is duplicated work that the next call stops
        repeating. A lock here would serialise thirteen agents behind one
        SQLite read to save that.
        """
        generation = await self._read.projection_generation()
        if self._value is not None and self._generation == generation:
            return self._value
        value = await self._build()
        if await self._read.projection_generation() == generation:
            self._generation = generation
            self._value = value
        return value
