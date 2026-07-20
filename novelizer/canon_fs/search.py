from __future__ import annotations

from langchain_core.tools import tool

from novelizer.canon_fs.paths import build_path_index

# A long novel can match hundreds of records; an uncapped list would crowd out
# the prose the agent is actually here to read.
SEARCH_RESULT_CAP = 20


def build_search_canon_tool(embedding_store, read_store):
    """Factory so the tool closes over story-scoped stores (one tool
    instance per runner, mirroring how runners close over settings)."""

    @tool
    async def search_canon(query: str, kinds: list[str] | None = None) -> str:
        """Search the story canon by MEANING — chapters, characters, world
        entries, threads, secrets, themes, promises, chapter briefs, arcs. Use
        this when you don't know the exact words: "where was the locket last
        seen", "scenes about betrayal". For an exact name, slug, or quoted
        phrase use grep instead; it is faster and exact.

        Returns one line per hit: (kind) <canon file path> — '<title>' [id: <id>].
        Read the file at that path for the full content, and cite the id
        exactly as shown. Results are ranked and capped; if what you need
        isn't here, narrow the query or filter by kind, e.g.
        kinds=["chapter", "secret"].

        Path convention for the M7/M8-deferred kinds, which have no dedicated
        canon_fs file: promises point at the shared outline ledger
        (/outline/ledger.md); open briefs point at the briefs directory
        (/outline/briefs/) since briefs are not individually slugged into
        canon_fs; arcs have no backing file at all, so their hit line carries
        "(no file — cite id)" in the path slot instead.

        Example: search_canon("the debt Mateo owes", kinds=["thread", "secret"])
        """
        try:
            hits = await embedding_store.search(query, kinds=kinds)
        except ValueError as e:
            # The store's message names the valid kinds -- corrective
            # feedback the agent can act on directly, rather than a dead end.
            return str(e)
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
        FALLBACK_PATH_BY_KIND = {
            "promise": "/outline/ledger.md",
            "brief": "/outline/briefs/",
            "arc": "(no file — cite id)",
        }
        lines = [
            f"({h.kind}) "
            f"{path_by_id.get(h.id) or FALLBACK_PATH_BY_KIND.get(h.kind, '(no file)')} "
            f"— '{h.title}' [id: {h.id}]"
            for h in hits[:SEARCH_RESULT_CAP]
        ]
        if len(hits) > SEARCH_RESULT_CAP:
            # Announce the truncation: a silently-cut list reads as exhaustive,
            # and the agent stops looking for what it never saw.
            lines.append(
                f"... {len(hits) - SEARCH_RESULT_CAP} more results — narrow your query "
                f"or filter by kind."
            )
        return "\n".join(lines)

    return search_canon
