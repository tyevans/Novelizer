import uuid
from types import SimpleNamespace
from langchain_core.messages import HumanMessage, SystemMessage
from novelizer.run_context import current_run_id, current_agent_name
from novelizer.telemetry.callbacks import TelemetryCallbackHandler, render_messages
from novelizer.telemetry.events import TelemetryEventType, TokenDelta


class FakeRecorder:
    def __init__(self):
        self.emitted = []
        self.tokens = []
        self._counts = {}

    async def emit(self, event_type, aggregate_id, payload):
        self.emitted.append((event_type, payload))

    def publish_token(self, delta):
        self.tokens.append(delta)

    def next_call_index(self, run_id):
        self._counts[run_id] = self._counts.get(run_id, 0) + 1
        return self._counts[run_id]


def _in_run(fn):
    """Run coroutine fn under an ambient novelizer run context."""
    import asyncio

    async def wrapper():
        rid = current_run_id.set("nrun-1")
        name = current_agent_name.set("author")
        try:
            return await fn()
        finally:
            current_run_id.reset(rid)
            current_agent_name.reset(name)
    return asyncio.run(wrapper())


def test_render_messages_includes_role_and_content():
    text = render_messages([[SystemMessage(content="Be brief."), HumanMessage(content="Write.")]])
    assert "[system]" in text and "Be brief." in text
    assert "[human]" in text and "Write." in text


def test_chat_model_start_emits_call_started_with_prompt_and_index():
    rec = FakeRecorder()
    h = TelemetryCallbackHandler(rec)
    lc_run = uuid.uuid4()

    async def go():
        await h.on_chat_model_start(
            {"kwargs": {"model_name": "qwen"}},
            [[HumanMessage(content="Write the next chapter.")]],
            run_id=lc_run,
        )
    _in_run(go)
    (etype, payload), = rec.emitted
    assert etype == TelemetryEventType.LLM_CALL_STARTED
    assert payload.run_id == "nrun-1" and payload.agent_name == "author"
    assert payload.call_index == 1 and payload.model == "qwen"
    assert "Write the next chapter." in payload.prompt


def test_new_token_publishes_delta_and_llm_end_reports_usage_tokens():
    rec = FakeRecorder()
    h = TelemetryCallbackHandler(rec)
    lc_run = uuid.uuid4()

    async def go():
        await h.on_chat_model_start({"kwargs": {}}, [[HumanMessage(content="x")]], run_id=lc_run)
        await h.on_llm_new_token("The ", run_id=lc_run)
        await h.on_llm_new_token("sea", run_id=lc_run)
        response = SimpleNamespace(generations=[[SimpleNamespace(
            message=SimpleNamespace(usage_metadata={"output_tokens": 42}))]])
        await h.on_llm_end(response, run_id=lc_run)
    _in_run(go)
    assert [d.text for d in rec.tokens] == ["The ", "sea"]
    assert all(isinstance(d, TokenDelta) and d.run_id == "nrun-1" for d in rec.tokens)
    etype, payload = rec.emitted[-1]
    assert etype == TelemetryEventType.LLM_CALL_FINISHED
    assert payload.output_tokens == 42
    assert payload.duration_s >= 0.0


def test_new_token_extracts_reasoning_content_from_the_chunk_as_a_thinking_delta():
    """Reasoning-capable OpenAI-compatible endpoints put reasoning text in
    additional_kwargs, not .content -- on_llm_new_token's `token` arg alone
    would silently drop it, so it must be read from the chunk explicitly."""
    rec = FakeRecorder()
    h = TelemetryCallbackHandler(rec)
    lc_run = uuid.uuid4()

    async def go():
        await h.on_chat_model_start({"kwargs": {}}, [[HumanMessage(content="x")]], run_id=lc_run)
        reasoning_chunk = SimpleNamespace(
            message=SimpleNamespace(additional_kwargs={"reasoning_content": "let me think"}))
        await h.on_llm_new_token("", chunk=reasoning_chunk, run_id=lc_run)
        answer_chunk = SimpleNamespace(message=SimpleNamespace(additional_kwargs={}))
        await h.on_llm_new_token("The sea", chunk=answer_chunk, run_id=lc_run)
    _in_run(go)
    assert [(d.text, d.kind) for d in rec.tokens] == [
        ("let me think", "thinking"), ("The sea", "text"),
    ]


