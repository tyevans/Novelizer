from novelizer.agents.middleware import ExcludeToolsMiddleware, TodoContextMiddleware


class FakeTool:
    def __init__(self, name): self.name = name


class FakeRequest:
    def __init__(self, tools): self.tools = tools
    def override(self, tools): return FakeRequest(tools)


class FakeSystemMessage:
    def __init__(self, content_blocks):
        self.content_blocks = content_blocks


class FakeTodoRequest:
    def __init__(self, todos, system_message=None):
        self.state = {"todos": todos}
        self.system_message = system_message

    def override(self, system_message):
        return FakeTodoRequest(self.state["todos"], system_message=system_message)


def test_todo_context_skips_when_no_todos():
    mw = TodoContextMiddleware()
    request = FakeTodoRequest([], system_message=FakeSystemMessage([{"type": "text", "text": "base"}]))
    seen = {}
    def handler(req):
        seen["request"] = req
        return "ok"
    assert mw.wrap_model_call(request, handler) == "ok"
    assert seen["request"] is request


def test_todo_context_appends_current_list_to_system_message():
    mw = TodoContextMiddleware()
    todos = [{"content": "read last chapter", "status": "completed"},
              {"content": "draft scene", "status": "in_progress"},
              {"content": "set intents", "status": "pending"}]
    request = FakeTodoRequest(todos, system_message=FakeSystemMessage([{"type": "text", "text": "base"}]))
    seen = {}
    def handler(req):
        seen["blocks"] = req.system_message.content
        return "ok"
    mw.wrap_model_call(request, handler)
    text = seen["blocks"][-1]["text"]
    assert "[x] read last chapter" in text
    assert "[~] draft scene" in text
    assert "[ ] set intents" in text


def test_todo_context_handles_missing_system_message():
    mw = TodoContextMiddleware()
    request = FakeTodoRequest([{"content": "plan", "status": "pending"}], system_message=None)
    seen = {}
    def handler(req):
        seen["blocks"] = req.system_message.content
        return "ok"
    mw.wrap_model_call(request, handler)
    assert "[ ] plan" in seen["blocks"][0]["text"]


async def test_todo_context_async_appends_current_list():
    mw = TodoContextMiddleware()
    todos = [{"content": "draft", "status": "in_progress"}]
    request = FakeTodoRequest(todos, system_message=FakeSystemMessage([{"type": "text", "text": "base"}]))
    seen = {}
    async def handler(req):
        seen["blocks"] = req.system_message.content
        return "ok"
    assert await mw.awrap_model_call(request, handler) == "ok"
    assert "[~] draft" in seen["blocks"][-1]["text"]


def test_sync_filters_named_tools_and_calls_handler_with_rest():
    mw = ExcludeToolsMiddleware(excluded=frozenset({"write_todos"}))
    seen = {}
    def handler(request):
        seen["tools"] = [t.name for t in request.tools]
        return "response"
    request = FakeRequest([FakeTool("write_todos"), FakeTool("read_file")])
    assert mw.wrap_model_call(request, handler) == "response"
    assert seen["tools"] == ["read_file"]


def test_sync_handles_dict_tools():
    mw = ExcludeToolsMiddleware(excluded=frozenset({"write_todos"}))
    seen = {}
    def handler(request):
        seen["tools"] = request.tools
        return "response"
    request = FakeRequest([{"name": "write_todos"}, {"name": "search_canon"}])
    mw.wrap_model_call(request, handler)
    assert seen["tools"] == [{"name": "search_canon"}]


def test_empty_exclusion_passes_request_through_unchanged():
    mw = ExcludeToolsMiddleware(excluded=frozenset())
    request = FakeRequest([FakeTool("write_todos")])
    def handler(req):
        assert req is request
        return "ok"
    assert mw.wrap_model_call(request, handler) == "ok"


async def test_async_filters_named_tools():
    mw = ExcludeToolsMiddleware(excluded=frozenset({"write_todos"}))
    seen = {}
    async def handler(request):
        seen["tools"] = [t.name for t in request.tools]
        return "response"
    request = FakeRequest([FakeTool("write_todos"), FakeTool("ls")])
    assert await mw.awrap_model_call(request, handler) == "response"
    assert seen["tools"] == ["ls"]
