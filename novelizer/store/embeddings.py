from __future__ import annotations
import chromadb
from chromadb.utils.embedding_functions import OpenAIEmbeddingFunction
from novelizer.store.models import WorldEntry, Character, Chapter


class EmbeddingStore:
    def __init__(
        self,
        path: str,
        embed_model: str = "nomic-embed-text",
        base_url: str = "http://localhost:8080/v1",
        api_key: str = "not-needed",
    ) -> None:
        self._client = chromadb.PersistentClient(path=path)
        ef = OpenAIEmbeddingFunction(api_key=api_key, model_name=embed_model, api_base=base_url)
        self._world = self._client.get_or_create_collection("world_entries", embedding_function=ef)
        self._chars = self._client.get_or_create_collection("characters", embedding_function=ef)
        self._chapters = self._client.get_or_create_collection("chapters", embedding_function=ef)

    def close(self) -> None:
        pass  # chromadb PersistentClient auto-flushes

    async def upsert_world_entry(self, entry: WorldEntry) -> None:
        text = f"{entry.title}\n{entry.body}"
        self._world.upsert(ids=[entry.id], documents=[text], metadatas=[{"title": entry.title}])

    async def upsert_character(self, char: Character) -> None:
        text = f"{char.name}\n{char.traits}\n{char.backstory}"
        self._chars.upsert(ids=[char.id], documents=[text], metadatas=[{"name": char.name}])

    async def upsert_chapter(self, chapter: Chapter) -> None:
        self._chapters.upsert(
            ids=[chapter.id],
            documents=[chapter.prose],
            metadatas=[{"title": chapter.title}],
        )

    async def delete(self, entity_id: str, collection: str) -> None:
        col = {"world_entries": self._world, "characters": self._chars, "chapters": self._chapters}[collection]
        col.delete(ids=[entity_id])

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

    async def query_chapters(self, query: str, n: int = 5) -> list[Chapter]:
        if self._chapters.count() == 0:
            return []
        results = self._chapters.query(query_texts=[query], n_results=min(n, self._chapters.count()))
        chapters = []
        for i, doc_id in enumerate(results["ids"][0]):
            meta = results["metadatas"][0][i]
            chapters.append(Chapter(id=doc_id, title=meta.get("title", ""), prose=results["documents"][0][i]))
        return chapters
