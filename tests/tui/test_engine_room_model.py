from hypothesis import given, strategies as st
from novelizer.canon.events import StoredEvent
from novelizer.telemetry.events import TelemetryEventType, TokenDelta
from novelizer.tui.widgets.engine_room_model import (
    LiveRunState, apply_bus_item, route_agent, seed_state, seed_states,
    strip_line, stream_line_kind, vitals_line, live_body, trace_line, trace_detail,
)


def _ev(seq, etype, payload, created_at="2026-07-18T12:04:32+00:00"):
    return StoredEvent(sequence=seq, id=f"e{seq}", event_type=etype,
                       aggregate_id="r1", payload=payload, created_at=created_at)


def _run_started(seq=1):
    return _ev(seq, TelemetryEventType.AGENT_RUN_STARTED,
               {"run_id": "r1", "agent_name": "author"})


def _call_started(seq=2):
    return _ev(seq, TelemetryEventType.LLM_CALL_STARTED,
               {"run_id": "r1", "agent_name": "author", "call_index": 1,
                "model": "qwen", "prompt": "[system]\nWrite."})


def test_run_started_resets_state_to_a_fresh_running_run():
    s = apply_bus_item(LiveRunState(text="stale", tokens=9), _run_started(), now=100.0)
    assert s.status == "running" and s.agent_name == "author" and s.run_id == "r1"
    assert s.tokens == 0 and s.text == "" and s.started_at == 100.0


def test_call_started_carries_prompt_model_and_index():
    s = apply_bus_item(LiveRunState(status="running", run_id="r1"), _call_started(), now=101.0)
    assert s.prompt == "[system]\nWrite." and s.model == "qwen" and s.call_index == 1


def test_token_deltas_accumulate_text_and_count():
    s = LiveRunState(status="running", run_id="r1", agent_name="author")
    s = apply_bus_item(s, TokenDelta(run_id="r1", agent_name="author", text="The "), now=1.0)
    s = apply_bus_item(s, TokenDelta(run_id="r1", agent_name="author", text="sea"), now=1.1)
    assert s.text == "The sea" and s.tokens == 2


def test_text_is_tail_capped():
    from novelizer.tui.widgets.engine_room_model import TEXT_CAP
    s = LiveRunState(status="running", run_id="r1", text="x" * TEXT_CAP)
    s = apply_bus_item(s, TokenDelta(run_id="r1", agent_name="author", text="END"), now=1.0)
    assert len(s.text) == TEXT_CAP and s.text.endswith("END")


def test_run_failed_marks_failed_with_error():
    s = LiveRunState(status="running", run_id="r1", agent_name="author")
    ev = _ev(3, TelemetryEventType.AGENT_RUN_FAILED,
             {"run_id": "r1", "agent_name": "author", "error_type": "TimeoutError",
              "error_message": "proxy", "phase": "llm_call", "duration_s": 4.0})
    s = apply_bus_item(s, ev, now=104.0)
    assert s.status == "failed" and "TimeoutError" in s.error and s.ended_at == 104.0


def test_strip_line_running_idle_and_failed_forms():
    running = LiveRunState(status="running", agent_name="author", started_at=100.0,
                           tokens=3400, call_index=1)
    line = strip_line(running, now=152.0)
    assert "▶" in line and "author" in line and "drafting" in line
    assert "3.4k tok" in line and "52s" in line
    idle = strip_line(LiveRunState(), now=0.0, next_hint="next: editor in 12s")
    assert idle.startswith("idle") and "next: editor in 12s" in idle
    failed = LiveRunState(status="failed", agent_name="author", ended_at=100.0)
    fline = strip_line(failed, now=220.0)
    assert "✗" in fline and "author" in fline and "Engine Room" in fline and "2m" in fline


def test_live_body_stream_not_attached_notice_after_restart_mid_run():
    s = seed_state([_run_started(), _call_started()], now=10.0)
    assert s.status == "running" and s.stream_attached is False
    assert "stream not attached" in live_body(s)


def test_seed_state_of_a_finished_run_is_not_stuck_running():
    fin = _ev(3, TelemetryEventType.AGENT_RUN_FINISHED,
              {"run_id": "r1", "agent_name": "author", "duration_s": 52.0})
    s = seed_state([_run_started(), _call_started(), fin], now=10.0)
    assert s.status == "finished"


def test_vitals_line_running_and_finished_forms():
    running = LiveRunState(status="running", agent_name="author", model="qwen",
                           call_index=2, tokens=1500, started_at=100.0)
    line = vitals_line(running, now=110.0)
    assert "author" in line and "qwen" in line and "call 2" in line and "1.5k tok" in line and "10s" in line

    finished = LiveRunState(status="finished", agent_name="author", tokens=2500,
                            started_at=100.0, ended_at=142.0)
    fline = vitals_line(finished, now=999.0)
    assert "author" in fline and "finished" in fline and "42s" in fline and "2.5k tok" in fline


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


