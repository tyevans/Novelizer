from __future__ import annotations
import json
import os
from typing import Optional
import aiosqlite
from novelizer.store.models import (
    WorldEntry, Character, Event, Chapter,
    RetconRequest, DirectorSignal, CanonStatus, RetconStatus,
)

CREATE_TABLES = """
CREATE TABLE IF NOT EXISTS world_entries (
    id TEXT PRIMARY KEY,
    data TEXT NOT NULL,
    canon_status TEXT NOT NULL,
    supersedes_id TEXT
);
CREATE TABLE IF NOT EXISTS characters (
    id TEXT PRIMARY KEY,
    data TEXT NOT NULL,
    canon_status TEXT NOT NULL,
    supersedes_id TEXT
);
CREATE TABLE IF NOT EXISTS events (
    id TEXT PRIMARY KEY,
    data TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS chapters (
    id TEXT PRIMARY KEY,
    data TEXT NOT NULL,
    editorial_status TEXT NOT NULL,
    supersedes_id TEXT
);
CREATE TABLE IF NOT EXISTS retcon_requests (
    id TEXT PRIMARY KEY,
    data TEXT NOT NULL,
    status TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS director_signals (
    id TEXT PRIMARY KEY,
    data TEXT NOT NULL,
    consumed INTEGER NOT NULL DEFAULT 0
);
"""


