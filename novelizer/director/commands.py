from __future__ import annotations
import logging
from novelizer.canon.events import EventType, InspirationHandSuperseded
from novelizer.canon.autonomy import AutonomyLevel, AutonomyState, ProposalStatus
from novelizer.canon.event_store import EventStore
from novelizer.canon.proposal_service import ProposalService
from novelizer.muse.report import muse_status_report
from novelizer.settings.story_dir import StoryDirectory
from novelizer.store.models import DirectorSignal, SignalKind

logger = logging.getLogger(__name__)


async def seed(events, text: str) -> None:
    sig = DirectorSignal(kind=SignalKind.seed, body=text)
    await events.append(EventType.DIRECTOR_SIGNAL_CREATED, sig.id, sig)


async def seed_story_dir(story: StoryDirectory, text: str) -> None:
    """Append a seed signal directly to a story's event log, without a running
    Runtime. Used at story-creation time: the picker runs before Runtime boots,
    and EventStore is standalone (creates its own schema on init)."""
    events = EventStore(str(story.db_path))
    await events.init()
    try:
        await seed(events, text)
    finally:
        await events.close()


async def focus(events, entity: str) -> None:
    sig = DirectorSignal(kind=SignalKind.focus, body=entity)
    await events.append(EventType.DIRECTOR_SIGNAL_CREATED, sig.id, sig)


def pause(scheduler, agent_name: str) -> None:
    scheduler.pause_agent(agent_name)


def resume(scheduler, agent_name: str) -> None:
    scheduler.resume_agent(agent_name)


async def autonomy(events, state: AutonomyState) -> None:
    await events.append(EventType.AUTONOMY_CHANGED, "singleton", state)


async def approve(proposals: ProposalService, read, proposal_id: str) -> str:
    proposal = await read.get_proposal(proposal_id)
    if proposal is None:
        return f"Proposal not found: {proposal_id}"
    if proposal.status != ProposalStatus.open:
        return f"Proposal {proposal_id} is already {proposal.status.value}."
    await proposals.approve(proposal)
    logger.info("approved proposal %s (%s)", proposal_id, proposal.target_event_type)
    return f"Approved proposal {proposal_id} ({proposal.target_event_type})"


async def reject(proposals: ProposalService, read, proposal_id: str) -> str:
    proposal = await read.get_proposal(proposal_id)
    if proposal is None:
        return f"Proposal not found: {proposal_id}"
    if proposal.status != ProposalStatus.open:
        return f"Proposal {proposal_id} is already {proposal.status.value}."
    await proposals.reject(proposal)
    logger.info("rejected proposal %s (%s)", proposal_id, proposal.target_event_type)
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
    # The status bar advertises colon-prefixed commands (":seed", ":focus"),
    # so accept the prefix as well as the bare form.
    cmd = parts[0].lower().removeprefix(":")
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
    if cmd == "muse":
        if rest and rest[0].lower() == "reroll":
            active = await runtime.read.get_active_hand()
            if active is not None:
                await runtime.events.append(
                    EventType.INSPIRATION_HAND_SUPERSEDED, active.id,
                    InspirationHandSuperseded(hand_id=active.id),
                )
            # Deal without waiting for the projector: deal_fresh_hand doesn't
            # check for an active hand, and the projection sorts itself out
            # (the superseded event lands before the new drawn event).
            # Note: if the Author already holds the old hand mid-draft when a
            # reroll fires, that chapter's eventual consumption no-ops against
            # the (now superseded) hand and its uptake goes untracked. This is
            # an accepted, human-triggered edge case.
            hand = await runtime.muse.deal_fresh_hand()
            return f"Rerolled. New hand: {'; '.join(hand.names)}"
        return muse_status_report(
            await runtime.read.get_active_hand(),
            await runtime.read.list_hands(),
            await runtime.read.list_uptake(),
        )
    return f"Unknown command: {line.strip()}"
