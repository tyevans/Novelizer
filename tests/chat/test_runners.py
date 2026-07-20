from __future__ import annotations


class FakeSettings:
    author_model = "gpt-4o-mini"
    agent_model = "local-model"
    llm_base_url = None
    llm_api_key = "test-key"
    author_temperature = 0.7
    agent_temperature = 0.7
    llm_max_tokens = None


def test_build_chat_runner_bare_stays_constructible(monkeypatch):
    from novelizer.chat import runners as runners_mod

    captured = {}

    class FakeGraph:
        pass

    def fake_create_deep_agent(*, model, system_prompt, response_format, backend=None, tools=None, middleware=None):
        captured["kwargs"] = {
            "system_prompt": system_prompt, "backend": backend, "tools": tools,
        }
        return FakeGraph()

    import deepagents
    monkeypatch.setattr(deepagents, "create_deep_agent", fake_create_deep_agent)

    runner = runners_mod.build_chat_runner(FakeSettings(), "author")

    assert runner is not None
    assert captured["kwargs"]["backend"] is None
    assert captured["kwargs"]["tools"] is None
    assert runners_mod.RETRIEVAL_NOTE not in captured["kwargs"]["system_prompt"]


def test_build_chat_runner_with_canon_backend_builds():
    from novelizer.chat.runners import build_chat_runner
    from novelizer.canon_fs.backend import CanonBackend

    backend = CanonBackend(read_store=None)
    runner = build_chat_runner(FakeSettings(), "author", backend=backend, tools=[])
    assert runner is not None


def test_build_chat_runner_with_backend_includes_retrieval_note(monkeypatch):
    from novelizer.chat import runners as runners_mod
    from novelizer.canon_fs.backend import CanonBackend

    captured = {}

    class FakeGraph:
        def with_config(self, config):
            return self

    def fake_create_deep_agent(*, model, system_prompt, response_format, backend=None, tools=None, middleware=None):
        captured["system_prompt"] = system_prompt
        return FakeGraph()

    import deepagents
    monkeypatch.setattr(deepagents, "create_deep_agent", fake_create_deep_agent)

    backend = CanonBackend(read_store=None)
    runners_mod.build_chat_runner(FakeSettings(), "author", backend=backend, tools=[])
    assert runners_mod.RETRIEVAL_NOTE in captured["system_prompt"]


def test_build_chat_runner_with_backend_bounds_recursion():
    from novelizer.chat.runners import build_chat_runner
    from novelizer.canon_fs.backend import CanonBackend

    backend = CanonBackend(read_store=None)
    runner = build_chat_runner(FakeSettings(), "author", backend=backend, tools=[])
    assert runner.config.get("recursion_limit") == 50


def test_build_chat_runner_binds_callbacks_at_graph_scope_not_model():
    from novelizer.chat.runners import build_chat_runner
    from novelizer.canon_fs.backend import CanonBackend
    from langchain_core.callbacks.base import BaseCallbackHandler

    handler = BaseCallbackHandler()
    backend = CanonBackend(read_store=None)
    runner = build_chat_runner(FakeSettings(), "author", callbacks=[handler], backend=backend, tools=[])
    assert handler in (runner.config.get("callbacks") or [])


def test_build_chat_runner_streams_when_callbacks_provided(monkeypatch):
    """CPT-M5 final-review fix: streaming must follow `callbacks is not None`,
    mirroring build_author_runner -- otherwise on_llm_new_token never fires
    for chat and the Engine Room live view degrades."""
    from novelizer.chat import runners as runners_mod

    seen = {}

    def fake_build_chat_model(model_name, base_url, api_key, temperature, max_tokens=None, callbacks=None, streaming=False):
        seen["callbacks"] = callbacks
        seen["streaming"] = streaming
        class FakeModel:
            pass
        return FakeModel()

    import novelizer.agents.llm as llm_mod
    monkeypatch.setattr(llm_mod, "build_chat_model", fake_build_chat_model)

    class FakeGraph:
        def with_config(self, config):
            return self

    def fake_create_deep_agent(*, model, system_prompt, response_format, backend=None, tools=None, middleware=None):
        return FakeGraph()

    import deepagents
    monkeypatch.setattr(deepagents, "create_deep_agent", fake_create_deep_agent)

    from langchain_core.callbacks.base import BaseCallbackHandler
    handler = BaseCallbackHandler()

    runners_mod.build_chat_runner(FakeSettings(), "author", callbacks=[handler])
    assert seen["callbacks"] is None
    assert seen["streaming"] is True

    seen.clear()
    runners_mod.build_chat_runner(FakeSettings(), "author")
    assert seen["callbacks"] is None
    assert not seen["streaming"]
