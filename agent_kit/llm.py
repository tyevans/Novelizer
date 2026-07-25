from __future__ import annotations

from langchain_openai import ChatOpenAI

# Default context window for local OpenAI-compatible endpoints. deepagents'
# create_deep_agent always attaches SummarizationMiddleware; without a model
# profile it falls back to a fixed 170k-token trigger — past a 128k window,
# so compaction would never fire before a request overflows. Stamping
# max_input_tokens switches deepagents onto its fraction-based defaults
# (trigger at 85%, keep last 10%), sized for the actual window.
CONTEXT_WINDOW_TOKENS = 128_000

# Tool-heavy passes can exceed LangGraph's default of 25; 50 and then 100 still
# tripped in practice, so give agent graphs generous headroom.
#
# This ceiling is NOT the knob for over-surveying, and must not be raised again.
# It was raised three times to accommodate runs that would not stop, and at 200
# it still killed 27% of runs -- because tripping it discards the entire run
# (GraphRecursionError is raised before `structured_response` exists), and every
# extra step is another paid LLM call. A higher ceiling therefore makes each
# failure more expensive without making it rarer. ToolCallBudgetMiddleware below
# is the real limiter; this stays only as the backstop it was meant to be.
GRAPH_RECURSION_LIMIT = 200

# Soft tool-call budget per run. 30 sits just above the measured healthy-run mean
# of 26.5 tool calls, so a normal pass never notices it, while the doomed runs
# that averaged 80.8 are landed long before the recursion backstop. The extra
# margin is the grace period between "please wrap up" and "tools withdrawn":
# ten calls is enough for a model to finish a read it had already started and
# then emit, and short enough that the tail cannot run away again.
TOOL_CALL_SOFT_BUDGET = 30
TOOL_CALL_HARD_MARGIN = 10

# The openai client's stock retry policy (2 retries, exponential backoff from
# 0.5s) gives up within ~2 seconds, but a saturated local endpoint returns 429
# for multi-second windows when several agents, embeddings, and chat share it —
# and one exhausted request aborts an entire agent pass mid-run, discarding all
# its in-flight tool work. 10 retries (capped at 8s apart, Retry-After honored)
# buys roughly a minute of patience at request level, where waiting preserves
# the pass instead of restarting it.
LLM_MAX_RETRIES = 10


class _ReasoningAwareChatOpenAI(ChatOpenAI):
    """ChatOpenAI, plus surfacing provider-specific reasoning/thinking deltas
    into additional_kwargs.

    Plain ChatOpenAI targets the official OpenAI API only: non-standard
    streamed fields like reasoning_content (what vLLM and other local
    reasoning-enabled OpenAI-compatible servers send) are silently dropped in
    _convert_delta_to_message_chunk before a callback ever sees them. This
    override re-reads the raw delta dict streamed alongside content and
    stashes reasoning_content (or the `reasoning` key some proxies use) back
    onto the chunk, so a telemetry callback's on_llm_new_token can read it
    via chunk.message.additional_kwargs."""

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
    context_window_tokens: int = CONTEXT_WINDOW_TOKENS,
    max_retries: int = LLM_MAX_RETRIES,
):
    """Build a LangChain chat model bound to an OpenAI-compatible endpoint.

    max_tokens caps generation per request: an uncapped local model can
    ramble past a proxy's request timeout and never return, which the caller
    sees as a hang.

    callbacks (telemetry handlers) imply streaming=True by default — token-
    by-token delivery is what makes on_llm_new_token fire. Pass `streaming`
    explicitly to decouple the two.
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
        max_retries=max_retries,
        profile={"max_input_tokens": context_window_tokens},
    )


def build_agent_runner(
    *, model, system_prompt: str, response_format, tools=None,
    middleware=None, backend=None, callbacks=None,
    recursion_limit: int = GRAPH_RECURSION_LIMIT,
    tool_call_soft_budget: int = TOOL_CALL_SOFT_BUDGET,
    tool_call_hard_margin: int = TOOL_CALL_HARD_MARGIN,
):
    """Build a deepagents graph satisfying the Runner protocol: the generic
    form of the per-domain runner builders. Callers pass their system
    prompt, a pydantic response_format, and their tools; the result's
    ainvoke returns a dict whose "structured_response" key carries the
    parsed response_format instance.

    The soft tool-call budget is installed here, alongside the recursion limit,
    because both are chassis policy rather than per-agent taste -- a consumer
    should not have to opt in to not-throwing-away-its-own-work. It is ordered
    FIRST, ahead of any caller middleware: at the hard stop it empties the tool
    list, and a middleware running after it would otherwise filter or re-derive
    tools the budget had already withdrawn. Pass
    `tool_call_soft_budget=0` to disable it for a caller that genuinely needs
    an unbounded pass.
    """
    from deepagents import create_deep_agent

    from agent_kit.middleware import ToolCallBudgetMiddleware

    kwargs: dict = {
        "model": model,
        "system_prompt": system_prompt,
        "response_format": response_format,
    }
    if tools is not None:
        kwargs["tools"] = list(tools)
    chain = list(middleware) if middleware is not None else []
    if tool_call_soft_budget:
        chain.insert(0, ToolCallBudgetMiddleware(
            soft_budget=tool_call_soft_budget, hard_margin=tool_call_hard_margin))
    if chain:
        kwargs["middleware"] = chain
    if backend is not None:
        kwargs["backend"] = backend
    graph = create_deep_agent(**kwargs)
    config: dict = {"recursion_limit": recursion_limit}
    if callbacks:
        config["callbacks"] = callbacks
    return graph.with_config(config)
