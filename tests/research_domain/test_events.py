import pytest
from pydantic import ValidationError

from research_domain.events import (
    ClaimProposed,
    SourceCorroborated,
    ClaimRefuted,
    ClaimCorrected,
)


def test_claim_proposed_requires_claim_id_source_id_text():
    event = ClaimProposed(claim_id="claim-1", source_id="source-a", text="x")
    assert event.claim_id == "claim-1"
    assert event.source_id == "source-a"
    assert event.text == "x"


def test_claim_proposed_missing_field_raises():
    with pytest.raises(ValidationError):
        ClaimProposed(claim_id="claim-1", source_id="source-a")


def test_source_corroborated_requires_source_id_claim_id():
    event = SourceCorroborated(source_id="source-a", claim_id="claim-1")
    assert event.source_id == "source-a"
    assert event.claim_id == "claim-1"


def test_claim_refuted_requires_claim_id_target_claim_id_reason():
    event = ClaimRefuted(claim_id="claim-2", target_claim_id="claim-1", reason="contradicted by source-b")
    assert event.claim_id == "claim-2"
    assert event.target_claim_id == "claim-1"
    assert event.reason == "contradicted by source-b"


def test_claim_corrected_requires_claim_id_target_claim_id_reason():
    event = ClaimCorrected(claim_id="claim-3", target_claim_id="claim-1", reason="superseded with better data")
    assert event.claim_id == "claim-3"
    assert event.target_claim_id == "claim-1"
    assert event.reason == "superseded with better data"


def test_claim_refuted_missing_target_claim_id_raises():
    with pytest.raises(ValidationError):
        ClaimRefuted(claim_id="claim-2", reason="x")
