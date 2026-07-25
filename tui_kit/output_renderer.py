"""How a tool call's output becomes a widget.

Open for extension: a JSON pretty-printer or a diff view is a new
OutputRenderer appended to DEFAULT_RENDERERS, not another branch in a
rendering function. PlainRenderer is the total fallback, so pick_renderer
never fails to return one.
"""
from __future__ import annotations
from typing import Protocol
from textual.widget import Widget
from textual.widgets import Markdown, Static
from tui_kit.markdown_detect import ext_is_markdown, looks_markdown


class OutputRenderer(Protocol):
    def matches(self, text: str, path: str) -> bool: ...
    def render(self, text: str) -> Widget: ...


class MarkdownRenderer:
    def matches(self, text: str, path: str) -> bool:
        return ext_is_markdown(path) or looks_markdown(text)

    def render(self, text: str) -> Widget:
        # Markdown parses its source as markdown, never as Textual markup,
        # so untrusted "[...]" sequences are safe here.
        return Markdown(text, classes="er-output-md")


class PlainRenderer:
    def matches(self, text: str, path: str) -> bool:
        return True  # total fallback

    def render(self, text: str) -> Widget:
        return Static(text, markup=False, classes="er-output-plain")


DEFAULT_RENDERERS: tuple[OutputRenderer, ...] = (MarkdownRenderer(), PlainRenderer())


def pick_renderer(text: str, path: str,
                  renderers: tuple[OutputRenderer, ...] | None = None) -> OutputRenderer:
    for r in renderers or DEFAULT_RENDERERS:
        if r.matches(text, path):
            return r
    return PlainRenderer()
