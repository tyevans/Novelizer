from novelizer.agents.llm import build_chat_model


def test_build_chat_model_targets_given_model_and_endpoint():
    m = build_chat_model("my-model", "http://localhost:1234/v1", "key", temperature=0.5)
    # ChatOpenAI stores the model name and base URL; no network call is made here.
    assert m.model_name == "my-model"
    assert "1234" in str(m.openai_api_base)


def test_build_chat_model_caps_max_tokens():
    m = build_chat_model("my-model", "http://localhost:1234/v1", "key", temperature=0.5, max_tokens=512)
    assert m.max_tokens == 512


import pytest
from novelizer.settings.models import EffectiveSettings


@pytest.mark.parametrize("module,builder", [
    ("author", "build_author_runner"),
    ("world_architect", "build_world_architect_runner"),
    ("character_keeper", "build_character_keeper_runner"),
    ("editor", "build_editor_runner"),
    ("continuity_checker", "build_continuity_checker_runner"),
    ("retconner", "build_retconner_runner"),
    ("structure_analyst", "build_structure_analyst_runner"),
])
def test_runner_builders_pass_llm_max_tokens(monkeypatch, module, builder):
    """Every agent runner must cap generation: an uncapped local model with
    reasoning enabled can ramble past the proxy timeout and never complete."""
    import importlib
    import deepagents
    import novelizer.agents.llm as llm_mod
    captured = {}

    def fake_build(model, base_url, api_key, temperature=0.8, max_tokens=None, callbacks=None):
        captured["max_tokens"] = max_tokens
        return object()

    monkeypatch.setattr(llm_mod, "build_chat_model", fake_build)
    monkeypatch.setattr(deepagents, "create_deep_agent", lambda **kw: object())
    settings = EffectiveSettings(llm_max_tokens=1234)
    mod = importlib.import_module(f"novelizer.agents.{module}")
    getattr(mod, builder)(settings)
    assert captured["max_tokens"] == 1234


def test_build_chat_model_with_callbacks_enables_streaming():
    from langchain_core.callbacks.base import BaseCallbackHandler
    from novelizer.agents.llm import build_chat_model

    handler = BaseCallbackHandler()
    m = build_chat_model("gpt-x", "http://localhost:1", "k", callbacks=[handler])
    assert m.streaming is True
    assert handler in (m.callbacks or [])


def test_build_chat_model_without_callbacks_keeps_current_defaults():
    from novelizer.agents.llm import build_chat_model

    m = build_chat_model("gpt-x", "http://localhost:1", "k")
    assert m.streaming is False
    assert not m.callbacks


def test_every_builder_accepts_a_callbacks_kwarg():
    import inspect
    from novelizer.agents.author import build_author_runner
    from novelizer.agents.world_architect import build_world_architect_runner
    from novelizer.agents.character_keeper import build_character_keeper_runner
    from novelizer.agents.editor import build_editor_runner
    from novelizer.agents.continuity_checker import (
        build_continuity_checker_runner, build_continuity_mining_runner,
    )
    from novelizer.agents.retconner import build_retconner_runner
    from novelizer.agents.structure_analyst import build_structure_analyst_runner

    for builder in [build_author_runner, build_world_architect_runner,
                    build_character_keeper_runner, build_editor_runner,
                    build_continuity_checker_runner, build_continuity_mining_runner,
                    build_retconner_runner, build_structure_analyst_runner]:
        assert "callbacks" in inspect.signature(builder).parameters, builder.__name__
