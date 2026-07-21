# M2 — Postgres(+pgvector) Backend Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a Postgres storage adapter to `substrate/` — an append-only `events` table, an `embeddings` table, and a `derived_deps` edge table for blast-radius queries — as an alternative to the existing SQLite adapter, validated under concurrent writes against a real Postgres instance.

**Architecture:** No system-level Postgres install is available in this environment (confirmed: no running server, no passwordless sudo to install one) and no remote database service is reachable. Docker is available, and the `pgvector/pgvector:pg16` image is already pulled locally, so the isolated test environment the spec requires ("never the shared/main checkout, per the standing DB-lock-incident rule") is a locally-run, ephemeral Docker container — started and torn down by the test suite itself, never a shared or persistent database. `asyncpg` (0.30.0) is already an installed dependency; no new dependency is added for the driver. Everything here lives in `substrate/`, which per M1 still imports nothing from `novelizer.*` — this milestone does not touch fiction code at all, it only adds a second backend option next to the existing SQLite path.

**Tech Stack:** Python 3, `asyncpg` (already installed), `pgvector/pgvector:pg16` Docker image (already pulled), pytest with a session-scoped container-lifecycle fixture.

## Global Constraints

- `substrate/` continues to import nothing from `novelizer.*` (same constraint as M1, re-verified in Task 6).
- The existing SQLite adapter (`novelizer/canon/event_store.py` and friends) is not modified or removed — this milestone is additive only, per the spec's M2 text verbatim: "Offered as an alternative to the existing SQLite adapter — SQLite is not removed."
- No Redis — per the spec's explicit M2 scope ("No Redis at this stage.").
- Every Postgres-backed test must skip cleanly (not error) if Docker is unavailable in the environment running the suite, so this milestone's tests never break the existing `pytest -v` full-suite run in an environment without Docker. Use a single shared pytest fixture for this, defined once in Task 1, reused by every later task's tests.
- The container used for tests is ephemeral per test session (started fresh, torn down after) — never a long-lived shared database, per the DB-lock-incident rule already governing this project (see memory: milestone-execution-state — "NEVER run test suites in the main checkout").

---

### Task 1: Docker-backed Postgres test fixture

**Files:**
- Create: `tests/substrate/postgres_fixture.py`
- Test: `tests/substrate/test_postgres_fixture.py`

**Interfaces:**
- Consumes: nothing from earlier milestones.
- Produces: a pytest fixture `postgres_dsn` (function-scoped, defined in `tests/substrate/postgres_fixture.py`, imported via `pytest_plugins` or direct import in each later test module — use direct import: `from tests.substrate.postgres_fixture import postgres_dsn`) that: (a) skips the test with `pytest.skip(...)` if `docker` is not on PATH or the daemon is unreachable, (b) otherwise starts a `pgvector/pgvector:pg16` container on a random free host port with a throwaway database/user/password, waits for it to accept connections, yields a DSN string (`postgresql://user:pass@localhost:PORT/dbname`), and stops+removes the container on teardown regardless of test outcome.

- [ ] **Step 1: Write the failing test**

```python
# tests/substrate/test_postgres_fixture.py
import asyncpg
import pytest

from tests.substrate.postgres_fixture import postgres_dsn


@pytest.mark.asyncio
async def test_postgres_dsn_is_a_live_connectable_postgres(postgres_dsn):
    conn = await asyncpg.connect(postgres_dsn)
    try:
        version = await conn.fetchval("SELECT version()")
        assert "PostgreSQL" in version
    finally:
        await conn.close()


@pytest.mark.asyncio
async def test_pgvector_extension_is_available(postgres_dsn):
    conn = await asyncpg.connect(postgres_dsn)
    try:
        await conn.execute("CREATE EXTENSION IF NOT EXISTS vector")
        row = await conn.fetchrow("SELECT extname FROM pg_extension WHERE extname = 'vector'")
        assert row is not None
    finally:
        await conn.close()
```

