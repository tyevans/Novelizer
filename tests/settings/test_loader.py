from pathlib import Path

import pytest
from hypothesis import given
from hypothesis import strategies as st

from novelizer.settings.layers import GlobalConfig, StoryConfig
from novelizer.settings.loader import EnvOverrides, build_effective, load_effective_settings
from novelizer.settings.models import EffectiveSettings
from novelizer.settings.story_dir import StoryDirectory, create_story
from novelizer.settings.toml_io import write_toml_file


def _env(**kwargs) -> EnvOverrides:
    # _env_file=None so a developer's real .env can't leak into tests
    return EnvOverrides(_env_file=None, **kwargs)


def test_precedence_env_over_story_over_global():
    eff = build_effective(
        GlobalConfig(author_model="g", agent_model="g", prose_profile="g"),
        StoryConfig(agent_model="s", prose_profile="s"),
        _env(prose_profile="e"),
    )
    assert eff.author_model == "g"   # global beats default
    assert eff.agent_model == "s"    # story beats global
    assert eff.prose_profile == "e"  # env beats story
    assert eff.embed_model == "nomic-embed-text"  # untouched default survives


def test_story_dir_forces_derived_paths(tmp_path):
    sd = StoryDirectory(root=tmp_path / "novel")
    eff = build_effective(GlobalConfig(), StoryConfig(), _env(), story_dir=sd)
    assert eff.db_path == str(sd.db_path)
    assert eff.chroma_path == str(sd.chroma_path)


def test_story_title_carried():
    eff = build_effective(GlobalConfig(), StoryConfig(title="My Novel"), _env())
    assert eff.story_title == "My Novel"


def test_load_effective_settings_reads_files(tmp_path, monkeypatch):
    for field in EnvOverrides.model_fields:
        monkeypatch.delenv(f"NOVELIZER_{field.upper()}", raising=False)
    monkeypatch.chdir(tmp_path)
    gpath = tmp_path / "config.toml"
    write_toml_file(gpath, {"author_model": "global-m", "author_temperature": 0.3})
    sd = create_story(tmp_path / "novel", title="N")
    write_toml_file(sd.story_toml, {"title": "N", "author_temperature": 0.9})
    eff = load_effective_settings(story_dir=sd, global_path=gpath)
    assert eff.author_model == "global-m"
    assert eff.author_temperature == 0.9
    assert eff.db_path == str(sd.db_path)


def test_load_effective_settings_env_wins(tmp_path, monkeypatch):
    gpath = tmp_path / "config.toml"
    write_toml_file(gpath, {"author_model": "global-m"})
    monkeypatch.setenv("NOVELIZER_AUTHOR_MODEL", "env-m")
    eff = load_effective_settings(global_path=gpath)
    assert eff.author_model == "env-m"


def test_load_effective_settings_missing_files_ok(tmp_path, monkeypatch):
    for field in EnvOverrides.model_fields:
        monkeypatch.delenv(f"NOVELIZER_{field.upper()}", raising=False)
    monkeypatch.chdir(tmp_path)
    eff = load_effective_settings(global_path=tmp_path / "absent.toml")
    assert eff.author_model == "local-model"


# Representative overridable keys, one per value type.
_PROPERTY_KEYS = {
    "agent_model": st.text(min_size=1, max_size=12),
    "prose_profile": st.text(min_size=1, max_size=12),
    "author_temperature": st.floats(0.0, 2.0, allow_nan=False),
    "author_interval": st.integers(1, 100_000),
}

_layer = st.fixed_dictionaries({}, optional=_PROPERTY_KEYS)


@given(global_d=_layer, story_d=_layer, env_d=_layer)
def test_precedence_property(global_d, story_d, env_d):
    """For every key: env > story > global > built-in default."""
    defaults = EffectiveSettings()
    eff = build_effective(
        GlobalConfig(**global_d), StoryConfig(**story_d), _env(**env_d)
    )
    for key in _PROPERTY_KEYS:
        expected = env_d.get(key, story_d.get(key, global_d.get(key, getattr(defaults, key))))
        assert getattr(eff, key) == expected


@pytest.mark.parametrize("flag_name", [
    "world_architect_tools_enabled",
    "character_keeper_tools_enabled",
    "editor_tools_enabled",
    "retconner_tools_enabled",
    "structure_analyst_tools_enabled",
    "plotter_tools_enabled",
])
def test_phase_b_agent_tools_enabled_precedence(flag_name):
    """Phase-B per-agent tools_enabled flags follow env > story > global > default precedence."""
    # Default is True
    eff = build_effective(GlobalConfig(), StoryConfig(), _env())
    assert getattr(eff, flag_name) is True

    # Global can override
    global_kwargs = {flag_name: False}
    eff = build_effective(GlobalConfig(**global_kwargs), StoryConfig(), _env())
    assert getattr(eff, flag_name) is False

    # Story overrides global
    story_kwargs = {flag_name: True}
    eff = build_effective(GlobalConfig(**global_kwargs), StoryConfig(**story_kwargs), _env())
    assert getattr(eff, flag_name) is True

    # Env overrides all
    env_kwargs = {flag_name: False}
    eff = build_effective(GlobalConfig(**global_kwargs), StoryConfig(**story_kwargs), _env(**env_kwargs))
    assert getattr(eff, flag_name) is False
