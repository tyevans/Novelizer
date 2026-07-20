from __future__ import annotations
from langchain.chat_models import init_chat_model


def build_chat_model(
    model: str, base_url: str, api_key: str, temperature: float = 0.8,
    max_tokens: int | None = None, callbacks=None, streaming=None,
):
    """Build a LangChain chat model bound to an OpenAI-compatible endpoint.

    max_tokens caps generation per request: an uncapped local model (especially
    with server-side reasoning enabled) can ramble past a proxy's request
    timeout and never return, which the caller sees as a hang.

    callbacks (telemetry handlers) imply streaming=True by default — token-by-
    token delivery is what makes on_llm_new_token fire for the live Engine Room
    view. Pass `streaming` explicitly to decouple the two -- e.g. builders that
    bind callbacks at graph scope (via with_config) instead of on the model
    itself still need streaming=True to get token deltas.
    """
    if streaming is None:
        streaming = callbacks is not None
    return init_chat_model(
        f"openai:{model}",
        base_url=base_url,
        api_key=api_key,
        temperature=temperature,
        max_tokens=max_tokens,
        callbacks=callbacks,
        streaming=streaming,
    )