class WorldDB:
    def __init__(self, path: str) -> None:
        os.makedirs(os.path.dirname(path) if os.path.dirname(path) else ".", exist_ok=True)
        self._path = path
        self._conn: Optional[aiosqlite.Connection] = None

    async def init(self) -> None:
        self._conn = await aiosqlite.connect(self._path)
        self._conn.row_factory = aiosqlite.Row
        await self._conn.executescript(CREATE_TABLES)
        await self._conn.commit()

    async def close(self) -> None:
        if self._conn:
            await self._conn.close()

    # --- WorldEntry ---

    async def save_world_entry(self, entry: WorldEntry) -> None:
        await self._conn.execute(
            "INSERT OR REPLACE INTO world_entries (id, data, canon_status, supersedes_id) VALUES (?,?,?,?)",
            (entry.id, entry.model_dump_json(), entry.canon_status, entry.supersedes_id),
        )
        await self._conn.commit()

    async def list_world_entries(self, domain: Optional[str] = None) -> list[WorldEntry]:
        if domain:
            cur = await self._conn.execute(
                "SELECT data FROM world_entries WHERE canon_status = ? AND json_extract(data,'$.domain') = ?",
                (CanonStatus.active, domain),
            )
        else:
            cur = await self._conn.execute(
                "SELECT data FROM world_entries WHERE canon_status = ?",
                (CanonStatus.active,),
            )
        rows = await cur.fetchall()
        return [WorldEntry.model_validate_json(r[0]) for r in rows]

    async def mark_superseded(self, entry_id: str) -> None:
        await self._conn.execute(
            "UPDATE world_entries SET canon_status = ? WHERE id = ?",
            (CanonStatus.superseded, entry_id),
        )
        await self._conn.commit()

    # --- Character ---

    async def save_character(self, char: Character) -> None:
        await self._conn.execute(
            "INSERT OR REPLACE INTO characters (id, data, canon_status, supersedes_id) VALUES (?,?,?,?)",
            (char.id, char.model_dump_json(), char.canon_status, char.supersedes_id),
        )
        await self._conn.commit()

    async def list_characters(self) -> list[Character]:
        cur = await self._conn.execute(
            "SELECT data FROM characters WHERE canon_status = ?", (CanonStatus.active,)
        )
        rows = await cur.fetchall()
        return [Character.model_validate_json(r[0]) for r in rows]

    async def get_character(self, char_id: str) -> Optional[Character]:
        cur = await self._conn.execute("SELECT data FROM characters WHERE id = ?", (char_id,))
        row = await cur.fetchone()
        return Character.model_validate_json(row[0]) if row else None

    # --- Event ---

    async def save_event(self, event: Event) -> None:
        await self._conn.execute(
            "INSERT OR REPLACE INTO events (id, data) VALUES (?,?)",
            (event.id, event.model_dump_json()),
        )
        await self._conn.commit()

    async def list_events(self) -> list[Event]:
        cur = await self._conn.execute("SELECT data FROM events ORDER BY rowid")
        rows = await cur.fetchall()
        return [Event.model_validate_json(r[0]) for r in rows]

    # --- Chapter ---

    async def save_chapter(self, chapter: Chapter) -> None:
        await self._conn.execute(
            "INSERT OR REPLACE INTO chapters (id, data, editorial_status, supersedes_id) VALUES (?,?,?,?)",
            (chapter.id, chapter.model_dump_json(), chapter.editorial_status, chapter.supersedes_id),
        )
        await self._conn.commit()

    async def list_chapters(self, status: Optional[str] = None) -> list[Chapter]:
        if status:
            cur = await self._conn.execute(
                "SELECT data FROM chapters WHERE editorial_status = ? ORDER BY rowid",
                (status,),
            )
        else:
            cur = await self._conn.execute(
                "SELECT data FROM chapters WHERE supersedes_id IS NULL ORDER BY rowid"
            )
        rows = await cur.fetchall()
        return [Chapter.model_validate_json(r[0]) for r in rows]

    # --- RetconRequest ---

    async def save_retcon_request(self, req: RetconRequest) -> None:
        await self._conn.execute(
            "INSERT OR REPLACE INTO retcon_requests (id, data, status) VALUES (?,?,?)",
            (req.id, req.model_dump_json(), req.status),
        )
        await self._conn.commit()

    async def list_retcon_requests(self, status: Optional[RetconStatus] = None) -> list[RetconRequest]:
        if status:
            cur = await self._conn.execute(
                "SELECT data FROM retcon_requests WHERE status = ? ORDER BY rowid",
                (status,),
            )
        else:
            cur = await self._conn.execute("SELECT data FROM retcon_requests ORDER BY rowid")
        rows = await cur.fetchall()
        return [RetconRequest.model_validate_json(r[0]) for r in rows]

    async def update_retcon_status(self, req_id: str, status: RetconStatus, resolved_by: Optional[str] = None) -> None:
        cur = await self._conn.execute("SELECT data FROM retcon_requests WHERE id = ?", (req_id,))
        row = await cur.fetchone()
        if not row:
            return
        req = RetconRequest.model_validate_json(row[0])
        req.status = status
        req.resolved_by = resolved_by
        await self._conn.execute(
            "UPDATE retcon_requests SET data = ?, status = ? WHERE id = ?",
            (req.model_dump_json(), status, req_id),
        )
        await self._conn.commit()

    # --- DirectorSignal ---

    async def save_director_signal(self, sig: DirectorSignal) -> None:
        await self._conn.execute(
            "INSERT OR REPLACE INTO director_signals (id, data, consumed) VALUES (?,?,?)",
            (sig.id, sig.model_dump_json(), int(sig.consumed)),
        )
        await self._conn.commit()

    async def list_unconsumed_signals(self, target_agent: Optional[str] = None) -> list[DirectorSignal]:
        cur = await self._conn.execute(
            "SELECT data FROM director_signals WHERE consumed = 0 ORDER BY rowid"
        )
        rows = await cur.fetchall()
        sigs = [DirectorSignal.model_validate_json(r[0]) for r in rows]
        if target_agent is not None:
            sigs = [s for s in sigs if s.target_agent is None or s.target_agent == target_agent]
        return sigs

    async def mark_signal_consumed(self, sig_id: str) -> None:
        await self._conn.execute(
            "UPDATE director_signals SET consumed = 1 WHERE id = ?", (sig_id,)
        )
        await self._conn.commit()

    # --- Counts for scheduler ---

    async def count_open_retcons(self) -> int:
        cur = await self._conn.execute(
            "SELECT COUNT(*) FROM retcon_requests WHERE status = ?", ("open",)
        )
        row = await cur.fetchone()
        return row[0]

    async def count_draft_chapters(self) -> int:
        cur = await self._conn.execute(
            "SELECT COUNT(*) FROM chapters WHERE editorial_status = ?", ("draft",)
        )
        row = await cur.fetchone()
        return row[0]

    async def count_world_entries(self) -> int:
        cur = await self._conn.execute(
            "SELECT COUNT(*) FROM world_entries WHERE canon_status = ?", (CanonStatus.active,)
        )
        row = await cur.fetchone()
        return row[0]
