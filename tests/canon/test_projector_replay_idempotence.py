"""Replaying the log must never duplicate a projection.

The projector commits each event's writes in its own transaction but used to
persist `last_sequence` only after the whole batch. A raise partway through
therefore left every earlier event committed with the cursor still behind them,
and novelizer/tui/app.py's projector loop catches the error and calls catch_up()
again every tick -- so one permanently-failing event re-applied the whole
uncursored prefix a few times a second, forever.

That is only visible for handlers whose write is not naturally idempotent. Most
upsert on a primary key; `causal_edges` and `secret_references` were bare
INSERTs into tables with no unique constraint, so they grew a duplicate row per
pass. Duplicated causal edges reach find_paradoxes and both agent prompts.

The fix is the cursor, and *only* the cursor. Deduping inside those handlers
looks tempting and is wrong: this projection is a faithful multiset fold of the
log, pinned by test_causal_edge_declared_is_never_deduped,
test_secret_referenced_is_never_deduped and
test_causal_graph_fold_never_drops_or_duplicates_edges. Two identical declared
edges are two facts and the read model must show both. Suppressing a genuine
repeat to compensate for a cursor that re-read the log would trade a real bug
for a quieter one, and dedupe of *authored intent* already lives upstream, in
the commit helpers' guards. With the cursor advancing atomically each event is
applied exactly once, so the fold stays faithful and nothing is re-applied.
"""
from __future__ import annotations
import os
import tempfile

import pytest

from novelizer.canon import projections
from novelizer.canon.event_store import EventStore
from novelizer.canon.events import CausalEdgeDeclared, EventType, SecretReferenced
from novelizer.canon.projector import Projector


@pytest.fixture
async def wired():
    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    events = EventStore(path)
    await events.init()
    proj = Projector(events, path)
    await proj.init()
    yield events, proj, path
    await proj.close()
    await events.close()
    os.unlink(path)


async def _count(proj, table: str) -> int:
    cur = await proj._conn.execute(f"SELECT COUNT(*) FROM {table}")
    return (await cur.fetchone())[0]


async def test_a_failing_event_does_not_replay_its_predecessors(wired):
    """The scenario the TUI loop actually produces."""
    events, proj, _ = wired
    await events.append(EventType.CAUSAL_EDGE_DECLARED, "c1",
                        CausalEdgeDeclared(cause_chapter_id="ch1", effect_chapter_id="ch2"))
    # A poison event: its handler raises every time, like a payload shape the
    # current handler cannot read.
    await events.append_raw(EventType.THEME_INTRODUCED, "t1", {"deliberately": "malformed"})

    for _ in range(3):  # the loop retries forever; three passes is enough
        with pytest.raises(Exception):
            await proj.catch_up()

    assert await _count(proj, "causal_edges") == 1, (
        "the causal edge was re-projected on every retry because the cursor "
        "never advanced past it"
    )


async def test_the_cursor_advances_with_each_event_not_per_batch(wired):
    events, proj, _ = wired
    first = await events.append(EventType.CAUSAL_EDGE_DECLARED, "c1",
                                CausalEdgeDeclared(cause_chapter_id="ch1", effect_chapter_id="ch2"))
    await events.append_raw(EventType.THEME_INTRODUCED, "t1", {"deliberately": "malformed"})

    with pytest.raises(Exception):
        await proj.catch_up()

    assert await proj._last_sequence() == first.sequence, (
        "the cursor must sit at the last successfully projected event, so the "
        "failing one is retried and the successful ones are not"
    )


async def test_distinct_causal_edges_still_both_land(wired):
    """Idempotence must not collapse genuinely different edges."""
    events, proj, _ = wired
    await events.append(EventType.CAUSAL_EDGE_DECLARED, "c1",
                        CausalEdgeDeclared(cause_chapter_id="ch1", effect_chapter_id="ch2"))
    await events.append(EventType.CAUSAL_EDGE_DECLARED, "c2",
                        CausalEdgeDeclared(cause_chapter_id="ch2", effect_chapter_id="ch3"))
    await proj.catch_up()
    assert await _count(proj, "causal_edges") == 2


async def test_a_secret_referenced_in_two_chapters_keeps_both(wired):
    events, proj, _ = wired
    await events.append(EventType.SECRET_REFERENCED, "s1",
                        SecretReferenced(id="s1", character_id="alice", chapter_id="ch1"))
    await events.append(EventType.SECRET_REFERENCED, "s1",
                        SecretReferenced(id="s1", character_id="alice", chapter_id="ch2"))
    await proj.catch_up()
    assert await _count(proj, "secret_references") == 2
