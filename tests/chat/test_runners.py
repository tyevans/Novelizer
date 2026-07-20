from novelizer.chat.runners import CHAT_RETRIEVAL_NOTE, build_chat_runner


class FakeSettings:
    author_model = "gpt-4o-mini"
    agent_model = "gpt-4o-mini"
    llm_base_url = None
    llm_api_key = "test-key"
    agent_temperature = 0.7
    llm_max_tokens = None


class FakeBackend:
    """Stands in for CanonBackend; create_deep_agent only stores it."""


def test_build_chat_runner_without_backend_stays_constructible():
    runner = build_chat_runner(FakeSettings(), "author")
    assert runner is not None


def test_build_chat_runner_with_backend_builds():
    runner = build_chat_runner(FakeSettings(), "editor", backend=FakeBackend(), tools=[])
    assert runner is not None


def test_build_chat_runner_with_backend_adds_retrieval_note_and_config(monkeypatch):
    captured = {}

    class FakeGraph:
        def with_config(self, config):
            captured["config"] = config
            return self

    def fake_create_deep_agent(**kwargs):
        captured["kwargs"] = kwargs
        return FakeGraph()

    monkeypatch.setattr("deepagents.create_deep_agent", fake_create_deep_agent)
    sentinel_cb = object()
    build_chat_runner(FakeSettings(), "editor", callbacks=[sentinel_cb], backend=FakeBackend(), tools=[])
    assert CHAT_RETRIEVAL_NOTE.strip() in captured["kwargs"]["system_prompt"]
    assert captured["kwargs"]["backend"] is not None
    assert captured["config"]["recursion_limit"] == 50
    assert captured["config"]["callbacks"] == [sentinel_cb]


def test_build_chat_runner_with_backend_excludes_write_todos(monkeypatch):
    from novelizer.agents.middleware import ExcludeToolsMiddleware
    captured = {}

    class FakeGraph:
        def with_config(self, config): return self

    def fake_create_deep_agent(**kwargs):
        captured["kwargs"] = kwargs
        return FakeGraph()

    monkeypatch.setattr("deepagents.create_deep_agent", fake_create_deep_agent)
    build_chat_runner(FakeSettings(), "editor", backend=FakeBackend(), tools=[])
    mws = captured["kwargs"]["middleware"]
    assert any(isinstance(m, ExcludeToolsMiddleware) for m in mws)


def test_build_chat_runner_without_backend_prompt_unchanged(monkeypatch):
    captured = {}

    def fake_create_deep_agent(**kwargs):
        captured["kwargs"] = kwargs
        class G:
            def with_config(self, c): return self
        return G()

    monkeypatch.setattr("deepagents.create_deep_agent", fake_create_deep_agent)
    build_chat_runner(FakeSettings(), "author")
    assert "file tools" not in captured["kwargs"]["system_prompt"]
    assert "middleware" not in captured["kwargs"]
