from __future__ import annotations
import asyncio
import logging
import re
from typing import Optional
import aiosqlite
from novelizer.canon import db
from novelizer.canon import projections
from novelizer.canon.event_store import EventStore
from novelizer.canon.events import StoredEvent
from novelizer.canon.projections import ProjectionContext, projects  # noqa: F401 - re-exported

logger = logging.getLogger(__name__)

_CREATE = """
CREATE TABLE IF NOT EXISTS chapters (
    id TEXT PRIMARY KEY, data TEXT NOT NULL, editorial_status TEXT NOT NULL, supersedes_id TEXT
);
CREATE TABLE IF NOT EXISTS world_entries (
    id TEXT PRIMARY KEY, data TEXT NOT NULL, canon_status TEXT NOT NULL, supersedes_id TEXT
);
CREATE TABLE IF NOT EXISTS characters (
    id TEXT PRIMARY KEY, data TEXT NOT NULL, canon_status TEXT NOT NULL, supersedes_id TEXT
);
CREATE TABLE IF NOT EXISTS director_signals (
    id TEXT PRIMARY KEY, data TEXT NOT NULL, consumed INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS flags (
    id TEXT PRIMARY KEY, data TEXT NOT NULL, status TEXT NOT NULL, category TEXT NOT NULL,
    escalated INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS proposals (
    id TEXT PRIMARY KEY, data TEXT NOT NULL, status TEXT NOT NULL, proposing_agent TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS autonomy_state (
    id TEXT PRIMARY KEY, data TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS projector_state (
    id TEXT PRIMARY KEY, last_sequence INTEGER NOT NULL
);
CREATE TABLE IF NOT EXISTS threads (
    id TEXT PRIMARY KEY, data TEXT NOT NULL, state TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS promises (
    id TEXT PRIMARY KEY, data TEXT NOT NULL, state TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS secrets (
    id TEXT PRIMARY KEY, data TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS themes (
    id TEXT PRIMARY KEY, data TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS secret_knowledge (
    secret_id TEXT NOT NULL, character_id TEXT NOT NULL, chapter_id TEXT NOT NULL DEFAULT '',
    note TEXT NOT NULL DEFAULT '', PRIMARY KEY (secret_id, character_id)
);
CREATE TABLE IF NOT EXISTS secret_references (
    secret_id TEXT NOT NULL, character_id TEXT NOT NULL, chapter_id TEXT NOT NULL DEFAULT '', note TEXT NOT NULL DEFAULT ''
);
CREATE TABLE IF NOT EXISTS causal_edges (
    cause_chapter_id TEXT NOT NULL, effect_chapter_id TEXT NOT NULL, note TEXT NOT NULL DEFAULT ''
);
CREATE TABLE IF NOT EXISTS structure_scores (
    id TEXT PRIMARY KEY, data TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS chat_messages (
    message_id TEXT PRIMARY KEY, agent_name TEXT NOT NULL, role TEXT NOT NULL, text TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS inspiration_hands (
    id TEXT PRIMARY KEY, data TEXT NOT NULL, status TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS inspiration_uptake (
    hand_id TEXT NOT NULL, kind TEXT NOT NULL, item TEXT NOT NULL,
    chapter_id TEXT NOT NULL DEFAULT '', PRIMARY KEY (hand_id, kind, item)
);
CREATE TABLE IF NOT EXISTS blueprints (
    id TEXT PRIMARY KEY, data TEXT NOT NULL, active INTEGER NOT NULL
);
CREATE TABLE IF NOT EXISTS beats (
    id TEXT PRIMARY KEY, data TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS chapter_briefs (
    id TEXT PRIMARY KEY, data TEXT NOT NULL, status TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS arcs (
    id TEXT PRIMARY KEY, data TEXT NOT NULL, character_id TEXT NOT NULL, active INTEGER NOT NULL
);
CREATE TABLE IF NOT EXISTS chapter_summaries (
    id TEXT PRIMARY KEY, data TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS speech_segments (
    chapter_id TEXT NOT NULL, segment_index INTEGER NOT NULL,
    kind TEXT NOT NULL, character_id TEXT, character_name TEXT NOT NULL DEFAULT '',
    start_offset INTEGER NOT NULL, end_offset INTEGER NOT NULL, text TEXT NOT NULL,
    PRIMARY KEY (chapter_id, segment_index)
);
"""

