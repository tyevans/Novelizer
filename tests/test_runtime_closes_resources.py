"""Runtime.close() must release every resource Runtime.start() acquired.

Runtime constructs the EmbeddingStore in start() (nothing else owns it), so
Runtime is the only thing that can free it. Leaving it out of close() made the
whole suite's `finally: await rt.close()` discipline a no-op for that one
resource: every test looked like it cleaned up and none of them did.

The test is written against the symmetry rule rather than against Chroma
internals -- anything start() acquires and close() forgets is the same bug.
"""
from __future__ import annotations

import pytest

from novelizer.runtime import Runtime
from novelizer.settings import EffectiveSettings as Settings
from tests.conftest import FakeEmbeddingFunction


class RecordingEmbeddingStore:
    """Stands in for EmbeddingStore and records whether it was closed."""

    def __init__(self) -> None:
        self.closed = 0

    def close(self) -> None:
        self.closed += 1


@pytest.fixture
def settings(tmp_path):
    return Settings(db_path=str(tmp_path / "world.db"))


async def test_close_closes_the_embedding_store(settings):
    rt = Runtime(settings, runner=None)
    rt._canon_backend, rt._canon_tools = object(), []
    rt.embeddings = RecordingEmbeddingStore()
    await rt.start()
    store = rt.embeddings
    assert isinstance(store, RecordingEmbeddingStore), "start() replaced the injected store"

    await rt.close()

    assert store.closed == 1, (
        "Runtime.close() left the embedding store open -- start() acquired it, "
        "so close() owes its release"
    )


async def test_close_tolerates_a_runtime_that_never_started(settings):
    """close() is called from `finally`, so it must survive a failed start()."""
    rt = Runtime(settings, runner=None)
    rt._canon_backend, rt._canon_tools = object(), []
    await rt.start()
    await rt.close()
    await rt.close()  # must not raise


async def test_repeated_start_close_cycles_do_not_accumulate_threads(tmp_path):
    """The invariant that matters: start/close is net-neutral on OS threads.

    chromadb's Rust core spawns a large tokio worker pool per client (~15-20
    threads). Before Runtime.close() released the client, every started Runtime
    leaked its whole pool: 2 cycles left 54 OS threads, 5 left 111, 10 left 206
    -- unbounded. A full suite run builds ~60 Runtimes, which is how the process
    reached the ~112-thread pile documented as the "chromadb shutdown wedge" in
    docs/TESTING-TUI.md.

    Asserting "constant" rather than an exact count keeps this robust to
    unrelated pools; what must never return is *growth per cycle*.
    """
    import os

    def os_threads() -> int:
        return len(os.listdir("/proc/self/task"))

    async def cycle(index: int) -> None:
        rt = Runtime(Settings(db_path=str(tmp_path / f"run{index}" / "world.db")), runner=None)
        rt._canon_backend, rt._canon_tools = object(), []
        await rt.start()
        await rt.close()

    await cycle(0)  # first cycle warms any one-time pools
    baseline = os_threads()
    for i in range(1, 4):
        await cycle(i)
    assert os_threads() <= baseline, (
        f"threads grew from {baseline} to {os_threads()} over 3 start/close cycles -- "
        "a per-Runtime resource is being leaked"
    )


async def test_start_then_close_leaves_no_live_chroma_system(settings):
    """End to end, with the real EmbeddingStore: no leaked Chroma system."""
    from chromadb.api.shared_system_client import SharedSystemClient

    rt = Runtime(settings, runner=None)
    rt._canon_backend, rt._canon_tools = object(), []
    await rt.start()
    path = rt.embeddings._client.get_settings().persist_directory
    assert path in SharedSystemClient._identifier_to_system

    await rt.close()

    assert path not in SharedSystemClient._identifier_to_system, (
        "a started-and-closed Runtime leaked its Chroma system; enough of these "
        "in one process block a later client inside Chroma's create_collection"
    )
