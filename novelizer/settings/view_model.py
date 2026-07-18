from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from novelizer.settings.global_store import update_global_config
from novelizer.settings.layers import (
    GlobalConfig,
    StoryConfig,
    global_config_path,
    parse_global,
    parse_story,
)
from novelizer.settings.loader import EnvOverrides
from novelizer.settings.models import STORY_OVERRIDABLE_KEYS, EffectiveSettings
from novelizer.settings.story_dir import StoryDirectory
from novelizer.settings.toml_io import load_toml_file, write_toml_file

RESTART_REQUIRED_KEYS: frozenset[str] = frozenset({
    "llm_base_url", "llm_api_key", "llm_max_tokens", "author_model", "agent_model", "embed_model",
})

SECRET_KEYS: frozenset[str] = frozenset({"llm_api_key"})
_SECRET_KEYS = SECRET_KEYS  # internal alias, kept for existing call sites in this module
_HIDDEN_KEYS: frozenset[str] = frozenset({
    "last_opened_story", "suppress_flat_migration_prompt",
    "db_path", "chroma_path", "story_title",
})
_REDACTED = "••••••"


@dataclass(frozen=True)
class SettingsRow:
    key: str
    value: str
    source: str   # default | global | story | env
    scope: str    # story | global — which file an edit writes
    editable: bool
    restart_required: bool


def load_layer_configs(
    story_dir: StoryDirectory | None,
    global_path: Path | None = None,
) -> tuple[GlobalConfig, StoryConfig, EnvOverrides]:
    gpath = global_path if global_path is not None else global_config_path()
    global_cfg = parse_global(load_toml_file(gpath), source=str(gpath)) if gpath.exists() else GlobalConfig()
    story_cfg = StoryConfig()
    if story_dir is not None and story_dir.story_toml.exists():
        story_cfg = parse_story(load_toml_file(story_dir.story_toml), source=str(story_dir.story_toml))
    return global_cfg, story_cfg, EnvOverrides()


def build_settings_rows(
    global_cfg: GlobalConfig,
    story_cfg: StoryConfig,
    env: EnvOverrides,
    effective: EffectiveSettings,
) -> list[SettingsRow]:
    rows: list[SettingsRow] = []
    for key in EffectiveSettings.model_fields:
        if key in _HIDDEN_KEYS:
            continue
        if getattr(env, key, None) is not None:
            source = "env"
        elif getattr(story_cfg, key, None) is not None:
            source = "story"
        elif getattr(global_cfg, key, None) is not None:
            source = "global"
        else:
            source = "default"
        scope = "story" if key in STORY_OVERRIDABLE_KEYS else "global"
        value = _REDACTED if key in _SECRET_KEYS else str(getattr(effective, key))
        rows.append(SettingsRow(
            key=key,
            value=value,
            source=source,
            scope=scope,
            editable=source != "env",
            restart_required=key in RESTART_REQUIRED_KEYS,
        ))
    return sorted(rows, key=lambda r: (0 if r.scope == "story" else 1, r.key))


def parse_value(key: str, raw: str):
    annotation = EffectiveSettings.model_fields[key].annotation
    raw = raw.strip()
    try:
        if annotation is int:
            return int(raw)
        if annotation is float:
            return float(raw)
        if annotation is bool:
            if raw.lower() in ("true", "1"):
                return True
            if raw.lower() in ("false", "0"):
                return False
            raise ValueError(raw)
        return raw
    except ValueError:
        raise ValueError(f"{key}: {raw!r} is not a valid {getattr(annotation, '__name__', annotation)}") from None


def apply_edit(
    key: str,
    raw: str,
    story_dir: StoryDirectory,
    global_path: Path | None = None,
) -> str:
    """Write one edit to the owning file. The runtime is never touched here —
    the settings watcher picks the file change up (single apply path)."""
    scope = "story" if key in STORY_OVERRIDABLE_KEYS else "global"
    if scope == "global":
        value = parse_value(key, raw)
        update_global_config(path=global_path, **{key: value})
        shown = _REDACTED if key in _SECRET_KEYS else value
        return f"{key} = {shown} (global)"
    data = load_toml_file(story_dir.story_toml) if story_dir.story_toml.exists() else {}
    if raw.strip() == "":
        data.pop(key, None)
        write_toml_file(story_dir.story_toml, data)
        return f"{key} cleared — inherits again"
    value = parse_value(key, raw)
    data[key] = value
    write_toml_file(story_dir.story_toml, data)
    shown = _REDACTED if key in _SECRET_KEYS else value
    return f"{key} = {shown} (this story)"
