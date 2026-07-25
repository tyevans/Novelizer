from __future__ import annotations
import time
from textual.app import ComposeResult
from textual.containers import Vertical, VerticalScroll
from textual.widgets import Static
from tui_kit.contracts import AgentTheme
from rich.text import Text
from tui_kit.run_model import LiveRunState, StreamBlock, ProseBlock, ThinkingBlock, CallBlock, styled_vitals


class LiveStreamPanel(Vertical):
    """A single-agent live token/tool-call stream rendered as one string
    into one Static. Owns no bus subscription and no identity — the mounting
    screen computes the LiveRunState for its own key and calls render().

    The Engine Room used to render this way too; it now mounts a widget per
    block (tui_kit.widgets.stream_view.StreamView). Chat and Research keep
    this simpler panel: they watch one agent at a time, so the concurrency
    and scrollback problems that forced the rewrite do not arise."""

    _VITALS_ID = "#lsp_vitals"
    _STREAM_ID = "#lsp_stream"
    _STREAM_SCROLL_ID = "#lsp_stream_scroll"

    def __init__(self, theme: AgentTheme, *args, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        self._theme = theme
        self._rendered_body: str = ""

    def compose(self) -> ComposeResult:
        yield Static("idle — waiting for the scheduler",
                     id=self._VITALS_ID.removeprefix("#"), classes="lsp-vitals", markup=False)
        with VerticalScroll(id=self._STREAM_SCROLL_ID.removeprefix("#"), classes="lsp-stream-scroll"):
            yield Static("", id=self._STREAM_ID.removeprefix("#"), classes="lsp-stream", markup=False)

    def render(self, state: LiveRunState | None = None, now: float | None = None):
        if state is None:
            # Textual's internal Widget.render() calls this with no args to
            # get this container's own visual; Vertical paints nothing itself.
            return ""
        now = time.monotonic() if now is None else now
        self.query_one(self._VITALS_ID, Static).update(styled_vitals(state, now, self._theme))
        body = live_body(state)
        if body != self._rendered_body:
            self.query_one(self._STREAM_ID, Static).update(styled_body(body))
            self._rendered_body = body
            self.query_one(self._STREAM_SCROLL_ID, VerticalScroll).scroll_end(animate=False)


# -- string rendering ---------------------------------------------------------
# These used to live in run_model.py and back the Engine Room too. The Engine
# Room now renders a widget per block (tui_kit.widgets.stream_view), so this
# single-agent panel is their only remaining consumer -- they live with it
# rather than in the shared model.


def live_body(state: LiveRunState) -> str:
    if state.status == "running" and not state.stream_attached:
        return "run in progress (stream not attached — restarted mid-run)"
    if state.status == "idle":
        return "no run yet"
    lines: list[str] = []
    for b in state.blocks:
        lines.append(_render_block(b))
    body = "\n".join(lines).strip("\n")
    if state.status == "failed" and body:
        return body + "\n\n✗ crashed"
    return body or "(waiting for first token…)"


def _render_block(b: StreamBlock) -> str:
    if isinstance(b, ProseBlock):
        return b.text
    if isinstance(b, ThinkingBlock):
        return f"💭 {b.text}"
    if isinstance(b, CallBlock):
        header = f"▸ call {b.call_index} ({b.model})"
        if b.status == "done":
            return header + f"\n   ↳ {b.duration_s:.1f}s"
        return header
    # ToolBlock
    suffix = f" ×{b.repeat_count}" if b.repeat_count > 1 else ""
    indent = "       " if b.delegate else "   "
    if b.delegate:
        lines = [f"    ⚒ ↳ {b.delegate}: {b.tool_name}({b.input_summary}){suffix}"]
    else:
        lines = [f"⚒ {b.tool_name}({b.input_summary}){suffix}"]
    if b.status == "done":
        lines.append(f"{indent}↳ done in {b.duration_s:.1f}s")
    elif b.status == "failed":
        lines.append(f"{indent}↳ ✗ {b.error}")
    if b.summary:
        lines.append(f"{indent}↳ {b.summary}")
    if b.preview:
        for out_line in b.preview.split("\n"):
            lines.append(f"{indent}  {out_line}")
    return "\n".join(lines)


def stream_line_kind(line: str) -> str:
    """Classify a live_body() line for widget-level styling. Pure/text-only
    so it stays testable without Rich or Textual."""
    s = line.strip()
    if s.startswith("⚒"):
        return "tool"
    if s.startswith("▸") or s.startswith("↳"):
        return "call"
    if s.startswith("💭"):
        return "thinking"
    return "prose"


# Rich styles per stream_line_kind(); "" leaves prose in the theme default so
# the agent's own accent color (applied to the vitals bar) stays the visual
# anchor rather than competing with a wall of colored prose.
_LINE_STYLES = {"tool": "bold cyan", "call": "dim", "thinking": "italic dim magenta"}


def styled_body(body: str) -> Text:
    # Text objects are never markup-parsed regardless of a Static's
    # markup=False setting, so untrusted stream content (tool summaries,
    # prompts) stays safe here the same way it does as a plain str.
    text = Text()
    lines = body.split("\n")
    for i, line in enumerate(lines):
        style = _LINE_STYLES.get(stream_line_kind(line), "")
        text.append(line, style=style)
        if i != len(lines) - 1:
            text.append("\n")
    return text
