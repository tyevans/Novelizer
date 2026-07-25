"""Embedding endpoint resolution: the chat endpoint and the embedding endpoint
are separately configurable, because chat routers (OpenRouter) serve no
embedding models at all."""

from __future__ import annotations

import pytest

from novelizer.settings.layers import GlobalConfig, StoryConfigError, parse_story
from novelizer.settings.models import STORY_OVERRIDABLE_KEYS, EffectiveSettings


def _settings(**kwargs) -> EffectiveSettings:
    return EffectiveSettings(llm_base_url="http://chat:1/v1", llm_api_key="sk-chat", **kwargs)


def test_unset_embed_endpoint_reuses_chat_endpoint():
    """The all-local one-endpoint setup must keep working with no new config."""
    s = _settings()
    assert s.resolved_embed_base_url == "http://chat:1/v1"
    assert s.resolved_embed_api_key == "sk-chat"


def test_dedicated_embed_endpoint_wins():
    s = _settings(embed_base_url="http://ollama:11434/v1", embed_api_key="sk-embed")
    assert s.resolved_embed_base_url == "http://ollama:11434/v1"
    assert s.resolved_embed_api_key == "sk-embed"


def test_dedicated_embed_endpoint_does_not_inherit_chat_key():
    """Security: a separate endpoint is a different provider. Forwarding the
    chat key would leak a paid credential (e.g. an OpenRouter key) to it."""
    s = _settings(embed_base_url="http://ollama:11434/v1")
    assert s.resolved_embed_api_key == "not-needed"
    assert "sk-chat" not in s.resolved_embed_api_key


def test_whitespace_only_embed_url_is_not_an_endpoint():
    s = _settings(embed_base_url="   ")
    assert s.resolved_embed_base_url == "http://chat:1/v1"
    assert s.resolved_embed_api_key == "sk-chat"


def test_embed_endpoint_is_global_only_not_story_overridable():
    """Like llm_base_url: an installation fact, not a per-story creative knob."""
    assert "embed_base_url" not in STORY_OVERRIDABLE_KEYS
    assert "embed_api_key" not in STORY_OVERRIDABLE_KEYS


def test_embed_api_key_is_forbidden_in_story_toml():
    """Stories are shareable, so they must never carry an embedding secret."""
    with pytest.raises(StoryConfigError):
        parse_story({"embed_api_key": "sk-leak"}, source="story.toml")


def test_global_config_accepts_embed_endpoint_keys():
    cfg = GlobalConfig(embed_base_url="http://ollama:11434/v1", embed_api_key="sk-e")
    assert cfg.embed_base_url == "http://ollama:11434/v1"
    assert cfg.embed_api_key == "sk-e"
