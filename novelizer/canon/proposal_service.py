from __future__ import annotations
from novelizer.canon.event_store import EventStore
from novelizer.canon.events import EventType
from novelizer.canon.autonomy import Proposal, ProposalStatus


class ProposalService:
    """Turns an open Proposal into either its real target event + proposal.approved,
    or a proposal.rejected — the only two ways a proposal leaves the 'open' state.
    """

    def __init__(self, event_store: EventStore) -> None:
        self._events = event_store

    async def approve(self, proposal: Proposal) -> None:
        if proposal.status != ProposalStatus.open:
            return
        await self._events.append_raw(proposal.target_event_type, proposal.target_aggregate_id, proposal.payload)
        approved = proposal.model_copy(update={"status": ProposalStatus.approved})
        await self._events.append(EventType.PROPOSAL_APPROVED, proposal.id, approved)

    async def reject(self, proposal: Proposal) -> None:
        if proposal.status != ProposalStatus.open:
            return
        rejected = proposal.model_copy(update={"status": ProposalStatus.rejected})
        await self._events.append(EventType.PROPOSAL_REJECTED, proposal.id, rejected)
