from __future__ import annotations
import logging
from dataclasses import dataclass
from typing import Awaitable, Callable
from novelizer.canon.beat_templates import BEAT_TEMPLATES
from novelizer.canon.events import (
    BlueprintRetargeted,
    EventType,
    InspirationHandSuperseded,
    SecretRevealPlanned,
    ThreadResolutionPlanned,
)
from novelizer.canon.autonomy import AutonomyLevel, AutonomyState, ProposalStatus
from novelizer.canon.event_store import EventStore
from novelizer.canon.proposal_service import ProposalService
from novelizer.canon.threads import TERMINAL_STATES
from novelizer.agents.intents import mint_blueprint
from novelizer.muse.report import muse_status_report
from novelizer.settings.story_dir import StoryDirectory
from novelizer.store.models import DirectorSignal, SignalKind


@dataclass(frozen=True)
class Command:
    """One entry point reachable from both the colon-bar and the command
    palette. `callback` takes (runtime, raw_args_string) and returns the
    result text shown in the feed, or None if required args are missing."""

    name: str
    description: str
    callback: Callable[[object, str], Awaitable[str | None]]
    takes_args: bool = True


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


async def adopt_blueprint_story_dir(
    story: StoryDirectory, framework: str, target_chapter_count: int, genre: str = ""
) -> None:
    """Append a blueprint.adopted event directly to a story's event log,
    without a running Runtime -- sibling of seed_story_dir. Used at
    story-creation time by the picker's Frame step: the creation form IS
    the sign-off (see BlueprintAdopted's docstring), so this bypasses the
    gated commit path deliberately, unlike commit_blueprint_plan.

    Raises ValueError for a framework not in BEAT_TEMPLATES or a
    target_chapter_count below 3 -- the caller is a form with a Select, so
    this is a programming-error guard, not a user-facing validation path.
    """
    if framework not in BEAT_TEMPLATES:
        raise ValueError(f"unknown framework: {framework!r}")
    if target_chapter_count < 3:
        raise ValueError(f"target_chapter_count must be >= 3, got {target_chapter_count!r}")
    payload = mint_blueprint(framework, target_chapter_count, genre)
    events = EventStore(str(story.db_path))
    await events.init()
    try:
        await events.append(EventType.BLUEPRINT_ADOPTED, payload.blueprint_id, payload)
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


async def plan_thread_resolution(
    events, read, thread_id: str, window_lo: int, window_hi: int, note: str = ""
) -> str:
    thread = await read.get_thread(thread_id)
    if thread is None:
        return f"no such thread: {thread_id}"
    if thread.state.value in TERMINAL_STATES:
        return f"thread {thread_id} is already {thread.state.value}"
    if not ((window_lo == 0 and window_hi == 0) or (1 <= window_lo <= window_hi)):
        return f"invalid window {window_lo}-{window_hi} (need 1 <= lo <= hi, or 0 0 to clear)"
    await events.append(
        EventType.THREAD_RESOLUTION_PLANNED,
        thread_id,
        ThreadResolutionPlanned(
            id=thread_id, window_lo=window_lo, window_hi=window_hi, planned_payoff_note=note
        ),
    )
    return f"resolution window ch{window_lo}-{window_hi} planned for '{thread.name}'"


async def retarget_blueprint(events, read, target_chapter_count: int) -> str:
    active = await read.get_active_blueprint()
    if active is None:
        return "no active blueprint to retarget"
    if target_chapter_count < 3:
        return f"invalid target_chapter_count {target_chapter_count} (need >= 3)"
    if target_chapter_count == active.target_chapter_count:
        return f"blueprint is already targeted at {target_chapter_count} chapters -- no change"
    await events.append(
        EventType.BLUEPRINT_RETARGETED,
        active.id,
        BlueprintRetargeted(blueprint_id=active.id, target_chapter_count=target_chapter_count),
    )
    return f"blueprint retargeted to {target_chapter_count} chapters"


