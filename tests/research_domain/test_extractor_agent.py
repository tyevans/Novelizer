from __future__ import annotations

from research_domain.agents import ExtractorAgent
from research_domain.corpus import CorpusReader
from research_domain.runtime import ResearchRuntime
from research_domain.schemas import ClaimDraft, ExtractorOutput

from tests.substrate.postgres_fixture import postgres_dsn  # noqa: F401


class FakeExtractorRunner:
    """Returns scripted claims per source_id, parsed from the prompt's
    SOURCE_ID line (the prompt convention the real runner also sees)."""

    def __init__(self, claims_by_source: dict[str, list[str]]):
        self._by_source = claims_by_source
        self.calls: list[str] = []

    async def ainvoke(self, inputs: dict) -> dict:
        prompt = inputs["messages"][0]["content"]
        source_id = next(
            line.split("SOURCE_ID:", 1)[1].strip()
            for line in prompt.splitlines() if line.startswith("SOURCE_ID:")
        )
        self.calls.append(source_id)
        return {"structured_response": ExtractorOutput(
            claims=[ClaimDraft(text=t) for t in self._by_source.get(source_id, [])])}


def _corpus(tmp_path):
    (tmp_path / "a.md").write_text("water boils at 100C at sea level", encoding="utf-8")
    (tmp_path / "b.md").write_text("water boils at 90C everywhere", encoding="utf-8")
    return CorpusReader(tmp_path)


async def test_readiness_zero_when_no_pending_docs(tmp_path, postgres_dsn):
    runtime = ResearchRuntime(postgres_dsn, stream="ext-idle-stream")
    await runtime.connect()
    try:
        await runtime.catch_up()
        agent = ExtractorAgent(FakeExtractorRunner({}), runtime, CorpusReader(tmp_path))
        assert await agent.readiness() == 0.0
    finally:
        await runtime.close()


async def test_extracts_one_doc_per_run_and_commits_claims(tmp_path, postgres_dsn):
    runtime = ResearchRuntime(postgres_dsn, stream="ext-commit-stream")
    await runtime.connect()
    try:
        await runtime.catch_up()
        corpus = _corpus(tmp_path)
        runner = FakeExtractorRunner({
            "a.md": ["water boils at 100C at sea level"],
            "b.md": ["water boils at 90C everywhere"],
        })
        agent = ExtractorAgent(runner, runtime, corpus)
        assert await agent.readiness() == 0.7

        await agent.run_once()
        assert runner.calls == ["a.md"]  # one doc per run, oldest-sorted first
        claims = runtime.list_claims()
        assert len(claims) == 1 and claims[0]["source_id"] == "a.md"
        assert claims[0]["claim_id"]  # minted, non-empty

        await agent.run_once()
        assert runner.calls == ["a.md", "b.md"]
        assert runtime.claimed_source_ids() == {"a.md", "b.md"}
        # backlog drained -> readiness gated to 0
        assert await agent.readiness() == 0.0
    finally:
        await runtime.close()


async def test_zero_claim_doc_goes_fruitless_without_blocking_new_docs(tmp_path, postgres_dsn):
    runtime = ResearchRuntime(postgres_dsn, stream="ext-zero-stream")
    await runtime.connect()
    try:
        await runtime.catch_up()
        (tmp_path / "empty.md").write_text("nothing factual here", encoding="utf-8")
        runner = FakeExtractorRunner({"empty.md": [], "new.md": ["fresh fact"]})
        agent = ExtractorAgent(runner, runtime, CorpusReader(tmp_path))
        await agent.run_once()
        assert runtime.list_claims() == []
        # doc examined and fruitless -> workable queue empty -> idle
        assert await agent.readiness() == 0.0
        # a new doc becomes workable immediately...
        (tmp_path / "new.md").write_text("fresh fact", encoding="utf-8")
        assert await agent.readiness() == 0.7
        # ...and the next run processes new.md, NOT empty.md again
        await agent.run_once()
        assert runner.calls == ["empty.md", "new.md"]
        assert runtime.claimed_source_ids() == {"new.md"}
    finally:
        await runtime.close()


async def test_dedups_duplicate_claim_texts_within_one_output(tmp_path, postgres_dsn):
    runtime = ResearchRuntime(postgres_dsn, stream="ext-dedup-stream")
    await runtime.connect()
    try:
        await runtime.catch_up()
        (tmp_path / "a.md").write_text("stuff", encoding="utf-8")
        # runner proposes the same claim twice (case/spacing variants) plus one distinct
        runner = FakeExtractorRunner({"a.md": [
            "Water boils at 100C at sea level",
            "water  boils at 100c AT SEA LEVEL",
            "salt raises the boiling point",
        ]})
        agent = ExtractorAgent(runner, runtime, CorpusReader(tmp_path))
        await agent.run_once()
        texts = sorted(c["text"] for c in runtime.list_claims())
        assert len(texts) == 2  # normalized duplicate collapsed
        assert "salt raises the boiling point" in texts
    finally:
        await runtime.close()
