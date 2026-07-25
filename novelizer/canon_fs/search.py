from __future__ import annotations

from langchain_core.tools import tool

from novelizer.canon_fs.paths import build_path_index
from novelizer.store.embeddings import EmptyIndexError

# A long novel can match hundreds of records; an uncapped list would crowd out
# the prose the agent is actually here to read.
SEARCH_RESULT_CAP = 20


def build_search_canon_tool(embedding_store, read_store, kg_store):
    """Factory so the tool closes over story-scoped stores (one tool
    instance per runner, mirroring how runners close over settings)."""

    @tool
    async def search_canon(query: str, kinds: list[str] | None = None) -> str:
        """Search the story canon by MEANING — chapters, characters, world
        entries, threads, secrets, themes, promises, chapter briefs, arcs,
        and knowledge-graph entities (minor places, factions, items, and
        relations the other kinds don't formalize). Use this when you don't
        know the exact words: "where was the locket last seen", "scenes
        about betrayal", "who frequents the Salted Gull". For an exact
        name, slug, or quoted phrase use grep instead; it is faster and
        exact.

        Returns one line per hit: (kind) <canon file path> — '<title>' [id: <id>].
        Read the file at that path for the full content, and cite the id
        exactly as shown. Entity hits have no file to read — the line
        already carries their description and relations inline. Results
        are ranked and capped; if what you need isn't here, narrow the
        query or filter by kind, e.g. kinds=["thread", "secret"].

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
        except EmptyIndexError:
            # An empty index is an UNAVAILABLE search, not a miss. Answering
            # "No results." tells the agent canon is silent on the topic, and
            # its only recourse -- rephrase and retry -- reissues a call that
            # cannot ever succeed (observed in production: 690 calls, 690
            # misses, one run looping 62 times until it was searching its own
            # context). Route it to the same browse-instead fallback a live
            # outage gets, and name the cause so the operator sees it too.
            return ("Search unavailable (semantic index is empty — nothing has "
                    "been indexed yet); browse the canon filesystem with "
                    "ls/glob/grep instead.")
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
        lines = []
        for h in hits[:SEARCH_RESULT_CAP]:
            if h.kind == "entity":
                lines.append(await _format_entity_hit(h, kg_store))
                continue
            lines.append(
                f"({h.kind}) "
                f"{path_by_id.get(h.id) or FALLBACK_PATH_BY_KIND.get(h.kind, '(no file)')} "
                f"— '{h.title}' [id: {h.id}]"
            )
        if len(hits) > SEARCH_RESULT_CAP:
            # Announce the truncation: a silently-cut list reads as exhaustive,
            # and the agent stops looking for what it never saw.
            lines.append(
                f"... {len(hits) - SEARCH_RESULT_CAP} more results — narrow your query "
                f"or filter by kind."
            )
        return "\n".join(lines)

    return search_canon


async def _format_entity_hit(hit, kg_store) -> str:
    entity_id = int(hit.id)
    entity = await kg_store.get_entity(entity_id)
    if entity is None:
        return f"(entity) (no file — cite id) — '{hit.title}' [id: {hit.id}]"
    relations = await kg_store.entity_relations(entity_id)
    rel_text = ", ".join(f"{r['relation_type']} {r['other_name']}" for r in relations)
    detail = f"{entity['description']}" if entity["description"] else ""
    suffix = f" Relations: {rel_text}." if rel_text else ""
    return (
        f"(entity) [{entity['entity_type']}] {entity['name']} [id: {hit.id}] "
        f"— {detail}{suffix}"
    )
