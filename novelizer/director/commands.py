from __future__ import annotations
from novelizer.canon.events import EventType
from novelizer.canon.autonomy import AutonomyLevel, AutonomyState, ProposalStatus
from novelizer.canon.proposal_service import ProposalService
from novelizer.store.models import DirectorSignal, SignalKind


async def seed(events, text: str) -> None:
    sig = DirectorSignal(kind=SignalKind.seed, body=text)
    await events.append(EventType.DIRECTOR_SIGNAL_CREATED, sig.id, sig)


async def focus(events, entity: str) -> None:
    sig = DirectorSignal(kind=SignalKind.focus, body=entity)
    await events.append(EventType.DIRECTOR_SIGNAL_CREATED, sig.id, sig)


def pause(scheduler, agent_name: str) -> None:
    scheduler.pause_agent(agent_name)


def resume(scheduler, agent_name: str) -> None:
    scheduler.resume_agent(agent_name)


async def autonomy(events, state: AutonomyState) -> None:
    await events.append(EventType.AUTONOMY_CHANGED, "singleton", state)


async def approve(events, read, proposal_id: str) -> str:
    proposal = await read.get_proposal(proposal_id)
    if proposal is None:
        return f"Proposal not found: {proposal_id}"
    if proposal.status != ProposalStatus.open:
        return f"Proposal {proposal_id} is already {proposal.status.value}."
    await ProposalService(events).approve(proposal)
    return f"Approved proposal {proposal_id} ({proposal.target_event_type})"


async def reject(events, read, proposal_id: str) -> str:
    proposal = await read.get_proposal(proposal_id)
    if proposal is None:
        return f"Proposal not found: {proposal_id}"
    if proposal.status != ProposalStatus.open:
        return f"Proposal {proposal_id} is already {proposal.status.value}."
    await ProposalService(events).reject(proposal)
    return f"Rejected proposal {proposal_id} ({proposal.target_event_type})"


async def _dispatch_decision(runtime, proposal_id: str, action: str) -> str:
    """Resolve the proposal and mutate through the runtime's single ProposalService
    instance so the open-status guard in ProposalService always applies."""
    proposal = await runtime.read.get_proposal(proposal_id)
    if proposal is None:
        return f"Proposal not found: {proposal_id}"
    if proposal.status != ProposalStatus.open:
        return f"Proposal {proposal_id} is already {proposal.status.value}."
    if action == "approve":
        await runtime.proposals.approve(proposal)
        return f"Approved proposal {proposal_id} ({proposal.target_event_type})"
    await runtime.proposals.reject(proposal)
    return f"Rejected proposal {proposal_id} ({proposal.target_event_type})"


async def dispatch(runtime, line: str) -> str:
    parts = line.strip().split(maxsplit=2)
    if not parts:
        return "Empty command."
    cmd = parts[0].lower()
    rest = parts[1:]
    if cmd == "seed" and rest:
        text = line.strip().split(maxsplit=1)[1]
        await seed(runtime.events, text)
        return f"Seed injected: {text}"
    if cmd == "focus" and rest:
        text = line.strip().split(maxsplit=1)[1]
        await focus(runtime.events, text)
        return f"Focus set: {text}"
    if cmd == "pause" and rest:
        pause(runtime.scheduler, rest[0])
        return f"Paused: {rest[0]}"
    if cmd == "resume" and rest:
        resume(runtime.scheduler, rest[0])
        return f"Resumed: {rest[0]}"
    if cmd == "autonomy" and rest:
        level_str = rest[0]
        agent = rest[1] if len(rest) > 1 else None
        try:
            level = AutonomyLevel(level_str)
        except ValueError:
            return f"Unknown autonomy level: {level_str}"
        current = await runtime.read.get_autonomy_state()
        if agent:
            overrides = dict(current.overrides)
            overrides[agent] = level
            next_state = AutonomyState(global_level=current.global_level, overrides=overrides)
            await autonomy(runtime.events, next_state)
            return f"Autonomy for {agent} set to {level.value}"
        next_state = AutonomyState(global_level=level, overrides=current.overrides)
        await autonomy(runtime.events, next_state)
        return f"Global autonomy set to {level.value}"
    if cmd == "approve" and rest:
        return await _dispatch_decision(runtime, rest[0], "approve")
    if cmd == "reject" and rest:
        return await _dispatch_decision(runtime, rest[0], "reject")
    return f"Unknown command: {line.strip()}"
