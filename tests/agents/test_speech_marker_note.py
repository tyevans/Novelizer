"""Every agent that can see prose must know the markers are invisible.

Derived from AGENT_REGISTRY rather than a hand-written list: an agent added
later would otherwise silently miss the note. A test that copies the value it
guards protects nothing, so this asserts the note reaches each builder's real
system prompt.

Reuses the technique from test_output_conventions_note.py -- intercept
deepagents.create_deep_agent's system_prompt kwarg -- and extends it to the
lighter builders (build_simple_runner, from agent_kit) used by agents that
carry no tool_grant but still read raw prose.
"""
from __future__ import annotations

import agent_kit
import deepagents
import pytest

from novelizer.agents import prompts
from novelizer.agents.registry import AGENT_REGISTRY
from novelizer.canon_fs.backend import CanonBackend


class _FakeSettings:
    agent_model = "gpt-4o-mini"
    author_model = "gpt-4o-mini"
    resolved_light_model = "gpt-4o-mini"
    llm_base_url = None
    llm_api_key = "test-key"
    agent_temperature = 0.7
    author_temperature = 0.8
    llm_max_tokens = 1000
    light_reasoning = None


class _FakeGraph:
    def with_config(self, config):
        return self


@pytest.fixture
def prompt_for_spec(monkeypatch):
    captured: dict = {}

    def fake_create_deep_agent(*args, **kwargs):
        captured["system_prompt"] = kwargs.get("system_prompt")
        return _FakeGraph()

    def fake_build_simple_runner(*, system_prompt, **kwargs):
        captured["system_prompt"] = system_prompt
        return _FakeGraph()

    monkeypatch.setattr(deepagents, "create_deep_agent", fake_create_deep_agent)
    monkeypatch.setattr(agent_kit, "build_simple_runner", fake_build_simple_runner)

    def _get(spec):
        import importlib

        captured.clear()
        module = importlib.import_module(f"novelizer.agents.{spec.name}")
        builder = getattr(module, f"build_{spec.name}_runner", None)
        if builder is None:
            return None
        kwargs = {}
        if spec.tool_grant is not None:
            kwargs = {"backend": CanonBackend(read_store=None), "tools": []}
        builder(_FakeSettings(), **kwargs)
        return captured.get("system_prompt")

    return _get


def test_note_names_both_tags():
    assert "<speech" in prompts.SPEECH_MARKER_NOTE
    assert "<thought" in prompts.SPEECH_MARKER_NOTE


@pytest.mark.parametrize("spec", AGENT_REGISTRY, ids=lambda s: s.name)
def test_every_tooled_agent_prompt_carries_the_marker_note(spec, prompt_for_spec):
    prompt = prompt_for_spec(spec)
    if prompt is None:
        pytest.skip(f"{spec.name} builds no system prompt")
    assert "<speech" in prompt, f"{spec.name} prompt lacks the speaker-marker note"


def test_author_prompt_states_the_marker_contract():
    from novelizer.agents.author import AUTHOR_SYSTEM_PROMPT
    assert '<speech char="' in AUTHOR_SYSTEM_PROMPT
    assert '<thought char="' in AUTHOR_SYSTEM_PROMPT
