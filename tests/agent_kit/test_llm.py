from __future__ import annotations

from agent_kit.llm import (
    CONTEXT_WINDOW_TOKENS, GRAPH_RECURSION_LIMIT, LLM_MAX_RETRIES, build_chat_model,
)


def test_recursion_limit_and_context_window_defaults():
    assert GRAPH_RECURSION_LIMIT == 100
    assert CONTEXT_WINDOW_TOKENS == 128_000


def test_max_retries_default_reaches_the_openai_client():
    """The openai client's stock policy (2 retries, sub-second backoff) gives
    up inside a multi-second 429 window from a saturated local server, and the
    resulting RateLimitError aborts a whole agent pass. The builder must stamp
    a generous request-level retry budget on the underlying client so requests
    ride out saturation instead of killing the run."""
    m = build_chat_model("m", "http://localhost:9999/v1", "k")
    assert LLM_MAX_RETRIES >= 8
    assert m.max_retries == LLM_MAX_RETRIES
    assert m.root_async_client.max_retries == LLM_MAX_RETRIES


def test_max_retries_is_overridable():
    m = build_chat_model("m", "http://localhost:9999/v1", "k", max_retries=3)
    assert m.root_async_client.max_retries == 3


def test_build_chat_model_stamps_profile_and_params():
    m = build_chat_model("test-model", "http://localhost:9999/v1", "key",
                         temperature=0.3, max_tokens=512)
    assert m.temperature == 0.3
    assert m.max_tokens == 512
    assert m.profile["max_input_tokens"] == CONTEXT_WINDOW_TOKENS
    assert m.streaming is False  # no callbacks -> streaming off


def test_callbacks_imply_streaming_and_explicit_flag_decouples():
    from langchain_core.callbacks.base import BaseCallbackHandler
    handler = BaseCallbackHandler()
    with_cb = build_chat_model("m", "http://localhost:9999/v1", "k", callbacks=[handler])
    assert with_cb.streaming is True
    decoupled = build_chat_model("m", "http://localhost:9999/v1", "k",
                                 callbacks=[handler], streaming=False)
    assert decoupled.streaming is False


def test_context_window_parameterized():
    m = build_chat_model("m", "http://localhost:9999/v1", "k",
                         context_window_tokens=32_000)
    assert m.profile["max_input_tokens"] == 32_000


def test_reasoning_content_is_recovered_from_the_raw_streamed_delta():
    """Plain ChatOpenAI silently drops non-standard streamed fields like
    reasoning_content -- _ReasoningAwareChatOpenAI must lift it back onto the
    chunk so a telemetry callback can see it via additional_kwargs."""
    from langchain_core.messages import AIMessageChunk
    m = build_chat_model("my-model", "http://localhost:1234/v1", "key")
    raw_chunk = {
        "choices": [{"index": 0, "delta": {"content": "The sea",
                                           "reasoning_content": "pondering the tide"},
                    "finish_reason": None}],
    }
    gen_chunk = m._convert_chunk_to_generation_chunk(raw_chunk, AIMessageChunk, None)
    assert gen_chunk.message.content == "The sea"
    assert gen_chunk.message.additional_kwargs["reasoning_content"] == "pondering the tide"


def test_reasoning_content_absent_leaves_chunk_unaffected():
    from langchain_core.messages import AIMessageChunk
    m = build_chat_model("my-model", "http://localhost:1234/v1", "key")
    raw_chunk = {"choices": [{"index": 0, "delta": {"content": "hi"}, "finish_reason": None}]}
    gen_chunk = m._convert_chunk_to_generation_chunk(raw_chunk, AIMessageChunk, None)
    assert "reasoning_content" not in gen_chunk.message.additional_kwargs
