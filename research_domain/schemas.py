"""Structured outputs for the research agents. IDs are never minted by the
LLM: ClaimDraft carries only text; claim_ids are uuid4-minted at commit
time by the agent, and Verifier/Retractor reference existing ids they saw
via tools or the prompt."""
from __future__ import annotations

from pydantic import BaseModel, Field


class ClaimDraft(BaseModel):
    text: str


class ExtractorOutput(BaseModel):
    claims: list[ClaimDraft] = Field(default_factory=list)


class RefutationDraft(BaseModel):
    source_id: str
    counter_text: str
    reason: str


class VerificationDraft(BaseModel):
    claim_id: str
    corroborating_source_ids: list[str] = Field(default_factory=list)
    refutation: RefutationDraft | None = None


class VerifierOutput(BaseModel):
    verdicts: list[VerificationDraft] = Field(default_factory=list)


class CorrectionDraft(BaseModel):
    superseding_claim_id: str
    target_claim_id: str
    reason: str


class RetractorOutput(BaseModel):
    corrections: list[CorrectionDraft] = Field(default_factory=list)
