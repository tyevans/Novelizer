from __future__ import annotations

from research_domain.agents import ExtractorAgent
from research_domain.cli import build_run_components
from research_domain.corpus import CorpusReader
from research_domain.runtime import ResearchRuntime
from research_domain.schemas import ClaimDraft, ExtractorOutput

from tests.substrate.postgres_fixture import postgres_dsn  # noqa: F401


class FakeExtractorRunner:
    async def ainvoke(self, inputs: dict) -> dict:
        prompt = inputs["messages"][0]["content"]
        source_id = next(
            line.split("SOURCE_ID:", 1)[1].strip()
            for line in prompt.splitlines() if line.startswith("SOURCE_ID:"))
        return {"structured_response": ExtractorOutput(
            claims=[ClaimDraft(text=f"claim from {source_id}")])}


async def test_build_run_components_with_injected_agents_ticks_headlessly(tmp_path, postgres_dsn):
    (tmp_path / "doc.md").write_text("some fact", encoding="utf-8")

    runtime = ResearchRuntime(postgres_dsn, stream="cli-run-stream")
    corpus = CorpusReader(tmp_path)
    agents = [ExtractorAgent(FakeExtractorRunner(), runtime, corpus, interval=1)]

    built_runtime, scheduler = build_run_components(
        dsn=postgres_dsn, stream="cli-run-stream", corpus_dir=str(tmp_path),
        settings=None, interval=1, max_concurrent=2, agents=agents,
    )
    assert built_runtime is runtime or isinstance(built_runtime, ResearchRuntime)
    # injected-agents path must not have constructed any LLM machinery
    await runtime.connect()
    try:
        await runtime.catch_up()
        await scheduler.tick()
        await scheduler.drain_in_flight()
        assert runtime.claimed_source_ids() == {"doc.md"}
    finally:
        await runtime.close()
