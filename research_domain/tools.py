"""Read-only langchain tools over the corpus and the runtime's claim state.

Writes never happen through tools: structured output carries proposals,
and the agent's commit validates and appends events (the same intent
pattern novelizer's agents use)."""
from __future__ import annotations

from langchain_core.tools import tool

from research_domain.corpus import CorpusReader


def make_corpus_tools(reader: CorpusReader) -> list:
    @tool("list_documents")
    def list_documents_tool() -> str:
        """List every document source_id in the research corpus."""
        docs = reader.list_documents()
        return "\n".join(docs) if docs else "(corpus is empty)"

    @tool("read_document")
    def read_document_tool(source_id: str) -> str:
        """Read a corpus document's full text by its source_id."""
        try:
            return reader.read_document(source_id)
        except (FileNotFoundError, OSError):
            return f"(no such document: {source_id})"

    return [list_documents_tool, read_document_tool]


def make_claim_tools(runtime) -> list:
    @tool("list_claims")
    def list_claims_tool() -> str:
        """List all current claims as `claim_id [source_id]: text` lines."""
        claims = runtime.list_claims()
        if not claims:
            return "(no claims yet)"
        return "\n".join(
            f"{c['claim_id']} [{c['source_id']}]: {c['text']}" for c in claims
        )

    @tool("get_claim")
    def get_claim_tool(claim_id: str) -> str:
        """Get one claim by its claim_id."""
        c = runtime.get_claim(claim_id)
        if c is None:
            return f"(no such claim: {claim_id})"
        return f"{c['claim_id']} [{c['source_id']}]: {c['text']}"

    return [list_claims_tool, get_claim_tool]
