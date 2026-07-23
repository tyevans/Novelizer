from __future__ import annotations

from research_domain.corpus import CorpusReader
from research_domain.tools import make_claim_tools, make_corpus_tools


class StubRuntime:
    def __init__(self, claims):
        self._claims = claims
    def list_claims(self):
        return list(self._claims)
    def get_claim(self, claim_id):
        return next((c for c in self._claims if c["claim_id"] == claim_id), None)


def _tool_by_name(tools, name):
    return next(t for t in tools if t.name == name)


async def test_corpus_tools_list_and_read(tmp_path):
    (tmp_path / "a.md").write_text("alpha text", encoding="utf-8")
    tools = make_corpus_tools(CorpusReader(tmp_path))
    listed = await _tool_by_name(tools, "list_documents").ainvoke({})
    assert "a.md" in listed
    content = await _tool_by_name(tools, "read_document").ainvoke({"source_id": "a.md"})
    assert content == "alpha text"


async def test_read_document_missing_returns_message_not_raise(tmp_path):
    tools = make_corpus_tools(CorpusReader(tmp_path))
    out = await _tool_by_name(tools, "read_document").ainvoke({"source_id": "nope.md"})
    assert "no such document" in out


async def test_claim_tools_list_and_get():
    rt = StubRuntime([{"claim_id": "c1", "source_id": "a.md", "text": "sky is blue"}])
    tools = make_claim_tools(rt)
    listed = await _tool_by_name(tools, "list_claims").ainvoke({})
    assert "c1" in listed and "sky is blue" in listed
    got = await _tool_by_name(tools, "get_claim").ainvoke({"claim_id": "c1"})
    assert "a.md" in got
    missing = await _tool_by_name(tools, "get_claim").ainvoke({"claim_id": "zz"})
    assert "no such claim" in missing


async def test_empty_states_render_placeholders(tmp_path):
    corpus_tools = make_corpus_tools(CorpusReader(tmp_path))
    assert "empty" in await _tool_by_name(corpus_tools, "list_documents").ainvoke({})
    claim_tools = make_claim_tools(StubRuntime([]))
    assert "no claims" in await _tool_by_name(claim_tools, "list_claims").ainvoke({})
