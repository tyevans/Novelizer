import logging
from pathlib import Path

import pytest

from novelizer.settings.layers import (
    GlobalConfig,
    StoryConfig,
    StoryConfigError,
    global_config_path,
    parse_global,
    parse_story,
)
from novelizer.settings.loader import EnvOverrides
from novelizer.settings.models import STORY_OVERRIDABLE_KEYS


def test_parse_global_known_keys():
    cfg = parse_global({"llm_base_url": "http://h:1/v1", "author_model": "m"}, source="g.toml")
    assert cfg.llm_base_url == "http://h:1/v1"
    assert cfg.author_model == "m"
    assert cfg.llm_api_key is None  # unset stays None so it doesn't shadow defaults


def test_parse_global_warns_on_unknown_key(caplog):
    with caplog.at_level(logging.WARNING, logger="novelizer.settings"):
        parse_global({"db_path": "x", "frobnicate": 1}, source="g.toml")
    text = caplog.text
    assert "db_path" in text and "frobnicate" in text and "g.toml" in text


def test_parse_story_rejects_api_key():
    with pytest.raises(StoryConfigError) as exc:
        parse_story({"llm_api_key": "sk-real"}, source="story.toml")
    assert "llm_api_key" in str(exc.value)
    assert "story.toml" in str(exc.value)


def test_parse_story_warns_on_global_only_key(caplog):
    with caplog.at_level(logging.WARNING, logger="novelizer.settings"):
        cfg = parse_story({"llm_base_url": "http://h:1/v1", "prose_profile": "lush"}, source="s.toml")
    assert cfg.prose_profile == "lush"
    assert "llm_base_url" in caplog.text


def test_parse_story_title():
    cfg = parse_story({"title": "My Novel"}, source="s.toml")
    assert cfg.title == "My Novel"


def test_global_config_path_respects_xdg(monkeypatch, tmp_path):
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    assert global_config_path() == tmp_path / "novelizer" / "config.toml"


def test_global_config_path_defaults_to_dot_config(monkeypatch):
    monkeypatch.delenv("XDG_CONFIG_HOME", raising=False)
    p = global_config_path()
    assert p == Path.home() / ".config" / "novelizer" / "config.toml"


def test_story_config_fields_match_overridable_keys():
    """Guards against StoryConfig drifting from STORY_OVERRIDABLE_KEYS — both
    are hand-duplicated key sets and must stay in sync."""
    assert set(StoryConfig.model_fields) - {"title"} == STORY_OVERRIDABLE_KEYS


def test_global_config_fields_match_overridable_plus_global_only_keys():
    assert set(GlobalConfig.model_fields) == STORY_OVERRIDABLE_KEYS | {
        "llm_base_url", "llm_api_key", "llm_max_tokens", "default_stories_dir", "last_opened_story",
        "suppress_flat_migration_prompt", "max_concurrent_agents",
    }


def test_env_overrides_fields_match_global_config_fields():
    assert set(EnvOverrides.model_fields) == set(GlobalConfig.model_fields)


def test_suppress_flat_migration_prompt_defaults_false():
    from novelizer.settings import EffectiveSettings

    assert EffectiveSettings().suppress_flat_migration_prompt is False


def test_parse_global_accepts_llm_max_tokens():
    cfg = parse_global({"llm_max_tokens": 2048}, source="g.toml")
    assert cfg.llm_max_tokens == 2048


def test_parse_global_accepts_max_concurrent_agents():
    cfg = parse_global({"max_concurrent_agents": 4}, source="g.toml")
    assert cfg.max_concurrent_agents == 4


def test_max_concurrent_agents_not_story_overridable():
    assert "max_concurrent_agents" not in StoryConfig.model_fields
    assert "max_concurrent_agents" not in STORY_OVERRIDABLE_KEYS


def test_parse_global_accepts_prior_chapter_summary_chars():
    cfg = parse_global({"prior_chapter_summary_chars": 100}, source="g.toml")
    assert cfg.prior_chapter_summary_chars == 100


def test_parse_story_accepts_staleness_and_sag_spike_settings():
    cfg = parse_story({"staleness_threshold_chapters": 5, "sag_spike_delta": 0.2}, source="s.toml")
    assert cfg.staleness_threshold_chapters == 5
    assert cfg.sag_spike_delta == 0.2


def test_parse_story_accepts_author_and_checker_tools_enabled():
    cfg = parse_story({"author_tools_enabled": False, "checker_tools_enabled": False}, source="s.toml")
    assert cfg.author_tools_enabled is False
    assert cfg.checker_tools_enabled is False
