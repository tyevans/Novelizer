from __future__ import annotations
from novelizer.agents.registry import AGENT_REGISTRY
from novelizer.agents.registry_types import AgentSpec

EXPECTED_ORDER = [
    "world_architect", "character_keeper", "muse",
    "plotter", "author",
    "editor", "continuity_checker", "retconner", "structure_analyst",
    "summarizer", "triage", "flaglabeler",
]


def test_registry_lists_every_spec_in_scheduling_order():
    assert [spec.name for spec in AGENT_REGISTRY] == EXPECTED_ORDER


def test_registry_names_are_unique():
    names = [spec.name for spec in AGENT_REGISTRY]
    assert len(names) == len(set(names))


def test_every_entry_is_an_agent_spec_with_callable_construct():
    for spec in AGENT_REGISTRY:
        assert isinstance(spec, AgentSpec)
        assert callable(spec.construct)


def test_muse_has_no_tool_grant():
    muse_spec = next(spec for spec in AGENT_REGISTRY if spec.name == "muse")
    assert muse_spec.tool_grant is None


def test_tooled_agents_declare_the_correct_settings_field():
    expected = {
        "world_architect": "world_architect_tools_enabled",
        "character_keeper": "character_keeper_tools_enabled",
        "editor": "editor_tools_enabled",
        "continuity_checker": "checker_tools_enabled",
        "retconner": "retconner_tools_enabled",
        "structure_analyst": "structure_analyst_tools_enabled",
        "plotter": "plotter_tools_enabled",
        "author": "author_tools_enabled",
    }
    by_name = {spec.name: spec for spec in AGENT_REGISTRY}
    for name, setting in expected.items():
        assert by_name[name].tool_grant.enabled_setting == setting
