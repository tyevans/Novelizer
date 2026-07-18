from __future__ import annotations
from pydantic import BaseModel
from novelizer.canon.event_store import EventStore
from novelizer.canon.events import EventType
from novelizer.canon.autonomy import Proposal


class Committer:
    """The single seam through which agents write canon.

    M1.1 appends the event directly (full-auto). GatingCommitter (below) is
    the M1.3 replacement that may append a proposal instead, keyed on
    ``agent_name`` and ``event_type`` — without any agent changing.
    """

    def __init__(self, event_store: EventStore) -> None:
        self._events = event_store

    async def commit(self, agent_name: str, event_type: str, aggregate_id: str, payload: BaseModel) -> None:
        await self._events.append(event_type, aggregate_id, payload)


class GatingCommitter:
    """Drop-in replacement for Committer that consults an AutonomyPolicy.

    Same public `commit` signature as Committer, so Runtime can swap the
    implementation with zero agent-code changes.
    """

    def __init__(self, event_store: EventStore, policy) -> None:
        self._events = event_store
        self._policy = policy

    async def commit(self, agent_name: str, event_type: str, aggregate_id: str, payload: BaseModel) -> None:
        if await self._policy.is_gated(agent_name, event_type):
            proposal = Proposal(
                proposing_agent=agent_name,
                target_event_type=event_type,
                target_aggregate_id=aggregate_id,
                payload=payload.model_dump(mode="json"),
            )
            await self._events.append(EventType.PROPOSAL_CREATED, proposal.id, proposal)
            return
        await self._events.append(event_type, aggregate_id, payload)
