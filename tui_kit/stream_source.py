"""Where the stream's blocks come from.

The widget depends on this protocol, never on a concrete store: that is
what lets the widget tests run against a list and stay off SQLite, and
what will let a future domain back the same view with something else.
"""
from __future__ import annotations
from typing import Protocol, runtime_checkable
from tui_kit.run_model import StreamBlock


@runtime_checkable
class StreamSource(Protocol):
    async def page_before(self, sequence: int, limit: int) -> list[StreamBlock]:
        """The `limit` blocks immediately older than `sequence`, ascending."""
        ...

    async def fetch_output(self, sequence: int) -> str:
        """Full, untruncated output for the tool call at `sequence`.
        Empty string when it cannot be found -- a missing payload is a
        display gap, never an exception into the render path."""
        ...


class InMemoryStreamSource:
    """Test double, and the seed source before any store is wired."""

    def __init__(self, blocks: list[StreamBlock], outputs: dict[int, str]) -> None:
        self._blocks = list(blocks)
        self._outputs = dict(outputs)

    async def page_before(self, sequence: int, limit: int) -> list[StreamBlock]:
        older = [b for b in self._blocks if getattr(b, "sequence", 0) < sequence]
        return older[-limit:]

    async def fetch_output(self, sequence: int) -> str:
        return self._outputs.get(sequence, "")
