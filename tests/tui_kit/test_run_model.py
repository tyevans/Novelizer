from tui_kit.contracts import (
    RunStarted, RunFinished, RunFailed, LLMCallStarted, LLMCallFinished,
    ToolCallStarted, ToolCallFinished, ToolCallFailed, TokenDelta, ToolSummaryReady,
)
from tui_kit.run_model import (
    Block, LiveRunState, TEXT_CAP, apply_bus_item, route_agent, seed_state,
    seed_states, strip_line, stream_line_kind, vitals_line, live_body,
    normalize_input_summary, styled_vitals, styled_body,
)


class _FakeTheme:
    _GLYPHS = {"author": "@", "editor": "#"}
    _VERBS = {"author": "drafting"}

    def glyph(self, agent_name):
        return self._GLYPHS.get(agent_name, "?")

    def label(self, agent_name):
        return agent_name.title()

    def style(self, agent_name):
        return "gold3" if agent_name == "author" else "dim"

    def verb(self, agent_name):
        return self._VERBS.get(agent_name, "working")


THEME = _FakeTheme()


def test_run_started_resets_state_to_a_fresh_running_run():
    s = apply_bus_item(LiveRunState(blocks=(Block(kind="prose", text="stale"),), tokens=9),
                        RunStarted(run_id="r1", agent_name="author"), now=100.0)
    assert s.status == "running" and s.agent_name == "author" and s.run_id == "r1"
    assert s.tokens == 0 and s.blocks == () and s.started_at == 100.0


def test_call_started_carries_prompt_model_and_index():
    s = apply_bus_item(LiveRunState(status="running", run_id="r1"),
                        LLMCallStarted(run_id="r1", agent_name="author", call_index=1,
                                       model="qwen", prompt="[system]\nWrite."), now=101.0)
    assert s.prompt == "[system]\nWrite." and s.model == "qwen" and s.call_index == 1


def test_token_deltas_accumulate_into_a_trailing_prose_block():
    s = LiveRunState(status="running", run_id="r1", agent_name="author")
    s = apply_bus_item(s, TokenDelta(run_id="r1", agent_name="author", text="The "), now=1.0)
    s = apply_bus_item(s, TokenDelta(run_id="r1", agent_name="author", text="sea"), now=1.1)
    assert len(s.blocks) == 1
    assert s.blocks[0].kind == "prose" and s.blocks[0].text == "The sea"
    assert s.tokens == 2


def test_thinking_and_text_deltas_open_separate_blocks():
    s = LiveRunState(status="running", run_id="r1", agent_name="author")
    s = apply_bus_item(s, TokenDelta(run_id="r1", agent_name="author",
                                     text="let me consider", kind="thinking"), now=1.0)
    assert s.blocks[0].kind == "thinking"
    s = apply_bus_item(s, TokenDelta(run_id="r1", agent_name="author",
                                     text="The lighthouse", kind="text"), now=1.2)
    assert len(s.blocks) == 2 and s.blocks[1].kind == "prose"


def test_run_failed_marks_failed_with_error():
    s = LiveRunState(status="running", run_id="r1", agent_name="author")
    s = apply_bus_item(s, RunFailed(run_id="r1", agent_name="author", error_type="TimeoutError",
                                    error_message="proxy"), now=104.0)
    assert s.status == "failed" and "TimeoutError" in s.error and s.ended_at == 104.0


def test_run_finished_marks_finished():
    s = LiveRunState(status="running", run_id="r1", agent_name="author")
    s = apply_bus_item(s, RunFinished(run_id="r1", agent_name="author", duration_s=52.0), now=200.0)
    assert s.status == "finished" and s.ended_at == 200.0


def test_llm_call_finished_closes_the_call_block():
    s = LiveRunState(status="running", run_id="r1", agent_name="author")
    s = apply_bus_item(s, LLMCallStarted(run_id="r1", agent_name="author", call_index=1,
                                         model="qwen", prompt="p"), now=1.0)
    s = apply_bus_item(s, LLMCallFinished(run_id="r1", agent_name="author", call_index=1,
                                          duration_s=2.5, output_tokens=40), now=3.0)
    call = s.blocks[0]
    assert call.status == "done" and call.duration_s == 2.5


