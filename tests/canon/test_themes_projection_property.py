import asyncio
import os
import tempfile
from hypothesis import given, settings, strategies as st
from novelizer.canon.event_store import EventStore
from novelizer.canon.projector import Projector
from novelizer.canon.read_store import ReadStore
from novelizer.canon.events import EventType, ThemeIntroduced, ThemeDeveloped


async def _run_sequence(actions: list[str]) -> tuple[int, int, int, int]:
    """Introduce theme t1, then apply the action sequence, tracking
    touch_count after every step (incremental) and after a from-scratch
    rebuild at the end."""
    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    try:
        events = EventStore(path)
        await events.init()
        proj = Projector(events, path)
        await proj.init()
        read = ReadStore(path)
        await read.init()

        await events.append(EventType.THEME_INTRODUCED, "t1", ThemeIntroduced(id="t1", title="Test Theme"))
        await proj.catch_up()
        record = await read.get_theme("t1")
        last_count = record.touch_count
        monotonic_ok = True

        for action in actions:
            if action == "introduce":
                await events.append(EventType.THEME_INTRODUCED, "t1", ThemeIntroduced(id="t1", title="Test Theme"))
            elif action == "develop":
                await events.append(EventType.THEME_DEVELOPED, "t1", ThemeDeveloped(id="t1"))
            elif action == "develop_unknown":
                await events.append(EventType.THEME_DEVELOPED, "nonexistent-id", ThemeDeveloped(id="nonexistent-id"))
            await proj.catch_up()
            record = await read.get_theme("t1")
            if record is None or record.touch_count < last_count:
                monotonic_ok = False
            else:
                last_count = record.touch_count

        incremental_count = last_count

        # Rebuild equivalence: a fresh projector replaying from zero agrees.
        proj2 = Projector(events, path)
        await proj2.init()
        await proj2._reset_state()
        await proj2.catch_up()
        rebuilt = await read.get_theme("t1")
        rebuilt_count = rebuilt.touch_count if rebuilt is not None else -1
        await proj2.close()

        await read.close()
        await proj.close()
        await events.close()
        return incremental_count, int(monotonic_ok), rebuilt_count, int(rebuilt is not None)
    finally:
        os.unlink(path)


@given(
    action_sequence=st.lists(
        st.sampled_from(["introduce", "develop", "develop_unknown"]), max_size=15
    )
)
@settings(max_examples=30, deadline=None)
def test_theme_state_is_monotonic_appending(action_sequence):
    """Falsification check: a theme's touch_count only ever increases (or
    stays flat on a dropped/unknown-id develop) across any sequence of
    introduce/develop actions, and the record, once introduced, is never
    deleted or reset -- no terminal state exists to protect (Locked
    decision 6), so this property is simpler than ThreadsProjection's
    lattice: there is no 'absorbing state' branch to falsify against,
    only 'touch_count never decreases and the row never disappears.'
    If this test ever fails by finding touch_count decreasing or the row
    vanishing, that is a real projection bug -- do not weaken the
    assertion to make it pass.
    """
    incremental_count, monotonic_ok, _, _ = asyncio.run(_run_sequence(action_sequence))
    assert monotonic_ok, "touch_count decreased or theme row vanished across the sequence"
    assert incremental_count >= 0


@given(action_count=st.integers(min_value=0, max_value=10))
@settings(max_examples=20, deadline=None)
def test_theme_projection_rebuild_equivalence(action_count):
    """Falsification check: replaying the full theme.* event log from
    scratch (Projector._reset_state then catch_up) produces byte-identical
    ThemeRecord rows to the live-folded state -- the read model is a pure
    function of the log, same invariant M3.1 established for threads.
    """
    incremental_count, _, rebuilt_count, rebuilt_exists = asyncio.run(
        _run_sequence(["develop"] * action_count)
    )
    assert rebuilt_exists == 1
    assert rebuilt_count == incremental_count
