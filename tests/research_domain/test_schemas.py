from __future__ import annotations

from research_domain.schemas import (
    ClaimDraft,
    CorrectionDraft,
    ExtractorOutput,
    RefutationDraft,
    RetractorOutput,
    VerificationDraft,
    VerifierOutput,
)


def test_extractor_output_defaults_empty():
    assert ExtractorOutput().claims == []
    out = ExtractorOutput(claims=[ClaimDraft(text="water boils at 100C")])
    assert out.claims[0].text == "water boils at 100C"


def test_verification_draft_defaults():
    v = VerificationDraft(claim_id="c1")
    assert v.corroborating_source_ids == []
    assert v.refutation is None
    withref = VerificationDraft(
        claim_id="c1",
        refutation=RefutationDraft(source_id="b.md", counter_text="no", reason="contradicts"),
    )
    assert withref.refutation.source_id == "b.md"
    assert VerifierOutput().verdicts == []


def test_retractor_output_roundtrip():
    out = RetractorOutput(corrections=[
        CorrectionDraft(superseding_claim_id="c2", target_claim_id="c1", reason="newer data")])
    assert out.corrections[0].target_claim_id == "c1"
    assert RetractorOutput().corrections == []
