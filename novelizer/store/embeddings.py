from __future__ import annotations
import asyncio
from dataclasses import dataclass
import chromadb
from chromadb.utils.embedding_functions import OpenAIEmbeddingFunction
from novelizer.store.models import (
    WorldEntry, Character, Chapter, ThemeRecord, ThreadRecord, SecretRecord,
    PromiseRecord, ChapterBriefRecord, ArcRecord,
)
from novelizer.text_chunk import chunk_prose


@dataclass
class SearchHit:
    kind: str
    id: str
    title: str
    distance: float


# Conservative char cap so embedding input stays under the embed model's
# token context window (e.g. nomic-embed-text's 2048 tokens): ~4 chars/token
# is a safe average for English prose, so 6000 chars stays well under 2048
# tokens even for token-dense text. Without this, any record whose text
# tokenizes past the window makes upsert raise every catch_up retry,
# permanently stalling canon indexing at that event (see seq 65 stall).
# Applies to every non-chapter collection: those records (world entry body,
# character backstory, etc.) are short-form and losing tail content past
# this cap is an acceptable trade for never stalling the indexer.
_MAX_EMBED_CHARS = 6000

# Chapters have no length bound (prose is free-form and routinely runs
# 12,000-24,000+ chars), so a flat cap would silently drop everything past
# the first ~1,500 words from search. Chapters are chunked into overlapping
# windows instead, each embedded and stored as its own vector, so the whole
# chapter stays searchable. Overlap keeps a match from being split across a
# chunk boundary and missed entirely.
_CHAPTER_CHUNK_CHARS = 6000
_CHAPTER_CHUNK_OVERLAP = 500


def _cap(text: str) -> str:
    return text[:_MAX_EMBED_CHARS]


def _chunk_id(chapter_id: str, index: int) -> str:
    return f"{chapter_id}#{index}"