- [ ] **Step 2: Run to verify it fails**

Run: `.venv/bin/pytest tests/substrate/test_postgres_fixture.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'tests.substrate.postgres_fixture'`

- [ ] **Step 3: Write the fixture**

```python
# tests/substrate/postgres_fixture.py
from __future__ import annotations
import shutil
import socket
import subprocess
import time
import uuid

import pytest


def _docker_available() -> bool:
    if shutil.which("docker") is None:
        return False
    try:
        subprocess.run(
            ["docker", "info"], capture_output=True, timeout=5, check=True,
        )
        return True
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired, OSError):
        return False


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("", 0))
        return s.getsockname()[1]


@pytest.fixture
def postgres_dsn():
    if not _docker_available():
        pytest.skip("docker not available in this environment")

    port = _free_port()
    name = f"substrate-pg-test-{uuid.uuid4().hex[:8]}"
    user, password, db = "substrate", "substrate", "substrate"

    subprocess.run(
        [
            "docker", "run", "-d", "--rm", "--name", name,
            "-e", f"POSTGRES_USER={user}",
            "-e", f"POSTGRES_PASSWORD={password}",
            "-e", f"POSTGRES_DB={db}",
            "-p", f"{port}:5432",
            "pgvector/pgvector:pg16",
        ],
        capture_output=True, check=True, timeout=30,
    )
    dsn = f"postgresql://{user}:{password}@localhost:{port}/{db}"
    try:
        deadline = time.monotonic() + 30
        last_err = None
        while time.monotonic() < deadline:
            result = subprocess.run(
                ["docker", "exec", name, "pg_isready", "-U", user],
                capture_output=True, timeout=5,
            )
            if result.returncode == 0:
                break
            last_err = result.stdout + result.stderr
            time.sleep(0.5)
        else:
            raise RuntimeError(f"postgres container never became ready: {last_err}")
        yield dsn
    finally:
        subprocess.run(["docker", "stop", name], capture_output=True, timeout=15)
```

- [ ] **Step 4: Run to verify it passes**

Run: `.venv/bin/pytest tests/substrate/test_postgres_fixture.py -v`
Expected: PASS (2 passed) if Docker is available, or SKIPPED (2 skipped) if
not — either outcome is acceptable, but in this environment (Docker
confirmed present, `pgvector/pgvector:pg16` already pulled) it must PASS.

- [ ] **Step 5: Commit**

```bash
git add tests/substrate/postgres_fixture.py tests/substrate/test_postgres_fixture.py
git commit -m "test(substrate): add Docker-backed Postgres+pgvector test fixture"
```

---

### Task 2: `substrate.postgres.events` — append-only events table

**Files:**
- Create: `substrate/postgres/__init__.py` (empty)
- Create: `substrate/postgres/events.py`
- Test: `tests/substrate/test_postgres_events.py`

**Interfaces:**
- Consumes: `postgres_dsn` fixture from Task 1.
- Produces: `substrate.postgres.events.PostgresEventStore` — async class with `__init__(self, dsn: str)`, `async def connect(self) -> None` (creates the schema if absent, per the SQL in Step 3), `async def close(self) -> None`, `async def append(self, stream_id: str, event_type: str, payload: dict, parent_ids: list[str] | None = None, actor: str = "") -> int` (returns the assigned `seq`), `async def read_stream(self, stream_id: str) -> list[dict]` (returns rows as dicts, ordered by `seq` ascending, each with keys `seq, stream_id, event_type, payload, parent_ids, actor, created_at`).

- [ ] **Step 1: Write the failing tests**

