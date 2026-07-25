from textual.widgets import Markdown, Static
from tui_kit.output_renderer import (
    MarkdownRenderer, PlainRenderer, pick_renderer, DEFAULT_RENDERERS,
)

MD = "# Chapter One\n\n- a bullet\n- another"


def test_markdown_wins_on_extension_even_without_content_signals():
    assert isinstance(pick_renderer("just plain prose", "ch1.md"), MarkdownRenderer)


def test_markdown_wins_on_content_when_there_is_no_path():
    assert isinstance(pick_renderer(MD, ""), MarkdownRenderer)


def test_plain_is_the_fallback():
    assert isinstance(pick_renderer("2026-07-25 INFO started", "app.log"), PlainRenderer)


def test_markdown_renderer_produces_a_markdown_widget():
    assert isinstance(MarkdownRenderer().render(MD), Markdown)


def test_plain_renderer_produces_a_static_with_markup_disabled():
    """Tool output is untrusted: a markup-parsing Static would raise
    MarkupError on any '[...]' sequence in canon text."""
    w = PlainRenderer().render("a [not a tag] b")
    assert isinstance(w, Static)
    assert w._render_markup is False


def test_pick_renderer_never_returns_none_for_empty_output():
    assert pick_renderer("", "") is not None


def test_default_renderers_are_ordered_most_specific_first():
    assert isinstance(DEFAULT_RENDERERS[0], MarkdownRenderer)
    assert isinstance(DEFAULT_RENDERERS[-1], PlainRenderer)
