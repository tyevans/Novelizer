from __future__ import annotations

import pytest

from agent_kit import ExcludeToolsMiddleware
from novelizer.agents.middleware import TodoContextMiddleware
from novelizer.agents.author import build_author_runner
from novelizer.agents.plotter import build_plotter_runner
from novelizer.agents.world_architect import build_world_architect_runner
from novelizer.agents.character_keeper import build_character_keeper_runner
from novelizer.agents.editor import build_editor_runner
from novelizer.agents.retconner import build_retconner_runner
from novelizer.agents.structure_analyst import build_structure_analyst_runner
from novelizer.agents.continuity_checker import build_continuity_checker_runner
from novelizer.chat.runners import build_chat_runner


class FakeSettings:
    agent_model = "gpt-4o-mini"
    author_model = "gpt-4o-mini"
    llm_base_url = None
    llm_api_key = "test-key"
    agent_temperature = 0.7
    author_temperature = 0.7
    llm_max_tokens = None


class FakeBackend:
    pass


class FakeGraph:
    def with_config(self, config):
        return self


class Capture:
    def __init__(self):
        self.kwargs = None

    def __call__(self, *args, **kwargs):
        self.kwargs = kwargs
        return FakeGraph()


EXCLUDED_BUILDERS = [
    build_world_architect_runner,
    build_character_keeper_runner,
    build_editor_runner,
    build_retconner_runner,
    build_structure_analyst_runner,
    build_continuity_checker_runner,
]


@pytest.mark.parametrize("builder", EXCLUDED_BUILDERS)
def test_excludes_write_todos(monkeypatch, builder):
    capture = Capture()
    monkeypatch.setattr("deepagents.create_deep_agent", capture)
    builder(FakeSettings(), backend=FakeBackend(), tools=[])
    assert capture.kwargs is not None
    middleware = capture.kwargs.get("middleware", [])
    assert any(isinstance(m, ExcludeToolsMiddleware) for m in middleware)


def test_chat_runner_excludes_write_todos(monkeypatch):
    capture = Capture()
    monkeypatch.setattr("deepagents.create_deep_agent", capture)
    build_chat_runner(FakeSettings(), "editor", backend=FakeBackend(), tools=[])
    assert capture.kwargs is not None
    middleware = capture.kwargs.get("middleware", [])
    assert any(isinstance(m, ExcludeToolsMiddleware) for m in middleware)


def test_author_runner_keeps_write_todos(monkeypatch):
    capture = Capture()
    monkeypatch.setattr("deepagents.create_deep_agent", capture)
    build_author_runner(FakeSettings(), backend=FakeBackend(), tools=[])
    assert capture.kwargs is not None
    middleware = capture.kwargs.get("middleware", [])
    assert not any(isinstance(m, ExcludeToolsMiddleware) for m in middleware)


@pytest.mark.parametrize("builder", [build_author_runner, build_plotter_runner])
def test_todo_holders_surface_state_in_context(monkeypatch, builder):
    capture = Capture()
    monkeypatch.setattr("deepagents.create_deep_agent", capture)
    builder(FakeSettings(), backend=FakeBackend(), tools=[])
    assert capture.kwargs is not None
    middleware = capture.kwargs.get("middleware", [])
    assert any(isinstance(m, TodoContextMiddleware) for m in middleware)


@pytest.mark.parametrize(
    "builder",
    EXCLUDED_BUILDERS,
)
def test_non_todo_agents_skip_todo_context(monkeypatch, builder):
    capture = Capture()
    monkeypatch.setattr("deepagents.create_deep_agent", capture)
    builder(FakeSettings(), backend=FakeBackend(), tools=[])
    assert capture.kwargs is not None
    middleware = capture.kwargs.get("middleware", [])
    assert not any(isinstance(m, TodoContextMiddleware) for m in middleware)
