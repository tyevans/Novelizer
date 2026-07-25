"""The search_canon summarization kill switch.

Every semantic search costs an extra LLM call plus up to five file reads, on
the hot path of every pull-mode agent. This flag is how an operator turns that
bill off without a code change.
"""
from novelizer.settings.layers import GlobalConfig
from novelizer.settings.loader import StoryConfig
from novelizer.settings.models import EffectiveSettings


def test_defaults_to_on():
    assert EffectiveSettings().search_summarize is True


def test_global_config_can_turn_it_off():
    assert GlobalConfig(search_summarize=False).search_summarize is False


def test_global_config_leaves_it_unset_by_default():
    # None means "fall through to the built-in default", not "off".
    assert GlobalConfig().search_summarize is None


def test_story_config_can_turn_it_off():
    assert StoryConfig(search_summarize=False).search_summarize is False
