from __future__ import annotations

import logging
import os
from pathlib import Path

from pydantic import BaseModel

from novelizer.settings.models import FORBIDDEN_STORY_KEYS

logger = logging.getLogger("novelizer.settings")


class StoryConfigError(Exception):
    """story.toml contains keys that must never appear there (secrets)."""


class GlobalConfig(BaseModel):
    """~/.config/novelizer/config.toml. All fields optional: None means 'unset,
    fall through to built-in defaults'."""

    llm_base_url: str | None = None
    llm_api_key: str | None = None
    default_stories_dir: str | None = None
    last_opened_story: str | None = None

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
    projector_interval: float | None = None


class StoryConfig(BaseModel):
    """story.toml inside a story directory. Overrides global defaults."""

    title: str | None = None

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
    projector_interval: float | None = None


def global_config_path() -> Path:
    base = os.environ.get("XDG_CONFIG_HOME") or str(Path.home() / ".config")
    return Path(base) / "novelizer" / "config.toml"


def parse_global(data: dict, source: str) -> GlobalConfig:
    known = set(GlobalConfig.model_fields)
    for key in sorted(data.keys() - known):
        logger.warning("%s: unknown setting %r ignored", source, key)
    return GlobalConfig(**{k: v for k, v in data.items() if k in known})


def parse_story(data: dict, source: str) -> StoryConfig:
    forbidden = sorted(data.keys() & FORBIDDEN_STORY_KEYS)
    if forbidden:
        raise StoryConfigError(
            f"{source}: {forbidden} must not appear in story.toml — stories are shareable; "
            f"secrets belong in the global config ({global_config_path()})"
        )
    known = set(StoryConfig.model_fields)
    for key in sorted(data.keys() - known):
        logger.warning("%s: unknown or global-only setting %r ignored", source, key)
    return StoryConfig(**{k: v for k, v in data.items() if k in known})