def test_apply_bus_item_marks_the_boundary_between_thinking_and_answer_text():
    """Reasoning-model chunks arrive as separate thinking/text deltas: the
    switch between them must be visible, not silently concatenated."""
    s = LiveRunState(status="running", run_id="r1", agent_name="author")
    s = apply_bus_item(s, TokenDelta(run_id="r1", agent_name="author",
                                     text="let me consider", kind="thinking"), now=1.0)
    assert s.text == "\n💭 let me consider"
    s = apply_bus_item(s, TokenDelta(run_id="r1", agent_name="author",
                                     text=" the tide.", kind="thinking"), now=1.1)
    assert s.text == "\n💭 let me consider the tide."
    s = apply_bus_item(s, TokenDelta(run_id="r1", agent_name="author",
                                     text="The lighthouse", kind="text"), now=1.2)
    assert s.text == "\n💭 let me consider the tide.\nThe lighthouse"


def test_stream_line_kind_classifies_marker_lines():
    assert stream_line_kind("⚒ search_web(dragons)") == "tool"
    assert stream_line_kind("   ↳ done in 1.2s") == "call"
    assert stream_line_kind("▸ call 1 (qwen)") == "call"
    assert stream_line_kind("💭 thinking about it") == "thinking"
    assert stream_line_kind("Once upon a time") == "prose"


def test_route_agent_reads_agent_name_from_token_deltas_and_events():
    assert route_agent(TokenDelta(run_id="r1", agent_name="author", text="x")) == "author"
    started = _ev(1, TelemetryEventType.AGENT_RUN_STARTED,
                  {"run_id": "r1", "agent_name": "editor"})
    assert route_agent(started) == "editor"
    assert route_agent(TokenDelta(run_id="r1", agent_name="", text="x")) is None
    assert route_agent("not a bus item") is None


def test_seed_states_keeps_concurrent_agents_isolated():
    """Two agents can be running at once (max_concurrent_agents > 1): seeding
    must not let one agent's replayed run clobber another's bucket."""
    events = [
        _ev(1, TelemetryEventType.AGENT_RUN_STARTED, {"run_id": "r1", "agent_name": "author"}),
        _ev(2, TelemetryEventType.AGENT_RUN_STARTED, {"run_id": "r2", "agent_name": "editor"}),
        _ev(3, TelemetryEventType.TOOL_CALL_STARTED,
           {"run_id": "r1", "agent_name": "author", "tool_name": "search_web",
            "input_summary": "dragons"}),
        _ev(4, TelemetryEventType.TOOL_CALL_STARTED,
           {"run_id": "r2", "agent_name": "editor", "tool_name": "read",
            "input_summary": "ch1.md"}),
    ]
    states = seed_states(events, now=10.0)
    assert set(states) == {"author", "editor"}
    assert "search_web" in states["author"].text and "read" not in states["author"].text
    assert "ch1.md" in states["editor"].text and "search_web" not in states["editor"].text
    assert states["author"].run_id == "r1" and states["editor"].run_id == "r2"


def test_apply_bus_item_folds_llm_call_boundaries_into_the_live_text_stream():
    """Fix: on tool-calling turns the model may emit zero streamed tokens, so
    call start/finish must be visible on their own or the feed goes blank."""
    s = LiveRunState(status="running", run_id="r1", agent_name="author")
    s = apply_bus_item(s, _call_started(), now=1.0)
    assert "▸ call 1" in s.text and "qwen" in s.text

    fin = _ev(3, TelemetryEventType.LLM_CALL_FINISHED,
              {"run_id": "r1", "agent_name": "author", "call_index": 1,
               "duration_s": 2.5, "output_tokens": 40})
    s = apply_bus_item(s, fin, now=3.0)
    assert "2.5s" in s.text and "40 tok" in s.text


def test_apply_bus_item_folds_tool_calls_into_the_live_text_stream():
    s = LiveRunState(status="running", run_id="r1", agent_name="author", text="Once upon a time")
    started = _ev(6, TelemetryEventType.TOOL_CALL_STARTED,
                  {"run_id": "r1", "agent_name": "author", "tool_name": "search_web",
                   "input_summary": "dragons"})
    s = apply_bus_item(s, started, now=1.0)
    assert "⚒ search_web(dragons)" in s.text
    assert s.text.startswith("Once upon a time")

    finished = _ev(7, TelemetryEventType.TOOL_CALL_FINISHED,
                   {"run_id": "r1", "agent_name": "author", "tool_name": "search_web",
                    "duration_s": 1.2})
    s = apply_bus_item(s, finished, now=2.0)
    assert "done in 1.2s" in s.text


