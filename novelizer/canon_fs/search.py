from __future__ import annotations

from langchain_core.tools import tool

from novelizer.canon_fs.paths import build_path_index


def build_search_canon_tool(embedding_store, read_store):
    """Factory so the tool closes over story-scoped stores (one tool
    instance per runner, mirroring how runners close over settings)."""

    @tool
    async def search_canon(query: str, kinds: list[str] | None = None) -> str:
        """Semantic search over the whole story canon (chapters, characters,
        world entries, threads, secrets, themes). Returns the best matches
        with their canon file path and exact record id — read the file at
        the returned path for full content. kinds filters to a subset, e.g.
        ["chapter", "secret"].
        """
        try:
            hits = await embedding_store.search(query, kinds=kinds)
        except Exception as e:
            return (f"Search unavailable ({type(e).__name__}); browse the canon "
                    f"filesystem with ls/glob/grep instead.")
        if not hits:
            return "No results."
        index = build_path_index(
            chapters=await read_store.list_chapters(),
            characters=await read_store.list_characters(),
            world_entries=await read_store.list_world_entries(),
            threads=await read_store.list_threads(),
            secrets=await read_store.list_secrets(),
            themes=await read_store.list_themes(),
        )
        path_by_id = {record_id: p for p, (_, record_id) in index.items()}
        lines = [
            f"({h.kind}) {path_by_id.get(h.id, '(no file)')} — '{h.title}' [id: {h.id}]"
            for h in hits
        ]
        return "\n".join(lines)

    return search_canon
