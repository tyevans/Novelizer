"""The LLM pass behind search_canon's CONTEXT block.

search.py answers "what matched". This module answers "what does canon
actually say about it, given why you asked" -- one bounded model call over the
bodies of the top hits.

Everything here is best-effort by construction: `summarize` returns "" on any
failure, and search.py treats "" as "just send the hit lines". A search that
found the right records must never fail because the synthesis did.
"""
from __future__ import annotations

import logging

from deepagents.backends.utils import file_data_to_string

from novelizer.canon_fs.reads import TRUNCATION_MARKER

logger = logging.getLogger("novelizer.canon_fs.search_summary")

# How many hits get their bodies read. Five is enough to answer most "does
# canon say X" questions and bounded enough to stay cheap on a hot path.
SUMMARY_SOURCE_CAP = 5
# Per-file read window. A long chapter would otherwise blow the very context
# this feature exists to conserve.
SUMMARY_BODY_LINES = 120
# Generation cap. The prompt asks for ~120 words; this is the hard stop.
SUMMARY_MAX_TOKENS = 400


def _body_text(result) -> str:
    """ReadResult -> plain text, with sliced_read's truncation notice removed.

    That notice is addressed to an agent ("call read_file again with
    offset=..."), not to a summarizer. Passing it through invites the model to
    repeat an instruction aimed at the wrong party.
    """
    if result is None or getattr(result, "error", None):
        return ""
    text = file_data_to_string(result.file_data)
    marker = text.find(TRUNCATION_MARKER)
    if marker != -1:
        # Cut back to the start of the line the marker sits on.
        text = text[:text.rfind("\n", 0, marker) + 1]
    return text.strip()


async def gather_excerpts(hits, backend, path_by_id, entity_lines) -> list[str]:
    """Up to SUMMARY_SOURCE_CAP labelled excerpt blocks for the top hits.

    Entity hits carry their content inline already; fileless kinds (arc,
    brief, promise) contribute a title only. A read failure on one hit drops
    that hit and keeps the rest -- a partial excerpt set still summarizes
    usefully.
    """
    blocks: list[str] = []
    for hit in hits[:SUMMARY_SOURCE_CAP]:
        header = f"--- ({hit.kind}) '{hit.title}' [id: {hit.id}]"
        if hit.kind == "entity":
            line = entity_lines.get(hit.id, "")
            blocks.append(f"{header}\n{line}".strip())
            continue
        path = path_by_id.get(hit.id)
        if not path:
            # arc / brief / promise: no readable file. The title is still a
            # signal worth showing the summarizer.
            blocks.append(header)
            continue
        try:
            result = await backend.aread(path, limit=SUMMARY_BODY_LINES)
        except Exception:
            logger.debug("search summary: read failed for %s", path, exc_info=True)
            continue
        body = _body_text(result)
        if not body:
            continue
        blocks.append(f"{header}\n{body}")
    return blocks
