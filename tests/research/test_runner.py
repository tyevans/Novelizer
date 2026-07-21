class FakeSettings:
    agent_model = "local-model"
    llm_base_url = None
    llm_api_key = "test-key"
    agent_temperature = 0.7
    llm_max_tokens = None


def test_build_research_runner_bare_stays_constructible(monkeypatch):
    from novelizer.research import runner as runner_mod

    captured = {}

    class FakeGraph:
        pass

    def fake_create_deep_agent(*, model, system_prompt, response_format, backend=None, tools=None, skills=None, middleware=None):
        captured["kwargs"] = {"system_prompt": system_prompt, "backend": backend, "tools": tools}
        return FakeGraph()

    import deepagents
    monkeypatch.setattr(deepagents, "create_deep_agent", fake_create_deep_agent)

    graph = runner_mod.build_research_runner(FakeSettings())

    assert graph is not None
    assert captured["kwargs"]["backend"] is None
    assert captured["kwargs"]["tools"] is None
    assert "never modify canon" in captured["kwargs"]["system_prompt"]


def test_build_research_runner_with_backend_includes_diagnostic_tools(monkeypatch):
    from novelizer.research import runner as runner_mod
    from novelizer.canon_fs.backend import CanonBackend

    captured = {}

    class FakeGraph:
        def with_config(self, config):
            captured["config"] = config
            return self

    def fake_create_deep_agent(*, model, system_prompt, response_format, backend=None, tools=None, skills=None, middleware=None):
        captured["tools"] = tools
        captured["response_format"] = response_format
        return FakeGraph()

    import deepagents
    monkeypatch.setattr(deepagents, "create_deep_agent", fake_create_deep_agent)

    backend = CanonBackend(read_store=None)
    graph = runner_mod.build_research_runner(FakeSettings(), backend=backend, tools=["search_canon_stub"])

    from novelizer.research.schemas import ResearchAnswer
    assert captured["response_format"] is ResearchAnswer
    tool_names = {getattr(t, "name", t) for t in captured["tools"]}
    assert "search_canon_stub" in tool_names
    assert {"check_stale_threads", "check_leaks", "check_paradoxes",
            "check_promise_ledger", "check_beat_drift", "check_completion"} <= tool_names
    assert captured["config"]["recursion_limit"] == 100