```python
# tests/substrate/test_postgres_events.py
import pytest

from substrate.postgres.events import PostgresEventStore
from tests.substrate.postgres_fixture import postgres_dsn


@pytest.mark.asyncio
async def test_append_assigns_monotonic_seq_within_and_across_streams(postgres_dsn):
    store = PostgresEventStore(postgres_dsn)
    await store.connect()
    try:
        seq1 = await store.append("stream-a", "thing.created", {"n": 1})
        seq2 = await store.append("stream-a", "thing.created", {"n": 2})
        seq3 = await store.append("stream-b", "thing.created", {"n": 3})
        assert seq1 < seq2 < seq3
    finally:
        await store.close()


@pytest.mark.asyncio
async def test_read_stream_returns_only_that_streams_events_in_order(postgres_dsn):
    store = PostgresEventStore(postgres_dsn)
    await store.connect()
    try:
        await store.append("stream-x", "a.created", {"v": 1})
        await store.append("stream-y", "a.created", {"v": 99})
        await store.append("stream-x", "a.updated", {"v": 2})
        rows = await store.read_stream("stream-x")
        assert [r["event_type"] for r in rows] == ["a.created", "a.updated"]
        assert [r["payload"] for r in rows] == [{"v": 1}, {"v": 2}]
        assert rows[0]["seq"] < rows[1]["seq"]
    finally:
        await store.close()


@pytest.mark.asyncio
async def test_parent_ids_and_actor_round_trip(postgres_dsn):
    store = PostgresEventStore(postgres_dsn)
    await store.connect()
    try:
        parent = str(__import__("uuid").uuid4())
        await store.append(
            "stream-z", "b.created", {"v": 1}, parent_ids=[parent], actor="scout",
        )
        rows = await store.read_stream("stream-z")
        assert rows[0]["parent_ids"] == [parent]
        assert rows[0]["actor"] == "scout"
    finally:
        await store.close()


@pytest.mark.asyncio
async def test_events_table_rejects_update_and_delete(postgres_dsn):
    store = PostgresEventStore(postgres_dsn)
    await store.connect()
    try:
        await store.append("stream-w", "c.created", {"v": 1})
        with pytest.raises(Exception):
            await store._conn.execute("UPDATE substrate_events SET payload = '{}' WHERE seq = 1")
        with pytest.raises(Exception):
            await store._conn.execute("DELETE FROM substrate_events WHERE seq = 1")
    finally:
        await store.close()


@pytest.mark.asyncio
async def test_concurrent_appends_to_same_stream_preserve_total_order(postgres_dsn):
    import asyncio

    store = PostgresEventStore(postgres_dsn)
    await store.connect()
    try:
        async def _append_many(n_start):
            s = PostgresEventStore(postgres_dsn)
            await s.connect()
            try:
                for i in range(5):
                    await s.append("stream-concurrent", "x.created", {"writer": n_start, "i": i})
            finally:
                await s.close()

        await asyncio.gather(*[_append_many(w) for w in range(4)])
        rows = await store.read_stream("stream-concurrent")
        assert len(rows) == 20
        seqs = [r["seq"] for r in rows]
        assert seqs == sorted(seqs)
        assert len(set(seqs)) == 20  # no lost or duplicate writes
    finally:
        await store.close()
```

- [ ] **Step 2: Run to verify they fail**

Run: `.venv/bin/pytest tests/substrate/test_postgres_events.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'substrate.postgres.events'`

- [ ] **Step 3: Write the implementation**

```python
# substrate/postgres/__init__.py
```

