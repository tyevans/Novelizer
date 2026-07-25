from __future__ import annotations
import hashlib

import pytest

from novelizer.canon import db as _db

# Test modules import `postgres_dsn` directly; its session-scoped container
# dependency must be resolvable from every requesting directory (substrate,
# research_domain), so surface it here.
from tests.substrate.postgres_fixture import pg_container  # noqa: F401

# --- SQLite: skip fsync in tests ---------------------------------------------
# db.connect opens WAL databases at the default synchronous=FULL, so every
# event append pays an fsync. Test DBs are throwaway tmpfiles; durability
# across power loss is not a property any test asserts. synchronous=OFF
# turns the suite's thousands of tiny commits from disk-bound to CPU-bound
# (measured: the canon/brain/telemetry scope went 170s -> 32s, and the worst
# single property test 19.7s -> 2.6s, with identical example counts).
_real_connect = _db.connect


async def _nosync_connect(path: str):
    conn = await _real_connect(path)
    await conn.execute("PRAGMA synchronous=OFF")
    return conn


_db.connect = _nosync_connect


# --- chromadb: release each test's Chroma systems -----------------------------
# chromadb's Rust core spawns ~15-20 tokio worker threads per PersistentClient
# and only tears them down when the client is closed. Production closes its one
# client (Runtime.close -> EmbeddingStore.close), but ~20 test sites construct an
# EmbeddingStore directly and let it fall out of scope, which frees no threads:
# a full run accumulated 1536 tokio threads across ~1800 OS threads and then
# stalled mid-suite -- the wedge described in docs/TESTING-TUI.md.
#
# This releases only systems a test actually created (diffed against the set
# alive before it), so a client still referenced by an outer scope is untouched.
# Every Chroma-touching fixture in the suite is function-scoped, so per-test
# release is safe; a module- or session-scoped one would need excluding here.
@pytest.fixture(autouse=True)
def _release_chroma_systems():
    from chromadb.api.shared_system_client import SharedSystemClient

    before = set(SharedSystemClient._identifier_to_system)
    try:
        yield
    finally:
        for identifier in set(SharedSystemClient._identifier_to_system) - before:
            system = SharedSystemClient._identifier_to_system.pop(identifier, None)
            SharedSystemClient._identifier_to_refcount.pop(identifier, None)
            if system is not None:
                try:
                    system.stop()
                except Exception:
                    # A store the test already closed, or a half-built client
                    # from a test asserting a construction failure: the goal is
                    # to free threads, never to fail an otherwise-passing test.
                    pass


class FakeEmbeddingFunction:
    """Deterministic, network-free stand-in for chromadb's OpenAIEmbeddingFunction.

    Hashes character trigrams of each input string into a fixed 16-dim bag-
    of-trigrams count vector (L2-normalized). Similar strings share many
    trigrams and land close together in the vector space -- unlike a naive
    `hash(text) % N` one-hot, which would scatter near-identical strings
    randomly -- so near-duplicate-detection tests are meaningful without any
    ML dependency or live embedding endpoint.
    """

    _DIM = 16

    @staticmethod
    def is_legacy() -> bool:
        # chromadb's modern EmbeddingFunction protocol probes this; without it
        # every collection op emits a DeprecationWarning (suite is zero-warning).
        return False

    @staticmethod
    def get_config() -> dict:
        return {}

    @staticmethod
    def default_space() -> str:
        return "l2"

    @staticmethod
    def supported_spaces() -> list[str]:
        return ["l2", "cosine", "ip"]

    @staticmethod
    def build_from_config(config: dict) -> "FakeEmbeddingFunction":
        return FakeEmbeddingFunction()

    def __call__(self, input: list[str]) -> list[list[float]]:
        return [self._embed(text) for text in input]

    def embed_query(self, input: list[str]) -> list[list[float]]:
        return self(input)

    @staticmethod
    def name() -> str:
        return "fake_trigram_embedding"

    def _embed(self, text: str) -> list[float]:
        vec = [0.0] * self._DIM
        normalized = text.lower()
        trigrams = [normalized[i : i + 3] for i in range(len(normalized) - 2)] or [normalized]
        for tri in trigrams:
            bucket = int(hashlib.md5(tri.encode()).hexdigest(), 16) % self._DIM
            vec[bucket] += 1.0
        norm = sum(v * v for v in vec) ** 0.5
        if norm == 0:
            return vec
        return [v / norm for v in vec]
