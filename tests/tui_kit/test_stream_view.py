import pytest
from textual.app import App, ComposeResult
from tui_kit.run_model import ProseBlock, ThinkingBlock, CallBlock, ToolBlock
from tui_kit.stream_model import WINDOW_CAP
from tui_kit.stream_source import InMemoryStreamSource
from tui_kit.widgets.stream_view import StreamView
from textual.widgets import Button, Collapsible, Markdown, Static


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


@pytest.mark.asyncio
async def test_set_agents_renders_an_all_chip_plus_one_per_agent():
    async with _App().run_test() as pilot:
        view = pilot.app.query_one("#stream", StreamView)
        view.set_agents(["author", "editor", "plotter"])
        await pilot.pause()
        assert len(pilot.app.query(Button)) == 4


@pytest.mark.asyncio
async def test_set_agents_is_idempotent_and_does_not_duplicate_chips():
    async with _App().run_test() as pilot:
        view = pilot.app.query_one("#stream", StreamView)
        view.set_agents(["author", "editor"])
        await pilot.pause()
        view.set_agents(["author", "editor"])
        await pilot.pause()
        assert len(pilot.app.query(Button)) == 3


@pytest.mark.asyncio
async def test_a_growing_fleet_only_adds_chips_never_restructures_the_view():
    """The tab design broke at 13 agents. Chips must not."""
    async with _App().run_test() as pilot:
        view = pilot.app.query_one("#stream", StreamView)
        view.set_agents([f"agent{i}" for i in range(13)])
        await pilot.pause()
        assert len(pilot.app.query(Button)) == 14


@pytest.mark.asyncio
async def test_toggling_a_chip_hides_other_agents_blocks():
    async with _App().run_test() as pilot:
        view = pilot.app.query_one("#stream", StreamView)
        view.set_agents(["author", "editor"])
        view.append_blocks((ProseBlock(text="a", agent_name="author"),
                            ProseBlock(text="b", agent_name="editor")))
        await pilot.pause()
        view.toggle_agent_filter("author")
        await pilot.pause()
        assert view.active_filter() == frozenset({"author"})
        assert len(view.visible_keys()) == 1


@pytest.mark.asyncio
async def test_filtered_out_widgets_are_hidden_not_unmounted():
    """Toggling must be instant and must not disturb the window or fold state."""
    async with _App().run_test() as pilot:
        view = pilot.app.query_one("#stream", StreamView)
        view.set_agents(["author", "editor"])
        view.append_blocks((ProseBlock(text="a", agent_name="author"),
                            ProseBlock(text="b", agent_name="editor")))
        await pilot.pause()
        view.toggle_agent_filter("author")
        await pilot.pause()
        assert len(view.mounted_keys()) == 2
        assert len(view.visible_keys()) == 1


# -- block identity must be absolute -----------------------------------------
#
# block_key(b, index) is only a stable widget identity if `index` never
# shifts. Two things shift it: filtering (enumerating the *filtered* list
# renumbers every survivor) and trim_window (dropping blocks from the head
# renumbers everything after them). Either one makes _reconcile write one
# block's content into another block's widget.


def _rows(view):
    """Ordered (kind, rendered text) of the window's direct children -- the
    widgets themselves, in mount order, not the model they came from."""
    window = view.query_one("#sv_window")
    out = []
    for w in window.children:
        if isinstance(w, Collapsible):
            out.append(("tool", getattr(w.title, "plain", w.title)))
        else:
            out.append(("text", str(w.renderable)))
    return out


@pytest.mark.asyncio
async def test_a_filter_does_not_renumber_blocks_into_each_others_widgets():
    async with _App().run_test() as pilot:
        view = pilot.app.query_one("#stream", StreamView)
        view.set_agents(["author", "editor"])
        view.append_blocks((ProseBlock(text="a", agent_name="author"),
                            ProseBlock(text="b", agent_name="editor")))
        await pilot.pause()
        view.toggle_agent_filter("editor")
        await pilot.pause()
        view.append_blocks((ProseBlock(text="c", agent_name="editor"),))
        await pilot.pause()
        assert _rows(view) == [("text", "@ a"), ("text", "# b"), ("text", "# c")]


