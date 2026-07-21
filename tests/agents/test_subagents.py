from __future__ import annotations
from novelizer.agents.subagents import build_researcher_subagent, RESEARCHER_SYSTEM_PROMPT


def test_returns_subagent_dict_with_expected_keys():
    spec = build_researcher_subagent("character_keeper")
    assert spec["name"] == "researcher"
    assert isinstance(spec["description"], str) and spec["description"]
    assert spec["system_prompt"].startswith(RESEARCHER_SYSTEM_PROMPT[:20])


def test_system_prompt_mentions_the_dispatching_agent_by_name():
    spec = build_researcher_subagent("continuity_checker")
    assert "continuity_checker" in spec["system_prompt"]


def test_extra_instructions_are_appended_to_the_system_prompt():
    spec = build_researcher_subagent("character_keeper", extra_instructions="\nCheck aliases too.")
    assert spec["system_prompt"].endswith("\nCheck aliases too.")


def test_no_tools_or_model_keys_present():
    """No tools/model keys -- deepagents inherits the parent's canon-read
    toolkit and model when both are omitted from a SubAgent spec."""
    spec = build_researcher_subagent("editor")
    assert "tools" not in spec
    assert "model" not in spec


def test_researcher_name_is_identical_across_dispatching_agents():
    """Same shared identity regardless of parent -- Design Decision 2."""
    a = build_researcher_subagent("author")
    b = build_researcher_subagent("retconner")
    assert a["name"] == b["name"] == "researcher"
