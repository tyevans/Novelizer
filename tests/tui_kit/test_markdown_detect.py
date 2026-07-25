import pytest
from tui_kit.markdown_detect import ext_is_markdown, looks_markdown


@pytest.mark.parametrize("path", ["ch1.md", "canon/ch1.markdown", "A.MD", "  ch1.md  "])
def test_ext_is_markdown_accepts_markdown_paths(path):
    assert ext_is_markdown(path)


@pytest.mark.parametrize("path", ["", "ch1.txt", "data.json", "notes.md.bak", "ch1md"])
def test_ext_is_markdown_rejects_everything_else(path):
    assert not ext_is_markdown(path)


def test_looks_markdown_needs_two_distinct_signals():
    assert not looks_markdown("# just a heading and nothing else")
    assert looks_markdown("# Chapter One\n\n- a bullet\n- another")


def test_looks_markdown_accepts_fenced_code_plus_a_heading():
    assert looks_markdown("## Usage\n\n```python\nx = 1\n```")


def test_looks_markdown_rejects_json():
    assert not looks_markdown('{"a": 1, "b": [2, 3], "c": {"d": "# not a heading"}}')


def test_looks_markdown_rejects_plain_prose():
    assert not looks_markdown("The rain had not stopped for three days.\nShe waited.")


def test_looks_markdown_is_stable_under_line_endings_and_trailing_space():
    doc = "# Chapter One\n\n- a bullet\n- another"
    assert looks_markdown(doc) == looks_markdown(doc.replace("\n", "\r\n"))
    assert looks_markdown(doc) == looks_markdown(doc + "   \n\n")


def test_looks_markdown_is_false_on_empty():
    assert not looks_markdown("")