```python
# substrate/postgres/events.py
from __future__ import annotations
import json
from typing import Any, Optional

import asyncpg

_SCHEMA = """
CREATE TABLE IF NOT EXISTS substrate_events (
    seq BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    stream_id TEXT NOT NULL,
    event_type TEXT NOT NULL,
    payload JSONB NOT NULL,
    parent_ids UUID[] NOT NULL DEFAULT '{}',
    actor TEXT NOT NULL DEFAULT '',
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS substrate_events_stream_id_seq_idx
    ON substrate_events (stream_id, seq);

CREATE OR REPLACE FUNCTION substrate_events_append_only() RETURNS trigger AS $$
BEGIN
    RAISE EXCEPTION 'substrate_events is append-only: % not permitted', TG_OP;
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS substrate_events_no_update ON substrate_events;
CREATE TRIGGER substrate_events_no_update
    BEFORE UPDATE ON substrate_events
    FOR EACH ROW EXECUTE FUNCTION substrate_events_append_only();

DROP TRIGGER IF EXISTS substrate_events_no_delete ON substrate_events;
CREATE TRIGGER substrate_events_no_delete
    BEFORE DELETE ON substrate_events
    FOR EACH ROW EXECUTE FUNCTION substrate_events_append_only();
"""


class PostgresEventStore:
    def __init__(self, dsn: str) -> None:
        self._dsn = dsn
        self._conn: Optional[asyncpg.Connection] = None

    async def connect(self) -> None:
        self._conn = await asyncpg.connect(self._dsn)
        await self._conn.execute(_SCHEMA)

    async def close(self) -> None:
        if self._conn is not None:
            await self._conn.close()

    async def append(
        self,
        stream_id: str,
        event_type: str,
        payload: dict,
        parent_ids: list[str] | None = None,
        actor: str = "",
    ) -> int:
        row = await self._conn.fetchrow(
            "INSERT INTO substrate_events (stream_id, event_type, payload, parent_ids, actor) "
            "VALUES ($1, $2, $3::jsonb, $4::uuid[], $5) RETURNING seq",
            stream_id, event_type, json.dumps(payload), parent_ids or [], actor,
        )
        return row["seq"]

    async def read_stream(self, stream_id: str) -> list[dict]:
        rows = await self._conn.fetch(
            "SELECT seq, stream_id, event_type, payload, parent_ids, actor, created_at "
            "FROM substrate_events WHERE stream_id = $1 ORDER BY seq ASC",
            stream_id,
        )
        return [
            {
                "seq": r["seq"],
                "stream_id": r["stream_id"],
                "event_type": r["event_type"],
                "payload": json.loads(r["payload"]),
                "parent_ids": list(r["parent_ids"]),
                "actor": r["actor"],
                "created_at": r["created_at"],
            }
            for r in rows
        ]
```

Note: each concurrent writer in the test opens its own connection (`asyncpg`
connections are not safe for concurrent use from multiple coroutines on one
connection) — this is why the concurrency test constructs a fresh
`PostgresEventStore` per simulated writer rather than sharing `store`.

- [ ] **Step 4: Run to verify they pass**

Run: `.venv/bin/pytest tests/substrate/test_postgres_events.py -v`
Expected: PASS (5 passed)

- [ ] **Step 5: Commit**

```bash
git add substrate/postgres/__init__.py substrate/postgres/events.py tests/substrate/test_postgres_events.py
git commit -m "feat(substrate): add PostgresEventStore, append-only events table with concurrency test"
```

---

### Task 3: `substrate.postgres.embeddings` — pgvector-backed embeddings table

**Files:**
- Create: `substrate/postgres/embeddings.py`
- Test: `tests/substrate/test_postgres_embeddings.py`

**Interfaces:**
- Consumes: `postgres_dsn` fixture from Task 1. Independent of Task 2's `PostgresEventStore` (separate table, separate class — per the spec, embeddings are keyed by `(target_kind, target_id, model)`, not a foreign key to `substrate_events`).
- Produces: `substrate.postgres.embeddings.PostgresEmbeddingStore` — `__init__(self, dsn: str, dimensions: int)`, `async def connect(self) -> None` (creates the `vector` extension and table with an HNSW index sized to `dimensions`), `async def close(self) -> None`, `async def upsert(self, target_kind: str, target_id: str, model: str, vector: list[float]) -> None`, `async def nearest(self, model: str, query_vector: list[float], limit: int = 5) -> list[dict]` (returns `[{"target_kind", "target_id", "distance"}]` ordered nearest-first, filtered to the given `model`).

- [ ] **Step 1: Write the failing tests**

