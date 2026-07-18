from __future__ import annotations
import uuid
from datetime import datetime, timezone
from enum import StrEnum
from pydantic import BaseModel, Field


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _uuid() -> str:
    return str(uuid.uuid4())


class AutonomyLevel(StrEnum):
    full_auto = "full_auto"
    gated_retcons = "gated_retcons"
    gated_canon = "gated_canon"
    gated_all = "gated_all"


class ProposalStatus(StrEnum):
    open = "open"
    approved = "approved"
    rejected = "rejected"


class Proposal(BaseModel):
    id: str = Field(default_factory=_uuid)
    created_at: datetime = Field(default_factory=_now)
    proposing_agent: str
    target_event_type: str
    target_aggregate_id: str
    payload: dict
    status: ProposalStatus = ProposalStatus.open


class AutonomyState(BaseModel):
    global_level: AutonomyLevel = AutonomyLevel.full_auto
    overrides: dict[str, AutonomyLevel] = Field(default_factory=dict)

    def level_for(self, agent_name: str) -> AutonomyLevel:
        return self.overrides.get(agent_name, self.global_level)