def test_tool_call_opens_and_closes_a_tool_block():
    s = LiveRunState(status="running", run_id="r1", agent_name="author")
    s = apply_bus_item(s, ToolCallStarted(run_id="r1", agent_name="author",
                                          tool_name="search_web", input_summary="dragons"), now=1.0)
    tool = s.blocks[0]
    assert tool.kind == "tool" and tool.tool_name == "search_web" and tool.status == "running"
    s = apply_bus_item(s, ToolCallFinished(run_id="r1", agent_name="author",
                                           tool_name="search_web", duration_s=1.2), now=2.0)
    assert s.blocks[0].status == "done" and s.blocks[0].duration_s == 1.2
    assert len(s.blocks) == 1


def test_tool_call_failed_marks_the_block_failed():
    s = LiveRunState(status="running", run_id="r1", agent_name="author")
    s = apply_bus_item(s, ToolCallStarted(run_id="r1", agent_name="author",
                                          tool_name="search_web", input_summary="dragons"), now=1.0)
    s = apply_bus_item(s, ToolCallFailed(run_id="r1", agent_name="author", tool_name="search_web",
                                         duration_s=0.3, error_type="ValueError"), now=1.0)
    assert s.blocks[0].status == "failed" and s.blocks[0].error == "ValueError"


def test_repeated_identical_tool_calls_collapse_with_a_counter():
    s = LiveRunState(status="running", run_id="r1", agent_name="author")
    for _ in range(3):
        s = apply_bus_item(s, ToolCallStarted(run_id="r1", agent_name="author",
                                              tool_name="read_file", input_summary="ch3.md"), now=1.0)
        s = apply_bus_item(s, ToolCallFinished(run_id="r1", agent_name="author",
                                               tool_name="read_file", duration_s=0.1), now=1.1)
    assert len(s.blocks) == 1
    assert s.blocks[0].repeat_count == 3 and s.blocks[0].status == "done"


def test_different_delegates_do_not_collapse():
    s = LiveRunState(status="running", run_id="r1", agent_name="character_keeper")
    for delegate in ("", "researcher"):
        s = apply_bus_item(s, ToolCallStarted(run_id="r1", agent_name="character_keeper",
                                              tool_name="read_file", input_summary="ch3.md",
                                              delegate=delegate), now=1.0)
        s = apply_bus_item(s, ToolCallFinished(run_id="r1", agent_name="character_keeper",
                                               tool_name="read_file", duration_s=0.1), now=1.1)
    assert len(s.blocks) == 2
    assert all(b.repeat_count == 1 for b in s.blocks)


def test_parallel_same_tool_results_attach_to_their_own_call():
    """Regression: five parallel read_file calls had results attached LIFO to
    whichever same-named block was still running, so each error rendered under
    the wrong call. A result carrying input_summary must land on the block
    that made that exact call."""
    s = LiveRunState(status="running", run_id="r1", agent_name="continuity_checker")
    s = apply_bus_item(s, ToolCallStarted(run_id="r1", agent_name="continuity_checker",
                                          tool_name="read_file",
                                          input_summary="/characters/death.md"), now=1.0)
    s = apply_bus_item(s, ToolCallStarted(run_id="r1", agent_name="continuity_checker",
                                          tool_name="read_file",
                                          input_summary="/world/the-silvanthrine.md"), now=1.0)
    s = apply_bus_item(s, ToolCallFinished(run_id="r1", agent_name="continuity_checker",
                                           tool_name="read_file", duration_s=0.2,
                                           input_summary="/characters/death.md",
                                           output_summary="# Death"), now=1.2)
    death, silvanthrine = s.blocks
    assert death.status == "done" and death.output == "# Death"
    assert silvanthrine.status == "running"


def test_parallel_same_tool_failures_attach_to_their_own_call():
    s = LiveRunState(status="running", run_id="r1", agent_name="continuity_checker")
    for path in ("/world/a.md", "/world/b.md"):
        s = apply_bus_item(s, ToolCallStarted(run_id="r1", agent_name="continuity_checker",
                                              tool_name="read_file", input_summary=path), now=1.0)
    s = apply_bus_item(s, ToolCallFailed(run_id="r1", agent_name="continuity_checker",
                                         tool_name="read_file", duration_s=0.1,
                                         error_type="FileNotFoundError",
                                         input_summary="/world/a.md"), now=1.1)
    a, b = s.blocks
    assert a.status == "failed" and a.error == "FileNotFoundError"
    assert b.status == "running"


