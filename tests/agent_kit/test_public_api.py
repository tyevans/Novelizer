from __future__ import annotations

import agent_kit

# The two interval-multiplier backoff constants are deliberately absent: the
# ladders they were replaced by are seconds-based and internal to the chassis,
# so nothing outside agent_kit has a reason to name them.
EXPECTED = {
    "BaseAgent", "Runner",
    "Scheduler",
    "TelemetryEventType", "TelemetryEmitter",
    "AgentRunStarted", "AgentRunFinished", "AgentRunFailed",
    "SchedulerPicked", "SchedulerEligibilityChanged",
    "current_run_id", "current_agent_name",
    "build_chat_model", "build_agent_runner",
    "GRAPH_RECURSION_LIMIT", "CONTEXT_WINDOW_TOKENS", "LLM_MAX_RETRIES",
    "ExcludeToolsMiddleware",
}


def test_all_matches_expected_surface():
    assert set(agent_kit.__all__) == EXPECTED


def test_every_name_importable():
    for name in agent_kit.__all__:
        assert getattr(agent_kit, name) is not None
