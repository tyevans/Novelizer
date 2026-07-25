from hypothesis import given, strategies as st
from novelizer.canon.events import StoredEvent
from novelizer.telemetry.events import TelemetryEventType, TokenDelta, ToolSummaryReady
from novelizer.tui.telemetry_adapter import to_contract_event, trace_line, trace_detail
from tui_kit.contracts import (
    RunStarted, RunFinished, RunFailed, LLMCallStarted, LLMCallFinished,
    ToolCallStarted, ToolCallFinished, ToolCallFailed,
)
from tui_kit.contracts import TokenDelta as ContractTokenDelta
from tui_kit.contracts import ToolSummaryReady as ContractToolSummaryReady


def _ev(seq, etype, payload, created_at="2026-07-18T12:04:32+00:00"):
    return StoredEvent(sequence=seq, id=f"e{seq}", event_type=etype,
                       aggregate_id="r1", payload=payload, created_at=created_at)


def test_token_delta_translates_1_to_1():
    item = TokenDelta(run_id="r1", agent_name="author", text="hi", kind="thinking")
    out = to_contract_event(item)
    assert out == ContractTokenDelta(run_id="r1", agent_name="author", text="hi", kind="thinking")


def test_tool_summary_ready_translates_1_to_1():
    item = ToolSummaryReady(run_id="r1", agent_name="author", tool_name="t",
                            input_summary="x", summary="s")
    out = to_contract_event(item)
    assert out == ContractToolSummaryReady(run_id="r1", agent_name="author", tool_name="t",
                                           input_summary="x", summary="s")


def test_run_started_translates():
    ev = _ev(1, TelemetryEventType.AGENT_RUN_STARTED, {"run_id": "r1", "agent_name": "author"})
    assert to_contract_event(ev) == RunStarted(run_id="r1", agent_name="author")


def test_run_finished_translates():
    ev = _ev(2, TelemetryEventType.AGENT_RUN_FINISHED,
            {"run_id": "r1", "agent_name": "author", "duration_s": 52.0})
    assert to_contract_event(ev) == RunFinished(run_id="r1", agent_name="author", duration_s=52.0)


def test_run_failed_translates():
    ev = _ev(3, TelemetryEventType.AGENT_RUN_FAILED,
            {"run_id": "r1", "agent_name": "author", "error_type": "TimeoutError",
             "error_message": "proxy", "phase": "llm_call", "duration_s": 4.0})
    out = to_contract_event(ev)
    assert out == RunFailed(run_id="r1", agent_name="author", error_type="TimeoutError",
                            error_message="proxy")


def test_llm_call_started_translates():
    ev = _ev(4, TelemetryEventType.LLM_CALL_STARTED,
            {"run_id": "r1", "agent_name": "author", "call_index": 1, "model": "qwen",
             "prompt": "[system]\nWrite."})
    out = to_contract_event(ev)
    assert out == LLMCallStarted(run_id="r1", agent_name="author", call_index=1,
                                 model="qwen", prompt="[system]\nWrite.")


def test_llm_call_finished_translates():
    ev = _ev(5, TelemetryEventType.LLM_CALL_FINISHED,
            {"run_id": "r1", "agent_name": "author", "call_index": 1,
             "duration_s": 2.5, "output_tokens": 40})
    out = to_contract_event(ev)
    assert out == LLMCallFinished(run_id="r1", agent_name="author", call_index=1,
                                  duration_s=2.5, output_tokens=40)


def test_tool_call_started_translates_without_pre_normalizing():
    """to_contract_event passes input_summary through raw -- tui_kit.run_model's
    apply_bus_item normalizes it, matching the original single-normalization
    contract (see normalize_input_summary's docstring)."""
    ev = _ev(6, TelemetryEventType.TOOL_CALL_STARTED,
            {"run_id": "r1", "agent_name": "author", "tool_name": "search_web",
             "input_summary": "line one\nline two", "delegate": "researcher"})
    out = to_contract_event(ev)
    assert out == ToolCallStarted(run_id="r1", agent_name="author", tool_name="search_web",
                                  input_summary="line one\nline two", delegate="researcher")


