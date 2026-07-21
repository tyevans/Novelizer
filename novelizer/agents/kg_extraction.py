from __future__ import annotations

from novelizer.agents.schemas import KGExtractionOutput

KG_EXTRACTION_SYSTEM_PROMPT = """You extract entities and relationships from a
novel chapter's prose. Extract every named person, place, faction, organization,
item, or creature mentioned -- not just major characters, which are already
tracked elsewhere; you exist specifically to catch what would otherwise be lost.
Use short, freeform entity_type labels (e.g. "character", "location", "faction",
"item", "creature") -- there is no fixed list, pick what fits.

For relations, use short lowercase relation_type labels (e.g. "located_in",
"owns", "member_of", "friend_of"). Only extract what the prose actually states;
do not infer facts it doesn't support. If nothing is extractable, return empty
lists for both entities and relations."""


def kg_extraction_prompt(chapter_title: str, chapter_prose: str) -> str:
    return f"Chapter: {chapter_title}\n\n{chapter_prose}"


def build_kg_extraction_runner(settings, callbacks=None):
    from deepagents import create_deep_agent
    from langchain.agents.structured_output import ProviderStrategy
    from novelizer.agents.llm import build_chat_model
    # Extraction is fact-finding, not composition: run cold regardless of the
    # room's creative temperature. At higher temperature the model free-runs
    # inside JSON string fields until the token cap (same failure mode as
    # build_continuity_mining_runner's mining pass).
    model = build_chat_model(
        settings.agent_model, settings.llm_base_url, settings.llm_api_key,
        temperature=0.2, max_tokens=settings.llm_max_tokens, callbacks=callbacks,
    )
    return create_deep_agent(model=model, system_prompt=KG_EXTRACTION_SYSTEM_PROMPT, response_format=ProviderStrategy(KGExtractionOutput))
