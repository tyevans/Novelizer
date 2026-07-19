from novelizer.agents.middleware import ExcludeToolsMiddleware


class FakeTool:
    def __init__(self, name): self.name = name


class FakeRequest:
    def __init__(self, tools): self.tools = tools
    def override(self, tools): return FakeRequest(tools)


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