def test_tool_call_finished_translates():
    ev = _ev(7, TelemetryEventType.TOOL_CALL_FINISHED,
            {"run_id": "r1", "agent_name": "author", "tool_name": "search_web",
             "duration_s": 1.2, "output_summary": "found stuff"})
    out = to_contract_event(ev)
    assert out == ToolCallFinished(run_id="r1", agent_name="author", tool_name="search_web",
                                   duration_s=1.2, output_summary="found stuff")


def test_tool_call_failed_translates():
    ev = _ev(8, TelemetryEventType.TOOL_CALL_FAILED,
            {"run_id": "r1", "agent_name": "author", "tool_name": "search_web",
             "duration_s": 0.3, "error_type": "ValueError"})
    out = to_contract_event(ev)
    assert out == ToolCallFailed(run_id="r1", agent_name="author", tool_name="search_web",
                                 duration_s=0.3, error_type="ValueError")


def test_tool_results_carry_input_summary_for_pairing():
    """Telemetry's finished/failed payloads carry the call's input_summary;
    the adapter must pass it through so run_model can attach parallel
    same-tool results to the block that made that exact call."""
    fin = _ev(11, TelemetryEventType.TOOL_CALL_FINISHED,
             {"run_id": "r1", "agent_name": "author", "tool_name": "read_file",
              "duration_s": 1.2, "input_summary": "/characters/death.md",
              "output_summary": "# Death"})
    assert to_contract_event(fin).input_summary == "/characters/death.md"
    fail = _ev(12, TelemetryEventType.TOOL_CALL_FAILED,
              {"run_id": "r1", "agent_name": "author", "tool_name": "read_file",
               "duration_s": 0.3, "error_type": "ValueError",
               "input_summary": "/world/the-silvanthrine.md"})
    assert to_contract_event(fail).input_summary == "/world/the-silvanthrine.md"


def test_scheduler_events_and_unknown_event_types_translate_to_none():
    picked = _ev(9, TelemetryEventType.SCHEDULER_PICKED, {"agent_name": "author"})
    assert to_contract_event(picked) is None
    elig = _ev(10, TelemetryEventType.SCHEDULER_ELIGIBILITY_CHANGED,
              {"agent_name": "author", "eligible": True, "reason": "ready"})
    assert to_contract_event(elig) is None
    assert to_contract_event("not a bus item") is None


def test_trace_line_formats_key_event_shapes():
    fin = _ev(3, TelemetryEventType.AGENT_RUN_FINISHED,
              {"run_id": "r1", "agent_name": "author", "duration_s": 52.0})
    assert "12:04:32" in trace_line(fin) and "author" in trace_line(fin) and "✓" in trace_line(fin)
    fail = _ev(4, TelemetryEventType.AGENT_RUN_FAILED,
               {"run_id": "r1", "agent_name": "editor", "error_type": "TimeoutError",
                "error_message": "x", "phase": "agent", "duration_s": 1.0})
    assert "✗" in trace_line(fail) and "TimeoutError" in trace_line(fail)
    picked = _ev(5, TelemetryEventType.SCHEDULER_PICKED, {"agent_name": "author"})
    assert "picked author" in trace_line(picked)


def test_run_cancelled_translates_and_terminates_the_live_run():
    """A cancelled run is terminal, so the live view must close its block
    rather than show the agent running forever. tui_kit's run model has no
    cancelled status of its own, so it lands as a RunFailed carrying
    CancelledError -- the durable trace keeps the distinction."""
    from tui_kit.run_model import seed_state

    ev = _ev(5, TelemetryEventType.AGENT_RUN_CANCELLED,
             {"run_id": "r1", "agent_name": "author", "phase": "agent", "duration_s": 3.0})
    item = to_contract_event(ev)
    assert item == RunFailed(run_id="r1", agent_name="author",
                             error_type="CancelledError", error_message="run cancelled")

    started = _ev(4, TelemetryEventType.AGENT_RUN_STARTED,
                  {"run_id": "r1", "agent_name": "author"})
    state = seed_state([to_contract_event(started), item], now=100.0)
    assert state.status != "running"

    line = trace_line(ev)
    assert "author" in line and "cancelled" in line


