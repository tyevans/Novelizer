from __future__ import annotations
import html
from ebooklib import epub
from novelizer.store.models import Chapter


def _paragraphs(prose: str) -> str:
    blocks = [b.strip() for b in prose.split("\n\n") if b.strip()]
    return "".join(f"<p>{html.escape(b)}</p>" for b in blocks)


def build_epub(chapters: list[Chapter], *, title: str, author: str) -> bytes:
    if not chapters:
        raise ValueError("no chapters to export")

    book = epub.EpubBook()
    book.set_identifier(f"novelizer-{title}")
    book.set_title(title)
    book.set_language("en")
    book.add_author(author)

    epub_chapters = []
    for i, chapter in enumerate(chapters):
        item = epub.EpubHtml(
            title=chapter.title,
            file_name=f"chap_{i}.xhtml",
            lang="en",
        )
        item.content = f"<h1>{html.escape(chapter.title)}</h1>{_paragraphs(chapter.prose)}"
        book.add_item(item)
        epub_chapters.append(item)

    book.toc = tuple(epub_chapters)
    book.add_item(epub.EpubNcx())
    book.add_item(epub.EpubNav())
    book.spine = ["nav"] + epub_chapters

    import tempfile
    import os

    fd, path = tempfile.mkstemp(suffix=".epub")
    os.close(fd)
    try:
        epub.write_epub(path, book)
        with open(path, "rb") as f:
            return f.read()
    finally:
        os.remove(path)
