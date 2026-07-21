import ebooklib
import pytest
from ebooklib import epub
from novelizer.export.epub import build_epub
from novelizer.store.models import Chapter


def _chapters():
    return [
        Chapter(title="The Drowned Bell", prose="First line.\n\nSecond paragraph."),
        Chapter(title="Ashes at Dawn", prose="Only one paragraph here."),
    ]


def test_build_epub_raises_on_empty_chapter_list():
    with pytest.raises(ValueError):
        build_epub([], title="Empty Book", author="Nobody")


def test_build_epub_produces_readable_epub_with_all_chapters():
    data = build_epub(_chapters(), title="The Drowned Bell", author="A. Author")
    assert isinstance(data, bytes)
    assert data[:2] == b"PK"  # epub is a zip container


def test_build_epub_toc_matches_chapter_titles_in_order(tmp_path):
    data = build_epub(_chapters(), title="The Drowned Bell", author="A. Author")
    out = tmp_path / "book.epub"
    out.write_bytes(data)

    book = epub.read_epub(str(out))
    titles = [item.title for item in book.toc]
    assert titles == ["The Drowned Bell", "Ashes at Dawn"]

    docs = [
        item for item in book.get_items()
        if item.get_type() == ebooklib.ITEM_DOCUMENT and item.file_name != "nav.xhtml"
    ]
    assert len(docs) == 2


def test_build_epub_splits_prose_into_paragraphs(tmp_path):
    data = build_epub(_chapters(), title="The Drowned Bell", author="A. Author")
    out = tmp_path / "book.epub"
    out.write_bytes(data)

    book = epub.read_epub(str(out))
    first_chapter = next(
        item for item in book.get_items()
        if item.get_type() == ebooklib.ITEM_DOCUMENT and item.file_name == "chap_0.xhtml"
    )
    content = first_chapter.get_content().decode("utf-8")
    assert content.count("<p>") == 2
    assert "First line." in content
    assert "Second paragraph." in content
    assert "<h1>The Drowned Bell</h1>" in content


def test_build_epub_sets_title_and_author(tmp_path):
    data = build_epub(_chapters(), title="The Drowned Bell", author="A. Author")
    out = tmp_path / "book.epub"
    out.write_bytes(data)

    book = epub.read_epub(str(out))
    assert book.get_metadata("DC", "title")[0][0] == "The Drowned Bell"
    assert book.get_metadata("DC", "creator")[0][0] == "A. Author"