```python
# tests/substrate/test_postgres_embeddings.py
import pytest

from substrate.postgres.embeddings import PostgresEmbeddingStore
from tests.substrate.postgres_fixture import postgres_dsn


@pytest.mark.asyncio
async def test_upsert_then_nearest_finds_closest_vector(postgres_dsn):
    store = PostgresEmbeddingStore(postgres_dsn, dimensions=3)
    await store.connect()
    try:
        await store.upsert("chapter", "ch-1", "model-a", [1.0, 0.0, 0.0])
        await store.upsert("chapter", "ch-2", "model-a", [0.0, 1.0, 0.0])
        results = await store.nearest("model-a", [0.9, 0.1, 0.0], limit=1)
        assert results[0]["target_kind"] == "chapter"
        assert results[0]["target_id"] == "ch-1"
    finally:
        await store.close()


@pytest.mark.asyncio
async def test_upsert_same_target_and_model_replaces_not_duplicates(postgres_dsn):
    store = PostgresEmbeddingStore(postgres_dsn, dimensions=3)
    await store.connect()
    try:
        await store.upsert("chapter", "ch-1", "model-a", [1.0, 0.0, 0.0])
        await store.upsert("chapter", "ch-1", "model-a", [0.0, 0.0, 1.0])
        results = await store.nearest("model-a", [0.0, 0.0, 0.9], limit=5)
        matches = [r for r in results if r["target_id"] == "ch-1"]
        assert len(matches) == 1


@pytest.mark.asyncio
async def test_nearest_filters_by_model(postgres_dsn):
    store = PostgresEmbeddingStore(postgres_dsn, dimensions=3)
    await store.connect()
    try:
        await store.upsert("chapter", "ch-1", "model-a", [1.0, 0.0, 0.0])
        await store.upsert("chapter", "ch-2", "model-b", [1.0, 0.0, 0.0])
        results = await store.nearest("model-b", [1.0, 0.0, 0.0], limit=5)
        assert [r["target_id"] for r in results] == ["ch-2"]
    finally:
        await store.close()
```

- [ ] **Step 2: Run to verify they fail**

Run: `.venv/bin/pytest tests/substrate/test_postgres_embeddings.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'substrate.postgres.embeddings'`

- [ ] **Step 3: Write the implementation**

```python
# substrate/postgres/embeddings.py
from __future__ import annotations
from typing import Optional

import asyncpg


class PostgresEmbeddingStore:
    def __init__(self, dsn: str, dimensions: int) -> None:
        self._dsn = dsn
        self._dimensions = dimensions
        self._conn: Optional[asyncpg.Connection] = None

    async def connect(self) -> None:
        self._conn = await asyncpg.connect(self._dsn)
        await self._conn.execute("CREATE EXTENSION IF NOT EXISTS vector")
        await self._conn.execute(
            f"""
            CREATE TABLE IF NOT EXISTS substrate_embeddings (
                target_kind TEXT NOT NULL,
                target_id TEXT NOT NULL,
                model TEXT NOT NULL,
                embedding VECTOR({self._dimensions}) NOT NULL,
                PRIMARY KEY (target_kind, target_id, model)
            )
            """
        )
        await self._conn.execute(
            "CREATE INDEX IF NOT EXISTS substrate_embeddings_hnsw_idx "
            "ON substrate_embeddings USING hnsw (embedding vector_l2_ops)"
        )

    async def close(self) -> None:
        if self._conn is not None:
            await self._conn.close()

    async def upsert(self, target_kind: str, target_id: str, model: str, vector: list[float]) -> None:
        vector_literal = "[" + ",".join(str(v) for v in vector) + "]"
        await self._conn.execute(
            "INSERT INTO substrate_embeddings (target_kind, target_id, model, embedding) "
            "VALUES ($1, $2, $3, $4::vector) "
            "ON CONFLICT (target_kind, target_id, model) DO UPDATE SET embedding = EXCLUDED.embedding",
            target_kind, target_id, model, vector_literal,
        )

    async def nearest(self, model: str, query_vector: list[float], limit: int = 5) -> list[dict]:
        vector_literal = "[" + ",".join(str(v) for v in query_vector) + "]"
        rows = await self._conn.fetch(
            "SELECT target_kind, target_id, embedding <-> $1::vector AS distance "
            "FROM substrate_embeddings WHERE model = $2 "
            "ORDER BY embedding <-> $1::vector ASC LIMIT $3",
            vector_literal, model, limit,
        )
        return [
            {"target_kind": r["target_kind"], "target_id": r["target_id"], "distance": r["distance"]}
            for r in rows
        ]
```

