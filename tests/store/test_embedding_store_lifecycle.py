"""EmbeddingStore owns a Chroma client, so it must release it.

chromadb's PersistentClient holds a sqlite-backed system, shared per path and
reference-counted. Its own close() docstring calls releasing it "particularly
important for PersistentClient to avoid SQLite file locking issues" -- a client
that is never closed leaks background threads and a file handle, and enough of
them in one process eventually block a later client inside Chroma's native
create_collection. That is a resource-ownership rule, so it belongs in a test:
whoever constructs the client is responsible for freeing it.
"""
from __future__ import annotations

from chromadb.api.shared_system_client import SharedSystemClient

from novelizer.store.embeddings import EmbeddingStore
from tests.conftest import FakeEmbeddingFunction


def _live_systems() -> dict:
    return SharedSystemClient._identifier_to_system


def test_close_releases_the_chroma_system(tmp_path):
    path = str(tmp_path / "embeddings")
    store = EmbeddingStore(path, embedding_function=FakeEmbeddingFunction())
    assert path in _live_systems(), "constructing the store should register a Chroma system"

    store.close()

    assert path not in _live_systems(), (
        "close() left the Chroma system running -- the sqlite handle and its "
        "worker threads are leaked for the life of the process"
    )


def test_close_is_idempotent(tmp_path):
    """Callers close in `finally` blocks; a double close must not raise."""
    path = str(tmp_path / "embeddings")
    store = EmbeddingStore(path, embedding_function=FakeEmbeddingFunction())
    store.close()
    store.close()  # must not raise
    assert path not in _live_systems()


def test_a_fresh_store_works_on_a_closed_store_s_path(tmp_path):
    """Reopening the same path after close() must yield a usable store.

    This is the shape the whole test suite exercises: run after run builds a
    Runtime over its own directory, and a stale system left behind by the
    previous one must not poison the next.
    """
    path = str(tmp_path / "embeddings")
    first = EmbeddingStore(path, embedding_function=FakeEmbeddingFunction())
    first.close()

    second = EmbeddingStore(path, embedding_function=FakeEmbeddingFunction())
    try:
        assert path in _live_systems()
    finally:
        second.close()
