from __future__ import annotations

import importlib.resources

from pydantic import BaseModel, ConfigDict

_DEFAULT_VOICE_PACK = str(importlib.resources.files("novelizer.voices").joinpath("default.toml"))

# Settings a story.toml may override.
STORY_OVERRIDABLE_KEYS: frozenset[str] = frozenset({
    "voice_pack", "prose_profile",
    "author_model", "agent_model", "embed_model",
    "author_temperature", "agent_temperature",
    "author_interval", "default_agent_interval",
    "continuity_interval", "structure_analyst_interval", "projector_interval", "muse_interval",
    "prior_chapter_summary_chars", "keeper_prose_chars", "staleness_threshold_chapters", "sag_spike_delta",
    "muse_era", "muse_exclusion_hands",
    "author_tools_enabled", "checker_tools_enabled", "chat_tools_enabled",
    "world_architect_tools_enabled", "character_keeper_tools_enabled", "editor_tools_enabled",
    "retconner_tools_enabled", "structure_analyst_tools_enabled",
    "plotter_interval", "plotter_tools_enabled",
    "triage_interval", "triage_tools_enabled",
})

# Secrets: hard error if present in story.toml (stories are shareable).
FORBIDDEN_STORY_KEYS: frozenset[str] = frozenset({"llm_api_key"})


class EffectiveSettings(BaseModel):
    """Immutable merge of defaults <- global <- story <- env. Field names match
    the legacy Settings class; agent code and Runtime consume this unchanged."""

    model_config = ConfigDict(frozen=True)

    # Storage — derived from the story directory when one is given (see loader).
    db_path: str = "stories/world.db"
    chroma_path: str = "stories/chroma"
    embed_model: str = "nomic-embed-text"

    # OpenAI-compatible LLM endpoint (global-only in files)
    llm_base_url: str = "http://localhost:8080/v1"
    llm_api_key: str = "not-needed"
    author_model: str = "local-model"
    author_temperature: float = 0.8
    agent_model: str = "local-model"
    agent_temperature: float = 0.7
    # Per-request generation cap for every agent runner. Uncapped local models
    # (especially with server-side reasoning enabled) can generate past a
    # proxy's request timeout, so no request ever completes.
    llm_max_tokens: int = 4096
    # Chars of prior-chapter prose shown to the Author as context.
    prior_chapter_summary_chars: int = 200
    # Chars of each recent chapter shown to the Character Keeper. Must cover
    # whole chapters — characters introduced late in a chapter are invisible
    # to discovery otherwise.
    keeper_prose_chars: int = 6000
    # Chapters elapsed since a thread's last touch before it's flagged stale.
    staleness_threshold_chapters: int = 3
    # Tension deviation from the mean, in either direction, that flags a chapter sag/spike.
    sag_spike_delta: float = 0.3
    # Muse: era bucket for name draws (victorian/interwar/midcentury/late20th/modern)
    # and how many recent hands' items are excluded from a fresh deal.
    muse_era: str = "modern"
    muse_exclusion_hands: int = 3
    # Scheduler dispatch pool size: how many agents may run concurrently.
    max_concurrent_agents: int = 2

    # Cadence (seconds)
    author_interval: int = 300
    default_agent_interval: int = 120
    continuity_interval: int = 900
    structure_analyst_interval: int = 180
    plotter_interval: int = 240
    muse_interval: int = 60
    triage_interval: int = 120
    projector_interval: float = 0.5

    # Voice
    voice_pack: str = _DEFAULT_VOICE_PACK
    prose_profile: str = "plain"

    # Story metadata / app-level
    story_title: str | None = None
    default_stories_dir: str = "stories"
    last_opened_story: str | None = None
    suppress_flat_migration_prompt: bool = False

    # Tool enablement: whether Author, Checker, and chat personas can use external tools.
    author_tools_enabled: bool = True
    checker_tools_enabled: bool = True
    chat_tools_enabled: bool = True
    # Phase-B per-agent tool enablement flags
    world_architect_tools_enabled: bool = True
    character_keeper_tools_enabled: bool = True
    editor_tools_enabled: bool = True
    retconner_tools_enabled: bool = True
    structure_analyst_tools_enabled: bool = True
    plotter_tools_enabled: bool = True
    triage_tools_enabled: bool = True