def test_result_input_summary_is_normalized_before_matching():
    """ToolCallStarted blocks store the normalized (120-char, ␤) summary while
    telemetry results carry the raw string — matching must normalize both."""
    raw = "/world/" + "x" * 200 + ".md"
    s = LiveRunState(status="running", run_id="r1", agent_name="author")
    s = apply_bus_item(s, ToolCallStarted(run_id="r1", agent_name="author",
                                          tool_name="read_file", input_summary=raw), now=1.0)
    s = apply_bus_item(s, ToolCallStarted(run_id="r1", agent_name="author",
                                          tool_name="read_file", input_summary="/world/y.md"), now=1.0)
    s = apply_bus_item(s, ToolCallFinished(run_id="r1", agent_name="author",
                                           tool_name="read_file", duration_s=0.2,
                                           input_summary=raw, output_summary="long"), now=1.2)
    assert s.blocks[0].status == "done" and s.blocks[0].output == "long"
    assert s.blocks[1].status == "running"


def test_results_without_input_summary_keep_the_last_running_fallback():
    """Producers that don't set input_summary on results still close blocks
    the old way: last running block with the same tool name."""
    s = LiveRunState(status="running", run_id="r1", agent_name="author")
    s = apply_bus_item(s, ToolCallStarted(run_id="r1", agent_name="author",
                                          tool_name="read_file", input_summary="ch1.md"), now=1.0)
    s = apply_bus_item(s, ToolCallFinished(run_id="r1", agent_name="author",
                                           tool_name="read_file", duration_s=0.1), now=1.1)
    assert s.blocks[0].status == "done"


def test_tool_summary_ready_patches_the_matching_finished_block():
    s = LiveRunState(status="running", run_id="r1", agent_name="author",
                     blocks=(Block(kind="tool", tool_name="search_web",
                                   input_summary="dragons", status="done", duration_s=1.0),))
    s = apply_bus_item(s, ToolSummaryReady(run_id="r1", agent_name="author",
                                           tool_name="search_web", input_summary="dragons",
                                           summary="found three articles"), now=5.0)
    assert s.blocks[0].summary == "found three articles"


def test_tool_summary_ready_is_a_no_op_when_the_run_has_moved_on():
    s = LiveRunState(status="running", run_id="r2", agent_name="author", blocks=())
    s2 = apply_bus_item(s, ToolSummaryReady(run_id="r1", agent_name="author", tool_name="search_web",
                                            input_summary="dragons", summary="stale"), now=5.0)
    assert s2 == s


def test_seed_state_of_a_finished_run_is_not_stuck_running():
    s = seed_state([RunStarted(run_id="r1", agent_name="author"),
                    RunFinished(run_id="r1", agent_name="author", duration_s=52.0)], now=10.0)
    assert s.status == "finished"


def test_seed_state_marks_stream_not_attached_when_still_running():
    s = seed_state([RunStarted(run_id="r1", agent_name="author")], now=10.0)
    assert s.status == "running" and s.stream_attached is False
    assert "stream not attached" in live_body(s)


def test_seed_states_keeps_concurrent_agents_isolated():
    events = [
        RunStarted(run_id="r1", agent_name="author"),
        RunStarted(run_id="r2", agent_name="editor"),
        ToolCallStarted(run_id="r1", agent_name="author", tool_name="search_web",
                        input_summary="dragons"),
        ToolCallStarted(run_id="r2", agent_name="editor", tool_name="read", input_summary="ch1.md"),
    ]
    states = seed_states(events, now=10.0)
    assert set(states) == {"author", "editor"}
    assert states["author"].blocks[0].tool_name == "search_web"
    assert states["editor"].blocks[0].tool_name == "read"


def test_route_agent_reads_agent_name_from_any_contract_event():
    assert route_agent(TokenDelta(run_id="r1", agent_name="author", text="x")) == "author"
    assert route_agent(RunStarted(run_id="r1", agent_name="editor")) == "editor"
    assert route_agent(TokenDelta(run_id="r1", agent_name="", text="x")) is None
    assert route_agent("not a bus item") is None


def test_strip_line_running_idle_and_failed_forms():
    running = LiveRunState(status="running", agent_name="author", started_at=100.0,
                           tokens=3400, call_index=1)
    line = strip_line(running, now=152.0, theme=THEME)
    assert "▶" in line and "author" in line and "drafting" in line
    assert "3.4k tok" in line and "52s" in line
    idle = strip_line(LiveRunState(), now=0.0, theme=THEME, next_hint="next: editor in 12s")
    assert idle.startswith("idle") and "next: editor in 12s" in idle
    failed = LiveRunState(status="failed", agent_name="author", ended_at=100.0)
    fline = strip_line(failed, now=220.0, theme=THEME)
    assert "✗" in fline and "author" in fline and "Engine Room" in fline and "2m" in fline


