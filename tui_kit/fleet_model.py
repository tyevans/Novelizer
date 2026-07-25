"""Many agents' runs, folded per agent and merged into one chronological
stream. No Textual imports; black-box testable.

run_model.LiveRunState is deliberately ONE run: RunStarted resets it and any
event whose run_id differs is dropped. Folding a whole fleet into a single
LiveRunState therefore silently discards every event of every agent except
the most recently started one -- the running agent's prose freezes
mid-sentence and its tool blocks stay "running" forever. So the fleet keeps
one LiveRunState per agent, and a merged entry list that carries the global
ordering the per-agent block lists cannot express.

The ordering rule: entries are appended in arrival order and never removed or
reordered, so an entry's index in `entries` is a permanent identity -- which
is exactly what StreamView's block_key requires.
"""
from __future__ import annotations
from dataclasses import dataclass, field, replace
from rich.text import Text
from tui_kit.contracts import AgentTheme
from tui_kit.run_model import (
    LiveRunState, StreamBlock, apply_bus_item, route_agent, styled_vitals,
)


@dataclass(frozen=True)
class Entry:
    """One block, tagged with the run it belongs to so a later event for that
    run can update it in place rather than append a duplicate."""

    agent: str
    run_id: str
    index: int          # position within that run's own block list
    block: StreamBlock


@dataclass(frozen=True)
class FleetState:
    states: dict[str, LiveRunState] = field(default_factory=dict)
    entries: tuple[Entry, ...] = ()


def set_run_state(fleet: FleetState, agent: str, state: LiveRunState) -> FleetState:
    """Record `state` as `agent`'s current run and reconcile its blocks into
    the merged list. Blocks of the same (agent, run) are updated where they
    already sit; new ones are appended at the tail, which is where they
    belong chronologically -- they only just arrived.

    `agent` may be empty (a caller pushing a LiveRunState directly): runs are
    still separated by run_id, so a nameless second run appends rather than
    overwriting the first.
    """
    entries = list(fleet.entries)
    positions = [j for j, e in enumerate(entries)
                 if e.agent == agent and e.run_id == state.run_id]
    for i, j in enumerate(positions):
        if i < len(state.blocks) and entries[j].block != state.blocks[i]:
            entries[j] = replace(entries[j], block=state.blocks[i])
    for i in range(len(positions), len(state.blocks)):
        entries.append(Entry(agent=agent, run_id=state.run_id, index=i,
                             block=state.blocks[i]))
    states = dict(fleet.states)
    states[agent] = state
    return FleetState(states=states, entries=tuple(entries))


def apply_fleet(fleet: FleetState, item, now: float) -> FleetState:
    """Fold one contract event into its own agent's run, leaving every other
    agent's run untouched."""
    agent = route_agent(item)
    if not agent:
        return fleet
    prev = fleet.states.get(agent, LiveRunState())
    return set_run_state(fleet, agent, apply_bus_item(prev, item, now))


def blocks(fleet: FleetState) -> tuple[StreamBlock, ...]:
    """The unified stream, oldest first."""
    return tuple(e.block for e in fleet.entries)


def seed_fleet(recent: list, now: float) -> FleetState:
    """Replay after a restart. Every agent that was mid-run is restored, not
    just whichever ran last, and each is marked detached: the ephemeral token
    stream from before the restart is gone, so saying "streaming" would lie."""
    fleet = FleetState()
    for ev in recent:
        fleet = apply_fleet(fleet, ev, now)
    states = {a: (replace(s, stream_attached=False) if s.status == "running" else s)
              for a, s in fleet.states.items()}
    return FleetState(states=states, entries=fleet.entries)


def running_states(fleet: FleetState) -> list[LiveRunState]:
    return sorted((s for s in fleet.states.values() if s.status == "running"),
                  key=lambda s: s.started_at)


def primary_state(fleet: FleetState) -> LiveRunState:
    """The one run a single-run surface (the activity strip, the prompt pane)
    should show: the newest running agent, else the most recently ended."""
    running = running_states(fleet)
    if running:
        return running[-1]
    if not fleet.states:
        return LiveRunState()
    return max(fleet.states.values(), key=lambda s: max(s.ended_at, s.started_at))


def fleet_vitals(fleet: FleetState, now: float, theme: AgentTheme,
                 hold: str = "") -> Text:
    """One vitals line for the whole fleet: every running agent, side by side.

    `hold` is the fleet-wide reason nothing is producing (see
    roster.fleet_hold_summary). It only ever captions a fleet with nothing
    running: a leftover hold beside a live token stream would be a lie, and
    the holds are polled on a different cadence than the stream.
    """
    running = running_states(fleet)
    if running:
        line = Text()
        for i, state in enumerate(running):
            if i:
                line.append("  │  ", style="dim")
            line.append_text(styled_vitals(state, now, theme))
        return line
    state = primary_state(fleet)
    line = styled_vitals(state, now, theme, hold=hold)
    if hold and state.status != "idle":
        # vitals_line only captions the *idle* line; a crashed or finished
        # last run still needs the fleet's holds beside it, or a rate-limited
        # fleet and a converged one read identically.
        line = line + Text(f" · holds: {hold}", style="dim")
    return line