def test_run_truncated_shows_in_the_trace_but_does_not_end_the_live_run():
    """Visibility without lying: the operator must be able to tell a run that
    answered from everything it needed from one the budget landed, so the
    durable trace names it and says how far it got. It maps to no contract event
    because it is NOT terminal -- the run is still going and its own
    run_finished follows, so marking the live block finished here would close it
    early and hide the rest of the run."""
    ev = _ev(6, TelemetryEventType.AGENT_RUN_TRUNCATED,
             {"run_id": "r1", "agent_name": "world_architect",
              "stage": "forced", "tool_calls": 41})
    assert to_contract_event(ev) is None
    line = trace_line(ev)
    assert "world_architect" in line
    assert "truncated" in line and "forced" in line and "41" in line


def test_trace_line_sanitizes_tool_call_input_summary():
    noisy = _ev(9, TelemetryEventType.TOOL_CALL_STARTED,
                {"run_id": "r1", "agent_name": "author", "tool_name": "grep",
                 "input_summary": "line one\nline two\nline three"})
    line = trace_line(noisy)
    assert "\n" not in line and "␤" in line
    long_input = _ev(10, TelemetryEventType.TOOL_CALL_STARTED,
                      {"run_id": "r1", "agent_name": "author", "tool_name": "grep",
                       "input_summary": "x" * 500})
    assert len(trace_line(long_input)) < 550


@given(st.lists(st.sampled_from([
    TelemetryEventType.AGENT_RUN_STARTED, TelemetryEventType.AGENT_RUN_FINISHED,
    TelemetryEventType.LLM_CALL_STARTED, TelemetryEventType.LLM_CALL_FINISHED,
    TelemetryEventType.SCHEDULER_PICKED, TelemetryEventType.SCHEDULER_ELIGIBILITY_CHANGED,
]), max_size=40))
def test_trace_replay_is_one_to_one_never_drops_or_duplicates(types):
    events = [_ev(i + 1, t, {"run_id": "r", "agent_name": "author", "eligible": True,
                             "reason": "ready", "call_index": 1, "model": "m", "prompt": "p",
                             "duration_s": 1.0, "output_tokens": 1, "error_type": "E",
                             "error_message": "m", "phase": "agent"})
              for i, t in enumerate(types)]
    lines = [trace_line(e) for e in events]
    assert len(lines) == len(events)
    assert all(isinstance(line, str) and line for line in lines)


def test_unmapped_stored_events_are_filtered_before_seeding_not_raised():
    """The app's restart-seed path builds `[c for c in (to_contract_event(e)
    for e in recent) if c is not None]` before calling seed_state/seed_states
    (see novelizer/tui/app.py _telemetry_bus_loop). A raw StoredEvent type
    with no mapping (e.g. a scheduler event) must come back as None here so
    that filter silently drops it -- not raise, and not survive into the
    seeded state as some stale/garbage entry."""
    from tui_kit.run_model import seed_state, seed_states, LiveRunState

    started = _ev(1, TelemetryEventType.AGENT_RUN_STARTED,
                 {"run_id": "r1", "agent_name": "author"})
    unmapped = _ev(2, TelemetryEventType.SCHEDULER_PICKED, {"agent_name": "author"})
    finished = _ev(3, TelemetryEventType.AGENT_RUN_FINISHED,
                  {"run_id": "r1", "agent_name": "author", "duration_s": 1.0})

    raw = [started, unmapped, finished]
    adapted = [to_contract_event(e) for e in raw]
    assert adapted == [RunStarted(run_id="r1", agent_name="author"), None,
                       RunFinished(run_id="r1", agent_name="author", duration_s=1.0)]

    filtered = [c for c in adapted if c is not None]
    assert None not in filtered
    assert len(filtered) == 2

    state = seed_state(filtered, now=100.0)
    assert state == seed_state([c for c in adapted if c], now=100.0)
    assert state.status == "finished"  # not left dangling as "running"

    per_agent = seed_states(filtered, now=100.0)
    assert per_agent["author"] == state
    assert per_agent["author"] != LiveRunState()


def test_trace_detail_shows_prompt_and_produced_domain_events():
    call = _ev(2, TelemetryEventType.LLM_CALL_STARTED,
              {"run_id": "r1", "agent_name": "author", "call_index": 1, "model": "qwen",
               "prompt": "[system]\nWrite."})
    produced = [StoredEvent(sequence=9, id="d9", event_type="chapter.created",
                            aggregate_id="ch-12", payload={"title": "T"},
                            created_at="t", run_id="r1")]
    text = trace_detail(call, produced)
    assert "[system]\nWrite." in text
    assert "produced: chapter.created ch-12" in text
