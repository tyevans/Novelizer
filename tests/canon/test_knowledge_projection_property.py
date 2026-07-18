import asyncio
import os
import tempfile
from hypothesis import given, settings, strategies as st
from novelizer.canon.event_store import EventStore
from novelizer.canon.projector import Projector
from novelizer.canon.read_store import ReadStore
from novelizer.canon.events import EventType, SecretCreated, SecretLearned, SecretRevealed
from novelizer.canon.secrets import knowledge_cell_state

SECRET_ID = "s1"
CHARACTER_ID = "c1"


def _expected_cell(actions: list[str]) -> str:
    """Pure oracle: known/revealed only ever flip False->True, so the final
    cell state is fully determined by whether 'learn'/'reveal' ever
    appeared, independent of order or repetition (Locked decision #2's
    monotonic-lattice contract)."""
    known = "learn" in actions
    revealed = "reveal" in actions
    if revealed:
        return "revealed"
    return "known" if known else "unknown"


async def _run_sequence(actions: list[str]) -> tuple[str, str]:
    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    try:
        events = EventStore(path)
        await events.init()
        proj = Projector(events, path)
        await proj.init()
        read = ReadStore(path)
        await read.init()

        await events.append(EventType.SECRET_CREATED, SECRET_ID, SecretCreated(id=SECRET_ID, title="A Secret"))
        for action in actions:
            if action == "learn":
                await events.append(EventType.SECRET_LEARNED, SECRET_ID, SecretLearned(id=SECRET_ID, character_id=CHARACTER_ID))
            else:
                await events.append(EventType.SECRET_REVEALED, SECRET_ID, SecretRevealed(id=SECRET_ID))
        await proj.catch_up()
        matrix = await read.knowledge_matrix()
        incremental_cell = knowledge_cell_state(matrix, SECRET_ID, CHARACTER_ID)

        # Rebuild equivalence: a fresh projector replaying from zero agrees.
        proj2 = Projector(events, path)
        await proj2.init()
        await proj2._reset_state()
        await proj2.catch_up()
        rebuilt_matrix = await read.knowledge_matrix()
        rebuilt_cell = knowledge_cell_state(rebuilt_matrix, SECRET_ID, CHARACTER_ID)
        await proj2.close()

        await read.close()
        await proj.close()
        await events.close()
        return incremental_cell, rebuilt_cell
    finally:
        os.unlink(path)


@given(st.lists(st.sampled_from(["learn", "reveal"]), max_size=8))
@settings(max_examples=50, deadline=None)
def test_knowledge_matrix_is_monotonic_for_any_event_sequence(actions):
    """For any interleaving/repetition of learn/reveal events following a
    secret.created, the projected (secret, character) cell state matches the
    monotonic-lattice oracle (unknown -> known -> revealed, never backwards,
    revealed is set-once), and a from-scratch rebuild agrees (replay
    idempotence)."""
    incremental_cell, rebuilt_cell = asyncio.run(_run_sequence(actions))
    expected = _expected_cell(actions)
    assert incremental_cell == expected
    assert rebuilt_cell == expected


@given(st.integers(min_value=0, max_value=5))
@settings(max_examples=20, deadline=None)
def test_revealed_flag_is_set_once_under_repeated_reveals(n_reveals):
    """Repeating secret.revealed any number of times never un-sets or
    re-sets the flag beyond True -- it is idempotent (Locked decision #2)."""
    actions = ["reveal"] * n_reveals
    incremental_cell, rebuilt_cell = asyncio.run(_run_sequence(actions))
    expected = "revealed" if n_reveals > 0 else "unknown"
    assert incremental_cell == expected
    assert rebuilt_cell == expected
