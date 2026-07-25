import pytest
from textual.app import App, ComposeResult
from tui_kit.run_model import ProseBlock, ThinkingBlock, CallBlock, ToolBlock
from tui_kit.stream_source import InMemoryStreamSource
from tui_kit.widgets.stream_view import StreamView
from textual.widgets import Collapsible, Markdown, Static


class _Theme:
    def glyph(self, n): return {"author": "@", "editor": "#"}.get(n, "?")
    def label(self, n): return n.title()
    def style(self, n): return "gold3"
    def verb(self, n): return "working"


class _App(App):
    def compose(self) -> ComposeResult:
        yield StreamView(theme=_Theme(), source=InMemoryStreamSource([], {}), id="stream")


@pytest.mark.asyncio
async def test_appending_blocks_mounts_one_widget_each():
    async with _App().run_test() as pilot:
        view = pilot.app.query_one("#stream", StreamView)
        view.append_blocks((ProseBlock(text="a", agent_name="author"),
                            ToolBlock(tool_name="read_file", input_summary="x",
                                      agent_name="editor")))
        await pilot.pause()
        assert len(view.mounted_keys()) == 2


@pytest.mark.asyncio
async def test_reappending_the_same_blocks_does_not_remount_them():
    """Streaming prose updates the trailing block on every token; remounting
    the world each time would make the pane unusable."""
    async with _App().run_test() as pilot:
        view = pilot.app.query_one("#stream", StreamView)
        view.append_blocks((ProseBlock(text="a", agent_name="author"),))
        await pilot.pause()
        first = view.mounted_keys()
        view.append_blocks(())
        await pilot.pause()
        assert view.mounted_keys() == first


@pytest.mark.asyncio
async def test_every_block_kind_mounts_without_error():
    async with _App().run_test() as pilot:
        view = pilot.app.query_one("#stream", StreamView)
        view.append_blocks((ProseBlock(text="p", agent_name="author"),
                            ThinkingBlock(text="t", agent_name="author"),
                            CallBlock(call_index=1, model="m", agent_name="author"),
                            ToolBlock(tool_name="t", input_summary="i", agent_name="author")))
        await pilot.pause()
        assert len(view.mounted_keys()) == 4


@pytest.mark.asyncio
async def test_blocks_from_different_agents_interleave_in_arrival_order():
    async with _App().run_test() as pilot:
        view = pilot.app.query_one("#stream", StreamView)
        view.append_blocks((ProseBlock(text="a", agent_name="author"),
                            ProseBlock(text="b", agent_name="editor"),
                            ProseBlock(text="c", agent_name="author")))
        await pilot.pause()
        assert len(view.mounted_keys()) == 3


class _MDApp(App):
    def compose(self) -> ComposeResult:
        source = InMemoryStreamSource([], {42: "# Chapter One\n\n- a\n- b"})
        yield StreamView(theme=_Theme(), source=source, id="stream")


@pytest.mark.asyncio
async def test_tool_blocks_mount_collapsed_by_default():
    async with _App().run_test() as pilot:
        view = pilot.app.query_one("#stream", StreamView)
        view.append_blocks((ToolBlock(tool_name="read_file", input_summary="ch1.md",
                                      status="done", agent_name="author", sequence=42),))
        await pilot.pause()
        assert pilot.app.query_one(Collapsible).collapsed is True


@pytest.mark.asyncio
async def test_failed_tool_calls_auto_expand():
    async with _App().run_test() as pilot:
        view = pilot.app.query_one("#stream", StreamView)
        view.append_blocks((ToolBlock(tool_name="write_scene", input_summary="ch4",
                                      status="failed", error="ValidationError",
                                      agent_name="author", sequence=7),))
        await pilot.pause()
        assert pilot.app.query_one(Collapsible).collapsed is False


@pytest.mark.asyncio
async def test_expanding_fetches_the_full_output_and_renders_markdown():
    async with _MDApp().run_test() as pilot:
        view = pilot.app.query_one("#stream", StreamView)
        view.append_blocks((ToolBlock(tool_name="read_file", input_summary="ch1.md",
                                      status="done", agent_name="author", sequence=42),))
        await pilot.pause()
        pilot.app.query_one(Collapsible).collapsed = False
        await pilot.pause()
        assert pilot.app.query(Markdown)


