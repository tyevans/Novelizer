from __future__ import annotations
from langchain_openai import ChatOpenAI

# The local OpenAI-compatible endpoint we run agents against serves a 128k
# context window. deepagents' create_deep_agent always attaches its own
# SummarizationMiddleware, but without a model profile it falls back to a
# fixed 170k-token trigger -- past our actual window, so compaction never
# fires before a request overflows. Stamping max_input_tokens here switches
# deepagents onto its fraction-based defaults (trigger at 85%, keep last
# 10%), sized correctly for this window.
CONTEXT_WINDOW_TOKENS = 128_000


class _ReasoningAwareChatOpenAI(ChatOpenAI):
    """ChatOpenAI, plus surfacing provider-specific reasoning/thinking deltas
    into additional_kwargs.

    langchain_openai's own module docstring is explicit that plain ChatOpenAI
    targets the official OpenAI API only: non-standard streamed fields like
    reasoning_content (what vLLM and other local reasoning-enabled OpenAI-
    compatible servers send) are "not extracted or preserved" -- they're
    silently dropped in _convert_delta_to_message_chunk before a callback
    ever sees them. This override re-reads the raw delta dict streamed
    alongside content and stashes reasoning_content (or the OpenAI-native
    `reasoning` key some proxies use instead) back onto the chunk, so
    TelemetryCallbackHandler.on_llm_new_token can read it via
    chunk.message.additional_kwargs."""

    def _convert_chunk_to_generation_chunk(self, chunk, default_chunk_class, base_generation_info):
        generation_chunk = super()._convert_chunk_to_generation_chunk(
            chunk, default_chunk_class, base_generation_info)
        if generation_chunk is None:
            return generation_chunk
        choices = chunk.get("choices") or chunk.get("chunk", {}).get("choices", [])
        if not choices:
            return generation_chunk
        delta = choices[0].get("delta") or {}
        reasoning = delta.get("reasoning_content") or delta.get("reasoning")
        if reasoning:
            generation_chunk.message.additional_kwargs["reasoning_content"] = reasoning
        return generation_chunk


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
    return _ReasoningAwareChatOpenAI(
        model=model,
        base_url=base_url,
        api_key=api_key,
        temperature=temperature,
        max_tokens=max_tokens,
        callbacks=callbacks,
        streaming=streaming,
        profile={"max_input_tokens": CONTEXT_WINDOW_TOKENS},
    )
