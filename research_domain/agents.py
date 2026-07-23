"""The three live research agents: extractor, verifier, retractor.

Each subclasses agent_kit.BaseAgent: poll (read runtime + corpus state),
work (one LLM call via the injected runner), commit (validate structured
output, append events via runtime.append_events).

Quiet-when-done uses the fruitless-set pattern, not watermark gating: an
examined item that yielded no events joins an in-memory set subtracted
from the workable queue, so it can never head-of-line-block items behind
it. The sets are process-local; a restart re-examines fruitless items,
which is safe because fruitless runs commit nothing."""
from __future__ import annotations

import uuid

from agent_kit import BaseAgent

from research_domain.corpus import CorpusReader
from research_domain.runtime import ResearchRuntime
from research_domain.schemas import ExtractorOutput


class StructuredResponseError(RuntimeError):
    """The runner returned no structured response."""


def _normalize(text: str) -> str:
    return " ".join(text.lower().split())


def _structured(result: dict, model_cls):
    raw = result.get("structured_response")
    if raw is None:
        raise StructuredResponseError(f"runner returned no {model_cls.__name__}")
    if isinstance(raw, model_cls):
        return raw
    return model_cls.model_validate(raw)


EXTRACTOR_PROMPT = """Extract every distinct factual claim from the document below.
A claim is a single, checkable assertion — not an opinion, not a heading.
Return each claim as its own entry, phrased as one standalone sentence.

SOURCE_ID: {source_id}

DOCUMENT:
{text}"""


class ExtractorAgent(BaseAgent):
    name = "extractor"

    def __init__(
        self,
        runner,
        runtime: ResearchRuntime,
        corpus: CorpusReader,
        interval: int = 60,
        personality: str = "",
    ) -> None:
        super().__init__(runner, interval, name="extractor", personality=personality)
        self._runtime = runtime
        self._corpus = corpus
        # Docs examined that yielded zero claims. In-memory by design: a
        # restart re-examines them, which commits nothing (idempotent).
        self._fruitless: set[str] = set()

    def _workable(self) -> list[str]:
        claimed = self._runtime.claimed_source_ids()
        return [
            d for d in self._corpus.list_documents()
            if d not in claimed and d not in self._fruitless
        ]

    async def readiness(self) -> float:
        return 0.7 if self._workable() else 0.0

    async def _run(self) -> None:
        workable = self._workable()
        if not workable:
            return
        source_id = workable[0]
        text = self._corpus.read_document(source_id)
        prompt = EXTRACTOR_PROMPT.format(source_id=source_id, text=text)
        result = await self._runner.ainvoke(
            {"messages": [{"role": "user", "content": prompt}]}
        )
        output = _structured(result, ExtractorOutput)

        existing = {
            (c["source_id"], _normalize(c["text"])) for c in self._runtime.list_claims()
        }
        events: list[tuple[str, dict]] = []
        for draft in output.claims:
            key = (source_id, _normalize(draft.text))
            if key in existing:
                continue
            existing.add(key)
            events.append((
                "claim.proposed",
                {"claim_id": uuid.uuid4().hex, "source_id": source_id, "text": draft.text},
            ))
        if events:
            await self._runtime.append_events(events)
        else:
            self._fruitless.add(source_id)
            self.note_pass()
