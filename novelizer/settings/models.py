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
    "continuity_interval", "structure_analyst_interval", "projector_interval",
    "prior_chapter_summary_chars", "staleness_threshold_chapters", "sag_spike_delta",
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
    # Chapters elapsed since a thread's last touch before it's flagged stale.
    staleness_threshold_chapters: int = 3
    # Tension deviation from the mean, in either direction, that flags a chapter sag/spike.
    sag_spike_delta: float = 0.3
    # Scheduler dispatch pool size: how many agents may run concurrently.
    max_concurrent_agents: int = 2

    # Cadence (seconds)
    author_interval: int = 300
    default_agent_interval: int = 120
    continuity_interval: int = 900
    structure_analyst_interval: int = 180
    projector_interval: float = 0.5

    # Voice
    voice_pack: str = _DEFAULT_VOICE_PACK
    prose_profile: str = "plain"

    # Story metadata / app-level
    story_title: str | None = None
    default_stories_dir: str = "stories"
    last_opened_story: str | None = None
    suppress_flat_migration_prompt: bool = False