async def plan_secret_reveal(events, read, secret_id: str, window_lo: int, window_hi: int) -> str:
    secret = await read.get_secret(secret_id)
    if secret is None:
        return f"no such secret: {secret_id}"
    if secret.revealed:
        return f"secret {secret_id} is already revealed"
    if not ((window_lo == 0 and window_hi == 0) or (1 <= window_lo <= window_hi)):
        return f"invalid window {window_lo}-{window_hi} (need 1 <= lo <= hi, or 0 0 to clear)"
    await events.append(
        EventType.SECRET_REVEAL_PLANNED,
        secret_id,
        SecretRevealPlanned(id=secret_id, window_lo=window_lo, window_hi=window_hi),
    )
    return f"reveal window ch{window_lo}-{window_hi} planned for '{secret.title}'"


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


async def _cmd_seed(runtime, args: str) -> str | None:
    if not args:
        return None
    await seed(runtime.events, args)
    return f"Seed injected: {args}"


async def _cmd_focus(runtime, args: str) -> str | None:
    if not args:
        return None
    await focus(runtime.events, args)
    return f"Focus set: {args}"


async def _cmd_pause(runtime, args: str) -> str | None:
    parts = args.split(maxsplit=1)
    if not parts:
        return None
    pause(runtime.scheduler, parts[0])
    return f"Paused: {parts[0]}"


async def _cmd_resume(runtime, args: str) -> str | None:
    parts = args.split(maxsplit=1)
    if not parts:
        return None
    resume(runtime.scheduler, parts[0])
    return f"Resumed: {parts[0]}"


async def _cmd_autonomy(runtime, args: str) -> str | None:
    parts = args.split(maxsplit=1)
    if not parts:
        return None
    level_str = parts[0]
    agent = parts[1] if len(parts) > 1 else None
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


async def _cmd_retarget(runtime, args: str) -> str | None:
    parts = args.split(maxsplit=1)
    if not parts:
        return None
    try:
        n = int(parts[0])
    except ValueError:
        return f"Invalid chapter count: {parts[0]}"
    return await retarget_blueprint(runtime.events, runtime.read, n)


async def _cmd_approve(runtime, args: str) -> str | None:
    parts = args.split(maxsplit=1)
    if not parts:
        return None
    return await _dispatch_decision(runtime, parts[0], "approve")


async def _cmd_reject(runtime, args: str) -> str | None:
    parts = args.split(maxsplit=1)
    if not parts:
        return None
    return await _dispatch_decision(runtime, parts[0], "reject")


async def _cmd_muse(runtime, args: str) -> str | None:
    parts = args.split(maxsplit=1)
    if parts and parts[0].lower() == "reroll":
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


COMMAND_REGISTRY: list[Command] = [
    Command("seed", "Inject a seed signal for the Author to pick up", _cmd_seed),
    Command("focus", "Set the current focus entity", _cmd_focus),
    Command("pause", "Pause an agent", _cmd_pause),
    Command("resume", "Resume a paused agent", _cmd_resume),
    Command("autonomy", "Set global or per-agent autonomy level", _cmd_autonomy),
    Command("retarget", "Retarget the active blueprint's chapter count", _cmd_retarget),
    Command("approve", "Approve a proposal by id", _cmd_approve),
    Command("reject", "Reject a proposal by id", _cmd_reject),
    Command("muse", "Show muse status, or 'muse reroll' for a fresh hand", _cmd_muse),
]


def find_command(name: str) -> Command | None:
    return next((c for c in COMMAND_REGISTRY if c.name == name), None)


async def dispatch(runtime, line: str) -> str:
    parts = line.strip().split(maxsplit=1)
    if not parts:
        return "Empty command."
    # The status bar advertises colon-prefixed commands (":seed", ":focus"),
    # so accept the prefix as well as the bare form.
    cmd = parts[0].lower().removeprefix(":")
    rest = parts[1] if len(parts) > 1 else ""
    command = find_command(cmd)
    if command is None:
        return f"Unknown command: {line.strip()}"
    result = await command.callback(runtime, rest)
    if result is None:
        return f"Unknown command: {line.strip()}"
    return result