- [ ] **Step 4: Run to verify they pass**

Run: `.venv/bin/pytest tests/substrate/test_postgres_embeddings.py -v`
Expected: PASS (3 passed)

- [ ] **Step 5: Commit**

```bash
git add substrate/postgres/embeddings.py tests/substrate/test_postgres_embeddings.py
git commit -m "feat(substrate): add pgvector-backed PostgresEmbeddingStore"
```

---

### Task 4: `substrate.postgres.deps` — derived_deps edge table and blast-radius query

**Files:**
- Create: `substrate/postgres/deps.py`
- Test: `tests/substrate/test_postgres_deps.py`

**Interfaces:**
- Consumes: `postgres_dsn` fixture from Task 1. Independent of Tasks 2-3.
- Produces: `substrate.postgres.deps.PostgresDepsStore` — `__init__(self, dsn: str)`, `async def connect(self) -> None`, `async def close(self) -> None`, `async def declare_edge(self, parent: str, child: str) -> None` (idempotent — declaring the same edge twice is a no-op, not an error), `async def blast_radius(self, node: str) -> list[str]` (returns every node transitively reachable as a descendant of `node` via the recursive edge table, not including `node` itself, deduplicated).

- [ ] **Step 1: Write the failing tests**

```python
# tests/substrate/test_postgres_deps.py
import pytest

from substrate.postgres.deps import PostgresDepsStore
from tests.substrate.postgres_fixture import postgres_dsn


@pytest.mark.asyncio
async def test_blast_radius_of_leaf_is_empty(postgres_dsn):
    store = PostgresDepsStore(postgres_dsn)
    await store.connect()
    try:
        await store.declare_edge("a", "b")
        assert await store.blast_radius("b") == []
    finally:
        await store.close()


@pytest.mark.asyncio
async def test_blast_radius_follows_multi_hop_chain(postgres_dsn):
    store = PostgresDepsStore(postgres_dsn)
    await store.connect()
    try:
        await store.declare_edge("a", "b")
        await store.declare_edge("b", "c")
        await store.declare_edge("c", "d")
        assert set(await store.blast_radius("a")) == {"b", "c", "d"}
    finally:
        await store.close()


@pytest.mark.asyncio
async def test_blast_radius_dedupes_diamond_dependency(postgres_dsn):
    store = PostgresDepsStore(postgres_dsn)
    await store.connect()
    try:
        await store.declare_edge("a", "b")
        await store.declare_edge("a", "c")
        await store.declare_edge("b", "d")
        await store.declare_edge("c", "d")
        result = await store.blast_radius("a")
        assert sorted(result) == ["b", "c", "d"]
        assert len(result) == 3  # "d" reachable via two paths, counted once
    finally:
        await store.close()


@pytest.mark.asyncio
async def test_declare_edge_is_idempotent(postgres_dsn):
    store = PostgresDepsStore(postgres_dsn)
    await store.connect()
    try:
        await store.declare_edge("a", "b")
        await store.declare_edge("a", "b")
        assert await store.blast_radius("a") == ["b"]
    finally:
        await store.close()
```

- [ ] **Step 2: Run to verify they fail**

Run: `.venv/bin/pytest tests/substrate/test_postgres_deps.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'substrate.postgres.deps'`

- [ ] **Step 3: Write the implementation**