class EmbeddingStore:
    def __init__(
        self,
        path: str,
        embed_model: str = "nomic-embed-text",
        base_url: str = "http://localhost:8080/v1",
        api_key: str = "not-needed",
        embedding_function=None,
    ) -> None:
        self._client = chromadb.PersistentClient(path=path)
        # `embedding_function` is the CI-testability seam: pass a deterministic
        # fake in tests so no suite run depends on a live embed endpoint. When
        # None (default/production), behavior is unchanged.
        ef = embedding_function if embedding_function is not None else OpenAIEmbeddingFunction(
            api_key=api_key, model_name=embed_model, api_base=base_url
        )
        self._world = self._client.get_or_create_collection("world_entries", embedding_function=ef)
        self._chars = self._client.get_or_create_collection("characters", embedding_function=ef)
        self._chapters = self._client.get_or_create_collection("chapters", embedding_function=ef)
        self._themes = self._client.get_or_create_collection("themes", embedding_function=ef)
        self._threads = self._client.get_or_create_collection("threads", embedding_function=ef)
        self._secrets = self._client.get_or_create_collection("secrets", embedding_function=ef)
        self._promises = self._client.get_or_create_collection("promises", embedding_function=ef)
        self._briefs = self._client.get_or_create_collection("briefs", embedding_function=ef)
        self._arcs = self._client.get_or_create_collection("arcs", embedding_function=ef)
        self._entities = self._client.get_or_create_collection("entities", embedding_function=ef)
        # Writes ARE reachable concurrently: agent intent commits
        # (novelizer/agents/intents.py) run as concurrent background tasks,
        # and CanonIndexer.catch_up() runs on every TUI tick. chromadb's
        # persistence is sqlite-backed, and unserialized cross-thread writes
        # risk "database is locked" errors (see commit 17387ac). This lock
        # serializes writes across all callers; asyncio.to_thread is what
        # keeps the event loop responsive while a write is in flight.
        self._write_lock = asyncio.Lock()

    def close(self) -> None:
        """Release the Chroma client this store constructed.

        Writes are already durable (the PersistentClient auto-flushes), so this
        is about the *handle*, not the data. chromadb reference-counts one
        sqlite-backed System per persist path and only tears it down when the
        last client closes; skipping that leaks the sqlite handle plus the
        client's worker threads for the life of the process. Chroma's own
        Client.close() docstring calls this "particularly important for
        PersistentClient to avoid SQLite file locking issues" -- and it is: with
        enough leaked systems in one process, a later client blocks inside
        Chroma's native create_collection and never returns.

        Idempotent, because callers close from `finally` blocks and may close
        twice on an error path.
        """
        client, self._client = self._client, None
        if client is not None:
            client.close()

    async def upsert_world_entry(self, entry: WorldEntry) -> None:
        text = _cap(f"{entry.title}\n{entry.body}")
        async with self._write_lock:
            await asyncio.to_thread(
                self._world.upsert, ids=[entry.id], documents=[text], metadatas=[{"title": entry.title}]
            )

    async def upsert_character(self, char: Character) -> None:
        text = _cap(f"{char.name}\n{char.traits}\n{char.backstory}")
        async with self._write_lock:
            await asyncio.to_thread(
                self._chars.upsert, ids=[char.id], documents=[text], metadatas=[{"name": char.name}]
            )

    async def upsert_chapter(self, chapter: Chapter) -> None:
        chunks = chunk_prose(chapter.prose, _CHAPTER_CHUNK_CHARS, _CHAPTER_CHUNK_OVERLAP)
        ids = [_chunk_id(chapter.id, i) for i in range(len(chunks))]
        metadatas = [
            {"title": chapter.title, "chapter_id": chapter.id, "chunk_index": i}
            for i in range(len(chunks))
        ]
        async with self._write_lock:
            await asyncio.to_thread(self._replace_chapter_chunks_sync, chapter.id, ids, chunks, metadatas)

    def _replace_chapter_chunks_sync(
        self, chapter_id: str, ids: list[str], chunks: list[str], metadatas: list[dict]
    ) -> None:
        # A revision can shrink the chunk count (shorter prose), which would
        # otherwise leave stale trailing chunks from the old, longer version
        # behind after upsert only overwrites the ids it's given.
        existing = self._chapters.get(where={"chapter_id": chapter_id}, include=[])
        stale_ids = [i for i in existing.get("ids", []) if i not in set(ids)]
        if stale_ids:
            self._chapters.delete(ids=stale_ids)
        self._chapters.upsert(ids=ids, documents=chunks, metadatas=metadatas)

    async def upsert_theme(self, theme: ThemeRecord) -> None:
        async with self._write_lock:
            await asyncio.to_thread(
                self._themes.upsert, ids=[theme.id], documents=[_cap(theme.title)], metadatas=[{"title": theme.title}]
            )

    async def upsert_thread(self, thread: ThreadRecord) -> None:
        text = _cap(f"{thread.name}\n{thread.last_note}" if thread.last_note else thread.name)
        async with self._write_lock:
            await asyncio.to_thread(
                self._threads.upsert, ids=[thread.id], documents=[text], metadatas=[{"title": thread.name}]
            )

    async def upsert_secret(self, secret: SecretRecord) -> None:
        async with self._write_lock:
            await asyncio.to_thread(
                self._secrets.upsert, ids=[secret.id], documents=[_cap(secret.title)], metadatas=[{"title": secret.title}]
            )

    async def upsert_promise(self, promise: PromiseRecord) -> None:
        text = _cap(f"{promise.name}\n{promise.description}\n{promise.last_note}\nstate: {promise.state}".strip())
        async with self._write_lock:
            await asyncio.to_thread(
                self._promises.upsert, ids=[promise.id], documents=[text],
                metadatas=[{"title": promise.name}],
            )

    async def upsert_brief(self, brief: ChapterBriefRecord) -> None:
        text = _cap(f"{brief.goal}\n{brief.synopsis}\nstatus: {brief.status}".strip())
        async with self._write_lock:
            await asyncio.to_thread(
                self._briefs.upsert, ids=[brief.id], documents=[text],
                metadatas=[{"title": brief.goal}],
            )

    async def upsert_arc(self, arc: ArcRecord) -> None:
        text = f"{arc.character_id}\n{arc.lie}\n{arc.truth}\n{arc.want}\n{arc.need}".strip()
        if arc.resolved:
            text = f"{text}\nresolved: {arc.outcome}".strip()
        text = _cap(text)
        async with self._write_lock:
            await asyncio.to_thread(
                self._arcs.upsert, ids=[arc.id], documents=[text],
                metadatas=[{"title": f"Arc: {arc.character_id}"}],
            )

    async def upsert_entity(self, entity_id: str, name: str, detail: str) -> None:
        text = _cap(f"{name}\n{detail}")
        async with self._write_lock:
            await asyncio.to_thread(
                self._entities.upsert, ids=[entity_id], documents=[text],
                metadatas=[{"title": name}],
            )

    async def delete_entity(self, entity_id: str) -> None:
        await self.delete(entity_id, "entities")

    async def delete(self, entity_id: str, collection: str) -> None:
        col = {
            "world_entries": self._world,
            "characters": self._chars,
            "chapters": self._chapters,
            "themes": self._themes,
            "threads": self._threads,
            "secrets": self._secrets,
            "promises": self._promises,
            "briefs": self._briefs,
            "arcs": self._arcs,
            "entities": self._entities,
        }[collection]
        # Offload to a thread: this is a synchronous chromadb/HTTP call that
        # would otherwise block the whole asyncio loop (see upsert_* -- same
        # reasoning: CPT-M3 now runs catch-up every TUI tick, so a blocking
        # call here freezes rendering each cycle whenever the embed endpoint
        # is unreachable, not just once at startup). The lock serializes this
        # against concurrent upserts/deletes from agent commits and catch_up.
        async with self._write_lock:
            if collection == "chapters":
                # Chapters are stored as multiple chunk ids ("{id}#0", ...),
                # never as entity_id itself -- delete by the chapter_id
                # metadata all of a chapter's chunks share instead.
                await asyncio.to_thread(self._delete_chapter_chunks_sync, entity_id)
            else:
                await asyncio.to_thread(col.delete, ids=[entity_id])

    def _delete_chapter_chunks_sync(self, chapter_id: str) -> None:
        existing = self._chapters.get(where={"chapter_id": chapter_id}, include=[])
        ids = existing.get("ids", [])
        if ids:
            self._chapters.delete(ids=ids)

    async def query_world_entries(self, query: str, n: int = 5) -> list[WorldEntry]:
        if self._world.count() == 0:
            return []
        results = self._world.query(query_texts=[query], n_results=min(n, self._world.count()))
        entries = []
        for i, doc_id in enumerate(results["ids"][0]):
            meta = results["metadatas"][0][i]
            entries.append(WorldEntry(id=doc_id, title=meta.get("title", ""), body=results["documents"][0][i]))
        return entries

    async def query_characters(self, query: str, n: int = 5) -> list[Character]:
        if self._chars.count() == 0:
            return []
        results = self._chars.query(query_texts=[query], n_results=min(n, self._chars.count()))
        chars = []
        for i, doc_id in enumerate(results["ids"][0]):
            meta = results["metadatas"][0][i]
            chars.append(Character(id=doc_id, name=meta.get("name", "")))
        return chars

    async def query_themes(self, query: str, n: int = 5) -> list[tuple[str, float]]:
        """Returns (theme_id, distance) pairs, closest first -- distance is
        whatever metric the collection's embedding_function/space uses
        (chromadb default: squared L2 for OpenAIEmbeddingFunction). Callers
        that need a similarity threshold (e.g. near-duplicate suggestion)
        compare against this raw distance rather than a hydrated record,
        since the score itself is the point of this query.
        """
        if self._themes.count() == 0:
            return []
        results = self._themes.query(query_texts=[query], n_results=min(n, self._themes.count()))
        ids = results["ids"][0]
        distances = results["distances"][0]
        return list(zip(ids, distances))

    async def query_chapters(self, query: str, n: int = 5) -> list[Chapter]:
        if self._chapters.count() == 0:
            return []
        # Over-fetch chunks: a chapter's several best-matching chunks would
        # otherwise crowd out other chapters before dedup narrows to n.
        fetch_n = min(n * 4, self._chapters.count())
        results = self._chapters.query(query_texts=[query], n_results=fetch_n)
        best_by_chapter: dict[str, Chapter] = {}
        for i, doc_id in enumerate(results["ids"][0]):
            meta = results["metadatas"][0][i]
            chapter_id = meta.get("chapter_id", doc_id)
            if chapter_id not in best_by_chapter:
                best_by_chapter[chapter_id] = Chapter(
                    id=chapter_id, title=meta.get("title", ""), prose=results["documents"][0][i]
                )
        return list(best_by_chapter.values())[:n]

    def _collections_by_kind(self) -> dict:
        return {
            "chapter": self._chapters,
            "character": self._chars,
            "world": self._world,
            "thread": self._threads,
            "secret": self._secrets,
            "theme": self._themes,
            "promise": self._promises,
            "brief": self._briefs,
            "arc": self._arcs,
            "entity": self._entities,
        }

    @staticmethod
    def _search_one_collection_sync(col, query: str, n: int) -> list[dict]:
        """Blocking count+query for a single collection -- runs inside
        asyncio.to_thread so query()'s HTTP embed call never blocks the
        event loop (reads are lock-free: no write contention to serialize)."""
        if col.count() == 0:
            return []
        return col.query(query_texts=[query], n_results=min(n, col.count()))

    async def search(self, query: str, kinds: list[str] | None = None, n: int = 8) -> list[SearchHit]:
        by_kind = self._collections_by_kind()
        wanted = list(by_kind) if kinds is None else kinds
        unknown = [k for k in wanted if k not in by_kind]
        if unknown:
            raise ValueError(f"Unknown kinds: {unknown}. Valid: {sorted(by_kind)}")
        hits: list[SearchHit] = []
        seen_chapter_ids: set[str] = set()
        for kind in wanted:
            col = by_kind[kind]
            results = await asyncio.to_thread(self._search_one_collection_sync, col, query, n)
            if not results:
                continue
            for i, doc_id in enumerate(results["ids"][0]):
                meta = results["metadatas"][0][i] or {}
                title = meta.get("title") or meta.get("name") or ""
                # Chapters are stored as multiple chunks per chapter; several
                # chunks of the same chapter can each match, so report the
                # chapter once at its best (lowest-distance, hence first
                # encountered -- chromadb returns results distance-sorted)
                # chunk's score instead of one SearchHit per chunk.
                if kind == "chapter":
                    doc_id = meta.get("chapter_id", doc_id)
                    if doc_id in seen_chapter_ids:
                        continue
                    seen_chapter_ids.add(doc_id)
                hits.append(SearchHit(kind=kind, id=doc_id, title=title,
                                      distance=results["distances"][0][i]))
        hits.sort(key=lambda h: h.distance)
        return hits[:n]
