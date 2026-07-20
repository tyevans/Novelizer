"""Fix 1 (CRITICAL) integration test: tool telemetry must fire in a real deep
agent graph, not just in unit-level callback tests.

Root cause: tool executions run in the agent graph's ToolNode under
invoke-time config -- constructor callbacks bound to the chat model never see
them. build_author_runner must bind telemetry callbacks at graph scope (via
with_config) so on_tool_start/end reach the recorder.

This drives a REAL deepagents graph (via build_author_runner with a canon
backend + tools) using a scripted fake chat model that first emits a tool
call to `ls`, then emits the ChapterDraft structured-output tool call. Before
the fix (callbacks passed only to the chat model's constructor), no
tool.call_started event reaches the recorder. After the fix, it does.
"""
import os
import tempfile
import pytest
from langchain_core.messages import AIMessage
from langchain_core.language_models.fake_chat_models import GenericFakeChatModel
from novelizer.agents.author import build_author_runner
from novelizer.canon.event_store import EventStore
from novelizer.canon.projector import Projector
from novelizer.canon.read_store import ReadStore
from novelizer.canon_fs.backend import CanonBackend
from novelizer.telemetry.callbacks import TelemetryCallbackHandler
from novelizer.telemetry.events import TelemetryEventType


class _FakeToolCallingModel(GenericFakeChatModel):
    """GenericFakeChatModel has no bind_tools; deepagents/langchain's agent
    factory calls model.bind_tools(...) unconditionally, so a fake used here
    must implement it (ignoring the tools, since we script exact messages)."""

    def bind_tools(self, tools, *, tool_choice=None, **kwargs):
        return self


class _FakeSettings:
    author_model = "gpt-4o-mini"
    llm_base_url = None
    llm_api_key = "test-key"
    author_temperature = 0.7
    llm_max_tokens = None


class _FakeRecorder:
    def __init__(self):
        self.events = []

    def next_call_index(self, run_id):
        return 0

    def publish_token(self, token_delta):
        pass

    async def emit(self, event_type, run_id, payload):
        self.events.append((event_type, payload))


@pytest.fixture
async def real_read_store(tmp_path):
    path = str(tmp_path / "world.db")
    events = EventStore(path)
    await events.init()
    proj = Projector(events, path)
    await proj.init()
    read = ReadStore(path)
    await read.init()
    try:
        yield read
    finally:
        await read.close()


async def test_tool_calls_in_a_real_deep_agent_reach_the_telemetry_recorder(
    real_read_store, monkeypatch,
):
    recorder = _FakeRecorder()
    handler = TelemetryCallbackHandler(recorder)

    scripted_messages = iter([
        AIMessage(content="", tool_calls=[
            {"name": "ls", "args": {"path": "/"}, "id": "call-1"},
        ]),
        AIMessage(content="", tool_calls=[
            {"name": "ChapterDraft", "args": {
                "title": "Test Chapter", "prose": "Some prose.", "character_ids": [],
            }, "id": "call-2"},
        ]),
    ])
    fake_model = _FakeToolCallingModel(messages=scripted_messages)

    import novelizer.agents.llm as llm_mod
    monkeypatch.setattr(llm_mod, "build_chat_model", lambda *a, **k: fake_model)

    backend = CanonBackend(real_read_store)
    runner = build_author_runner(
        _FakeSettings(), callbacks=[handler], backend=backend, tools=[],
    )

    result = await runner.ainvoke({"messages": [{"role": "user", "content": "hi"}]})

    assert result.get("structured_response") is not None

    events_by_type = {}
    for event_type, payload in recorder.events:
        events_by_type.setdefault(event_type, []).append(payload)

    started = events_by_type.get(TelemetryEventType.TOOL_CALL_STARTED)
    assert started, "expected a tool.call_started event to reach the recorder"
    assert started[0].tool_name == "ls"

    finished = events_by_type.get(TelemetryEventType.TOOL_CALL_FINISHED)
    assert finished, "expected a tool.call_finished event to reach the recorder"
    assert finished[0].tool_name == "ls"