def test_apply_bus_item_folds_tool_call_failure_into_the_live_text_stream():
    s = LiveRunState(status="running", run_id="r1", agent_name="author")
    failed = _ev(8, TelemetryEventType.TOOL_CALL_FAILED,
                {"run_id": "r1", "agent_name": "author", "tool_name": "search_web",
                 "error_type": "ValueError"})
    s = apply_bus_item(s, failed, now=1.0)
    assert "✗ ValueError" in s.text


def test_trace_line_formats_tool_call_events():
    started = _ev(6, TelemetryEventType.TOOL_CALL_STARTED,
                  {"run_id": "r1", "agent_name": "author", "tool_name": "search_web",
                   "input_summary": "query text"})
    assert "search_web" in trace_line(started)
    finished = _ev(7, TelemetryEventType.TOOL_CALL_FINISHED,
                   {"run_id": "r1", "agent_name": "author", "tool_name": "search_web",
                    "duration_s": 1.2, "output_chars": 200})
    assert "search_web" in trace_line(finished)
    failed = _ev(8, TelemetryEventType.TOOL_CALL_FAILED,
                 {"run_id": "r1", "agent_name": "author", "tool_name": "search_web",
                  "duration_s": 0.5, "error_type": "ValueError", "error_message": "bad"})
    assert "search_web" in trace_line(failed)


def test_trace_line_sanitizes_tool_call_input_summary():
    """Fix 5: newlines in a tool's input_summary must not break the single-line
    trace rendering, and long inputs must be capped for display."""
    noisy = _ev(9, TelemetryEventType.TOOL_CALL_STARTED,
                {"run_id": "r1", "agent_name": "author", "tool_name": "grep",
                 "input_summary": "line one\nline two\nline three"})
    line = trace_line(noisy)
    assert "\n" not in line
    assert "␤" in line

    long_input = _ev(10, TelemetryEventType.TOOL_CALL_STARTED,
                      {"run_id": "r1", "agent_name": "author", "tool_name": "grep",
                       "input_summary": "x" * 500})
    long_line = trace_line(long_input)
    assert len(long_line) < 500 + 50


@given(st.lists(st.sampled_from([
    TelemetryEventType.AGENT_RUN_STARTED, TelemetryEventType.AGENT_RUN_FINISHED,
    TelemetryEventType.LLM_CALL_STARTED, TelemetryEventType.LLM_CALL_FINISHED,
    TelemetryEventType.SCHEDULER_PICKED, TelemetryEventType.SCHEDULER_ELIGIBILITY_CHANGED,
]), max_size=40))
def test_trace_replay_is_one_to_one_never_drops_or_duplicates(types):
    # Same invariant family as the causal-edge property: replay maps events
    # to rows 1:1 — a trace that dedupes or drops lies about the machinery.
    events = [_ev(i + 1, t, {"run_id": "r", "agent_name": "author", "eligible": True,
                             "reason": "ready", "call_index": 1, "model": "m", "prompt": "p",
                             "duration_s": 1.0, "output_tokens": 1, "error_type": "E",
                             "error_message": "m", "phase": "agent"})
              for i, t in enumerate(types)]
    lines = [trace_line(e) for e in events]
    assert len(lines) == len(events)
    assert all(isinstance(line, str) and line for line in lines)


def test_trace_detail_shows_prompt_and_produced_domain_events():
    call = _call_started()
    produced = [StoredEvent(sequence=9, id="d9", event_type="chapter.created",
                            aggregate_id="ch-12", payload={"title": "T"},
                            created_at="t", run_id="r1")]
    text = trace_detail(call, produced)
    assert "[system]\nWrite." in text
    assert "produced: chapter.created ch-12" in text


def test_styled_vitals_includes_glyph_and_vitals_line():
    from novelizer.tui.widgets.engine_room_model import styled_vitals
    state = LiveRunState(status="running", agent_name="author", started_at=0.0,
                         model="m", call_index=1, tokens=5)
    text = styled_vitals(state, now=2.0)
    plain = text.plain
    assert "author" in plain
    assert "✎" in plain  # author's glyph from identity_for


def test_styled_body_applies_tool_style_to_tool_lines():
    from novelizer.tui.widgets.engine_room_model import styled_body
    text = styled_body("\n⚒ search_canon(query)\n")
    # spans carries the style runs; a tool-prefixed line gets the "bold cyan" style
    styles = [span.style for span in text.spans]
    assert "bold cyan" in styles


def test_styled_body_leaves_prose_unstyled():
    from novelizer.tui.widgets.engine_room_model import styled_body
    text = styled_body("plain prose line")
    assert text.spans == []