```python
# substrate/postgres/deps.py
from __future__ import annotations
from typing import Optional

import asyncpg

_SCHEMA = """
CREATE TABLE IF NOT EXISTS substrate_derived_deps (
    parent TEXT NOT NULL,
    child TEXT NOT NULL,
    PRIMARY KEY (parent, child)
);
"""

_BLAST_RADIUS_QUERY = """
WITH RECURSIVE descendants AS (
    SELECT child FROM substrate_derived_deps WHERE parent = $1
    UNION
    SELECT d.child
    FROM substrate_derived_deps d
    JOIN descendants desc ON d.parent = desc.child
)
SELECT child FROM descendants
"""


class PostgresDepsStore:
    def __init__(self, dsn: str) -> None:
        self._dsn = dsn
        self._conn: Optional[asyncpg.Connection] = None

    async def connect(self) -> None:
        self._conn = await asyncpg.connect(self._dsn)
        await self._conn.execute(_SCHEMA)

    async def close(self) -> None:
        if self._conn is not None:
            await self._conn.close()

    async def declare_edge(self, parent: str, child: str) -> None:
        await self._conn.execute(
            "INSERT INTO substrate_derived_deps (parent, child) VALUES ($1, $2) "
            "ON CONFLICT (parent, child) DO NOTHING",
            parent, child,
        )

    async def blast_radius(self, node: str) -> list[str]:
        rows = await self._conn.fetch(_BLAST_RADIUS_QUERY, node)
        return [r["child"] for r in rows]
```

- [ ] **Step 4: Run to verify they pass**

Run: `.venv/bin/pytest tests/substrate/test_postgres_deps.py -v`
Expected: PASS (4 passed)

- [ ] **Step 5: Commit**

```bash
git add substrate/postgres/deps.py tests/substrate/test_postgres_deps.py
git commit -m "feat(substrate): add derived_deps edge table with recursive blast-radius query"
```

---

### Task 5: Concurrent multi-agent write validation across all three tables

**Files:**
- Test: `tests/substrate/test_postgres_concurrency.py`

**Interfaces:**
- Consumes: `PostgresEventStore` (Task 2), `PostgresEmbeddingStore` (Task 3), `PostgresDepsStore` (Task 4), `postgres_dsn` fixture (Task 1).
- Produces: nothing new — this is the milestone's specific proof-of-success test, beyond Task 2's own narrower concurrency test, exercising all three stores together the way a real multi-agent deployment would (several "agents" writing events, embeddings, and dependency edges concurrently against one database).

- [ ] **Step 1: Write the test**

```python
# tests/substrate/test_postgres_concurrency.py
import asyncio
import pytest

from substrate.postgres.events import PostgresEventStore
from substrate.postgres.embeddings import PostgresEmbeddingStore
from substrate.postgres.deps import PostgresDepsStore
from tests.substrate.postgres_fixture import postgres_dsn


@pytest.mark.asyncio
async def test_concurrent_agents_write_events_embeddings_and_deps_without_loss(postgres_dsn):
    async def _agent(agent_id: int):
        events = PostgresEventStore(postgres_dsn)
        embeddings = PostgresEmbeddingStore(postgres_dsn, dimensions=2)
        deps = PostgresDepsStore(postgres_dsn)
        await events.connect()
        await embeddings.connect()
        await deps.connect()
        try:
            for i in range(10):
                target = f"agent-{agent_id}-item-{i}"
                await events.append("shared-stream", "claim.proposed", {"agent": agent_id, "i": i})
                await embeddings.upsert("claim", target, "model-a", [float(agent_id), float(i)])
                if i > 0:
                    await deps.declare_edge(f"agent-{agent_id}-item-{i - 1}", target)
        finally:
            await events.close()
            await embeddings.close()
            await deps.close()

    await asyncio.gather(*[_agent(a) for a in range(6)])

    verify_events = PostgresEventStore(postgres_dsn)
    await verify_events.connect()
    try:
        rows = await verify_events.read_stream("shared-stream")
        assert len(rows) == 60
        seqs = [r["seq"] for r in rows]
        assert len(set(seqs)) == 60
    finally:
        await verify_events.close()

    verify_deps = PostgresDepsStore(postgres_dsn)
    await verify_deps.connect()
    try:
        chain = await verify_deps.blast_radius("agent-0-item-0")
        assert set(chain) == {f"agent-0-item-{i}" for i in range(1, 10)}
    finally:
        await verify_deps.close()
```