@pytest.mark.asyncio
async def test_trimming_the_window_does_not_shift_content_between_widgets():
    async with _App().run_test() as pilot:
        view = pilot.app.query_one("#stream", StreamView)
        head = [ProseBlock(text=f"l{i}", agent_name="author") for i in range(WINDOW_CAP)]
        head[300] = ToolBlock(tool_name="read_file", input_summary="ch1.md",
                              status="done", agent_name="author", sequence=1)
        view.append_blocks(tuple(head))
        await pilot.pause()
        collapsible = pilot.app.query_one(Collapsible)

        view.append_blocks(tuple(ProseBlock(text=f"n{i}", agent_name="author")
                                 for i in range(5)))
        await pilot.pause()

        kept = head[5:] + [ProseBlock(text=f"n{i}", agent_name="author") for i in range(5)]
        expected = [("tool", view._tool_summary_line(b)) if isinstance(b, ToolBlock)
                    else ("text", f"@ {b.text}") for b in kept]
        assert _rows(view) == expected
        assert pilot.app.query_one(Collapsible) is collapsible


@pytest.mark.asyncio
async def test_a_block_mutating_in_place_keeps_its_key_and_its_widget():
    """Prose grows token by token and a tool goes running -> done. Neither is
    a new block, so neither may get a new key or a new widget."""
    async with _App().run_test() as pilot:
        view = pilot.app.query_one("#stream", StreamView)
        run = (ProseBlock(text="he", agent_name="author"),
               ToolBlock(tool_name="read_file", input_summary="ch1.md",
                         status="running", agent_name="author"))
        view.sync_tail(run, replacing=0)
        await pilot.pause()
        keys = view.mounted_keys()
        prose_widget = view.query_one("#sv_window").children[0]
        collapsible = pilot.app.query_one(Collapsible)

        grown = (ProseBlock(text="hello", agent_name="author"),
                 ToolBlock(tool_name="read_file", input_summary="ch1.md",
                           status="done", duration_s=1.5, agent_name="author",
                           sequence=9))
        view.sync_tail(grown, replacing=2)
        await pilot.pause()

        assert view.mounted_keys() == keys
        assert view.query_one("#sv_window").children[0] is prose_widget
        assert pilot.app.query_one(Collapsible) is collapsible
        assert str(prose_widget.renderable) == "@ hello"
        assert "1.5s" in getattr(collapsible.title, "plain", collapsible.title)


# -- untrusted content must never reach a markup parser ----------------------
#
# A Collapsible's title is markup-parsed (CollapsibleTitle.validate_label ->
# Content.from_text(label, markup=True) for a plain str). The tool summary
# line interpolates model-authored tool arguments, error strings and a
# cheap-LLM summary, so a plain str title silently swallows "[b]" and RAISES
# MarkupError on "[/]" -- and since _reconcile re-sets the title on every
# refresh tick, it would raise twice a second forever.

_HOSTILE_TOOL = ToolBlock(tool_name="read_file",
                          input_summary='path=[b]ch1.md and an unclosed [/] tag',
                          status="failed", error="err: [/] bad", agent_name="author",
                          sequence=3)


@pytest.mark.asyncio
async def test_a_markup_hostile_tool_title_neither_raises_nor_loses_text():
    async with _App().run_test() as pilot:
        view = pilot.app.query_one("#stream", StreamView)
        view.append_blocks((_HOSTILE_TOOL,))
        await pilot.pause()
        title = pilot.app.query_one(Collapsible).title
        plain = getattr(title, "plain", title)
        assert "[b]" in plain and "[/]" in plain