def test_llm_end_without_usage_falls_back_to_streamed_chunk_count():
    rec = FakeRecorder()
    h = TelemetryCallbackHandler(rec)
    lc_run = uuid.uuid4()

    async def go():
        await h.on_chat_model_start({"kwargs": {}}, [[HumanMessage(content="x")]], run_id=lc_run)
        await h.on_llm_new_token("a", run_id=lc_run)
        await h.on_llm_new_token("b", run_id=lc_run)
        await h.on_llm_end(SimpleNamespace(generations=[[]]), run_id=lc_run)
    _in_run(go)
    assert rec.emitted[-1][1].output_tokens == 2


def test_llm_error_emits_call_failed():
    rec = FakeRecorder()
    h = TelemetryCallbackHandler(rec)
    lc_run = uuid.uuid4()

    async def go():
        await h.on_chat_model_start({"kwargs": {}}, [[HumanMessage(content="x")]], run_id=lc_run)
        await h.on_llm_error(TimeoutError("proxy timeout"), run_id=lc_run)
    _in_run(go)
    etype, payload = rec.emitted[-1]
    assert etype == TelemetryEventType.LLM_CALL_FAILED
    assert payload.error_type == "TimeoutError" and "proxy timeout" in payload.error_message


def test_tool_start_emits_call_started_with_tool_name_and_truncated_input():
    rec = FakeRecorder()
    h = TelemetryCallbackHandler(rec)
    lc_run = uuid.uuid4()

    async def go():
        await h.on_tool_start({"name": "search_web"}, "x" * 500, run_id=lc_run)
    _in_run(go)
    (etype, payload), = rec.emitted
    assert etype == TelemetryEventType.TOOL_CALL_STARTED
    assert payload.run_id == "nrun-1" and payload.agent_name == "author"
    assert payload.tool_name == "search_web"
    assert len(payload.input_summary) == 300


def test_tool_end_emits_call_finished_with_duration_and_output_size():
    rec = FakeRecorder()
    h = TelemetryCallbackHandler(rec)
    lc_run = uuid.uuid4()

    async def go():
        await h.on_tool_start({"name": "search_web"}, "query", run_id=lc_run)
        await h.on_tool_end("some output text", run_id=lc_run)
    _in_run(go)
    etype, payload = rec.emitted[-1]
    assert etype == TelemetryEventType.TOOL_CALL_FINISHED
    assert payload.tool_name == "search_web"
    assert payload.duration_s >= 0.0
    assert payload.output_chars == len("some output text")


def test_tool_end_carries_input_summary_and_a_truncated_output_summary():
    rec = FakeRecorder()
    h = TelemetryCallbackHandler(rec)
    lc_run = uuid.uuid4()

    async def run():
        await h.on_tool_start({"name": "search_web"}, "dragons", run_id=lc_run)
        await h.on_tool_end("x" * 500, run_id=lc_run)

    _in_run(run)
    et, payload = rec.emitted[-1]
    assert et == TelemetryEventType.TOOL_CALL_FINISHED
    assert payload.input_summary == "dragons"
    assert len(payload.output_summary) <= 300


def test_tool_error_carries_input_summary():
    rec = FakeRecorder()
    h = TelemetryCallbackHandler(rec)
    lc_run = uuid.uuid4()

    async def run():
        await h.on_tool_start({"name": "search_web"}, "dragons", run_id=lc_run)
        await h.on_tool_error(ValueError("bad"), run_id=lc_run)

    _in_run(run)
    et, payload = rec.emitted[-1]
    assert et == TelemetryEventType.TOOL_CALL_FAILED
    assert payload.input_summary == "dragons"


def test_tool_error_emits_call_failed():
    rec = FakeRecorder()
    h = TelemetryCallbackHandler(rec)
    lc_run = uuid.uuid4()

    async def go():
        await h.on_tool_start({"name": "search_web"}, "query", run_id=lc_run)
        await h.on_tool_error(ValueError("bad input"), run_id=lc_run)
    _in_run(go)
    etype, payload = rec.emitted[-1]
    assert etype == TelemetryEventType.TOOL_CALL_FAILED
    assert payload.tool_name == "search_web"
    assert payload.error_type == "ValueError" and "bad input" in payload.error_message


