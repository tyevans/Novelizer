"""The unified stream: one widget per block, all agents interleaved.

Replaces the tab-per-agent panes. Tabs scaled with fleet size and hid the
one thing this view exists to show -- who is running at the same time as
whom.
"""
from __future__ import annotations
from dataclasses import replace
from rich.text import Text
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical, VerticalScroll
from textual.widgets import Button, Collapsible, Static
from tui_kit.contracts import AgentTheme
from tui_kit.output_renderer import pick_renderer
from tui_kit.run_model import (
    CallBlock, ProseBlock, StreamBlock, ThinkingBlock, ToolBlock, block_agent,
    block_key,
)
from tui_kit.stream_model import (
    StreamState, clear_filter, on_new_blocks, on_scroll, toggle_agent, trim_window,
)
from tui_kit.stream_source import StreamSource

# How close to the bottom still counts as "at the bottom". A couple of
# lines of slack: demanding an exact match makes reattaching feel broken.
SCROLL_EPSILON = 2


class StreamView(Vertical):
    DEFAULT_CSS = """
    .sv-tool { border: round $panel; }
    """

    BINDINGS = [Binding("end", "follow_end", "Follow", show=True)]

    _ALL_CHIP = "sv_chip__all"

    def __init__(self, theme: AgentTheme, source: StreamSource, *args, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        self._theme = theme
        self._source = source
        self._state = StreamState()
        self._mounted: dict[str, Static | Collapsible] = {}
        self._agents: list[str] = []
        # How many blocks have been dropped from the head of the window so
        # far. A block's identity is its position in the whole stream, not
        # its position in `self._state.blocks` -- that list is renumbered
        # every time trim_window drops from the head, and a key that moves
        # is a key that points at another block's widget.
        self._base = 0

    def compose(self) -> ComposeResult:
        yield Horizontal(id="sv_filter", classes="sv-filter")
        yield VerticalScroll(id="sv_window", classes="sv-window")
        yield Static("", id="sv_follow", classes="sv-follow", markup=False)

    def on_mount(self) -> None:
        self.query_one("#sv_follow", Static).display = False
        # The real Textual hook: watch the child's reactive scroll_y so we
        # learn about scrolling however it happens -- wheel, drag, keys,
        # or programmatic scroll_to -- not just the two-message guesses
        # (on_scroll_up/on_scroll_down) that only cover discrete key/wheel
        # steps.
        window = self.query_one("#sv_window", VerticalScroll)
        self.watch(window, "scroll_y", self._on_window_scroll_y, init=False)

    def _on_window_scroll_y(self, old_value: float, new_value: float) -> None:
        self.notify_scroll(self._at_bottom())

    # -- public API ----------------------------------------------------------

    def is_following(self) -> bool:
        return self._state.follow

    def notify_scroll(self, at_bottom: bool) -> None:
        self._state = on_scroll(self._state, at_bottom)
        self._refresh_follow_bar()

    def _at_bottom(self) -> bool:
        window = self.query_one("#sv_window", VerticalScroll)
        return window.scroll_offset.y >= window.max_scroll_y - SCROLL_EPSILON

    def action_follow_end(self) -> None:
        self._state = on_scroll(self._state, at_bottom=True)
        self.query_one("#sv_window", VerticalScroll).scroll_end(animate=False)
        self._refresh_follow_bar()

    def _refresh_follow_bar(self) -> None:
        bar = self.query_one("#sv_follow", Static)
        if self._state.follow:
            bar.display = False
            return
        n = self._state.unseen
        bar.update(f"↓ detached · {n} new · End to follow")
        bar.display = True

    def _trim(self, state: StreamState) -> StreamState:
        """trim_window, plus the bookkeeping that keeps keys absolute."""
        trimmed = trim_window(state)
        self._base += len(state.blocks) - len(trimmed.blocks)
        return trimmed

    def append_blocks(self, blocks: tuple[StreamBlock, ...]) -> None:
        self._state = self._trim(on_new_blocks(self._state, blocks))
        self._reconcile()
        if self._state.follow:
            self.query_one("#sv_window", VerticalScroll).scroll_end(animate=False)
        self._refresh_follow_bar()

    def sync_tail(self, blocks: tuple[StreamBlock, ...], replacing: int) -> None:
        """Replace the trailing `replacing` blocks with `blocks`.

        A caller that re-sends the whole current run on every tick (which is
        what EngineRoom.render_live does -- it is driven by a bus item *and*
        a refresh timer, each carrying the full state) cannot use
        append_blocks: it would duplicate. Nor can it append only the new
        tail, because blocks mutate in place -- a prose block grows with
        every token, a tool block goes running -> done, a summary arrives
        for a block several positions back. So the run's whole block list
        is re-stated each time and the widgets it already owns are updated
        rather than re-mounted; anything before `replacing` (earlier runs,
        paged-in history) is left alone.
        """
        replacing = max(0, min(replacing, len(self._state.blocks)))
        kept = self._state.blocks[:len(self._state.blocks) - replacing]
        added = max(0, len(blocks) - replacing)
        state = replace(self._state, blocks=kept + tuple(blocks))
        if not state.follow and added:
            state = replace(state, unseen=state.unseen + added)
        self._state = self._trim(state)
        self._reconcile()
        if self._state.follow:
            self.query_one("#sv_window", VerticalScroll).scroll_end(animate=False)
        self._refresh_follow_bar()

    def mounted_keys(self) -> list[str]:
        return list(self._mounted)

    def _keyed_blocks(self) -> list[tuple[str, StreamBlock]]:
        """Every block in the window with its absolute key. Never enumerate
        the *filtered* list for keys: filtering renumbers the survivors."""
        return [(block_key(b, self._base + i), b)
                for i, b in enumerate(self._state.blocks)]

    def visible_keys(self) -> list[str]:
        active = self._state.agent_filter
        return [key for key, b in self._keyed_blocks()
                if not active or block_agent(b) in active]

    def active_filter(self) -> frozenset[str]:
        return self._state.agent_filter

    def set_agents(self, names: list[str]) -> None:
        """The roster arrives as data. The widget's structure does not
        depend on how many agents there are -- which is exactly what the
        tab-per-agent design got wrong."""
        wanted = list(dict.fromkeys(names))
        if wanted == self._agents:
            return
        self._agents = wanted
        bar = self.query_one("#sv_filter", Horizontal)
        bar.remove_children()
        bar.mount(Button("all", id=self._ALL_CHIP, classes="sv-chip sv-chip-on"))
        for name in wanted:
            bar.mount(Button(f"{self._theme.glyph(name)} {self._theme.label(name)}",
                             id=f"sv_chip_{name}", classes="sv-chip"))

    def toggle_agent_filter(self, name: str) -> None:
        self._state = toggle_agent(self._state, name)
        self._apply_filter()

    def on_button_pressed(self, event) -> None:
        bid = event.button.id or ""
        if bid == self._ALL_CHIP:
            self._state = clear_filter(self._state)
            self._apply_filter()
        elif bid.startswith("sv_chip_"):
            self.toggle_agent_filter(bid[len("sv_chip_"):])

    def _apply_filter(self) -> None:
        """Hide rather than unmount: toggling stays instant and fold state
        and scroll position survive it."""
        shown = set(self.visible_keys())
        for key, widget in self._mounted.items():
            widget.display = key in shown
        active = self._state.agent_filter
        for chip in self.query(".sv-chip"):
            bid = chip.id or ""
            if bid == self._ALL_CHIP:
                on = not active
            else:
                on = bid[len("sv_chip_"):] in active
            chip.set_class(on, "sv-chip-on")

    def expanded_keys(self) -> list[str]:
        return [k for k, w in self._mounted.items()
                if isinstance(w, Collapsible) and not w.collapsed]

    # -- reconciliation ------------------------------------------------------

    def _reconcile(self) -> None:
        """Mount what is new, update what changed, leave the rest alone.
        Rebuilding the pane per token is what made the old string renderer
        force a scroll-to-bottom on every update."""
        window = self.query_one("#sv_window", VerticalScroll)
        keyed = self._keyed_blocks()
        for key, block in keyed:
            widget = self._mounted.get(key)
            if widget is None:
                widget = self._mount_block(window, key, block)
                self._mounted[key] = widget
                continue
            if isinstance(widget, Collapsible):
                widget.title = self._tool_summary_line(block)
            else:
                widget.update(self._line_for(block))
        # Blocks that fell out of the head (trim) or off the tail (a shorter
        # sync_tail) take their widgets with them; keys are absolute, so
        # nothing else will ever reuse them and leaving them mounted would
        # let the widget count grow without bound.
        live = {key for key, _ in keyed}
        for key in [k for k in self._mounted if k not in live]:
            self._mounted.pop(key).remove()
        # Mount everything, then decide what is *shown*. Filtering is a
        # display concern; unmounting on filter would throw away fold state
        # and scroll position.
        self._apply_filter()

    def _mount_block(self, window, key: str, block: StreamBlock):
        if not isinstance(block, ToolBlock):
            widget = Static(markup=False, classes=self._classes_for(block))
            window.mount(widget)
            widget.update(self._line_for(block))
            return widget
        # Failures open on arrival: an error the reader has to click for is
        # an error they will miss.
        collapsible = Collapsible(title=self._tool_summary_line(block),
                                  collapsed=block.status != "failed",
                                  classes="sv-tool")
        collapsible._sv_key = key
        collapsible._sv_sequence = block.sequence
        collapsible._sv_path = block.input_summary
        collapsible._sv_loaded = False
        window.mount(collapsible)
        if block.status == "failed":
            self._load_output(collapsible)
        return collapsible

    def on_collapsible_expanded(self, event) -> None:
        self._load_output(event.collapsible)

    def _load_output(self, collapsible) -> None:
        if getattr(collapsible, "_sv_loaded", False):
            return
        collapsible._sv_loaded = True   # set before the await: a second
        # expand while the fetch is in flight must not fetch again
        self.run_worker(self._fetch_and_mount(collapsible), exclusive=False,
                        group="sv-output")

    async def _fetch_and_mount(self, collapsible) -> None:
        seq = getattr(collapsible, "_sv_sequence", 0)
        text = await self._source.fetch_output(seq) if seq else ""
        if not text:
            await collapsible.mount(Static("(no output recorded)", markup=False,
                                           classes="sv-output-empty"))
            return
        renderer = pick_renderer(text, getattr(collapsible, "_sv_path", ""))
        await collapsible.mount(renderer.render(text))

    def _classes_for(self, block: StreamBlock) -> str:
        return {ProseBlock: "sv-prose", ThinkingBlock: "sv-thinking",
                CallBlock: "sv-call", ToolBlock: "sv-tool"}[type(block)]

    def _line_for(self, block: StreamBlock) -> Text:
        """A Text is never markup-parsed, so untrusted block content is safe
        regardless of the Static's markup setting."""
        gutter = Text(f"{self._theme.glyph(block.agent_name)} ",
                      style=self._theme.style(block.agent_name))
        if isinstance(block, ProseBlock):
            return gutter + Text(block.text)
        if isinstance(block, ThinkingBlock):
            return gutter + Text(block.text, style="italic dim magenta")
        if isinstance(block, CallBlock):
            tail = f" · {block.duration_s:.1f}s" if block.status == "done" else ""
            return gutter + Text(f"▸ call {block.call_index} ({block.model}){tail}", style="dim")
        return gutter + Text(self._tool_summary_line(block), style="bold cyan")

    def _tool_summary_line(self, b: ToolBlock) -> str:
        parts = [f"⚒ {b.tool_name}({b.input_summary})"]
        if b.repeat_count > 1:
            parts.append(f"×{b.repeat_count}")
        if b.status == "done":
            parts.append(f"· {b.duration_s:.1f}s")
        elif b.status == "failed":
            parts.append(f"· ✗ {b.error}")
        if b.summary:
            parts.append(f"· {b.summary}")
        return " ".join(parts)
