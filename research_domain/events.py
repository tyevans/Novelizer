from __future__ import annotations
from pydantic import BaseModel


class ClaimProposed(BaseModel):
    """Payload for claim.proposed — mints a new claim's identity.

    origin distinguishes how the claim entered the log: "extracted" means
    the extractor swept the source document itself; "verification" means
    the claim is a counter-claim minted while verifying some other claim.
    Missing origin (pre-existing streams written before this field existed)
    defaults to "extracted" for back-compat.
    """

    claim_id: str
    source_id: str
    text: str
    origin: str = "extracted"


class SourceCorroborated(BaseModel):
    """Payload for source.corroborated — additive evidence for an existing claim."""

    source_id: str
    claim_id: str


class ClaimRefuted(BaseModel):
    """Payload for claim.refuted — claim_id contradicts target_claim_id."""

    claim_id: str
    target_claim_id: str
    reason: str


class ClaimCorrected(BaseModel):
    """Payload for claim.corrected — claim_id supersedes target_claim_id.

    Same shape as ClaimRefuted; the distinction is which event type fired.
    """

    claim_id: str
    target_claim_id: str
    reason: str
