from __future__ import annotations

import pytest

from research_domain.runners import (
    EXTRACTOR_SYSTEM_PROMPT,
    RETRACTOR_SYSTEM_PROMPT,
    VERIFIER_SYSTEM_PROMPT,
    ModelSettings,
    build_role_runner,
)

SETTINGS = ModelSettings(model="test-model", base_url="http://localhost:9999/v1", api_key="k")


def test_prompts_are_distinct_and_role_specific():
    assert "extract" in EXTRACTOR_SYSTEM_PROMPT.lower()
    assert "verif" in VERIFIER_SYSTEM_PROMPT.lower()
    assert len({EXTRACTOR_SYSTEM_PROMPT, VERIFIER_SYSTEM_PROMPT, RETRACTOR_SYSTEM_PROMPT}) == 3


def test_build_role_runner_constructs_for_each_role():
    for role in ("extractor", "verifier", "retractor"):
        runner = build_role_runner(role, SETTINGS, tools=[])
        assert hasattr(runner, "ainvoke")  # satisfies the Runner protocol


def test_unknown_role_raises():
    with pytest.raises(ValueError, match="unknown role"):
        build_role_runner("scout", SETTINGS, tools=[])
