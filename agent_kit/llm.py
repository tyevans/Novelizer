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

# Default generation cap for the light path. Light calls are labels, one-line
# summaries, and short syntheses -- work whose correct output is short. The cap
# is deliberately far below llm_max_tokens: a light call that wants thousands of
# tokens is not a light call, and the cap failing loudly is better than a
# "cheap" pass quietly costing as much as a full one.
LIGHT_MAX_TOKENS = 512

# The chat-template variable that reasoning-capable templates read to decide
# whether to open a thinking block. llama-server's `--reasoning on|off` sets
# exactly this key in default_template_kwargs, and accepts the same key per
# request via chat_template_kwargs; vLLM follows the same convention.
#
# This is a *template* variable, not a sampler parameter: it only bites if the
# loaded model's chat template actually branches on it (Qwen3-style). Against a
# template that ignores it the flag is inert -- harmless, but not a guarantee,
# so treat "reasoning off" as a request rather than an enforcement.
THINKING_TEMPLATE_KEY = "enable_thinking"


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


def _with_thinking(extra_body: dict | None, reasoning: bool | None) -> dict | None:
    """Merge the thinking flag into extra_body without disturbing it.

    reasoning=None leaves the body untouched, which is what every caller that
    has no opinion gets: the served model keeps doing whatever it does today.
    Both the outer dict and the nested chat_template_kwargs are copied rather
    than mutated -- callers pass literals and shared dicts alike.
    """
    if reasoning is None:
        return extra_body
    merged = dict(extra_body or {})
    template_kwargs = dict(merged.get("chat_template_kwargs") or {})
    template_kwargs[THINKING_TEMPLATE_KEY] = bool(reasoning)
    merged["chat_template_kwargs"] = template_kwargs
    return merged


def build_chat_model(
    model: str, base_url: str, api_key: str, temperature: float = 0.8,
    max_tokens: int | None = None, callbacks=None, streaming=None,
    context_window_tokens: int = CONTEXT_WINDOW_TOKENS,
    max_retries: int = LLM_MAX_RETRIES,
    reasoning: bool | None = None, extra_body: dict | None = None,
):
    """Build a LangChain chat model bound to an OpenAI-compatible endpoint.

    max_tokens caps generation per request: an uncapped local model can
    ramble past a proxy's request timeout and never return, which the caller
    sees as a hang.

    callbacks (telemetry handlers) imply streaming=True by default — token-
    by-token delivery is what makes on_llm_new_token fire. Pass `streaming`
    explicitly to decouple the two.

    reasoning asks the server to enable (True) or suppress (False) the model's
    thinking block; None — the default — sends nothing and inherits whatever
    the endpoint was started with. See THINKING_TEMPLATE_KEY for why this is a
    request rather than a guarantee.
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
        extra_body=_with_thinking(extra_body, reasoning),
        profile={"max_input_tokens": context_window_tokens},
    )


def build_light_model(
    model: str, base_url: str, api_key: str, *,
    max_tokens: int = LIGHT_MAX_TOKENS, callbacks=None,
    reasoning: bool | None = False, **kwargs,
):
    """The one definition of a cheap call: cold, capped, and not thinking.

    Labelling, one-line summaries, and short extractive syntheses are the same
    kind of work — deterministic formatting over text someone else already
    wrote — and were each hand-tuned to their own temperature and token cap
    before this existed. Consolidating them means the reasoning flag (and the
    next such lever) lands in one place rather than five.

    reasoning defaults to False -- suppressing the thinking block is the point
    of the tier -- but stays overridable, because whether a chat template
    honors the flag is a property of the served model, not of this function.

    Everything else, notably the retry budget, is inherited from
    build_chat_model: cheap must not mean fragile, since a light pass dropped
    to a 429 loses real work just like an expensive one.
    """
    return build_chat_model(
        model, base_url, api_key, temperature=0.0, max_tokens=max_tokens,
        callbacks=callbacks, reasoning=reasoning, **kwargs,
    )


class _SimpleRunner:
    """A Runner that is one structured model call — no graph, no tool loop.

    Satisfies the same protocol as a deepagents graph (`ainvoke(inputs) ->
    dict` carrying "structured_response"), so a toolless agent switches paths
    without touching its poll/work/commit code.

    A graph exists to let a model choose tools and iterate. An agent with no
    tools has nothing to choose and nowhere to iterate, so the graph around it
    is pure overhead: middleware, a recursion limit, and a state machine
    wrapped around a single request.
    """

    def __init__(self, model, system_prompt: str, response_format) -> None:
        self._raw_model = model
        self._model = model.with_structured_output(response_format)
        self._system_prompt = system_prompt
        self.response_format = response_format

    # A deepagents graph hides its model behind layers of state; this runner has
    # exactly one, so it can answer what it was built with. Callers use these to
    # assert the cheap path really is cheap -- otherwise a light agent silently
    # reverting to the big model is invisible until the bill arrives.
    @property
    def model_name(self) -> str:
        return getattr(self._raw_model, "model_name", None) or self._raw_model.model

    @property
    def max_tokens(self) -> int | None:
        return self._raw_model.max_tokens

    @property
    def thinking_enabled(self) -> bool | None:
        """What this runner asked the server to do about reasoning, or None if
        it expressed no preference."""
        template_kwargs = (self._raw_model.extra_body or {}).get("chat_template_kwargs") or {}
        return template_kwargs.get(THINKING_TEMPLATE_KEY)

    async def ainvoke(self, inputs: dict) -> dict:
        messages = [{"role": "system", "content": self._system_prompt},
                    *inputs.get("messages", [])]
        # A refusal or unparseable reply surfaces as structured_response=None,
        # the same shape callers already handle when a graph fails to fill one.
        # Raising something different here would make the light path need its
        # own error handling at every call site.
        result = await self._model.ainvoke(messages)
        return {"structured_response": result}


def build_simple_runner(*, model, system_prompt: str, response_format):
    """Graph-free counterpart to build_agent_runner, for toolless agents."""
    return _SimpleRunner(model, system_prompt, response_format)


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
