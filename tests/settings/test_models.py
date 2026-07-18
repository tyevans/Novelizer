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
