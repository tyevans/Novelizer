from __future__ import annotations
from langchain.chat_models import init_chat_model


def build_chat_model(model: str, base_url: str, api_key: str, temperature: float = 0.8):
    """Build a LangChain chat model bound to an OpenAI-compatible endpoint."""
    return init_chat_model(
        f"openai:{model}",
        base_url=base_url,
        api_key=api_key,
        temperature=temperature,
    )
