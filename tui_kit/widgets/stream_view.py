"""The unified stream: one widget per block, all agents interleaved.

Replaces the tab-per-agent panes. Tabs scaled with fleet size and hid the
one thing this view exists to show -- who is running at the same time as
whom.
"""
from __future__ import annotations
from rich.text import Text
from textual.app import ComposeResult
from textual.containers import Vertical, VerticalScroll
from textual.widgets import Collapsible, Static
from tui_kit.contracts import AgentTheme
from tui_kit.output_renderer import pick_renderer
from tui_kit.run_model import (
    CallBlock, ProseBlock, StreamBlock, ThinkingBlock, ToolBlock, block_key,
)
from tui_kit.stream_model import StreamState, on_new_blocks, trim_window, visible_blocks
from tui_kit.stream_source import StreamSource


class StreamView(Vertical):
    DEFAULT_CSS = """
    .sv-tool { border: round $panel; }
    """

    def __init__(self, theme: AgentTheme, source: StreamSource, *args, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        self._theme = theme
        self._source = source
        self._state = StreamState()
        self._mounted: dict[str, Static | Collapsible] = {}

    def compose(self) -> ComposeResult:
        yield VerticalScroll(id="sv_window", classes="sv-window")

    # -- public API ----------------------------------------------------------

    def append_blocks(self, blocks: tuple[StreamBlock, ...]) -> None:
        self._state = trim_window(on_new_blocks(self._state, blocks))
        self._reconcile()

    def mounted_keys(self) -> list[str]:
        return list(self._mounted)

    def expanded_keys(self) -> list[str]:
        return [k for k, w in self._mounted.items()
                if isinstance(w, Collapsible) and not w.collapsed]

    # -- reconciliation ------------------------------------------------------

    def _reconcile(self) -> None:
        """Mount what is new, update what changed, leave the rest alone.
        Rebuilding the pane per token is what made the old string renderer
        force a scroll-to-bottom on every update."""
        window = self.query_one("#sv_window", VerticalScroll)
        for i, block in enumerate(visible_blocks(self._state)):
            key = block_key(block, i)
            widget = self._mounted.get(key)
            if widget is None:
                widget = self._mount_block(window, key, block)
                self._mounted[key] = widget
                continue
            if isinstance(widget, Collapsible):
                widget.title = self._tool_summary_line(block)
            else:
                widget.update(self._line_for(block))

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
