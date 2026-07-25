from __future__ import annotations

from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

from novelizer.settings.layers import (
    GlobalConfig,
    StoryConfig,
    global_config_path,
    parse_global,
    parse_story,
)
from novelizer.settings.models import EffectiveSettings
from novelizer.settings.story_dir import StoryDirectory
from novelizer.settings.toml_io import load_toml_file


class EnvOverrides(BaseSettings):
    """NOVELIZER_* environment variables — the highest-precedence layer."""

    # env_file=".env" is intentional back-compat with the legacy Settings
    # behavior: implicit repo-root .env pickup.
    model_config = SettingsConfigDict(env_prefix="NOVELIZER_", env_file=".env", extra="ignore")

    llm_base_url: str | None = None
    llm_api_key: str | None = None
    llm_max_tokens: int | None = None
    embed_base_url: str | None = None
    embed_api_key: str | None = None
    default_stories_dir: str | None = None
    last_opened_story: str | None = None
    suppress_flat_migration_prompt: bool | None = None

    voice_pack: str | None = None
    prose_profile: str | None = None
    author_model: str | None = None
    agent_model: str | None = None
    embed_model: str | None = None
    author_temperature: float | None = None
    agent_temperature: float | None = None
    author_interval: int | None = None
    default_agent_interval: int | None = None
    continuity_interval: int | None = None
    structure_analyst_interval: int | None = None
    plotter_interval: int | None = None
    muse_interval: int | None = None
    projector_interval: float | None = None
    prior_chapter_summary_chars: int | None = None
    keeper_prose_chars: int | None = None
    staleness_threshold_chapters: int | None = None
    sag_spike_delta: float | None = None
    muse_era: str | None = None
    muse_exclusion_hands: int | None = None
    max_concurrent_agents: int | None = None
    llm_pool_size: int | None = None
    background_drain_concurrency: int | None = None
    extractor_token_budget: int | None = None
    advisory_token_budget: int | None = None
    summarizer_interval: int | None = None
    author_tools_enabled: bool | None = None
    outline_gate_enabled: bool | None = None
    checker_tools_enabled: bool | None = None
    chat_tools_enabled: bool | None = None
    world_architect_tools_enabled: bool | None = None
    character_keeper_tools_enabled: bool | None = None
    editor_tools_enabled: bool | None = None
    retconner_tools_enabled: bool | None = None
    structure_analyst_tools_enabled: bool | None = None
    plotter_tools_enabled: bool | None = None
    world_architect_subagent_enabled: bool | None = None
    character_keeper_subagent_enabled: bool | None = None
    editor_subagent_enabled: bool | None = None
    retconner_subagent_enabled: bool | None = None
    structure_analyst_subagent_enabled: bool | None = None
    plotter_subagent_enabled: bool | None = None
    author_subagent_enabled: bool | None = None
    checker_subagent_enabled: bool | None = None


def build_effective(
    global_cfg: GlobalConfig,
    story_cfg: StoryConfig,
    env: EnvOverrides,
    story_dir: StoryDirectory | None = None,
) -> EffectiveSettings:
    """Pure merge: defaults <- global <- story <- env; storage paths derived
    from the story directory when one is given."""
    merged: dict = {}
    merged.update(global_cfg.model_dump(exclude_none=True))
    merged.update(story_cfg.model_dump(exclude_none=True, exclude={"title"}))
    merged.update(env.model_dump(exclude_none=True))
    if story_dir is not None:
        merged["db_path"] = str(story_dir.db_path)
        merged["chroma_path"] = str(story_dir.chroma_path)
    return EffectiveSettings(story_title=story_cfg.title, **merged)


def load_effective_settings(
    story_dir: StoryDirectory | None = None,
    global_path: Path | None = None,
) -> EffectiveSettings:
    gpath = global_path if global_path is not None else global_config_path()
    global_cfg = parse_global(load_toml_file(gpath), source=str(gpath)) if gpath.exists() else GlobalConfig()
    story_cfg = StoryConfig()
    if story_dir is not None and story_dir.story_toml.exists():
        story_cfg = parse_story(load_toml_file(story_dir.story_toml), source=str(story_dir.story_toml))
    return build_effective(global_cfg, story_cfg, EnvOverrides(), story_dir=story_dir)
