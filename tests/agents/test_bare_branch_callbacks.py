"""Red/green spy test for CPT-M6 Task 3 re-work: the bare (no-backend) branch of
each phase-b agent builder must pass callbacks through as the constructor
`callbacks=` argument to build_chat_model, matching the pre-Task-3 contract
(git show 6cd1149), and must NOT pass an explicit `streaming=` override.
"""
import importlib

import pytest


class _FakeSettings:
    agent_model = "gpt-4o-mini"
    llm_base_url = None
    llm_api_key = "test-key"
    agent_temperature = 0.7
    llm_max_tokens = None


AGENT_MODULES_AND_BUILDERS = [
    ("novelizer.agents.world_architect", "build_world_architect_runner"),
    ("novelizer.agents.character_keeper", "build_character_keeper_runner"),
    ("novelizer.agents.editor", "build_editor_runner"),
    ("novelizer.agents.retconner", "build_retconner_runner"),
    ("novelizer.agents.structure_analyst", "build_structure_analyst_runner"),
]


@pytest.mark.parametrize("module_name,builder_name", AGENT_MODULES_AND_BUILDERS)
def test_bare_branch_passes_callbacks_as_constructor_arg(module_name, builder_name, monkeypatch):
    module = importlib.import_module(module_name)
    builder = getattr(module, builder_name)

    calls = []

    def fake_build_chat_model(*args, **kwargs):
        calls.append(kwargs)
        return object()

    monkeypatch.setattr("agent_kit.build_chat_model", fake_build_chat_model)
    class _FakeGraph:
        def with_config(self, config):
            return self

    monkeypatch.setattr("deepagents.create_deep_agent", lambda *a, **k: _FakeGraph())

    sentinel_callbacks = ["sentinel-callback"]
    builder(_FakeSettings(), callbacks=sentinel_callbacks)

    assert len(calls) == 1, "bare branch should build exactly one model"
    kwargs = calls[0]
    assert kwargs.get("callbacks") is sentinel_callbacks, (
        "bare branch must pass callbacks through as the constructor callbacks= "
        f"argument; got callbacks={kwargs.get('callbacks')!r}"
    )
    assert "streaming" not in kwargs, (
        "bare branch must not pass an explicit streaming= override "
        f"(got streaming={kwargs.get('streaming')!r})"
    )