- [ ] **Step 2: Run to verify it fails first for the right reason**

Run: `.venv/bin/pytest tests/substrate/test_postgres_concurrency.py -v`
Expected: at this point Tasks 2-4 are already implemented, so this should
already PASS on first run if the schema/logic from those tasks is correct
— there is no new production code in this task. If it fails, that means
Tasks 2-4's implementations have a real concurrency defect; fix the
relevant task's implementation (not this test) before proceeding.

- [ ] **Step 3: Run to confirm it passes**

Run: `.venv/bin/pytest tests/substrate/test_postgres_concurrency.py -v`
Expected: PASS (1 passed)

- [ ] **Step 4: Commit**

```bash
git add tests/substrate/test_postgres_concurrency.py
git commit -m "test(substrate): validate concurrent multi-agent writes across events/embeddings/deps"
```

---

### Task 6: Full regression, import-boundary check, and M2 completion notes

**Files:**
- Modify: `docs/superpowers/specs/2026-07-20-cross-domain-substrate-design.md` (append an "M2 status" note under the M2 section only)

**Interfaces:**
- Consumes: all of Tasks 1-5.
- Produces: nothing new — verification gate.

- [ ] **Step 1: Run the full existing test suite**

Run: `.venv/bin/pytest tests/ -q`
Expected: same 1953 passed / 5 failed / 7 deselected baseline M1 left behind
(the 5 pre-existing, unrelated failures — see M1's status note in the spec
for their names), plus the new `tests/substrate/test_postgres_*` tests
(15 new tests: 2 + 5 + 3 + 4 + 1) all passing. Zero new failures outside
`tests/substrate/`.

- [ ] **Step 2: Verify the import-boundary constraint**

Run: `grep -rn "^from novelizer\|^import novelizer" substrate/`
Expected: no output.

- [ ] **Step 3: Append the M2 status note**

Locate `### M2 — Postgres(+pgvector) backend` in
`docs/superpowers/specs/2026-07-20-cross-domain-substrate-design.md` and
append after its existing paragraph (find the exact current text first,
since M1's edits may have shifted line numbers — do not assume the section
starts at any particular line):

```markdown

**M2 status (2026-07-21): done.** Added `substrate/postgres/` — `events.py`
(`PostgresEventStore`, append-only `substrate_events` table with an
identity `seq`, a stream/seq index, and BEFORE UPDATE/DELETE triggers that
raise rather than allow mutation), `embeddings.py`
(`PostgresEmbeddingStore`, `substrate_embeddings` table keyed by
`(target_kind, target_id, model)` with an HNSW index), and `deps.py`
(`PostgresDepsStore`, `substrate_derived_deps` edge table with a recursive
CTE for blast-radius queries). No system-level Postgres install was
available in this environment, so validation ran against an ephemeral
`pgvector/pgvector:pg16` Docker container started and torn down per test
session (`tests/substrate/postgres_fixture.py`) — never a shared or
persistent database, consistent with the project's standing DB-lock-
incident rule. Concurrency was validated at two levels: `PostgresEventStore`
alone (20 concurrent appends across 4 simulated writers, zero lost/duplicate
sequence numbers) and all three stores together under 6 simulated
concurrent agents each writing events, embeddings, and dependency edges
(`test_postgres_concurrency.py`). The existing SQLite adapter is untouched;
this is purely additive. No Redis was added, per the spec's explicit scope.
```

- [ ] **Step 4: Commit**

```bash
git add docs/superpowers/specs/2026-07-20-cross-domain-substrate-design.md
git commit -m "docs(m2): record M2 completion status"
```
