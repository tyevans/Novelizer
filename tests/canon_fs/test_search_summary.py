"""Excerpt gathering for search_canon's contextual summary.

The summarizer is only as good as what it is shown, and what it is shown must
be bounded: a 4k-word chapter body would blow the context this is meant to
save.
"""
from __future__ import annotations

from dataclasses import dataclass

from deepagents.backends.protocol import ReadResult
from deepagents.backends.utils import create_file_data

from novelizer.canon_fs.reads import TRUNCATION_MARKER
from novelizer.canon_fs.search_summary import (
    SUMMARY_BODY_LINES, SUMMARY_SOURCE_CAP, gather_excerpts,
)


@dataclass
class _Hit:
    id: str
    kind: str
    title: str


class _Backend:
    """Records every aread call so the tests can assert on the limit."""

    def __init__(self, bodies=None, errors=()):
        self.bodies = bodies or {}
        self.errors = set(errors)
        self.calls = []

    async def aread(self, path, offset=0, limit=2000):
        self.calls.append((path, limit))
        if path in self.errors:
            return ReadResult(error=f"File '{path}' not found.")
        return ReadResult(file_data=create_file_data(self.bodies.get(path, "")))


async def test_reads_bodies_for_file_backed_hits():
    hits = [_Hit("ch1", "chapter", "The Drowned Bell")]
    backend = _Backend({"/chapters/001-the-drowned-bell.md": "The bell rang."})
    out = await gather_excerpts(
        hits, backend, {"ch1": "/chapters/001-the-drowned-bell.md"}, {})
    assert len(out) == 1
    assert "The bell rang." in out[0]
    assert "The Drowned Bell" in out[0]
    assert "ch1" in out[0]


async def test_caps_the_number_of_bodies_read():
    hits = [_Hit(f"ch{i}", "chapter", f"C{i}") for i in range(20)]
    paths = {f"ch{i}": f"/chapters/{i}.md" for i in range(20)}
    backend = _Backend({p: "body" for p in paths.values()})
    out = await gather_excerpts(hits, backend, paths, {})
    assert len(out) == SUMMARY_SOURCE_CAP
    assert len(backend.calls) == SUMMARY_SOURCE_CAP


async def test_passes_the_body_line_limit():
    hits = [_Hit("ch1", "chapter", "One")]
    backend = _Backend({"/chapters/1.md": "body"})
    await gather_excerpts(hits, backend, {"ch1": "/chapters/1.md"}, {})
    assert backend.calls == [("/chapters/1.md", SUMMARY_BODY_LINES)]


async def test_a_read_error_does_not_lose_the_other_excerpts():
    hits = [_Hit("ch1", "chapter", "One"), _Hit("ch2", "chapter", "Two")]
    paths = {"ch1": "/chapters/1.md", "ch2": "/chapters/2.md"}
    backend = _Backend({"/chapters/2.md": "survived"}, errors=["/chapters/1.md"])
    out = await gather_excerpts(hits, backend, paths, {})
    assert len(out) == 1
    assert "survived" in out[0]


async def test_entity_hits_use_their_inline_line_and_read_nothing():
    hits = [_Hit("7", "entity", "The Salted Gull")]
    backend = _Backend()
    out = await gather_excerpts(
        hits, backend, {}, {"7": "(entity) [place] The Salted Gull — a tavern"})
    assert backend.calls == []
    assert "a tavern" in out[0]


async def test_fileless_kinds_contribute_title_only_and_read_nothing():
    # arcs have no backing file at all; briefs and promises have no
    # individually addressable one.
    hits = [_Hit("A-1", "arc", "Mateo's fall")]
    backend = _Backend()
    out = await gather_excerpts(hits, backend, {}, {})
    assert backend.calls == []
    assert "Mateo's fall" in out[0]


async def test_drops_the_truncation_notice():
    # sliced_read appends a SYSTEM NOTICE telling the READER to call read_file
    # again. Left in, the summarizer echoes that instruction into the CONTEXT
    # block, where it is nonsense addressed to the wrong party.
    body = f"real content\n[SYSTEM NOTICE — tool output] {TRUNCATION_MARKER}: you were shown lines 1-120 of 400."
    hits = [_Hit("ch1", "chapter", "One")]
    backend = _Backend({"/chapters/1.md": body})
    out = await gather_excerpts(hits, backend, {"ch1": "/chapters/1.md"}, {})
    assert "real content" in out[0]
    assert TRUNCATION_MARKER not in out[0]
