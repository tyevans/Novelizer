"""The three live research agents: extractor, verifier, retractor.

Each subclasses agent_kit.BaseAgent: poll (read runtime + corpus state),
work (one LLM call via the injected runner), commit (validate structured
output, append events via runtime.append_events).

Quiet-when-done uses the fruitless-set pattern, not watermark gating or
note_pass backoff: an examined item that yielded no events joins an
in-memory set subtracted from the workable queue, so it can never
head-of-line-block items behind it, and readiness drops to 0.0 once no
workable items remain. The sets are process-local; a restart re-examines
fruitless items, which is safe because fruitless runs commit nothing."""
from __future__ import annotations

import uuid

from agent_kit import BaseAgent

from research_domain.corpus import CorpusReader
from research_domain.runtime import ResearchRuntime
from research_domain.schemas import ExtractorOutput, RetractorOutput, VerifierOutput


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


VERIFIER_PROMPT = """Verify the following claim against the rest of the corpus.
Use the tools to read other documents. For the claim below, report:
- corroborating_source_ids: other documents that independently support it
  (never the claim's own source).
- refutation: if some document contradicts it, give that document's
  source_id, a one-sentence counter_text stating what that document
  asserts instead, and a short reason.
If the corpus neither supports nor contradicts it, return an empty verdict.

CLAIM_ID: {claim_id}
CLAIM_SOURCE: {source_id}
CLAIM: {text}

OTHER DOCUMENTS: {other_docs}"""


class VerifierAgent(BaseAgent):
    name = "verifier"

    def __init__(
        self,
        runner,
        runtime: ResearchRuntime,
        corpus: CorpusReader,
        interval: int = 60,
        personality: str = "",
    ) -> None:
        super().__init__(runner, interval, name="verifier", personality=personality)
        self._runtime = runtime
        self._corpus = corpus
        # Claims examined with an empty verdict (corpus is silent on them).
        # In-memory by design: a restart re-examines them, committing nothing.
        self._inconclusive: set[str] = set()

    def _workable(self) -> list[str]:
        return [
            c["claim_id"]
            for c in self._runtime.list_claims()
            if not self._runtime.corroborators_for(c["claim_id"])
            and not self._runtime.refuters_for(c["claim_id"])
            and c["claim_id"] not in self._inconclusive
        ]

    async def readiness(self) -> float:
        return 0.6 if self._workable() else 0.0

    async def _run(self) -> None:
        workable = self._workable()
        if not workable:
            return
        claim_id = workable[0]
        claim = self._runtime.get_claim(claim_id)
        other_docs = [d for d in self._corpus.list_documents() if d != claim["source_id"]]
        prompt = VERIFIER_PROMPT.format(
            claim_id=claim_id, source_id=claim["source_id"], text=claim["text"],
            other_docs=", ".join(other_docs) if other_docs else "(none)",
        )
        result = await self._runner.ainvoke(
            {"messages": [{"role": "user", "content": prompt}]}
        )
        output = _structured(result, VerifierOutput)

        events: list[tuple[str, dict]] = []
        for verdict in output.verdicts:
            if verdict.claim_id != claim_id:
                continue  # runner answered about something else; drop it
            already = set(self._runtime.corroborators_for(claim_id))
            for source_id in verdict.corroborating_source_ids:
                if source_id == claim["source_id"] or source_id in already:
                    continue
                already.add(source_id)
                events.append((
                    "source.corroborated",
                    {"source_id": source_id, "claim_id": claim_id},
                ))
            if verdict.refutation is not None:
                counter_id = uuid.uuid4().hex
                events.append((
                    "claim.proposed",
                    {"claim_id": counter_id,
                     "source_id": verdict.refutation.source_id,
                     "text": verdict.refutation.counter_text},
                ))
                events.append((
                    "claim.refuted",
                    {"claim_id": counter_id, "target_claim_id": claim_id,
                     "reason": verdict.refutation.reason},
                ))
        if events:
            await self._runtime.append_events(events)
        else:
            self._inconclusive.add(claim_id)


RETRACTOR_PROMPT = """Two claims in the research log contradict each other.
Decide which claim should stand. If a refuting claim should supersede the
target, return a correction naming it; if the original target claim should
stand, return no corrections.

TARGET_CLAIM_ID: {claim_id}
TARGET_CLAIM: {text} (from {source_id})

REFUTING CLAIMS:
{refuters}"""


class RetractorAgent(BaseAgent):
    name = "retractor"

    def __init__(
        self,
        runner,
        runtime: ResearchRuntime,
        interval: int = 60,
        personality: str = "",
    ) -> None:
        super().__init__(runner, interval, name="retractor", personality=personality)
        self._runtime = runtime
        # Targets examined where the original claim stood (or the verdict was
        # invalid). In-memory by design: a restart re-examines, commits nothing.
        self._stood: set[str] = set()

    def _workable(self) -> list[str]:
        return [
            target
            for target in self._runtime.contradiction_targets()
            if not self._runtime.superseders_for(target)
            and target not in self._stood
        ]

    async def readiness(self) -> float:
        return 0.5 if self._workable() else 0.0

    async def _run(self) -> None:
        workable = self._workable()
        if not workable:
            return
        target_id = workable[0]
        target = self._runtime.get_claim(target_id) or {
            "claim_id": target_id, "source_id": "(unknown)", "text": "(unknown claim)"
        }
        refuter_lines = []
        for rid in self._runtime.refuters_for(target_id):
            rc = self._runtime.get_claim(rid)
            if rc:
                refuter_lines.append(f"- {rid}: {rc['text']} (from {rc['source_id']})")
            else:
                refuter_lines.append(f"- {rid}: (unknown claim)")
        prompt = RETRACTOR_PROMPT.format(
            claim_id=target_id, text=target["text"], source_id=target["source_id"],
            refuters="\n".join(refuter_lines),
        )
        result = await self._runner.ainvoke(
            {"messages": [{"role": "user", "content": prompt}]}
        )
        output = _structured(result, RetractorOutput)

        events: list[tuple[str, dict]] = []
        valid_refuters = set(self._runtime.refuters_for(target_id))
        for correction in output.corrections:
            if correction.target_claim_id != target_id:
                continue
            if correction.superseding_claim_id not in valid_refuters:
                continue  # commit-time validation: only an actual refuter may supersede
            if self._runtime.superseders_for(target_id):
                continue  # already superseded (recheck at commit)
            events.append((
                "claim.corrected",
                {"claim_id": correction.superseding_claim_id,
                 "target_claim_id": target_id,
                 "reason": correction.reason},
            ))
        if events:
            await self._runtime.append_events(events)
        else:
            self._stood.add(target_id)
