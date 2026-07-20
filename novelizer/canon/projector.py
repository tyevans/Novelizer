from __future__ import annotations
import asyncio
import json
import logging
from typing import Optional
import aiosqlite
from novelizer.canon import db
from novelizer.canon.event_store import EventStore
from novelizer.canon.events import EventType, StoredEvent
from novelizer.store.models import Chapter, EditorialStatus, ThreadRecord, ThreadState, SecretRecord, ThemeRecord, HandStatus, InspirationHandRecord, PromiseRecord, PromiseState
from novelizer.canon.threads import TERMINAL_STATES
from novelizer.canon.promises import TERMINAL_PROMISE_STATES

logger = logging.getLogger(__name__)

# A revised chapter's prose more than this multiple of the original prose's
# length is a signal for a human/Retconner to notice via the feed, not
# something the Projector silently corrects (event sourcing: the log is the
# truth) -- see Locked decision 10's escape hatch.
_REVISION_LENGTH_SANITY_MULTIPLE = 4

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
"""


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
        for table in (
            "chapters", "world_entries", "characters", "director_signals",
            "retcon_requests", "proposals", "autonomy_state", "threads",
            "structure_scores", "secrets", "secret_knowledge", "secret_references",
            "causal_edges", "themes", "chat_messages", "inspiration_hands",
            "inspiration_uptake", "promises",
        ):
            await self._conn.execute(f"DELETE FROM {table}")
        await self._set_last_sequence(0)

    async def catch_up(self) -> int:
        async with self._write_lock:
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
        """Project one event atomically: BEGIN IMMEDIATE takes the write lock
        up front (subject to busy_timeout) so the event's reads and writes see
        one consistent snapshot, retrying if another connection holds the file
        past the busy window."""
        async def txn() -> None:
            if self._conn.in_transaction:
                await self._conn.execute("ROLLBACK")
            await self._conn.execute("BEGIN IMMEDIATE")
            await self._project(ev)
            await self._conn.commit()

        await db.retry_locked(txn)

    async def _project(self, ev: StoredEvent) -> None:
        data = json.dumps(ev.payload)
        p = ev.payload
        t = ev.event_type
        if t == EventType.CHAPTER_CREATED or t == EventType.CHAPTER_STATUS_CHANGED:
            await self._conn.execute(
                "INSERT OR REPLACE INTO chapters (id, data, editorial_status, supersedes_id) VALUES (?,?,?,?)",
                (p["id"], data, p.get("editorial_status", "draft"), p.get("supersedes_id")),
            )
        elif t == EventType.CHAPTER_REVISED:
            cur = await self._conn.execute("SELECT data FROM chapters WHERE id=?", (p["chapter_id"],))
            row = await cur.fetchone()
            if row is None:
                logger.warning(
                    "chapter.revised for unknown chapter_id=%s -- no-op (shouldn't happen under correct signal routing)",
                    p["chapter_id"],
                )
            else:
                existing = Chapter.model_validate_json(row[0])
                if existing.prose and len(p["prose"]) > _REVISION_LENGTH_SANITY_MULTIPLE * len(existing.prose):
                    logger.warning(
                        "chapter.revised prose for chapter_id=%s is >%dx the original length -- "
                        "committing anyway (event sourcing: the log is the truth, a length anomaly "
                        "is a signal to notice via the feed, not something to silently correct)",
                        p["chapter_id"], _REVISION_LENGTH_SANITY_MULTIPLE,
                    )
                revised = existing.model_copy(update={"prose": p["prose"], "editorial_status": EditorialStatus.draft})
                await self._conn.execute(
                    "INSERT OR REPLACE INTO chapters (id, data, editorial_status, supersedes_id) VALUES (?,?,?,?)",
                    (revised.id, revised.model_dump_json(), EditorialStatus.draft.value, revised.supersedes_id),
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
            cur = await self._conn.execute("SELECT id FROM threads WHERE id=?", (p["id"],))
            existing = await cur.fetchone()
            if existing is None:
                record = ThreadRecord(
                    id=p["id"], name=p["name"], state=ThreadState.planted,
                    last_note=p.get("note", ""), last_chapter_id=p.get("chapter_id", ""),
                )
                await self._conn.execute(
                    "INSERT OR REPLACE INTO threads (id, data, state) VALUES (?,?,?)",
                    (record.id, record.model_dump_json(), record.state.value),
                )
            # else: a thread id is minted exactly once. A second thread.planted
            # for an id that already has a row (any state, including terminal)
            # is a projection no-op — the event remains a fact in the log, but
            # first-plant-wins so replanting can never reset state/touch_count
            # or resurrect an absorbed terminal thread.
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
        elif t == EventType.PROMISE_MADE:
            cur = await self._conn.execute("SELECT id FROM promises WHERE id=?", (p["id"],))
            if await cur.fetchone() is None:
                record = PromiseRecord(
                    id=p["id"], name=p["name"], description=p.get("description", ""),
                    kind=p.get("kind", "foreshadow"), thread_id=p.get("thread_id", ""),
                    setup_chapter_id=p.get("chapter_id", ""),
                    window_lo=p.get("window_lo", 0), window_hi=p.get("window_hi", 0),
                    last_note=p.get("note", ""), last_chapter_id=p.get("chapter_id", ""),
                )
                await self._conn.execute(
                    "INSERT OR REPLACE INTO promises (id, data, state) VALUES (?,?,?)",
                    (record.id, record.model_dump_json(), record.state.value),
                )
            # else: a promise id is minted exactly once -- first-make-wins.
        elif t in (EventType.PROMISE_PROGRESSED, EventType.PROMISE_PAID, EventType.PROMISE_RELEASED):
            cur = await self._conn.execute("SELECT data FROM promises WHERE id=?", (p["id"],))
            row = await cur.fetchone()
            if row is not None:
                record = PromiseRecord.model_validate_json(row[0])
                if record.state.value not in TERMINAL_PROMISE_STATES:
                    new_state = {
                        EventType.PROMISE_PROGRESSED: PromiseState.open,
                        EventType.PROMISE_PAID: PromiseState.paid,
                        EventType.PROMISE_RELEASED: PromiseState.released,
                    }[t]
                    progress = record.progress_count + (1 if t == EventType.PROMISE_PROGRESSED else 0)
                    updated = record.model_copy(update={
                        "state": new_state, "progress_count": progress,
                        "last_note": p.get("note", p.get("reason", "")),
                        "last_chapter_id": p.get("chapter_id", ""),
                    })
                    await self._conn.execute(
                        "INSERT OR REPLACE INTO promises (id, data, state) VALUES (?,?,?)",
                        (updated.id, updated.model_dump_json(), updated.state.value),
                    )
                # else: paid/released are absorbing -- the event is a fact in
                # the log, but the promises projection does not change.
            # else: no row for this id yet -- nothing to project, no error raised.
        elif t == EventType.THREAD_RESOLUTION_PLANNED:
            cur = await self._conn.execute("SELECT data FROM threads WHERE id=?", (p["id"],))
            row = await cur.fetchone()
            if row is not None:
                record = ThreadRecord.model_validate_json(row[0])
                if record.state.value not in TERMINAL_STATES:
                    updated = record.model_copy(update={
                        "window_lo": p.get("window_lo", 0), "window_hi": p.get("window_hi", 0),
                        "planned_payoff_note": p.get("planned_payoff_note", ""),
                    })
                    await self._conn.execute(
                        "INSERT OR REPLACE INTO threads (id, data, state) VALUES (?,?,?)",
                        (updated.id, updated.model_dump_json(), updated.state.value),
                    )
                # else: no-op on a terminal thread.
            # else: unknown thread id -- no-op, no error raised.
        elif t == EventType.SECRET_REVEAL_PLANNED:
            cur = await self._conn.execute("SELECT data FROM secrets WHERE id=?", (p["id"],))
            row = await cur.fetchone()
            if row is not None:
                record = SecretRecord.model_validate_json(row[0])
                if not record.revealed:
                    updated = record.model_copy(update={
                        "reveal_window_lo": p.get("window_lo", 0),
                        "reveal_window_hi": p.get("window_hi", 0),
                    })
                    await self._conn.execute(
                        "INSERT OR REPLACE INTO secrets (id, data) VALUES (?,?)",
                        (updated.id, updated.model_dump_json()),
                    )
                # else: no-op once revealed.
            # else: unknown secret id -- no-op, no error raised.
        elif t == EventType.SECRET_CREATED:
            cur = await self._conn.execute("SELECT id FROM secrets WHERE id=?", (p["id"],))
            existing = await cur.fetchone()
            if existing is None:
                record = SecretRecord(id=p["id"], title=p["title"], revealed=False)
                await self._conn.execute(
                    "INSERT OR REPLACE INTO secrets (id, data) VALUES (?,?)",
                    (record.id, record.model_dump_json()),
                )
            # else: a secret id is minted exactly once. A second secret.created
            # for an id that already has a row is a projection no-op — same
            # first-plant-wins rule as thread.planted.
        elif t == EventType.SECRET_LEARNED:
            await self._conn.execute(
                "INSERT OR IGNORE INTO secret_knowledge (secret_id, character_id, chapter_id, note) "
                "VALUES (?,?,?,?)",
                (p["id"], p["character_id"], p.get("chapter_id", ""), p.get("note", "")),
            )
        elif t == EventType.SECRET_REFERENCED:
            await self._conn.execute(
                "INSERT INTO secret_references (secret_id, character_id, chapter_id, note) VALUES (?,?,?,?)",
                (p["id"], p["character_id"], p.get("chapter_id", ""), p.get("note", "")),
            )
        elif t == EventType.SECRET_REVEALED:
            cur = await self._conn.execute("SELECT data FROM secrets WHERE id=?", (p["id"],))
            row = await cur.fetchone()
            if row is not None:
                record = SecretRecord.model_validate_json(row[0])
                if not record.revealed:
                    updated = record.model_copy(update={"revealed": True})
                    await self._conn.execute(
                        "INSERT OR REPLACE INTO secrets (id, data) VALUES (?,?)",
                        (updated.id, updated.model_dump_json()),
                    )
                # else: set-once — already revealed, event is a fact in the
                # log but the projection does not change (Locked decision #2).
            # else: no row for this id yet (shouldn't happen under correct
            # agent behavior) — nothing to project, no error raised.
        elif t == EventType.THEME_INTRODUCED:
            cur = await self._conn.execute("SELECT id FROM themes WHERE id=?", (p["id"],))
            existing = await cur.fetchone()
            if existing is None:
                record = ThemeRecord(
                    id=p["id"], title=p["title"],
                    last_note=p.get("note", ""), last_chapter_id=p.get("chapter_id", ""),
                )
                await self._conn.execute(
                    "INSERT OR REPLACE INTO themes (id, data) VALUES (?,?)",
                    (record.id, record.model_dump_json()),
                )
            # else: a theme id is minted exactly once. A second theme.introduced
            # for an id that already has a row is a projection no-op — same
            # first-mint-wins rule as thread.planted/secret.created.
        elif t == EventType.THEME_DEVELOPED:
            cur = await self._conn.execute("SELECT data FROM themes WHERE id=?", (p["id"],))
            row = await cur.fetchone()
            if row is not None:
                record = ThemeRecord.model_validate_json(row[0])
                updated = record.model_copy(update={
                    "touch_count": record.touch_count + 1,
                    "last_note": p.get("note", ""),
                    "last_chapter_id": p.get("chapter_id", ""),
                })
                await self._conn.execute(
                    "INSERT OR REPLACE INTO themes (id, data) VALUES (?,?)",
                    (updated.id, updated.model_dump_json()),
                )
            # else: no row for this id yet (shouldn't happen under correct agent
            # behavior, since agents validate intents against known ids before
            # committing) — nothing to project, no error raised.
        elif t == EventType.CAUSAL_EDGE_DECLARED:
            await self._conn.execute(
                "INSERT INTO causal_edges (cause_chapter_id, effect_chapter_id, note) VALUES (?,?,?)",
                (p["cause_chapter_id"], p["effect_chapter_id"], p.get("note", "")),
            )
        elif t == EventType.ANNOTATION_STRUCTURE_SCORED:
            await self._conn.execute(
                "INSERT OR REPLACE INTO structure_scores (id, data) VALUES (?,?)",
                (p["chapter_id"], data),
            )
        elif t == EventType.CHAT_USER_MESSAGED or t == EventType.CHAT_AGENT_REPLIED:
            role = "user" if t == EventType.CHAT_USER_MESSAGED else "agent"
            await self._conn.execute(
                "INSERT OR IGNORE INTO chat_messages (message_id, agent_name, role, text) VALUES (?,?,?,?)",
                (p["message_id"], p["agent_name"], role, p.get("text", "")),
            )
        elif t == EventType.INSPIRATION_DRAWN:
            cur = await self._conn.execute("SELECT id FROM inspiration_hands WHERE id=?", (p["hand_id"],))
            existing = await cur.fetchone()
            if existing is None:
                record = InspirationHandRecord(
                    id=p["hand_id"], seed=p["seed"], corpus_version=p["corpus_version"],
                    era=p["era"], names=p.get("names", []), professions=p.get("professions", []),
                    settings=p.get("settings", []), beats=p.get("beats", []),
                )
                await self._conn.execute(
                    "INSERT OR REPLACE INTO inspiration_hands (id, data, status) VALUES (?,?,?)",
                    (record.id, record.model_dump_json(), record.status.value),
                )
            # else: a hand id is minted exactly once — first-mint-wins, same
            # rule as thread.planted/secret.created/theme.introduced.
        elif t in (EventType.INSPIRATION_HAND_CONSUMED, EventType.INSPIRATION_HAND_SUPERSEDED):
            cur = await self._conn.execute("SELECT data FROM inspiration_hands WHERE id=?", (p["hand_id"],))
            row = await cur.fetchone()
            if row is not None:
                record = InspirationHandRecord.model_validate_json(row[0])
                if record.status == HandStatus.active:
                    if t == EventType.INSPIRATION_HAND_CONSUMED:
                        updated = record.model_copy(update={
                            "status": HandStatus.consumed,
                            "consumed_chapter_id": p.get("chapter_id", ""),
                        })
                    else:
                        updated = record.model_copy(update={"status": HandStatus.superseded})
                    await self._conn.execute(
                        "INSERT OR REPLACE INTO inspiration_hands (id, data, status) VALUES (?,?,?)",
                        (updated.id, updated.model_dump_json(), updated.status.value),
                    )
                # else: consumed/superseded are absorbing — the event is a fact
                # in the log, but the projection does not change.
            # else: no row for this id (shouldn't happen under correct Muse
            # behavior) — nothing to project, no error raised.
        elif t == EventType.INSPIRATION_UPTAKE_RECORDED:
            await self._conn.execute(
                "INSERT OR IGNORE INTO inspiration_uptake (hand_id, kind, item, chapter_id) VALUES (?,?,?,?)",
                (p["hand_id"], p["kind"], p["item"], p.get("chapter_id", "")),
            )
        elif t == EventType.AUTONOMY_CHANGED:
            await self._conn.execute(
                "INSERT OR REPLACE INTO autonomy_state (id, data) VALUES ('singleton', ?)", (data,)
            )
