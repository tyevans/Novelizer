from __future__ import annotations
import asyncio
import sqlite3
import aiosqlite

# Explicit and higher than the stdlib's incidental 5s default: world.db has
# multiple writer connections (EventStore, Projector, headless CLI processes),
# and a lock wait that outlasts the busy handler surfaces as a crash.
BUSY_TIMEOUT_MS = 10_000


async def connect(path: str) -> aiosqlite.Connection:
    """Open a connection with the lock-hardening PRAGMAs all stores share."""
    conn = await aiosqlite.connect(path)
    await conn.execute("PRAGMA journal_mode=WAL")
    await conn.execute(f"PRAGMA busy_timeout={BUSY_TIMEOUT_MS}")
    return conn


async def retry_locked(fn, *, attempts: int = 4, base_delay_s: float = 0.05):
    """Run `await fn()`, retrying with exponential backoff when SQLite reports
    a lock. Any other OperationalError propagates immediately; `fn` must be
    safe to re-run (roll back its own partial transaction on entry)."""
    for attempt in range(1, attempts + 1):
        try:
            return await fn()
        except sqlite3.OperationalError as exc:
            if "locked" not in str(exc) or attempt == attempts:
                raise
            await asyncio.sleep(base_delay_s * 2 ** (attempt - 1))
