from __future__ import annotations
import asyncio
import json
from typing import Optional
import aiosqlite
from novelizer.canon.event_store import EventStore
from novelizer.canon.events import EventType, StoredEvent
from novelizer.store.models import ThreadRecord, ThreadState
from novelizer.canon.threads import TERMINAL_STATES

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
CREATE TABLE IF NOT EXISTS retcon_requests (
    id TEXT PRIMARY KEY, data TEXT NOT NULL, status TEXT NOT NULL
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
"""


class Projector:
    def __init__(self, event_store: EventStore, path: str) -> None:
        self._events = event_store
        self._path = path
        self._conn: Optional[aiosqlite.Connection] = None
        self._running = False

    async def init(self) -> None:
        self._conn = await aiosqlite.connect(self._path)
        await self._conn.execute("PRAGMA journal_mode=WAL")
        await self._conn.executescript(_CREATE)
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
        await self._conn.execute(
            "UPDATE projector_state SET last_sequence=? WHERE id='singleton'", (seq,)
        )
        await self._conn.commit()

    async def _reset_state(self) -> None:
        """Testing/rebuild helper: forget position and clear projections."""
        for table in (
            "chapters", "world_entries", "characters", "director_signals",
            "retcon_requests", "proposals", "autonomy_state", "threads",
        ):
            await self._conn.execute(f"DELETE FROM {table}")
        await self._set_last_sequence(0)

    async def catch_up(self) -> int:
        last = await self._last_sequence()
        events = await self._events.events_since(last)
        for ev in events:
            await self._apply(ev)
            last = ev.sequence
        await self._set_last_sequence(last)
        return last

    async def run(self, interval: float = 0.5) -> None:
        self._running = True
        while self._running:
            await self.catch_up()
            await asyncio.sleep(interval)

    def stop(self) -> None:
        self._running = False

    async def _apply(self, ev: StoredEvent) -> None:
        data = json.dumps(ev.payload)
        p = ev.payload
        t = ev.event_type
        if t == EventType.CHAPTER_CREATED or t == EventType.CHAPTER_STATUS_CHANGED:
            await self._conn.execute(
                "INSERT OR REPLACE INTO chapters (id, data, editorial_status, supersedes_id) VALUES (?,?,?,?)",
                (p["id"], data, p.get("editorial_status", "draft"), p.get("supersedes_id")),
            )
        elif t == EventType.WORLD_ENTRY_CREATED:
            await self._conn.execute(
                "INSERT OR REPLACE INTO world_entries (id, data, canon_status, supersedes_id) VALUES (?,?,?,?)",
                (p["id"], data, p.get("canon_status", "active"), p.get("supersedes_id")),
            )
        elif t == EventType.WORLD_ENTRY_SUPERSEDED:
            if p.get("supersedes_id"):
                await self._conn.execute(
                    "UPDATE world_entries SET canon_status='superseded' WHERE id=?", (p["supersedes_id"],)
                )
            await self._conn.execute(
                "INSERT OR REPLACE INTO world_entries (id, data, canon_status, supersedes_id) VALUES (?,?,?,?)",
                (p["id"], data, p.get("canon_status", "active"), p.get("supersedes_id")),
            )
        elif t == EventType.CHARACTER_CREATED or t == EventType.CHARACTER_UPDATED:
            await self._conn.execute(
                "INSERT OR REPLACE INTO characters (id, data, canon_status, supersedes_id) VALUES (?,?,?,?)",
                (p["id"], data, p.get("canon_status", "active"), p.get("supersedes_id")),
            )
        elif t == EventType.DIRECTOR_SIGNAL_CREATED:
            await self._conn.execute(
                "INSERT OR REPLACE INTO director_signals (id, data, consumed) VALUES (?,?,?)",
                (p["id"], data, 1 if p.get("consumed") else 0),
            )
        elif t == EventType.DIRECTOR_SIGNAL_CONSUMED:
            await self._conn.execute(
                "UPDATE director_signals SET consumed=1 WHERE id=?", (ev.aggregate_id,)
            )
        elif t == EventType.RETCON_REQUEST_CREATED:
            await self._conn.execute(
                "INSERT OR REPLACE INTO retcon_requests (id, data, status) VALUES (?,?,?)",
                (p["id"], data, p.get("status", "open")),
            )
        elif t == EventType.RETCON_REQUEST_RESOLVED or t == EventType.RETCON_REQUEST_REJECTED:
            await self._conn.execute(
                "INSERT OR REPLACE INTO retcon_requests (id, data, status) VALUES (?,?,?)",
                (p["id"], data, p.get("status", "resolved" if t == EventType.RETCON_REQUEST_RESOLVED else "rejected")),
            )
        elif t == EventType.PROPOSAL_CREATED:
            await self._conn.execute(
                "INSERT OR REPLACE INTO proposals (id, data, status, proposing_agent) VALUES (?,?,?,?)",
                (p["id"], data, p.get("status", "open"), p["proposing_agent"]),
            )
        elif t == EventType.PROPOSAL_APPROVED or t == EventType.PROPOSAL_REJECTED:
            new_status = "approved" if t == EventType.PROPOSAL_APPROVED else "rejected"
            await self._conn.execute(
                "UPDATE proposals SET status=? WHERE id=?", (new_status, p["id"])
            )
        elif t == EventType.THREAD_PLANTED:
            record = ThreadRecord(
                id=p["id"], name=p["name"], state=ThreadState.planted,
                last_note=p.get("note", ""), last_chapter_id=p.get("chapter_id", ""),
            )
            await self._conn.execute(
                "INSERT OR REPLACE INTO threads (id, data, state) VALUES (?,?,?)",
                (record.id, record.model_dump_json(), record.state.value),
            )
        elif t in (EventType.THREAD_TOUCHED, EventType.THREAD_PAID_OFF, EventType.THREAD_ABANDONED):
            cur = await self._conn.execute("SELECT data FROM threads WHERE id=?", (p["id"],))
            row = await cur.fetchone()
            if row is not None:
                record = ThreadRecord.model_validate_json(row[0])
                if record.state.value not in TERMINAL_STATES:
                    new_state = {
                        EventType.THREAD_TOUCHED: ThreadState.touched,
                        EventType.THREAD_PAID_OFF: ThreadState.paid_off,
                        EventType.THREAD_ABANDONED: ThreadState.abandoned,
                    }[t]
                    touch_count = record.touch_count + (1 if t == EventType.THREAD_TOUCHED else 0)
                    updated = record.model_copy(update={
                        "state": new_state,
                        "touch_count": touch_count,
                        "last_note": p.get("note", ""),
                        "last_chapter_id": p.get("chapter_id", ""),
                    })
                    await self._conn.execute(
                        "INSERT OR REPLACE INTO threads (id, data, state) VALUES (?,?,?)",
                        (updated.id, updated.model_dump_json(), updated.state.value),
                    )
                # else: absorbing terminal state — the event is a fact in the log,
                # but the threads projection does not change.
            # else: no row for this id yet (shouldn't happen under correct agent
            # behavior, since agents validate intents against known ids before
            # committing) — nothing to project, no error raised.
        elif t == EventType.AUTONOMY_CHANGED:
            await self._conn.execute(
                "INSERT OR REPLACE INTO autonomy_state (id, data) VALUES ('singleton', ?)", (data,)
            )
        await self._conn.commit()
