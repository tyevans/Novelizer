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
    "retconner_tools_enabled", "curator_tools_enabled", "structure_analyst_tools_enabled",
    "plotter_interval", "plotter_tools_enabled",
    "world_architect_subagent_enabled", "character_keeper_subagent_enabled",
    "editor_subagent_enabled", "retconner_subagent_enabled", "curator_subagent_enabled", "structure_analyst_subagent_enabled",
    "plotter_subagent_enabled", "author_subagent_enabled", "checker_subagent_enabled",
    "extractor_token_budget", "advisory_token_budget", "summarizer_interval",
})

# Secrets: hard error if present in story.toml (stories are shareable).
FORBIDDEN_STORY_KEYS: frozenset[str] = frozenset({"llm_api_key", "embed_api_key"})


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
    # Separate embedding endpoint. Chat routers are not embedding providers --
    # OpenRouter, for one, serves no embedding models at all -- so the semantic
    # index needs its own endpoint whenever the chat endpoint can't embed.
    # Empty means "reuse the chat endpoint", which keeps the all-local
    # one-endpoint setup (Ollama, llama.cpp) working with no extra config.
    # Global-only, like llm_base_url: an installation fact, not a story knob.
    embed_base_url: str = ""
    embed_api_key: str = ""
    # DEPRECATED (context-assembly v2): no code path reads this any more.
    prior_chapter_summary_chars: int = 200
    # DEPRECATED (context-assembly v2): no code path reads this any more.
    keeper_prose_chars: int = 6000
    # Context-assembly protocol v2 (.specs/context-assembly-v2.md).
    # Per-run verbatim budget for extractor sweeps (Keeper mining, Summarizer
    # input): ~96k chars at the chars/4 heuristic — fits 128k-context local
    # models with headroom for instructions and output.
    extractor_token_budget: int = 24000
    # Packed story-so-far budget for push-mode advisory blocks.
    advisory_token_budget: int = 2000
    # Chapters elapsed since a thread's last touch before it's flagged stale.
    staleness_threshold_chapters: int = 3
    # Outline-first soft gate: when True, the Author will not draft until a
    # first-pass blueprint exists (or the genesis fallback opens). Turn OFF to
    # restore the legacy outline-optional behavior (draft first, retrofit later).
    outline_gate_enabled: bool = True
    # Tension deviation from the mean, in either direction, that flags a chapter sag/spike.
    sag_spike_delta: float = 0.3
    # Muse: era bucket for name draws (victorian/interwar/midcentury/late20th/modern)
    # and how many recent hands' items are excluded from a fresh deal.
    muse_era: str = "modern"
    muse_exclusion_hands: int = 3
    # Scheduler dispatch pool size: how many agents may run concurrently.
    max_concurrent_agents: int = 2
    # Shared LLM concurrency ceiling (the AdaptivePool target). Global-only,
    # like max_concurrent_agents: it sizes the vLLM endpoint's real capacity (an
    # installation/hardware fact, stated as 4-8 usable), not a per-story creative
    # knob, so it is deliberately NOT in STORY_OVERRIDABLE_KEYS. Both the
    # scheduler and background KG extraction draw permits from this one pool.
    llm_pool_size: int = 6
    # Fan-out cap for the background drain (Phase 5): how many aggregate
    # partitions the embedding indexer / KG projector may drain concurrently in
    # one catch_up pass. A task-count bound -- 1000 pending aggregates must not
    # spawn 1000 tasks -- independent of llm_pool_size, which is the endpoint's
    # LLM-concurrency ceiling. Global-only for the same reason as llm_pool_size
    # and max_concurrent_agents: it bounds a process-wide resource, not a
    # per-story creative knob, so it is deliberately NOT story-overridable.
    background_drain_concurrency: int = 4

    # Cadence (seconds)
    # DEPRECATED (Phase 2, event-driven scheduling): these seven agent
    # *_interval keys are accepted-and-inert. Dispatch no longer consults an
    # interval -- ready() = now >= max(_fail_until, _idle_until), governed by
    # the fail/idle backoff ladders (agent_kit BaseAgent). The fields are kept
    # in the model and in STORY_OVERRIDABLE_KEYS ONLY for config back-compat:
    # removing them would hard-error on load for every existing story.toml /
    # config.toml that still sets one. See Runtime.apply_settings' interval_map.
    author_interval: int = 300
    default_agent_interval: int = 120
    continuity_interval: int = 900
    structure_analyst_interval: int = 180
    plotter_interval: int = 240
    muse_interval: int = 60
    triage_interval: int = 120
    # NOT deprecated: projector_interval still paces the TUI projector,
    # scheduler, and status-bar loops -- it is not an agent-cadence key.
    projector_interval: float = 0.5
    summarizer_interval: int = 300

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
    curator_tools_enabled: bool = True
    structure_analyst_tools_enabled: bool = True
    plotter_tools_enabled: bool = True
    triage_tools_enabled: bool = True

    # Subagent (delegated researcher) enablement -- separate from *_tools_enabled
    # per-agent, and only meaningful when the matching tools flag is also on.
    world_architect_subagent_enabled: bool = False
    character_keeper_subagent_enabled: bool = False
    editor_subagent_enabled: bool = False
    retconner_subagent_enabled: bool = False
    curator_subagent_enabled: bool = False
    structure_analyst_subagent_enabled: bool = False
    plotter_subagent_enabled: bool = False
    author_subagent_enabled: bool = False
    checker_subagent_enabled: bool = False
    triage_subagent_enabled: bool = False

    @property
    def resolved_embed_base_url(self) -> str:
        """Endpoint the embedding function talks to: the dedicated one when set,
        otherwise the chat endpoint (single-endpoint local setups)."""
        return self.embed_base_url.strip() or self.llm_base_url

    @property
    def resolved_embed_api_key(self) -> str:
        """Key for the embedding endpoint.

        Deliberately does NOT fall back to llm_api_key once embed_base_url is
        set: a dedicated embedding endpoint is a *different* provider, so
        forwarding the chat key would leak a paid credential to an unrelated
        host. Only the shared-endpoint case reuses the chat key.
        """
        if self.embed_base_url.strip():
            return self.embed_api_key.strip() or "not-needed"
        return self.embed_api_key.strip() or self.llm_api_key
