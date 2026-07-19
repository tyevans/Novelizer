from __future__ import annotations


class FakeSettings:
    author_model = "gpt-4o-mini"
    agent_model = "local-model"
    llm_base_url = None
    llm_api_key = "test-key"
    author_temperature = 0.7
    agent_temperature = 0.7
    llm_max_tokens = None


def test_build_chat_runner_bare_stays_constructible():
    from novelizer.chat.runners import build_chat_runner

    runner = build_chat_runner(FakeSettings(), "author")
    assert runner is not None
    assert not hasattr(runner, "config") or not (runner.config or {}).get("callbacks")


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

    def fake_create_deep_agent(*, model, system_prompt, response_format, backend=None, tools=None):
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
