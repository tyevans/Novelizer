from __future__ import annotations

from agent_kit.llm import CONTEXT_WINDOW_TOKENS, GRAPH_RECURSION_LIMIT, build_chat_model


def test_recursion_limit_and_context_window_defaults():
    assert GRAPH_RECURSION_LIMIT == 100
    assert CONTEXT_WINDOW_TOKENS == 128_000


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
