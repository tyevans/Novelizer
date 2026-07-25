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

from agent_kit import build_light_model
from deepagents.backends.utils import file_data_to_string
from langchain_core.messages import HumanMessage

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


_PROMPT = """You are answering a question about a novel's canon using ONLY the \
excerpts below.

The agent searched for: {query}
They are asking because: {purpose}

EXCERPTS
{excerpts}

Write at most 120 words answering what canon says, as it bears on why they are \
asking. Rules:
- Assert nothing that is not in the excerpts above. No inference beyond what \
the text states.
- If the excerpts do not answer the purpose, say so plainly in one sentence. \
That is a useful answer, not a failure.
- Refer to records by their titles and ids as shown.
- Plain prose. No markdown, no headings, no bullet list.
"""


async def summarize(query, purpose, excerpts, settings, callbacks=None) -> str:
    """A short grounded synthesis of `excerpts`, or "" if anything goes wrong.

    Never raises. The caller treats "" as "send the hit lines alone", so a
    summarizer outage costs the agent a nicety and nothing else.
    """
    if not excerpts:
        return ""
    prompt = _PROMPT.format(
        query=query, purpose=purpose, excerpts="\n\n".join(excerpts))
    try:
        # Light path: an extractive synthesis over bodies the caller already
        # retrieved, on a hot path, with a hard word budget -- cold, capped,
        # and no reason to think out loud first.
        model = build_light_model(
            settings.resolved_light_model, settings.llm_base_url, settings.llm_api_key,
            max_tokens=min(SUMMARY_MAX_TOKENS, settings.llm_max_tokens),
            callbacks=callbacks, reasoning=settings.light_reasoning,
        )
        response = await model.ainvoke([HumanMessage(content=prompt)])
    except Exception:
        # Debug, not warning: a degraded summary is invisible to the agent by
        # design, and this can fire on every search during an outage.
        logger.debug("search summary: model call failed", exc_info=True)
        return ""
    return str(response.content).strip()
