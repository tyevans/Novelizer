import pydantic
import pytest

from novelizer.settings.models import (
    EffectiveSettings,
    STORY_OVERRIDABLE_KEYS,
    FORBIDDEN_STORY_KEYS,
)


def test_defaults_match_legacy_settings():
    s = EffectiveSettings()
    assert s.db_path == "stories/world.db"
    assert s.chroma_path == "stories/chroma"
    assert s.embed_model == "nomic-embed-text"
    assert s.llm_base_url == "http://localhost:8080/v1"
    assert s.llm_api_key == "not-needed"
    assert s.author_model == "local-model"
    assert s.author_temperature == 0.8
    assert s.agent_model == "local-model"
    assert s.agent_temperature == 0.7
    assert s.author_interval == 300
    assert s.default_agent_interval == 120
    assert s.continuity_interval == 900
    assert s.structure_analyst_interval == 180
    assert s.projector_interval == 0.5
    assert s.voice_pack.endswith("default.toml")
    assert s.prose_profile == "plain"
    assert s.story_title is None
    assert s.default_stories_dir == "stories"
    assert s.last_opened_story is None


def test_effective_settings_is_frozen():
    s = EffectiveSettings()
    with pytest.raises(pydantic.ValidationError):
        s.author_model = "other"


def test_key_sets():
    assert "llm_api_key" in FORBIDDEN_STORY_KEYS
    assert "llm_api_key" not in STORY_OVERRIDABLE_KEYS
    assert "llm_base_url" not in STORY_OVERRIDABLE_KEYS
    assert "voice_pack" in STORY_OVERRIDABLE_KEYS
    assert "embed_model" in STORY_OVERRIDABLE_KEYS
    assert STORY_OVERRIDABLE_KEYS <= set(EffectiveSettings.model_fields)


def test_llm_max_tokens_default_caps_generation():
    assert EffectiveSettings().llm_max_tokens == 4096


def test_max_concurrent_agents_default_is_2():
    assert EffectiveSettings().max_concurrent_agents == 2
    assert "max_concurrent_agents" not in STORY_OVERRIDABLE_KEYS


def test_llm_pool_size_default_is_6():
    """Phase 3: the shared AIMD pool's target ceiling. 6 is the middle of the
    stated 4-8 usable vLLM concurrency. Global-only, not story-overridable --
    it describes the endpoint's real capacity, an installation/hardware fact,
    not a per-story creative choice, exactly like max_concurrent_agents."""
    assert EffectiveSettings().llm_pool_size == 6
    assert "llm_pool_size" not in STORY_OVERRIDABLE_KEYS


def test_llm_pool_size_round_trips_through_effective_settings():
    assert EffectiveSettings(llm_pool_size=8).llm_pool_size == 8


def test_background_drain_concurrency_default_is_4():
    """Phase 5: caps how many aggregate partitions the background drain fans out
    into concurrently -- a task-count bound (1000 pending aggregates must not
    spawn 1000 tasks), independent of the shared LLM pool's endpoint ceiling.
    Global-only, like llm_pool_size and max_concurrent_agents: it bounds a
    process-wide resource, not a per-story creative knob."""
    assert EffectiveSettings().background_drain_concurrency == 4
    assert "background_drain_concurrency" not in STORY_OVERRIDABLE_KEYS


def test_background_drain_concurrency_round_trips_through_effective_settings():
    assert EffectiveSettings(background_drain_concurrency=8).background_drain_concurrency == 8


def test_prior_chapter_summary_chars_default_is_200():
    assert EffectiveSettings().prior_chapter_summary_chars == 200
    assert "prior_chapter_summary_chars" in STORY_OVERRIDABLE_KEYS


def test_keeper_prose_chars_default_is_6000():
    assert EffectiveSettings().keeper_prose_chars == 6000
    assert "keeper_prose_chars" in STORY_OVERRIDABLE_KEYS


def test_staleness_and_sag_spike_defaults():
    s = EffectiveSettings()
    assert s.staleness_threshold_chapters == 3
    assert s.sag_spike_delta == 0.3
    assert "staleness_threshold_chapters" in STORY_OVERRIDABLE_KEYS
    assert "sag_spike_delta" in STORY_OVERRIDABLE_KEYS


def test_author_and_checker_tools_enabled_defaults():
    s = EffectiveSettings()
    assert s.author_tools_enabled is True
    assert s.checker_tools_enabled is True
    assert "author_tools_enabled" in STORY_OVERRIDABLE_KEYS
    assert "checker_tools_enabled" in STORY_OVERRIDABLE_KEYS


def test_chat_tools_enabled_default():
    s = EffectiveSettings()
    assert s.chat_tools_enabled is True
    assert "chat_tools_enabled" in STORY_OVERRIDABLE_KEYS


@pytest.mark.parametrize("flag_name", [
    "world_architect_tools_enabled",
    "character_keeper_tools_enabled",
    "editor_tools_enabled",
    "retconner_tools_enabled",
    "structure_analyst_tools_enabled",
    "plotter_tools_enabled",
])
def test_phase_b_agent_tools_enabled_defaults(flag_name):
    """Phase-B per-agent tools_enabled flags default to True and are story-overridable."""
    s = EffectiveSettings()
    assert getattr(s, flag_name) is True
    assert flag_name in STORY_OVERRIDABLE_KEYS


def test_plotter_interval_default_is_240():
    s = EffectiveSettings()
    assert s.plotter_interval == 240
    assert "plotter_interval" in STORY_OVERRIDABLE_KEYS


def test_context_assembly_settings_defaults():
    s = EffectiveSettings()
    assert s.extractor_token_budget == 24000
    assert s.advisory_token_budget == 2000
    assert s.summarizer_interval == 300


def test_context_assembly_settings_story_overridable():
    assert {"extractor_token_budget", "advisory_token_budget",
            "summarizer_interval"} <= STORY_OVERRIDABLE_KEYS


# The seven agent-cadence *_interval keys. Phase 2 of the event-driven
# scheduling redesign deleted the clock gate, so these no longer govern
# dispatch -- ready() consults only the fail/idle ladders now. They stay in the
# model and in STORY_OVERRIDABLE_KEYS on purpose: removing them would hard-error
# on load for every existing story.toml / config.toml that still carries one.
# "Accepted and inert", never "gone".
_DEPRECATED_INTERVAL_KEYS = [
    "author_interval", "default_agent_interval", "continuity_interval",
    "structure_analyst_interval", "plotter_interval", "muse_interval",
    "summarizer_interval",
]


@pytest.mark.parametrize("key", _DEPRECATED_INTERVAL_KEYS)
def test_deprecated_interval_key_still_loads_and_round_trips(key):
    """Back-compat guard: each inert *_interval key must remain a valid
    EffectiveSettings field that round-trips a supplied value. If any is
    dropped from the model, an existing story that sets it will fail to load."""
    s = EffectiveSettings(**{key: 999})
    assert getattr(s, key) == 999


@pytest.mark.parametrize("key", _DEPRECATED_INTERVAL_KEYS)
def test_deprecated_interval_key_stays_story_overridable(key):
    """The keys must stay in STORY_OVERRIDABLE_KEYS: a story.toml that carries
    one must be accepted, not rejected as an unknown/forbidden override."""
    assert key in STORY_OVERRIDABLE_KEYS