def test_tool_end_and_error_with_unknown_run_id_are_no_ops():
    rec = FakeRecorder()
    h = TelemetryCallbackHandler(rec)
    lc_run = uuid.uuid4()

    async def go():
        await h.on_tool_end("output", run_id=lc_run)
        await h.on_tool_error(ValueError("x"), run_id=lc_run)
    _in_run(go)
    assert rec.emitted == []


def test_tool_start_without_metadata_has_empty_delegate():
    rec = FakeRecorder()
    h = TelemetryCallbackHandler(rec)
    lc_run = uuid.uuid4()

    async def go():
        await h.on_tool_start({"name": "read_file"}, "path", run_id=lc_run)
    _in_run(go)
    (etype, payload), = rec.emitted
    assert etype == TelemetryEventType.TOOL_CALL_STARTED
    assert payload.delegate == ""


def test_tool_start_with_lc_agent_name_metadata_stamps_delegate():
    rec = FakeRecorder()
    h = TelemetryCallbackHandler(rec)
    lc_run = uuid.uuid4()

    async def go():
        await h.on_tool_start(
            {"name": "read_file"}, "path", run_id=lc_run,
            metadata={"lc_agent_name": "researcher"},
        )
    _in_run(go)
    (etype, payload), = rec.emitted
    assert payload.delegate == "researcher"
    assert payload.agent_name == "author"


def test_tool_end_carries_the_same_delegate_as_tool_start():
    rec = FakeRecorder()
    h = TelemetryCallbackHandler(rec)
    lc_run = uuid.uuid4()

    async def go():
        await h.on_tool_start(
            {"name": "read_file"}, "path", run_id=lc_run,
            metadata={"lc_agent_name": "researcher"},
        )
        await h.on_tool_end("output", run_id=lc_run)
    _in_run(go)
    etype, payload = rec.emitted[-1]
    assert etype == TelemetryEventType.TOOL_CALL_FINISHED
    assert payload.delegate == "researcher"


def test_tool_error_carries_the_same_delegate_as_tool_start():
    rec = FakeRecorder()
    h = TelemetryCallbackHandler(rec)
    lc_run = uuid.uuid4()

    async def go():
        await h.on_tool_start(
            {"name": "read_file"}, "path", run_id=lc_run,
            metadata={"lc_agent_name": "researcher"},
        )
        await h.on_tool_error(ValueError("bad input"), run_id=lc_run)
    _in_run(go)
    etype, payload = rec.emitted[-1]
    assert etype == TelemetryEventType.TOOL_CALL_FAILED
    assert payload.delegate == "researcher"


def test_llm_start_with_lc_agent_name_metadata_stamps_delegate():
    rec = FakeRecorder()
    h = TelemetryCallbackHandler(rec)
    lc_run = uuid.uuid4()

    async def go():
        await h.on_chat_model_start(
            {"kwargs": {"model_name": "qwen"}},
            [[HumanMessage(content="Write.")]],
            run_id=lc_run,
            metadata={"lc_agent_name": "researcher"},
        )
    _in_run(go)
    (etype, payload), = rec.emitted
    assert etype == TelemetryEventType.LLM_CALL_STARTED
    assert payload.delegate == "researcher"


def test_llm_end_and_error_carry_the_same_delegate_as_llm_start():
    rec = FakeRecorder()
    h = TelemetryCallbackHandler(rec)
    lc_run = uuid.uuid4()

    async def go():
        await h.on_chat_model_start(
            {"kwargs": {}}, [[HumanMessage(content="x")]], run_id=lc_run,
            metadata={"lc_agent_name": "researcher"},
        )
        response = SimpleNamespace(generations=[[SimpleNamespace(
            message=SimpleNamespace(usage_metadata={"output_tokens": 1}))]])
        await h.on_llm_end(response, run_id=lc_run)

        lc_run2 = uuid.uuid4()
        await h.on_chat_model_start(
            {"kwargs": {}}, [[HumanMessage(content="x")]], run_id=lc_run2,
            metadata={"lc_agent_name": "researcher"},
        )
        await h.on_llm_error(TimeoutError("timeout"), run_id=lc_run2)
    _in_run(go)
    finished = [p for e, p in rec.emitted if e == TelemetryEventType.LLM_CALL_FINISHED][0]
    failed = [p for e, p in rec.emitted if e == TelemetryEventType.LLM_CALL_FAILED][0]
    assert finished.delegate == "researcher"
    assert failed.delegate == "researcher"
