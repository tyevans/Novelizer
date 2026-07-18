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
