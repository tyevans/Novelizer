import asyncio
import os
import tempfile
from hypothesis import given, settings, strategies as st
from novelizer.canon.event_store import EventStore
from novelizer.canon.projector import Projector
from novelizer.canon.read_store import ReadStore
from novelizer.canon.events import EventType, CausalEdgeDeclared

_CHAPTER_POOL = ["c1", "c2", "c3", "c4"]
_edge_strategy = st.tuples(
    st.sampled_from(_CHAPTER_POOL), st.sampled_from(_CHAPTER_POOL), st.text(max_size=5),
)


async def _run_edges(edges: list[tuple[str, str, str]]) -> tuple[list, list]:
    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    try:
        events = EventStore(path)
        await events.init()
        proj = Projector(events, path)
        await proj.init()
        read = ReadStore(path)
        await read.init()

        for cause, effect, note in edges:
            await events.append(
                EventType.CAUSAL_EDGE_DECLARED, effect,
                CausalEdgeDeclared(cause_chapter_id=cause, effect_chapter_id=effect, note=note),
            )
        await proj.catch_up()
        incremental = sorted(
            (e.cause_chapter_id, e.effect_chapter_id, e.note) for e in await read.list_causal_edges()
        )

        proj2 = Projector(events, path)
        await proj2.init()
        await proj2._reset_state()
        await proj2.catch_up()
        rebuilt = sorted(
            (e.cause_chapter_id, e.effect_chapter_id, e.note) for e in await read.list_causal_edges()
        )
        await proj2.close()

        await read.close()
        await proj.close()
        await events.close()
        return incremental, rebuilt
    finally:
        os.unlink(path)


@given(st.lists(_edge_strategy, max_size=10))
@settings(max_examples=50, deadline=None)
def test_causal_graph_fold_never_drops_or_duplicates_edges(edges):
    """For any sequence of declared edges (including exact repeats and
    self-edges -- the projection itself does no validation, that's the
    commit-time job of BaseAgent._commit_causal_intents in Task 11), the
    projected row multiset exactly matches the declared event multiset:
    no edge is dropped, none is duplicated beyond what was actually
    declared, and a from-scratch rebuild agrees (replay idempotence)."""
    incremental, rebuilt = asyncio.run(_run_edges(edges))
    expected = sorted(edges)
    assert incremental == expected
    assert rebuilt == expected
