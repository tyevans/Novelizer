from __future__ import annotations
import os
from typing import Optional
from novelizer.store.db import WorldDB
from novelizer.store.embeddings import EmbeddingStore
from novelizer.store.models import (
    WorldEntry, Character, Event, Chapter,
    RetconRequest, DirectorSignal, RetconStatus,
)


class Store:
    def __init__(self, db_path: str, chroma_path: str, embed_model: str) -> None:
        self.db = WorldDB(db_path)
        self.embeddings = EmbeddingStore(path=chroma_path, embed_model=embed_model)

    async def init(self) -> None:
        await self.db.init()

    async def close(self) -> None:
        await self.db.close()
        self.embeddings.close()

    # WorldEntry

    async def save_world_entry(self, entry: WorldEntry) -> None:
        await self.db.save_world_entry(entry)
        await self.embeddings.upsert_world_entry(entry)

    async def list_world_entries(self, domain: Optional[str] = None) -> list[WorldEntry]:
        return await self.db.list_world_entries(domain=domain)

    async def supersede_world_entry(self, old_id: str, new_entry: WorldEntry) -> None:
        await self.db.mark_superseded(old_id)
        await self.embeddings.delete(old_id, "world_entries")
        await self.save_world_entry(new_entry)

    # Character

    async def save_character(self, char: Character) -> None:
        await self.db.save_character(char)
        await self.embeddings.upsert_character(char)

    async def list_characters(self) -> list[Character]:
        return await self.db.list_characters()

    async def get_character(self, char_id: str) -> Optional[Character]:
        return await self.db.get_character(char_id)

    # Event

    async def save_event(self, event: Event) -> None:
        await self.db.save_event(event)

    async def list_events(self) -> list[Event]:
        return await self.db.list_events()

    # Chapter

    async def save_chapter(self, chapter: Chapter) -> None:
        await self.db.save_chapter(chapter)
        await self.embeddings.upsert_chapter(chapter)

    async def list_chapters(self, status: Optional[str] = None) -> list[Chapter]:
        return await self.db.list_chapters(status=status)

    # RetconRequest

    async def save_retcon_request(self, req: RetconRequest) -> None:
        await self.db.save_retcon_request(req)

    async def list_retcon_requests(self, status: Optional[RetconStatus] = None) -> list[RetconRequest]:
        return await self.db.list_retcon_requests(status=status)

    async def resolve_retcon(self, req_id: str, resolved_by: str) -> None:
        await self.db.update_retcon_status(req_id, RetconStatus.resolved, resolved_by)

    async def reject_retcon(self, req_id: str) -> None:
        await self.db.update_retcon_status(req_id, RetconStatus.rejected)

    # DirectorSignal

    async def save_director_signal(self, sig: DirectorSignal) -> None:
        await self.db.save_director_signal(sig)

    async def list_unconsumed_signals(self, target_agent: Optional[str] = None) -> list[DirectorSignal]:
        return await self.db.list_unconsumed_signals(target_agent=target_agent)

    async def consume_signal(self, sig_id: str) -> None:
        await self.db.mark_signal_consumed(sig_id)

    # Semantic search (proxy to embeddings)

    async def semantic_world(self, query: str, n: int = 5) -> list[WorldEntry]:
        return await self.embeddings.query_world_entries(query, n=n)

    async def semantic_characters(self, query: str, n: int = 5) -> list[Character]:
        return await self.embeddings.query_characters(query, n=n)

    async def semantic_chapters(self, query: str, n: int = 5) -> list[Chapter]:
        return await self.embeddings.query_chapters(query, n=n)
