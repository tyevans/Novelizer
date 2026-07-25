"""The fleet fold: many agents' runs merged into one chronological stream.

A LiveRunState is ONE run -- RunStarted resets it and any event for another
run_id is dropped. A fleet view needs a fold per agent plus a stable global
ordering across them.
"""
from tui_kit.contracts import (
    LLMCallStarted, RunFinished, RunStarted, TokenDelta, ToolCallStarted,
)
from tui_kit.fleet_model import (
    FleetState, apply_fleet, blocks, fleet_vitals, primary_state, seed_fleet,
    set_run_state,
)
from tui_kit.run_model import LiveRunState, ProseBlock


class _Theme:
    def glyph(self, n): return {"author": "@", "editor": "#"}.get(n, "?")
    def label(self, n): return n.title()
    def style(self, n): return "gold3"
    def verb(self, n): return "working"


THEME = _Theme()


def _interleaved():
    return [
        RunStarted(run_id="r1", agent_name="author"),
        TokenDelta(run_id="r1", agent_name="author", text="the sea "),
        RunStarted(run_id="r2", agent_name="editor"),
        TokenDelta(run_id="r2", agent_name="editor", text="looks good"),
        TokenDelta(run_id="r1", agent_name="author", text="rose"),
    ]


def _fold(items, now=1.0):
    fleet = FleetState()
    for item in items:
        fleet = apply_fleet(fleet, item, now)
    return fleet


def test_a_second_agent_starting_does_not_freeze_the_first():
    fleet = _fold(_interleaved())
    texts = [b.text for b in blocks(fleet) if isinstance(b, ProseBlock)]
    assert texts == ["the sea rose", "looks good"]


def test_the_merged_order_is_arrival_order_and_never_reshuffles():
    fleet = _fold(_interleaved())
    agents = [b.agent_name for b in blocks(fleet)]
    assert agents == ["author", "editor"]  # author's block opened first
    fleet = apply_fleet(fleet, TokenDelta(run_id="r2", agent_name="editor", text="!"), 2.0)
    assert [b.agent_name for b in blocks(fleet)] == ["author", "editor"]


def test_a_blocks_position_is_stable_as_it_mutates():
    """Widget identity is the block's absolute position. If a later event for
    an earlier agent moved its block, one block's content would land in
    another block's widget."""
    fleet = _fold(_interleaved())
    before = blocks(fleet)
    fleet = apply_fleet(fleet, TokenDelta(run_id="r1", agent_name="author", text=" over"), 2.0)
    after = blocks(fleet)
    assert len(after) == len(before)
    assert after[0].text == "the sea rose over"
    assert after[1].text == "looks good"


def test_a_new_run_for_the_same_agent_appends_rather_than_replaces():
    """The unified stream is a history, not a per-run pane."""
    fleet = _fold([
        RunStarted(run_id="r1", agent_name="author"),
        TokenDelta(run_id="r1", agent_name="author", text="first"),
        RunFinished(run_id="r1", agent_name="author", duration_s=1.0),
        RunStarted(run_id="r2", agent_name="author"),
        TokenDelta(run_id="r2", agent_name="author", text="second"),
    ])
    assert [b.text for b in blocks(fleet)] == ["first", "second"]


def test_seeding_restores_every_agent_not_just_the_latest_run():
    fleet = seed_fleet(_interleaved(), now=1.0)
    assert set(fleet.states) == {"author", "editor"}
    assert [b.text for b in blocks(fleet)] == ["the sea rose", "looks good"]


def test_seeding_marks_a_run_that_survived_a_restart_as_detached():
    fleet = seed_fleet(_interleaved(), now=1.0)
    assert fleet.states["author"].stream_attached is False


def test_items_with_no_agent_are_ignored():
    assert apply_fleet(FleetState(), "not an event", 1.0) == FleetState()


def test_primary_state_prefers_the_most_recently_started_running_agent():
    fleet = _fold(_interleaved())
    assert primary_state(fleet).agent_name == "editor"


def test_primary_state_of_an_empty_fleet_is_idle():
    assert primary_state(FleetState()).status == "idle"


def test_fleet_vitals_shows_every_running_agent():
    fleet = _fold(_interleaved() + [
        LLMCallStarted(run_id="r1", agent_name="author", call_index=1, model="qwen", prompt="p"),
    ])
    line = fleet_vitals(fleet, now=2.0, theme=THEME).plain
    assert "author" in line and "editor" in line


def test_fleet_vitals_captions_an_idle_fleet_with_the_hold_summary():
    line = fleet_vitals(FleetState(), now=2.0, theme=THEME, hold="2× paused").plain
    assert "2× paused" in line


def test_a_hold_never_captions_a_running_agent():
    fleet = _fold([RunStarted(run_id="r1", agent_name="author")])
    line = fleet_vitals(fleet, now=2.0, theme=THEME, hold="2× paused").plain
    assert "paused" not in line


def test_set_run_state_keys_a_nameless_state_by_its_run():
    """A caller may push a LiveRunState directly (the widget-level API). Two
    runs with no agent name must still be two groups, not one."""
    fleet = set_run_state(FleetState(), "",
                          LiveRunState(status="running", run_id="r1",
                                       blocks=(ProseBlock(text="a"),)))
    fleet = set_run_state(fleet, "",
                          LiveRunState(status="running", run_id="r2",
                                       blocks=(ProseBlock(text="b"),)))
    assert [b.text for b in blocks(fleet)] == ["a", "b"]


def test_tool_blocks_from_two_agents_stay_attached_to_their_own_runs():
    fleet = _fold([
        RunStarted(run_id="r1", agent_name="author"),
        ToolCallStarted(run_id="r1", agent_name="author", tool_name="read_file",
                        input_summary="ch1.md"),
        RunStarted(run_id="r2", agent_name="editor"),
        ToolCallStarted(run_id="r2", agent_name="editor", tool_name="read_file",
                        input_summary="ch2.md"),
    ])
    got = [(b.agent_name, b.input_summary) for b in blocks(fleet)]
    assert got == [("author", "ch1.md"), ("editor", "ch2.md")]