# Derived from the schema, never hand-maintained: a table added to _CREATE but
# forgotten in a hand-written list would keep its stale rows across a rebuild.
# Declaration order is preserved so the DELETEs run deterministically.
PROJECTION_TABLES: tuple[str, ...] = tuple(
    name for name in re.findall(r"CREATE TABLE IF NOT EXISTS (\w+)", _CREATE)
    # projector_state holds the replay position and is reset separately.
    if name != "projector_state"
)


class Projector:
    def __init__(self, event_store: EventStore, path: str) -> None:
        self._events = event_store
        self._path = path
        self._conn: Optional[aiosqlite.Connection] = None
        self._running = False
        # catch_up is called from both the projector loop and command paths;
        # the lock keeps two runs from interleaving transactions on this
        # connection (and from double-applying non-idempotent projections).
        self._write_lock = asyncio.Lock()

    async def init(self) -> None:
        self._conn = await db.connect(self._path)
        await self._conn.executescript(_CREATE)
        # Additive migration: pre-escalation DBs lack the flags.escalated column.
        cur = await self._conn.execute("PRAGMA table_info(flags)")
        cols = [r[1] for r in await cur.fetchall()]
        if "escalated" not in cols:
            await self._conn.execute("ALTER TABLE flags ADD COLUMN escalated INTEGER NOT NULL DEFAULT 0")
        await self._conn.execute(
            "INSERT OR IGNORE INTO projector_state (id, last_sequence) VALUES ('singleton', 0)"
        )
        await self._conn.commit()

    async def close(self) -> None:
        if self._conn:
            await self._conn.close()

    async def _last_sequence(self) -> int:
        cur = await self._conn.execute("SELECT last_sequence FROM projector_state WHERE id='singleton'")
        return (await cur.fetchone())[0]

    async def _set_last_sequence(self, seq: int) -> None:
        # No rollback-on-retry here: _reset_state relies on this commit to
        # flush its preceding DELETEs; re-running UPDATE+COMMIT is idempotent.
        async def txn() -> None:
            await self._conn.execute(
                "UPDATE projector_state SET last_sequence=? WHERE id='singleton'", (seq,)
            )
            await self._conn.commit()

        await db.retry_locked(txn)

    async def _reset_state(self) -> None:
        """Testing/rebuild helper: forget position and clear projections."""
        async with self._write_lock:
            await self._reset_state_locked()

    async def _reset_state_locked(self) -> None:
        for table in PROJECTION_TABLES:
            await self._conn.execute(f"DELETE FROM {table}")
        await self._set_last_sequence(0)

    async def catch_up(self) -> int:
        # No cursor write here: _apply advances it inside each event's own
        # transaction. Persisting it once after the loop meant a raise partway
        # through left every earlier event committed with the cursor still
        # behind them -- and the TUI's projector loop catches the error and
        # calls catch_up() again every tick, so a permanently-failing event
        # re-applied the whole uncursored prefix a few times a second, forever.
        async with self._write_lock:
            last = await self._last_sequence()
            events = await self._events.events_since(last)
            for ev in events:
                await self._apply(ev)
                last = ev.sequence
            return last

    async def run(self, interval: float = 0.5) -> None:
        self._running = True
        while self._running:
            await self.catch_up()
            await asyncio.sleep(interval)

    def stop(self) -> None:
        self._running = False

    async def _apply(self, ev: StoredEvent) -> None:
        """Project one event atomically: BEGIN IMMEDIATE takes the write lock
        up front (subject to busy_timeout) so the event's reads and writes see
        one consistent snapshot, retrying if another connection holds the file
        past the busy window.

        The replay cursor moves inside that same transaction. Projection and
        position are one fact -- "the read model includes everything through
        sequence N" -- so committing them separately allowed a state that claims
        less than it contains, and every retry then re-applied the difference.
        """
        async def txn() -> None:
            if self._conn.in_transaction:
                await self._conn.execute("ROLLBACK")
            await self._conn.execute("BEGIN IMMEDIATE")
            await self._project(ev)
            await self._conn.execute(
                "UPDATE projector_state SET last_sequence=? WHERE id='singleton'",
                (ev.sequence,),
            )
            await self._conn.commit()

        await db.retry_locked(txn)

    async def _project(self, ev: StoredEvent) -> None:
        """Dispatch one event to its registered handler.

        An event type with no handler is a silent no-op: the caller still
        advances the replay position past it, so an unknown (e.g. newer or
        retired) event type can never wedge the projector.
        """
        handler = projections.HANDLERS.get(ev.event_type)
        if handler is None:
            return
        await handler(ProjectionContext(conn=self._conn, event=ev))
