import pydantic
import pytest

from novelizer.settings.models import (
    EffectiveSettings,
    STORY_OVERRIDABLE_KEYS,
    FORBIDDEN_STORY_KEYS,
)


def test_defaults_match_legacy_settings():
    s = EffectiveSettings()
    assert s.db_path == "stories/world.db"
    assert s.chroma_path == "stories/chroma"
    assert s.embed_model == "nomic-embed-text"
    assert s.llm_base_url == "http://localhost:8080/v1"
    assert s.llm_api_key == "not-needed"
    assert s.author_model == "local-model"
    assert s.author_temperature == 0.8
    assert s.agent_model == "local-model"
    assert s.agent_temperature == 0.7
    assert s.author_interval == 300
    assert s.default_agent_interval == 120
    assert s.continuity_interval == 900
    assert s.structure_analyst_interval == 180
    assert s.projector_interval == 0.5
    assert s.voice_pack.endswith("default.toml")
    assert s.prose_profile == "plain"
    assert s.story_title is None
    assert s.default_stories_dir == "stories"
    assert s.last_opened_story is None


def test_effective_settings_is_frozen():
    s = EffectiveSettings()
    with pytest.raises(pydantic.ValidationError):
        s.author_model = "other"


def test_key_sets():
    assert "llm_api_key" in FORBIDDEN_STORY_KEYS
    assert "llm_api_key" not in STORY_OVERRIDABLE_KEYS
    assert "llm_base_url" not in STORY_OVERRIDABLE_KEYS
    assert "voice_pack" in STORY_OVERRIDABLE_KEYS
    assert "embed_model" in STORY_OVERRIDABLE_KEYS
    assert STORY_OVERRIDABLE_KEYS <= set(EffectiveSettings.model_fields)


def test_llm_max_tokens_default_caps_generation():
    assert EffectiveSettings().llm_max_tokens == 4096


def test_max_concurrent_agents_default_is_2():
    assert EffectiveSettings().max_concurrent_agents == 2
    assert "max_concurrent_agents" not in STORY_OVERRIDABLE_KEYS


def test_prior_chapter_summary_chars_default_is_200():
    assert EffectiveSettings().prior_chapter_summary_chars == 200
    assert "prior_chapter_summary_chars" in STORY_OVERRIDABLE_KEYS


def test_staleness_and_sag_spike_defaults():
    s = EffectiveSettings()
    assert s.staleness_threshold_chapters == 3
    assert s.sag_spike_delta == 0.3
    assert "staleness_threshold_chapters" in STORY_OVERRIDABLE_KEYS
    assert "sag_spike_delta" in STORY_OVERRIDABLE_KEYS


def test_author_and_checker_tools_enabled_defaults():
    s = EffectiveSettings()
    assert s.author_tools_enabled is True
    assert s.checker_tools_enabled is True
    assert "author_tools_enabled" in STORY_OVERRIDABLE_KEYS
    assert "checker_tools_enabled" in STORY_OVERRIDABLE_KEYS


def test_chat_tools_enabled_default():
    s = EffectiveSettings()
    assert s.chat_tools_enabled is True
    assert "chat_tools_enabled" in STORY_OVERRIDABLE_KEYS
