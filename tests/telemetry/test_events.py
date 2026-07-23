from novelizer.telemetry.events import (
    TelemetryEventType, SchedulerPicked, SchedulerEligibilityChanged,
    AgentRunStarted, AgentRunFinished, AgentRunFailed,
    LlmCallStarted, LlmCallFinished, LlmCallFailed, TokenDelta,
)


def test_event_type_constants_are_dotted_strings():
    assert TelemetryEventType.AGENT_RUN_STARTED == "agent.run_started"
    assert TelemetryEventType.AGENT_RUN_FINISHED == "agent.run_finished"
    assert TelemetryEventType.AGENT_RUN_FAILED == "agent.run_failed"
    assert TelemetryEventType.LLM_CALL_STARTED == "llm.call_started"
    assert TelemetryEventType.LLM_CALL_FINISHED == "llm.call_finished"
    assert TelemetryEventType.LLM_CALL_FAILED == "llm.call_failed"
    assert TelemetryEventType.SCHEDULER_PICKED == "scheduler.picked"
    assert TelemetryEventType.SCHEDULER_ELIGIBILITY_CHANGED == "scheduler.eligibility_changed"


def test_payloads_round_trip_through_model_dump():
    started = LlmCallStarted(run_id="r1", agent_name="author", call_index=1,
                             model="m", prompt="[system]\nWrite.")
    assert LlmCallStarted(**started.model_dump()).prompt == "[system]\nWrite."
    failed = AgentRunFailed(run_id="r1", agent_name="author", error_type="ValueError",
                            error_message="boom", phase="llm_call", duration_s=1.5)
    assert AgentRunFailed(**failed.model_dump()).phase == "llm_call"


def test_token_delta_is_a_plain_model_not_a_telemetry_event_type():
    # TokenDelta is bus-only: it has no entry in TelemetryEventType, by design.
    d = TokenDelta(run_id="r1", agent_name="author", text="The ")
    assert d.text == "The "
    assert not hasattr(TelemetryEventType, "TOKEN_DELTA")


from novelizer.telemetry.events import ToolSummaryReady


def test_tool_summary_ready_is_bus_only_shape():
    item = ToolSummaryReady(run_id="r1", agent_name="author", tool_name="search_web",
                            input_summary="dragons", summary="found three articles")
    assert item.run_id == "r1" and item.tool_name == "search_web"
    assert item.summary == "found three articles"


def test_machinery_vocabulary_is_shared_with_agent_kit():
    """The five loop/scheduler event types and payload models must BE the
    agent_kit objects (identity, not just equal shapes) — recorders and
    tui adapters must agree with what agent_kit.BaseAgent/Scheduler emit."""
    import agent_kit
    from novelizer.telemetry import events

    assert events.AgentRunStarted is agent_kit.AgentRunStarted
    assert events.AgentRunFinished is agent_kit.AgentRunFinished
    assert events.AgentRunFailed is agent_kit.AgentRunFailed
    assert events.SchedulerPicked is agent_kit.SchedulerPicked
    assert events.SchedulerEligibilityChanged is agent_kit.SchedulerEligibilityChanged
    assert issubclass(events.TelemetryEventType, agent_kit.TelemetryEventType)
    for const in ("SCHEDULER_PICKED", "SCHEDULER_ELIGIBILITY_CHANGED",
                  "AGENT_RUN_STARTED", "AGENT_RUN_FINISHED", "AGENT_RUN_FAILED"):
        assert getattr(events.TelemetryEventType, const) == getattr(
            agent_kit.TelemetryEventType, const)
