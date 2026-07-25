from hypothesis import given, strategies as st
from tui_kit.run_model import ProseBlock, ToolBlock
from tui_kit.stream_model import (
    StreamState, visible_blocks, toggle_agent, clear_filter, on_scroll, on_new_blocks,
)


def _blocks():
    return (ProseBlock(text="a", agent_name="author"),
            ToolBlock(tool_name="read_file", input_summary="x", agent_name="editor"),
            ProseBlock(text="b", agent_name="author"))


def test_empty_filter_shows_every_agent():
    s = StreamState(blocks=_blocks())
    assert len(visible_blocks(s)) == 3


def test_toggling_an_agent_narrows_to_it():
    s = toggle_agent(StreamState(blocks=_blocks()), "author")
    assert [b.agent_name for b in visible_blocks(s)] == ["author", "author"]


def test_toggling_the_same_agent_twice_returns_to_everything():
    s = StreamState(blocks=_blocks())
    assert visible_blocks(toggle_agent(toggle_agent(s, "author"), "author")) == visible_blocks(s)


def test_clear_filter_restores_everything():
    s = toggle_agent(StreamState(blocks=_blocks()), "author")
    assert len(visible_blocks(clear_filter(s))) == 3


def test_scrolling_up_detaches_and_returning_to_bottom_reattaches():
    s = on_scroll(StreamState(), at_bottom=False)
    assert s.follow is False
    assert on_scroll(s, at_bottom=True).follow is True


def test_detached_stream_counts_unseen_blocks():
    s = on_scroll(StreamState(), at_bottom=False)
    s = on_new_blocks(s, (ProseBlock(text="x", agent_name="author"),))
    s = on_new_blocks(s, (ProseBlock(text="y", agent_name="author"),))
    assert s.unseen == 2


def test_following_stream_never_accumulates_unseen():
    s = on_new_blocks(StreamState(), (ProseBlock(text="x", agent_name="author"),))
    assert s.unseen == 0


def test_reattaching_clears_the_unseen_count():
    s = on_scroll(StreamState(), at_bottom=False)
    s = on_new_blocks(s, (ProseBlock(text="x", agent_name="author"),))
    assert on_scroll(s, at_bottom=True).unseen == 0


_AGENTS = st.sampled_from(["author", "editor", "plotter"])


@given(st.lists(_AGENTS.map(lambda a: ProseBlock(text="t", agent_name=a))),
       st.sets(_AGENTS))
def test_filtering_then_appending_equals_appending_then_filtering(blocks, agents):
    """The filter is a pure view over the block list, so it must not matter
    whether a block arrives before or after the filter is set."""
    state = StreamState(agent_filter=frozenset(agents))
    appended_then_filtered = visible_blocks(on_new_blocks(state, tuple(blocks)))
    filtered = [b for b in blocks if not agents or b.agent_name in agents]
    assert list(appended_then_filtered) == filtered
