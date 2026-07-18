from __future__ import annotations
from langchain.chat_models import init_chat_model


def build_chat_model(model: str, base_url: str, api_key: str, temperature: float = 0.8, max_tokens: int = 4096):
    """Build a LangChain chat model bound to an OpenAI-compatible endpoint.

    max_tokens must stay finite: local models at nonzero temperature can
    otherwise free-run (observed live: 42k tokens decoded in one stream).
    4096 comfortably fits a chapter draft plus structured-output overhead.
    """
    return init_chat_model(
        f"openai:{model}",
        base_url=base_url,
        api_key=api_key,
        temperature=temperature,
        max_tokens=max_tokens,
    )
