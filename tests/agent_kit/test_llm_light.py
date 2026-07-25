"""The light path: reasoning suppression, and a graph-free runner.

Two independent levers, tested apart because they are useful apart. A caller
may want a cheap single call from a reasoning model, or a full deepagents
graph from a model told not to think.
"""
from __future__ import annotations

import pytest

from agent_kit.llm import (
    LIGHT_MAX_TOKENS, build_chat_model, build_light_model, build_simple_runner,
)


# --- reasoning suppression ------------------------------------------------

def test_reasoning_defaults_to_untouched():
    """Silence is the current behavior: a builder that does not ask about
    reasoning must send no template kwargs at all, so existing agents keep
    whatever the served model does today."""
    m = build_chat_model("m", "http://localhost:9999/v1", "k")
    assert "chat_template_kwargs" not in (m.extra_body or {})


def test_reasoning_false_disables_thinking_in_the_chat_template():
    """llama-server reads enable_thinking out of chat_template_kwargs on the
    request body; `--reasoning off` is the same lever applied server-wide
    (it sets default_template_kwargs["enable_thinking"]="false")."""
    m = build_chat_model("m", "http://localhost:9999/v1", "k", reasoning=False)
    assert m.extra_body["chat_template_kwargs"] == {"enable_thinking": False}


def test_reasoning_true_is_explicit_rather_than_absent():
    """Asking for thinking must say so on the wire: a model whose template
    defaults to no-thinking should still think when a caller demands it."""
    m = build_chat_model("m", "http://localhost:9999/v1", "k", reasoning=True)
    assert m.extra_body["chat_template_kwargs"] == {"enable_thinking": True}


def test_reasoning_flag_preserves_caller_extra_body():
    """extra_body is a shared channel -- merging must not clobber a key the
    caller set for some unrelated provider feature."""
    m = build_chat_model("m", "http://localhost:9999/v1", "k", reasoning=False,
                         extra_body={"repeat_penalty": 1.1})
    extra = m.extra_body
    assert extra["repeat_penalty"] == 1.1
    assert extra["chat_template_kwargs"] == {"enable_thinking": False}


def test_reasoning_flag_preserves_caller_template_kwargs():
    m = build_chat_model(
        "m", "http://localhost:9999/v1", "k", reasoning=False,
        extra_body={"chat_template_kwargs": {"custom": "x"}},
    )
    tmpl = m.extra_body["chat_template_kwargs"]
    assert tmpl == {"custom": "x", "enable_thinking": False}


# --- the light model ------------------------------------------------------

def test_build_light_model_is_cold_capped_and_not_thinking():
    """One definition of 'cheap call', replacing five hand-picked copies of
    temperature=0.0 plus a magic token cap."""
    m = build_light_model("m", "http://localhost:9999/v1", "k")
    assert m.temperature == 0.0
    assert m.max_tokens == LIGHT_MAX_TOKENS
    assert m.extra_body["chat_template_kwargs"] == {"enable_thinking": False}


def test_build_light_model_cap_is_overridable_per_caller():
    """A one-line tool summary and a 120-word canon synthesis are both light
    calls; they do not share a token budget."""
    m = build_light_model("m", "http://localhost:9999/v1", "k", max_tokens=40)
    assert m.max_tokens == 40


def test_build_light_model_keeps_the_retry_budget():
    """Cheap must not mean fragile: a light call rides out a 429 like any
    other, or a saturated endpoint drops flag labels on the floor."""
    from agent_kit.llm import LLM_MAX_RETRIES
    m = build_light_model("m", "http://localhost:9999/v1", "k")
    assert m.root_async_client.max_retries == LLM_MAX_RETRIES


# --- the graph-free runner ------------------------------------------------

class _Out:
    def __init__(self, title: str) -> None:
        self.title = title


class _FakeStructuredModel:
    def __init__(self, result):
        self._result = result
        self.received = None

    async def ainvoke(self, messages, **kwargs):
        self.received = messages
        return self._result


class _FakeModel:
    def __init__(self, result):
        self.structured = _FakeStructuredModel(result)
        self.schema = None

    def with_structured_output(self, schema):
        self.schema = schema
        return self.structured


@pytest.mark.asyncio
async def test_simple_runner_satisfies_the_runner_protocol():
    """Drop-in for a deepagents graph: same inputs dict, same
    structured_response key -- so an agent switches path without touching
    its poll/work/commit code."""
    model = _FakeModel(_Out("A Title"))
    runner = build_simple_runner(model=model, system_prompt="label it",
                                 response_format=_Out)
    result = await runner.ainvoke({"messages": [{"role": "user", "content": "hi"}]})
    assert isinstance(result, dict)
    assert result["structured_response"].title == "A Title"
    assert model.schema is _Out


@pytest.mark.asyncio
async def test_simple_runner_prepends_the_system_prompt():
    """No graph means no middleware to inject the prompt; the runner owns it."""
    model = _FakeModel(_Out("t"))
    runner = build_simple_runner(model=model, system_prompt="you label flags",
                                 response_format=_Out)
    await runner.ainvoke({"messages": [{"role": "user", "content": "flag body"}]})
    sent = model.structured.received
    assert sent[0]["role"] == "system"
    assert sent[0]["content"] == "you label flags"
    assert sent[-1]["content"] == "flag body"


@pytest.mark.asyncio
async def test_simple_runner_returns_none_structured_response_on_refusal():
    """A model that returns nothing parseable must surface as a missing
    structured_response -- the same shape callers already handle when a graph
    fails to fill one -- rather than raising a different error per path."""
    model = _FakeModel(None)
    runner = build_simple_runner(model=model, system_prompt="p", response_format=_Out)
    result = await runner.ainvoke({"messages": [{"role": "user", "content": "x"}]})
    assert result["structured_response"] is None


@pytest.mark.asyncio
async def test_simple_runner_makes_exactly_one_model_call():
    """The whole point: no tool loop, no recursion limit, one request."""
    calls = 0
    out = _Out("t")

    class _Counting(_FakeModel):
        async def _noop(self):  # pragma: no cover - shape only
            ...

    model = _Counting(out)
    original = model.structured.ainvoke

    async def counted(messages, **kwargs):
        nonlocal calls
        calls += 1
        return await original(messages, **kwargs)

    model.structured.ainvoke = counted
    runner = build_simple_runner(model=model, system_prompt="p", response_format=_Out)
    await runner.ainvoke({"messages": [{"role": "user", "content": "x"}]})
    assert calls == 1
