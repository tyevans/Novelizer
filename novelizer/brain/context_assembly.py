from __future__ import annotations
import math
from dataclasses import dataclass
from typing import Protocol
from novelizer.text_chunk import chunk_prose

ELISION_MARKER = "[…truncated — summary pending]"
OMITTED_HEADER_FMT = "[{n} earlier chapters omitted]"


class TokenEstimator(Protocol):
    def estimate(self, text: str) -> int: ...


@dataclass(frozen=True)
class CharHeuristicEstimator:
    """chars/4 heuristic: no tokenizer exists in this repo (llm endpoint is a
    local OpenAI-compatible server with no stable tokenizer API); the protocol
    is the seam for injecting a real one later."""
    chars_per_token: float = 4.0

    def estimate(self, text: str) -> int:
        return math.ceil(len(text) / self.chars_per_token)


_DEFAULT_ESTIMATOR = CharHeuristicEstimator()


@dataclass(frozen=True)
class Window:
    text: str
    index: int
    total: int


def assemble_verbatim(
    text: str, budget_tokens: int, estimator: TokenEstimator | None = None
) -> list[Window]:
    """Full text as one window when it fits the budget; otherwise ordered
    overlapping windows covering the whole text — never a silent head-slice."""
    est = estimator or _DEFAULT_ESTIMATOR
    total = est.estimate(text)
    if not text or total <= budget_tokens:
        return [Window(text=text, index=0, total=1)]
    # Derive chars-per-token empirically so any estimator works here.
    ratio = len(text) / total
    window_chars = max(1, int(budget_tokens * ratio))
    overlap_chars = min(max(0, int((budget_tokens // 4) * ratio)), window_chars - 1)
    parts = chunk_prose(text, window_chars, overlap_chars)
    return [Window(text=p, index=i, total=len(parts)) for i, p in enumerate(parts)]


@dataclass(frozen=True)
class AdvisoryEntry:
    label: str
    summary: str | None = None
    verbatim: str | None = None


def assemble_advisory(
    entries: list[AdvisoryEntry], budget_tokens: int, estimator: TokenEstimator | None = None
) -> str:
    """Story-so-far block packed newest-first within budget. Prefers summaries;
    a chapter without one (Summarizer lag) falls back to a labeled verbatim
    head ending in ELISION_MARKER. Entries that don't fit are dropped oldest
    first and announced via OMITTED_HEADER_FMT — degraded is fine, silent is not."""
    est = estimator or _DEFAULT_ESTIMATOR
    kept: list[str] = []
    remaining = budget_tokens
    omitted = 0
    for entry in reversed(entries):  # newest first
        # Falsy (empty) summaries count as absent: an empty string must never
        # displace the labeled verbatim fallback — that would be a new silent
        # truncation path.
        if entry.summary:
            line = f"- {entry.label}: {entry.summary}"
        elif entry.verbatim is not None:
            head_chars = max(0, int(remaining * len(entry.verbatim) /
                                    max(1, est.estimate(entry.verbatim))))
            head = entry.verbatim[:head_chars]
            line = f"- {entry.label}: {head}"
            if head_chars < len(entry.verbatim):
                line += f" {ELISION_MARKER}"
        else:
            line = f"- {entry.label}: (no content)"
        cost = est.estimate(line)
        if kept and cost > remaining:
            omitted = len(entries) - len(kept)
            break
        kept.append(line)
        remaining -= cost
        if remaining <= 0 and len(kept) < len(entries):
            omitted = len(entries) - len(kept)
            break
    kept.reverse()  # chronological for the prompt
    if omitted:
        kept.insert(0, OMITTED_HEADER_FMT.format(n=omitted))
    return "\n".join(kept)