def test_vitals_line_running_and_finished_forms():
    running = LiveRunState(status="running", agent_name="author", model="qwen",
                           call_index=2, tokens=1500, started_at=100.0)
    line = vitals_line(running, now=110.0, theme=THEME)
    assert "author" in line and "qwen" in line and "call 2" in line and "1.5k tok" in line and "10s" in line
    finished = LiveRunState(status="finished", agent_name="author", tokens=2500,
                            started_at=100.0, ended_at=142.0)
    fline = vitals_line(finished, now=999.0, theme=THEME)
    assert "author" in fline and "finished" in fline and "42s" in fline and "2.5k tok" in fline


def test_live_body_renders_a_tool_block_as_a_grouped_multiline_unit():
    s = LiveRunState(status="running", run_id="r1", agent_name="author")
    s = apply_bus_item(s, ToolCallStarted(run_id="r1", agent_name="author",
                                          tool_name="search_web", input_summary="dragons"), now=1.0)
    s = apply_bus_item(s, ToolCallFinished(run_id="r1", agent_name="author",
                                           tool_name="search_web", duration_s=1.2), now=2.0)
    body = live_body(s)
    assert "⚒ search_web(dragons)" in body and "done in 1.2s" in body


def test_live_body_indents_delegated_tool_calls():
    s = LiveRunState(status="running", run_id="r1", agent_name="character_keeper",
                     blocks=(Block(kind="tool", tool_name="read_file",
                                   input_summary="/chapters/ch-0012.md",
                                   status="running", delegate="researcher"),))
    body = live_body(s)
    assert "    ⚒ ↳ researcher: read_file(/chapters/ch-0012.md)" in body


def test_text_is_still_tail_capped_via_prose_blocks():
    long_prose = "x" * TEXT_CAP
    s = LiveRunState(status="running", run_id="r1", blocks=(Block(kind="prose", text=long_prose),))
    s = apply_bus_item(s, TokenDelta(run_id="r1", agent_name="author", text="END"), now=1.0)
    assert s.blocks[-1].text.endswith("END")
    assert len(s.blocks[-1].text) <= TEXT_CAP + 3


def test_stream_line_kind_classifies_marker_lines():
    assert stream_line_kind("⚒ search_web(dragons)") == "tool"
    assert stream_line_kind("   ↳ done in 1.2s") == "call"
    assert stream_line_kind("▸ call 1 (qwen)") == "call"
    assert stream_line_kind("💭 thinking about it") == "thinking"
    assert stream_line_kind("Once upon a time") == "prose"


def test_normalize_input_summary_replaces_newlines_and_caps_length():
    raw = "line one\nline two\n" + "x" * 200
    normalized = normalize_input_summary(raw)
    assert "\n" not in normalized and len(normalized) <= 120


def test_tool_summary_ready_matches_a_multiline_over_120_char_input_summary():
    raw = "line one\nline two\nline three " + "x" * 200
    s = apply_bus_item(LiveRunState(status="running", run_id="r1", agent_name="author"),
                       ToolCallStarted(run_id="r1", agent_name="author", tool_name="search_web",
                                       input_summary=raw), now=1.0)
    s = apply_bus_item(s, ToolCallFinished(run_id="r1", agent_name="author",
                                           tool_name="search_web", duration_s=1.0), now=2.0)
    s = apply_bus_item(s, ToolSummaryReady(run_id="r1", agent_name="author", tool_name="search_web",
                                           input_summary=normalize_input_summary(raw),
                                           summary="found three articles"), now=5.0)
    assert s.blocks[0].summary == "found three articles"


def test_styled_vitals_includes_glyph_from_theme():
    state = LiveRunState(status="running", agent_name="author", started_at=0.0,
                         model="m", call_index=1, tokens=5)
    text = styled_vitals(state, now=2.0, theme=THEME)
    assert "author" in text.plain and "@" in text.plain


def test_styled_body_applies_tool_style_to_tool_lines():
    text = styled_body("\n⚒ search_canon(query)\n")
    styles = [span.style for span in text.spans]
    assert "bold cyan" in styles


def test_styled_body_leaves_prose_unstyled():
    text = styled_body("plain prose line")
    assert text.spans == []
