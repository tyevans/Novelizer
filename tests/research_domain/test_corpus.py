from __future__ import annotations
from pathlib import Path

import pytest

from research_domain.corpus import CorpusReader


def _make_corpus(root: Path) -> None:
    (root / "notes").mkdir()
    (root / ".hidden").mkdir()
    (root / "a.md").write_text("alpha claims", encoding="utf-8")
    (root / "notes" / "b.txt").write_text("beta claims", encoding="utf-8")
    (root / "notes" / "c.py").write_text("not a document", encoding="utf-8")
    (root / ".hidden" / "d.md").write_text("hidden", encoding="utf-8")
    (root / ".dotfile.md").write_text("hidden file", encoding="utf-8")


def test_lists_only_md_and_txt_sorted_skipping_hidden(tmp_path):
    _make_corpus(tmp_path)
    reader = CorpusReader(tmp_path)
    assert reader.list_documents() == ["a.md", "notes/b.txt"]


def test_source_ids_are_stable_across_calls(tmp_path):
    _make_corpus(tmp_path)
    reader = CorpusReader(tmp_path)
    assert reader.list_documents() == reader.list_documents()


def test_read_document_by_source_id(tmp_path):
    _make_corpus(tmp_path)
    reader = CorpusReader(tmp_path)
    assert reader.read_document("notes/b.txt") == "beta claims"


def test_read_missing_document_raises(tmp_path):
    _make_corpus(tmp_path)
    reader = CorpusReader(tmp_path)
    with pytest.raises(FileNotFoundError):
        reader.read_document("nope.md")


def test_empty_corpus_lists_nothing(tmp_path):
    assert CorpusReader(tmp_path).list_documents() == []
