"""FlagLabeler runs the light path.

It has no tools, its output is a noun phrase and one sentence, and it is
dispatched once per unlabelled flag -- the cheapest agent in the fleet was
paying for a deepagents graph and (on a reasoning model) a thinking block to
produce eight words.
"""
from __future__ import annotations

from novelizer.agents.flaglabeler import build_flaglabeler_runner
from novelizer.agents.schemas import FlagLabel
from novelizer.settings.models import EffectiveSettings


def _settings(**kw) -> EffectiveSettings:
    return EffectiveSettings(llm_base_url="http://localhost:9999/v1",
                             llm_api_key="k", agent_model="big", **kw)


def test_runner_is_graph_free():
    """No deepagents graph: the runner is the single-call kind."""
    from agent_kit.llm import _SimpleRunner
    runner = build_flaglabeler_runner(_settings())
    assert isinstance(runner, _SimpleRunner)


def test_runner_uses_the_light_model_when_one_is_configured():
    runner = build_flaglabeler_runner(_settings(light_model="tiny"))
    assert runner.model_name == "tiny"


def test_runner_falls_back_to_the_agent_model():
    """Default install has no light_model, and must keep working unchanged."""
    runner = build_flaglabeler_runner(_settings())
    assert runner.model_name == "big"


def test_runner_honors_the_light_reasoning_switch():
    off = build_flaglabeler_runner(_settings())
    on = build_flaglabeler_runner(_settings(light_reasoning=True))
    assert off.thinking_enabled is False
    assert on.thinking_enabled is True


def test_runner_asks_for_the_flag_label_schema():
    runner = build_flaglabeler_runner(_settings())
    assert runner.response_format is FlagLabel


def test_generation_cap_stays_tight():
    """A label is eight words; the old builder capped at 200 tokens and that
    ceiling is the honest one to keep."""
    runner = build_flaglabeler_runner(_settings())
    assert runner.max_tokens <= 200
