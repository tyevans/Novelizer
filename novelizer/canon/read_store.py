from __future__ import annotations
from typing import Optional
import aiosqlite
from novelizer.canon import db
from novelizer.store.models import (
    Chapter, WorldEntry, Character, DirectorSignal, Flag, ThreadRecord, StructureScore,
    SecretRecord, CausalEdgeRecord, SecretReferenceRecord, ThemeRecord, ChatMessageRecord,
    InspirationHandRecord, InspirationUptakeRecord, PromiseRecord,
    BlueprintRecord, BeatRecord, ChapterBriefRecord, ArcRecord,
)
from novelizer.canon.autonomy import Proposal, AutonomyState


class ReadStore:
    def __init__(self, path: str) -> None:
        self._path = path
        self._conn: Optional[aiosqlite.Connection] = None

    async def init(self) -> None:
        self._conn = await db.connect(self._path)

    async def close(self) -> None:
        if self._conn:
            await self._conn.close()

    async def list_chapters(self, status: Optional[str] = None) -> list[Chapter]:
        if status:
            cur = await self._conn.execute(
                "SELECT data FROM chapters WHERE editorial_status=? ORDER BY rowid", (status,)
            )
        else:
            cur = await self._conn.execute("SELECT data FROM chapters ORDER BY rowid")
        return [Chapter.model_validate_json(r[0]) for r in await cur.fetchall()]

    async def get_chapter(self, chapter_id: str) -> Optional[Chapter]:
        cur = await self._conn.execute("SELECT data FROM chapters WHERE id=?", (chapter_id,))
        row = await cur.fetchone()
        return Chapter.model_validate_json(row[0]) if row else None

    async def list_world_entries(self, domain: Optional[str] = None) -> list[WorldEntry]:
        if domain:
            cur = await self._conn.execute(
                "SELECT data FROM world_entries WHERE canon_status='active' "
                "AND json_extract(data,'$.domain')=? ORDER BY rowid", (domain,)
            )
        else:
            cur = await self._conn.execute(
                "SELECT data FROM world_entries WHERE canon_status='active' ORDER BY rowid"
            )
        return [WorldEntry.model_validate_json(r[0]) for r in await cur.fetchall()]

    async def list_characters(self) -> list[Character]:
        cur = await self._conn.execute(
            "SELECT data FROM characters WHERE canon_status='active' ORDER BY rowid"
        )
        return [Character.model_validate_json(r[0]) for r in await cur.fetchall()]

    async def list_unconsumed_signals(self, target_agent: Optional[str] = None) -> list[DirectorSignal]:
        cur = await self._conn.execute(
            "SELECT data FROM director_signals WHERE consumed=0 ORDER BY rowid"
        )
        sigs = [DirectorSignal.model_validate_json(r[0]) for r in await cur.fetchall()]
        if target_agent is not None:
            sigs = [s for s in sigs if s.target_agent is None or s.target_agent == target_agent]
        return sigs

    async def get_character(self, character_id: str) -> Optional[Character]:
        cur = await self._conn.execute("SELECT data FROM characters WHERE id=?", (character_id,))
        row = await cur.fetchone()
        return Character.model_validate_json(row[0]) if row else None

    async def list_flags(
        self, category: Optional[str] = None, status: Optional[str] = None,
        escalated: Optional[bool] = None,
    ) -> list[Flag]:
        clauses, params = [], []
        if category:
            clauses.append("category=?")
            params.append(category)
        if status:
            clauses.append("status=?")
            params.append(status)
        if escalated is not None:
            clauses.append("escalated=?")
            params.append(int(escalated))
        where = f" WHERE {' AND '.join(clauses)}" if clauses else ""
        cur = await self._conn.execute(f"SELECT data FROM flags{where} ORDER BY rowid", params)
        return [Flag.model_validate_json(r[0]) for r in await cur.fetchall()]

    async def list_proposals(self, status: Optional[str] = None) -> list[Proposal]:
        if status:
            cur = await self._conn.execute(
                "SELECT data FROM proposals WHERE status=? ORDER BY rowid", (status,)
            )
        else:
            cur = await self._conn.execute("SELECT data FROM proposals ORDER BY rowid")
        return [Proposal.model_validate_json(r[0]) for r in await cur.fetchall()]

    async def get_proposal(self, proposal_id: str) -> Optional[Proposal]:
        cur = await self._conn.execute("SELECT data FROM proposals WHERE id=?", (proposal_id,))
        row = await cur.fetchone()
        return Proposal.model_validate_json(row[0]) if row else None

    async def get_autonomy_state(self) -> AutonomyState:
        cur = await self._conn.execute("SELECT data FROM autonomy_state WHERE id='singleton'")
        row = await cur.fetchone()
        return AutonomyState.model_validate_json(row[0]) if row else AutonomyState()

    async def list_threads(self) -> list[ThreadRecord]:
        cur = await self._conn.execute("SELECT data FROM threads ORDER BY rowid")
        return [ThreadRecord.model_validate_json(r[0]) for r in await cur.fetchall()]

    async def get_thread(self, thread_id: str) -> Optional[ThreadRecord]:
        cur = await self._conn.execute("SELECT data FROM threads WHERE id=?", (thread_id,))
        row = await cur.fetchone()
        return ThreadRecord.model_validate_json(row[0]) if row else None

    async def list_promises(self) -> list[PromiseRecord]:
        cur = await self._conn.execute("SELECT data FROM promises ORDER BY rowid")
        return [PromiseRecord.model_validate_json(r[0]) for r in await cur.fetchall()]

    async def get_promise(self, promise_id: str) -> Optional[PromiseRecord]:
        cur = await self._conn.execute("SELECT data FROM promises WHERE id=?", (promise_id,))
        row = await cur.fetchone()
        return PromiseRecord.model_validate_json(row[0]) if row else None

    async def get_active_blueprint(self) -> Optional[BlueprintRecord]:
        cur = await self._conn.execute(
            "SELECT data FROM blueprints WHERE active=1 ORDER BY rowid DESC LIMIT 1"
        )
        row = await cur.fetchone()
        return BlueprintRecord.model_validate_json(row[0]) if row else None

    async def list_beats(self) -> list[BeatRecord]:
        cur = await self._conn.execute("SELECT data FROM beats ORDER BY rowid")
        return [BeatRecord.model_validate_json(r[0]) for r in await cur.fetchall()]

    async def list_briefs(self, status: Optional[str] = None) -> list[ChapterBriefRecord]:
        if status:
            cur = await self._conn.execute(
                "SELECT data FROM chapter_briefs WHERE status=? ORDER BY rowid", (status,)
            )
        else:
            cur = await self._conn.execute("SELECT data FROM chapter_briefs ORDER BY rowid")
        return [ChapterBriefRecord.model_validate_json(r[0]) for r in await cur.fetchall()]

    async def get_open_brief_for_ordinal(self, ordinal: int) -> Optional[ChapterBriefRecord]:
        cur = await self._conn.execute("SELECT data FROM chapter_briefs WHERE status='open' ORDER BY rowid")
        for r in await cur.fetchall():
            record = ChapterBriefRecord.model_validate_json(r[0])
            if record.target_ordinal == ordinal:
                return record
        return None

    async def list_arcs(self, active_only: bool = False) -> list[ArcRecord]:
        if active_only:
            cur = await self._conn.execute("SELECT data FROM arcs WHERE active=1 ORDER BY rowid")
        else:
            cur = await self._conn.execute("SELECT data FROM arcs ORDER BY rowid")
        return [ArcRecord.model_validate_json(r[0]) for r in await cur.fetchall()]

    async def get_arc(self, arc_id: str) -> Optional[ArcRecord]:
        cur = await self._conn.execute("SELECT data FROM arcs WHERE id=?", (arc_id,))
        row = await cur.fetchone()
        return ArcRecord.model_validate_json(row[0]) if row else None

    async def get_active_arc_for_character(self, character_id: str) -> Optional[ArcRecord]:
        cur = await self._conn.execute(
            "SELECT data FROM arcs WHERE character_id=? AND active=1 ORDER BY rowid DESC LIMIT 1",
            (character_id,),
        )
        row = await cur.fetchone()
        return ArcRecord.model_validate_json(row[0]) if row else None

    async def list_themes(self) -> list[ThemeRecord]:
        cur = await self._conn.execute("SELECT data FROM themes ORDER BY rowid")
        return [ThemeRecord.model_validate_json(r[0]) for r in await cur.fetchall()]

    async def get_theme(self, theme_id: str) -> Optional[ThemeRecord]:
        cur = await self._conn.execute("SELECT data FROM themes WHERE id=?", (theme_id,))
        row = await cur.fetchone()
        return ThemeRecord.model_validate_json(row[0]) if row else None

    async def list_structure_scores(self) -> list[StructureScore]:
        cur = await self._conn.execute("SELECT data FROM structure_scores ORDER BY rowid")
        return [StructureScore.model_validate_json(r[0]) for r in await cur.fetchall()]

    async def get_structure_score(self, chapter_id: str) -> Optional[StructureScore]:
        cur = await self._conn.execute("SELECT data FROM structure_scores WHERE id=?", (chapter_id,))
        row = await cur.fetchone()
        return StructureScore.model_validate_json(row[0]) if row else None

    async def list_secrets(self) -> list[SecretRecord]:
        cur = await self._conn.execute("SELECT data FROM secrets ORDER BY rowid")
        return [SecretRecord.model_validate_json(r[0]) for r in await cur.fetchall()]

    async def get_secret(self, secret_id: str) -> Optional[SecretRecord]:
        cur = await self._conn.execute("SELECT data FROM secrets WHERE id=?", (secret_id,))
        row = await cur.fetchone()
        return SecretRecord.model_validate_json(row[0]) if row else None

    async def knowledge_matrix(self) -> dict[str, dict]:
        """Return {secret_id: {"revealed": bool, "known_by": set[character_id]}}
        for every secret. `revealed` is read directly off each secret's own
        record (Locked decision #2) -- callers derive a specific cell's
        state with novelizer.canon.secrets.knowledge_cell_state.
        """
        matrix: dict[str, dict] = {}
        for secret in await self.list_secrets():
            cur = await self._conn.execute(
                "SELECT character_id FROM secret_knowledge WHERE secret_id=?", (secret.id,)
            )
            known_by = {r[0] for r in await cur.fetchall()}
            matrix[secret.id] = {"revealed": secret.revealed, "known_by": known_by}
        return matrix

    async def list_causal_edges(self) -> list[CausalEdgeRecord]:
        cur = await self._conn.execute(
            "SELECT cause_chapter_id, effect_chapter_id, note FROM causal_edges ORDER BY rowid"
        )
        return [
            CausalEdgeRecord(cause_chapter_id=r[0], effect_chapter_id=r[1], note=r[2])
            for r in await cur.fetchall()
        ]

    async def list_secret_references(self, secret_id: Optional[str] = None) -> list[SecretReferenceRecord]:
        query = "SELECT secret_id, character_id, chapter_id, note FROM secret_references"
        params: tuple = ()
        if secret_id is not None:
            query += " WHERE secret_id=?"
            params = (secret_id,)
        query += " ORDER BY rowid"
        cur = await self._conn.execute(query, params)
        return [
            SecretReferenceRecord(secret_id=r[0], character_id=r[1], chapter_id=r[2], note=r[3])
            for r in await cur.fetchall()
        ]

    async def list_chat_messages(self, agent_name: str, limit: int = 200) -> list[ChatMessageRecord]:
        cur = await self._conn.execute(
            "SELECT agent_name, role, text, message_id FROM chat_messages "
            "WHERE agent_name=? ORDER BY rowid DESC LIMIT ?",
            (agent_name, limit),
        )
        rows = list(await cur.fetchall())[::-1]
        return [
            ChatMessageRecord(agent_name=r[0], role=r[1], text=r[2], message_id=r[3]) for r in rows
        ]

    async def count_chat_messages(self, agent_name: str) -> int:
        cur = await self._conn.execute(
            "SELECT COUNT(*) FROM chat_messages WHERE agent_name=?", (agent_name,)
        )
        return (await cur.fetchone())[0]

    async def list_chat_conversations(self) -> list[str]:
        cur = await self._conn.execute(
            "SELECT DISTINCT agent_name FROM chat_messages ORDER BY agent_name"
        )
        return [r[0] for r in await cur.fetchall()]

    async def get_active_hand(self) -> Optional[InspirationHandRecord]:
        cur = await self._conn.execute(
            "SELECT data FROM inspiration_hands WHERE status='active' ORDER BY rowid DESC LIMIT 1"
        )
        row = await cur.fetchone()
        return InspirationHandRecord.model_validate_json(row[0]) if row else None

    async def get_hand(self, hand_id: str) -> Optional[InspirationHandRecord]:
        cur = await self._conn.execute("SELECT data FROM inspiration_hands WHERE id=?", (hand_id,))
        row = await cur.fetchone()
        return InspirationHandRecord.model_validate_json(row[0]) if row else None

    async def list_hands(self, status: Optional[str] = None) -> list[InspirationHandRecord]:
        # rowid order reflects last projection write (INSERT OR REPLACE), which
        # matches deal order only because a hand's status flips before the next
        # hand is dealt.
        if status:
            cur = await self._conn.execute(
                "SELECT data FROM inspiration_hands WHERE status=? ORDER BY rowid", (status,)
            )
        else:
            cur = await self._conn.execute("SELECT data FROM inspiration_hands ORDER BY rowid")
        return [InspirationHandRecord.model_validate_json(r[0]) for r in await cur.fetchall()]

    async def list_uptake(self, hand_id: Optional[str] = None) -> list[InspirationUptakeRecord]:
        query = "SELECT hand_id, kind, item, chapter_id FROM inspiration_uptake"
        params: tuple = ()
        if hand_id is not None:
            query += " WHERE hand_id=?"
            params = (hand_id,)
        query += " ORDER BY rowid"
        cur = await self._conn.execute(query, params)
        return [
            InspirationUptakeRecord(hand_id=r[0], kind=r[1], item=r[2], chapter_id=r[3])
            for r in await cur.fetchall()
        ]
