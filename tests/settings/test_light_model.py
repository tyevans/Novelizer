"""The third model tier.

There were two model knobs (author_model, agent_model) and therefore two
possible costs per pass. The light tier is the third: the model that labels a
flag or summarizes a tool call, which need not be the model that reasons about
continuity.
"""
from __future__ import annotations

from novelizer.settings.models import (
    STORY_OVERRIDABLE_KEYS, EffectiveSettings,
)
from novelizer.settings.view_model import RESTART_REQUIRED_KEYS


def test_light_model_defaults_to_the_agent_model():
    """Off by default: an installation that never sets light_model keeps
    running exactly one non-author model, as it does today."""
    s = EffectiveSettings(agent_model="big-thinker")
    assert s.light_model == ""
    assert s.resolved_light_model == "big-thinker"


def test_light_model_when_set_wins():
    s = EffectiveSettings(agent_model="big-thinker", light_model="tiny")
    assert s.resolved_light_model == "tiny"


def test_light_model_blank_is_treated_as_unset():
    """Same whitespace tolerance as resolved_embed_base_url -- a key left as
    an empty string in a toml is 'not configured', not 'the empty model'."""
    s = EffectiveSettings(agent_model="big-thinker", light_model="   ")
    assert s.resolved_light_model == "big-thinker"


def test_light_model_is_story_overridable():
    """A story with a different cast may want a different cheap model, for the
    same reason agent_model is overridable."""
    assert "light_model" in STORY_OVERRIDABLE_KEYS


def test_light_model_does_not_require_a_restart():
    """Unlike agent_model, every light consumer picks this up live: flaglabeler
    names it in rebuild_on, and the tool/search summarizers build a model per
    call. Flagging a restart would make the settings screen lie."""
    assert "light_model" not in RESTART_REQUIRED_KEYS


def test_light_reasoning_defaults_to_suppressed():
    """The point of the tier. Exposed as a setting anyway, because whether a
    template honors enable_thinking is a property of the served model, not
    something novelizer can know."""
    s = EffectiveSettings()
    assert s.light_reasoning is False


def test_light_reasoning_is_story_overridable():
    assert "light_reasoning" in STORY_OVERRIDABLE_KEYS
