from pathlib import Path

import pytest

from novelizer.settings import EffectiveSettings
from novelizer.settings.layers import GlobalConfig, StoryConfig
from novelizer.settings.loader import EnvOverrides
from novelizer.settings.story_dir import create_story
from novelizer.settings.toml_io import load_toml_file, write_toml_file
from novelizer.settings.view_model import (
    RESTART_REQUIRED_KEYS,
    SettingsRow,
    apply_edit,
    build_settings_rows,
    load_layer_configs,
    parse_value,
)


def _env(**kwargs) -> EnvOverrides:
    return EnvOverrides(_env_file=None, **kwargs)


def _rows(**layer_kwargs) -> dict[str, SettingsRow]:
    g = layer_kwargs.get("g", GlobalConfig())
    s = layer_kwargs.get("s", StoryConfig())
    e = layer_kwargs.get("e", _env())
    eff = layer_kwargs.get("eff", EffectiveSettings())
    return {r.key: r for r in build_settings_rows(g, s, e, eff)}


def test_source_resolution_and_scope():
    rows = _rows(
        g=GlobalConfig(author_model="gm", author_temperature=0.3),
        s=StoryConfig(author_temperature=0.9),
        e=_env(prose_profile="lush"),
        eff=EffectiveSettings(author_model="gm", author_temperature=0.9, prose_profile="lush"),
    )
    assert rows["author_model"].source == "global"
    assert rows["author_temperature"].source == "story"
    assert rows["prose_profile"].source == "env"
    assert rows["agent_model"].source == "default"
    assert rows["author_temperature"].scope == "story"
    assert rows["llm_base_url"].scope == "global"


def test_env_rows_not_editable_others_are():
    rows = _rows(e=_env(prose_profile="lush"), eff=EffectiveSettings(prose_profile="lush"))
    assert rows["prose_profile"].editable is False
    assert rows["author_temperature"].editable is True


def test_secret_redacted_and_restart_flags():
    rows = _rows(g=GlobalConfig(llm_api_key="sk-secret"), eff=EffectiveSettings(llm_api_key="sk-secret"))
    assert "sk-secret" not in rows["llm_api_key"].value
    assert rows["llm_api_key"].restart_required is True
    assert rows["author_temperature"].restart_required is False
    assert RESTART_REQUIRED_KEYS == {"llm_base_url", "llm_api_key", "author_model", "agent_model", "embed_model"}


def test_app_managed_and_derived_keys_hidden():
    rows = _rows()
    for hidden in ("last_opened_story", "suppress_flat_migration_prompt", "db_path", "chroma_path", "story_title"):
        assert hidden not in rows


def test_story_scope_rows_sort_first():
    ordered = build_settings_rows(GlobalConfig(), StoryConfig(), _env(), EffectiveSettings())
    scopes = [r.scope for r in ordered]
    assert scopes == sorted(scopes, key=lambda s: 0 if s == "story" else 1)


def test_parse_value_types():
    assert parse_value("author_interval", "120") == 120
    assert parse_value("author_temperature", "0.5") == 0.5
    assert parse_value("prose_profile", "lush") == "lush"
    with pytest.raises(ValueError):
        parse_value("author_interval", "not-a-number")


def test_apply_edit_story_scope_roundtrip(tmp_path):
    sd = create_story(tmp_path / "novel", title="N")
    apply_edit("author_temperature", "0.9", story_dir=sd, global_path=tmp_path / "g.toml")
    assert load_toml_file(sd.story_toml)["author_temperature"] == 0.9
    assert load_toml_file(sd.story_toml)["title"] == "N"  # preserved
    apply_edit("author_temperature", "", story_dir=sd, global_path=tmp_path / "g.toml")
    assert "author_temperature" not in load_toml_file(sd.story_toml)


def test_apply_edit_global_scope(tmp_path):
    sd = create_story(tmp_path / "novel", title="N")
    gpath = tmp_path / "g.toml"
    apply_edit("llm_base_url", "http://h:9/v1", story_dir=sd, global_path=gpath)
    assert load_toml_file(gpath)["llm_base_url"] == "http://h:9/v1"
    assert "llm_base_url" not in (load_toml_file(sd.story_toml))


def test_apply_edit_redacts_secret_in_message(tmp_path):
    sd = create_story(tmp_path / "novel", title="N")
    gpath = tmp_path / "g.toml"
    msg = apply_edit("llm_api_key", "sk-very-secret", story_dir=sd, global_path=gpath)
    assert "sk-very-secret" not in msg
    assert "llm_api_key" in msg
    assert load_toml_file(gpath)["llm_api_key"] == "sk-very-secret"  # still written correctly


def test_load_layer_configs(tmp_path):
    sd = create_story(tmp_path / "novel", title="N")
    write_toml_file(sd.story_toml, {"title": "N", "prose_profile": "lush"})
    gpath = tmp_path / "g.toml"
    write_toml_file(gpath, {"author_model": "gm"})
    g, s, e = load_layer_configs(sd, global_path=gpath)
    assert g.author_model == "gm"
    assert s.prose_profile == "lush"


def test_layer_model_defaults_are_none_for_source_attribution():
    """Every non-hidden EffectiveSettings field that a layer model exposes
    must default to None there — build_settings_rows relies on 'is None'
    meaning 'unset in this layer' to attribute the winning source."""
    from novelizer.settings.view_model import _HIDDEN_KEYS

    for layer_cls in (GlobalConfig, StoryConfig, EnvOverrides):
        for key in EffectiveSettings.model_fields:
            if key in _HIDDEN_KEYS:
                continue
            field = layer_cls.model_fields.get(key)
            if field is None:
                continue  # layer doesn't expose this key at all — fine
            assert field.default is None, (
                f"{layer_cls.__name__}.{key} must default to None for source attribution"
            )
