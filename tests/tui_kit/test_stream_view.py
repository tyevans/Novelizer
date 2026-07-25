import pytest
from textual.app import App, ComposeResult
from tui_kit.run_model import ProseBlock, ThinkingBlock, CallBlock, ToolBlock
from tui_kit.stream_source import InMemoryStreamSource
from tui_kit.widgets.stream_view import StreamView


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
