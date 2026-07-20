"""search_canon's description and response shape are prompt surface.

The agent picks between grep and search_canon from the description alone, and
an uncapped result list is a context-window risk on a long novel.
See docs/agent-prompting/proposal-fleet-shared.md §2.6.
"""
from __future__ import annotations

from dataclasses import dataclass

import pytest

from novelizer.canon_fs.search import SEARCH_RESULT_CAP, build_search_canon_tool


@dataclass
class _Hit:
    id: str
    kind: str
    title: str


class _Store:
    def __init__(self, hits):
        self._hits = hits

    async def search(self, query, kinds=None):
        return self._hits


class _ReadStore:
    async def list_chapters(self):
        return []

    async def list_characters(self):
        return []

    async def list_world_entries(self):
        return []

    async def list_threads(self):
        return []

    async def list_secrets(self):
        return []

    async def list_themes(self):
        return []


def _tool(hits):
    return build_search_canon_tool(_Store(hits), _ReadStore())


class TestDescription:
    def test_states_the_boundary_against_grep(self):
        """Without this the agent cannot tell which tool a lookup belongs to."""
        desc = _tool([]).description
        assert "grep" in desc

    def test_says_search_is_by_meaning(self):
        desc = _tool([]).description.lower()
        assert "meaning" in desc

    def test_carries_a_worked_example(self):
        assert "search_canon(" in _tool([]).description

    def test_documents_the_kinds_filter(self):
        assert "kinds=" in _tool([]).description


class TestResponseCap:
    async def test_caps_results_and_says_so(self):
        """A truncated list that doesn't announce itself reads as exhaustive."""
        hits = [_Hit(id=f"h{i}", kind="chapter", title=f"T{i}") for i in range(SEARCH_RESULT_CAP + 5)]
        out = await _tool(hits).ainvoke({"query": "q"})
        assert len(out.splitlines()) == SEARCH_RESULT_CAP + 1
        assert "narrow your query" in out.splitlines()[-1]

    async def test_no_cap_line_when_results_fit(self):
        hits = [_Hit(id="h1", kind="chapter", title="T1")]
        out = await _tool(hits).ainvoke({"query": "q"})
        assert "narrow your query" not in out
        assert out.splitlines() == ["(chapter) (no file) — 'T1' [id: h1]"]

    async def test_empty_results_unchanged(self):
        assert await _tool([]).ainvoke({"query": "q"}) == "No results."
