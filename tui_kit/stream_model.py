"""Unified-stream view state: which blocks are shown, and whether the
view is following the tail.

Split from run_model because the concerns differ: run_model folds events
into blocks (one reason to change: the event vocabulary), this folds user
intent over that list (one reason to change: the interaction design).
No Textual, no Rich -- the widget layer applies these decisions.
"""
from __future__ import annotations
from dataclasses import dataclass, replace
from tui_kit.run_model import StreamBlock, block_agent


@dataclass(frozen=True)
class StreamState:
    blocks: tuple[StreamBlock, ...] = ()
    # Empty means "every agent" -- the "all" chip. Storing the empty set
    # rather than the full roster keeps this layer ignorant of the roster.
    agent_filter: frozenset[str] = frozenset()
    follow: bool = True
    unseen: int = 0


def visible_blocks(state: StreamState) -> tuple[StreamBlock, ...]:
    if not state.agent_filter:
        return state.blocks
    return tuple(b for b in state.blocks if block_agent(b) in state.agent_filter)


def toggle_agent(state: StreamState, agent: str) -> StreamState:
    f = state.agent_filter
    updated = f - {agent} if agent in f else f | {agent}
    return replace(state, agent_filter=frozenset(updated))


def clear_filter(state: StreamState) -> StreamState:
    return replace(state, agent_filter=frozenset())


def on_scroll(state: StreamState, at_bottom: bool) -> StreamState:
    """The only place follow-mode changes. Reaching the bottom reattaches
    and clears the backlog counter; leaving it detaches."""
    if at_bottom:
        return replace(state, follow=True, unseen=0)
    return replace(state, follow=False)


def on_new_blocks(state: StreamState, blocks: tuple[StreamBlock, ...]) -> StreamState:
    """Append. While detached, count what the reader has not seen; while
    following, there is by definition no backlog."""
    merged = state.blocks + tuple(blocks)
    if state.follow:
        return replace(state, blocks=merged)
    return replace(state, blocks=merged, unseen=state.unseen + len(blocks))


# How many blocks stay mounted. Textual mounts a widget per block, so this
# bounds widget count and memory both; history beyond it is paged back in
# from the event store on demand.
WINDOW_CAP = 400


def trim_window(state: StreamState) -> StreamState:
    """Drop from the head -- the tail is what a following reader is
    watching, and paged-in history can always be re-fetched."""
    if len(state.blocks) <= WINDOW_CAP:
        return state
    return replace(state, blocks=state.blocks[-WINDOW_CAP:])


def prepend_blocks(state: StreamState, blocks: tuple[StreamBlock, ...]) -> StreamState:
    """Paged-in history. Never touches `unseen`: the reader scrolled here
    deliberately, so this is not a backlog."""
    return replace(state, blocks=tuple(blocks) + state.blocks)


def oldest_sequence(state: StreamState) -> int:
    """Cursor for the next backward page. Only tool blocks carry a store
    sequence; prose is reconstructed from the segments around them."""
    for b in state.blocks:
        seq = getattr(b, "sequence", 0)
        if seq:
            return seq
    return 0