@pytest.mark.asyncio
async def test_output_is_fetched_once_not_on_every_toggle():
    class _CountingSource(InMemoryStreamSource):
        calls = 0

        async def fetch_output(self, sequence):
            _CountingSource.calls += 1
            return await super().fetch_output(sequence)

    class _CountApp(App):
        def compose(self):
            yield StreamView(theme=_Theme(),
                             source=_CountingSource([], {42: "plain output"}), id="stream")

    async with _CountApp().run_test() as pilot:
        view = pilot.app.query_one("#stream", StreamView)
        view.append_blocks((ToolBlock(tool_name="t", input_summary="i", status="done",
                                      agent_name="author", sequence=42),))
        await pilot.pause()
        c = pilot.app.query_one(Collapsible)
        for collapsed in (False, True, False):
            c.collapsed = collapsed
            await pilot.pause()
        assert _CountingSource.calls == 1


@pytest.mark.asyncio
async def test_a_new_view_follows_the_tail():
    async with _App().run_test() as pilot:
        assert pilot.app.query_one("#stream", StreamView).is_following() is True


@pytest.mark.asyncio
async def test_scrolling_away_from_the_bottom_detaches():
    async with _App().run_test() as pilot:
        view = pilot.app.query_one("#stream", StreamView)
        view.append_blocks(tuple(ProseBlock(text=f"line {i}", agent_name="author")
                                 for i in range(200)))
        await pilot.pause()
        view.notify_scroll(at_bottom=False)
        assert view.is_following() is False


@pytest.mark.asyncio
async def test_returning_to_the_bottom_reattaches():
    async with _App().run_test() as pilot:
        view = pilot.app.query_one("#stream", StreamView)
        view.notify_scroll(at_bottom=False)
        view.notify_scroll(at_bottom=True)
        assert view.is_following() is True


@pytest.mark.asyncio
async def test_detached_view_shows_the_backlog_count_and_hides_it_when_following():
    async with _App().run_test() as pilot:
        view = pilot.app.query_one("#stream", StreamView)
        view.notify_scroll(at_bottom=False)
        view.append_blocks((ProseBlock(text="x", agent_name="author"),
                            ProseBlock(text="y", agent_name="author")))
        await pilot.pause()
        bar = pilot.app.query_one("#sv_follow", Static)
        assert bar.display is True
        assert "2" in str(bar.renderable)
        view.action_follow_end()
        await pilot.pause()
        assert bar.display is False


@pytest.mark.asyncio
async def test_end_reattaches_and_clears_the_backlog():
    async with _App().run_test() as pilot:
        view = pilot.app.query_one("#stream", StreamView)
        view.notify_scroll(at_bottom=False)
        view.append_blocks((ProseBlock(text="x", agent_name="author"),))
        view.action_follow_end()
        assert view.is_following() is True


@pytest.mark.asyncio
async def test_appending_while_detached_does_not_scroll_the_window():
    """The whole bug: a new token must not yank the reader back down."""
    async with _App().run_test() as pilot:
        view = pilot.app.query_one("#stream", StreamView)
        view.append_blocks(tuple(ProseBlock(text=f"l{i}", agent_name="author")
                                 for i in range(200)))
        await pilot.pause()
        window = pilot.app.query_one("#sv_window")
        window.scroll_to(y=0, animate=False)
        await pilot.pause()
        view.notify_scroll(at_bottom=False)
        view.append_blocks((ProseBlock(text="new", agent_name="author"),))
        await pilot.pause()
        assert window.scroll_offset.y == 0


@pytest.mark.asyncio
async def test_real_scrolling_flips_follow_state_without_calling_notify_scroll():
    """Proves the widget's own scroll wiring drives detach/reattach -- not
    just that on_scroll(state, at_bottom) has correct arithmetic. Never
    calls notify_scroll() directly."""
    async with _App().run_test() as pilot:
        view = pilot.app.query_one("#stream", StreamView)
        view.append_blocks(tuple(ProseBlock(text=f"l{i}", agent_name="author")
                                 for i in range(200)))
        await pilot.pause()
        assert view.is_following() is True

        window = pilot.app.query_one("#sv_window")
        window.scroll_to(y=0, animate=False)
        await pilot.pause()
        assert view.is_following() is False

        window.scroll_end(animate=False)
        await pilot.pause()
        assert view.is_following() is True
