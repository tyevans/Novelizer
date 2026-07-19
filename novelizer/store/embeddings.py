from __future__ import annotations
import chromadb
from chromadb.utils.embedding_functions import OpenAIEmbeddingFunction
from novelizer.store.models import WorldEntry, Character, Chapter, ThemeRecord


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

    async def upsert_theme(self, theme: ThemeRecord) -> None:
        self._themes.upsert(ids=[theme.id], documents=[theme.title], metadatas=[{"title": theme.title}])

    async def delete(self, entity_id: str, collection: str) -> None:
        col = {
            "world_entries": self._world,
            "characters": self._chars,
            "chapters": self._chapters,
            "themes": self._themes,
        }[collection]
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
        results = self._chapters.query(query_texts=[query], n_results=min(n, self._chapters.count()))
        chapters = []
        for i, doc_id in enumerate(results["ids"][0]):
            meta = results["metadatas"][0][i]
            chapters.append(Chapter(id=doc_id, title=meta.get("title", ""), prose=results["documents"][0][i]))
        return chapters
