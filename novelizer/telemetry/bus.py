from __future__ import annotations
import asyncio


class TelemetryBus:
    """In-process pub/sub for live machinery signals.

    High-frequency items (TokenDelta) and mirrored persisted telemetry
    events both flow through here so live consumers need one subscription.
    Bounded queues with drop-oldest: a slow or dead subscriber never blocks
    a publisher or other subscribers.
    """

    def __init__(self, maxsize: int = 256) -> None:
        self._maxsize = maxsize
        self._queues: list[asyncio.Queue] = []

    def subscribe(self) -> asyncio.Queue:
        q: asyncio.Queue = asyncio.Queue(maxsize=self._maxsize)
        self._queues.append(q)
        return q

    def unsubscribe(self, q: asyncio.Queue) -> None:
        if q in self._queues:
            self._queues.remove(q)

    def publish(self, item) -> None:
        for q in self._queues:
            if q.full():
                try:
                    q.get_nowait()
                except asyncio.QueueEmpty:
                    pass
            q.put_nowait(item)