@pytest.mark.asyncio
async def test_re_stating_a_markup_hostile_tool_block_does_not_raise():
    """_reconcile re-sets the title every tick; a MarkupError here propagates
    all the way out through render_live into the caller's bus loop."""
    async with _App().run_test() as pilot:
        view = pilot.app.query_one("#stream", StreamView)
        view.sync_tail((_HOSTILE_TOOL,), replacing=0)
        await pilot.pause()
        view.sync_tail((_HOSTILE_TOOL,), replacing=1)
        await pilot.pause()
        title = pilot.app.query_one(Collapsible).title
        assert "[/]" in getattr(title, "plain", title)


# -- sync_tail past the window cap -------------------------------------------


@pytest.mark.asyncio
async def test_sync_tail_past_the_window_cap_does_not_remount_the_world():
    """One run longer than WINDOW_CAP: `replacing` exceeds the number of
    blocks still in the window. Clamping it to the window length silently
    discards the offset, so every key misses and all 400 widgets unmount and
    remount on every tick -- losing fold and scroll state permanently."""
    async with _App().run_test() as pilot:
        view = pilot.app.query_one("#stream", StreamView)
        run = tuple(ProseBlock(text=f"l{i}", agent_name="author")
                    for i in range(WINDOW_CAP + 50))
        view.sync_tail(run, replacing=0)
        await pilot.pause()
        keys = view.mounted_keys()
        first_widget = view.query_one("#sv_window").children[0]

        grown = run + (ProseBlock(text="new", agent_name="author"),)
        view.sync_tail(grown, replacing=len(run))
        await pilot.pause()

        # One block in, one block out of the head: all but the first key survive.
        assert keys[1:] == view.mounted_keys()[:-1]
        assert view.query_one("#sv_window").children[0] is not first_widget
        assert _rows(view)[-1] == ("text", "@ new")


# -- the collapsed line must hint at the content ------------------------------


@pytest.mark.asyncio
async def test_the_collapsed_tool_line_shows_the_output_preview():
    async with _App().run_test() as pilot:
        view = pilot.app.query_one("#stream", StreamView)
        view.append_blocks((ToolBlock(tool_name="read_file", input_summary="ch1.md",
                                      status="done", duration_s=0.4, agent_name="author",
                                      sequence=42, preview="The sea rose over the wall"),))
        await pilot.pause()
        title = pilot.app.query_one(Collapsible).title
        assert "The sea rose over the wall" in getattr(title, "plain", title)


@pytest.mark.asyncio
async def test_a_tool_block_that_finishes_later_learns_its_store_sequence():
    """A tool block mounts while it is still running (sequence 0) and only
    learns its store sequence when the result arrives. The widget caches the
    sequence at mount time, so without a refresh the lazy fetch asks for 0
    and renders "(no output recorded)" forever."""
    async with _MDApp().run_test() as pilot:
        view = pilot.app.query_one("#stream", StreamView)
        view.sync_tail((ToolBlock(tool_name="read_file", input_summary="ch1.md",
                                  status="running", agent_name="author"),), replacing=0)
        await pilot.pause()
        view.sync_tail((ToolBlock(tool_name="read_file", input_summary="ch1.md",
                                  status="done", duration_s=0.4, agent_name="author",
                                  sequence=42),), replacing=1)
        await pilot.pause()
        collapsible = pilot.app.query_one(Collapsible)
        collapsible.collapsed = False
        await pilot.pause()
        assert pilot.app.query(Markdown)


@pytest.mark.asyncio
async def test_a_tool_block_that_fails_later_opens_itself():
    """Failures open on arrival -- including when the block was mounted as
    running and only failed on a later tick, which is the live path."""
    async with _App().run_test() as pilot:
        view = pilot.app.query_one("#stream", StreamView)
        view.sync_tail((ToolBlock(tool_name="write_scene", input_summary="ch4",
                                  status="running", agent_name="author"),), replacing=0)
        await pilot.pause()
        assert pilot.app.query_one(Collapsible).collapsed is True
        view.sync_tail((ToolBlock(tool_name="write_scene", input_summary="ch4",
                                  status="failed", error="ValidationError",
                                  agent_name="author", sequence=7),), replacing=1)
        await pilot.pause()
        assert pilot.app.query_one(Collapsible).collapsed is False
