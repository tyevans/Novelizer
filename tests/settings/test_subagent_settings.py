from __future__ import annotations
from novelizer.settings.models import EffectiveSettings, STORY_OVERRIDABLE_KEYS
from novelizer.settings.layers import GlobalConfig, StoryConfig
from novelizer.settings.loader import EnvOverrides

_SUBAGENT_FLAGS = [
    "world_architect_subagent_enabled", "character_keeper_subagent_enabled",
    "editor_subagent_enabled", "retconner_subagent_enabled",
    "structure_analyst_subagent_enabled", "plotter_subagent_enabled",
    "author_subagent_enabled", "checker_subagent_enabled",
]


def test_subagent_flags_default_to_false():
    s = EffectiveSettings()
    for flag in _SUBAGENT_FLAGS + ["triage_subagent_enabled"]:
        assert getattr(s, flag) is False, flag


def test_subagent_flags_are_story_overridable():
    """triage_subagent_enabled is intentionally excluded -- its tools_enabled
    counterpart isn't story-overridable either (settings/loader.py has no
    triage_tools_enabled field), so this mirrors that existing precedent."""
    for flag in _SUBAGENT_FLAGS:
        assert flag in STORY_OVERRIDABLE_KEYS, flag


def test_global_config_accepts_subagent_flags():
    cfg = GlobalConfig(character_keeper_subagent_enabled=True)
    assert cfg.character_keeper_subagent_enabled is True


def test_story_config_accepts_subagent_flags():
    cfg = StoryConfig(character_keeper_subagent_enabled=True)
    assert cfg.character_keeper_subagent_enabled is True


def test_env_overrides_accepts_subagent_flags():
    env = EnvOverrides(character_keeper_subagent_enabled=True)
    assert env.character_keeper_subagent_enabled is True
