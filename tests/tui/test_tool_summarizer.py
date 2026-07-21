import asyncio
import pytest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch
from novelizer.tui.tool_summarizer import summarize_tool_call


def _settings():
    return SimpleNamespace(agent_model="m", llm_base_url="http://x", llm_api_key="k")


def test_summarize_tool_call_builds_a_result_prompt_and_returns_stripped_content():
    fake_model = SimpleNamespace(ainvoke=AsyncMock(
        return_value=SimpleNamespace(content=" found three matching entries \n")))
    with patch("novelizer.tui.tool_summarizer.build_chat_model", return_value=fake_model) as build:
        result = asyncio.run(summarize_tool_call(
            _settings(), "search_web", "dragons", "3 results found", ""))
    assert result == "found three matching entries"
    build.assert_called_once()
    _, kwargs = build.call_args
    assert kwargs.get("max_tokens", None) is not None  # capped, stays cheap


def test_summarize_tool_call_uses_the_error_line_when_the_call_failed():
    captured = {}

    async def fake_ainvoke(messages):
        captured["prompt"] = messages[0].content
        return SimpleNamespace(content="failed to reach the search API")

    fake_model = SimpleNamespace(ainvoke=fake_ainvoke)
    with patch("novelizer.tui.tool_summarizer.build_chat_model", return_value=fake_model):
        result = asyncio.run(summarize_tool_call(
            _settings(), "search_web", "dragons", "", "TimeoutError: proxy"))
    assert result == "failed to reach the search API"
    assert "TimeoutError: proxy" in captured["prompt"]
    assert "dragons" in captured["prompt"]


def test_summarize_tool_call_propagates_llm_errors():
    """Verify that exceptions from model.ainvoke() propagate to the caller."""
    fake_model = SimpleNamespace(ainvoke=AsyncMock(
        side_effect=RuntimeError("LLM unavailable")))
    with patch("novelizer.tui.tool_summarizer.build_chat_model", return_value=fake_model):
        with pytest.raises(RuntimeError, match="LLM unavailable"):
            asyncio.run(summarize_tool_call(
                _settings(), "search_web", "test", "result", ""))
