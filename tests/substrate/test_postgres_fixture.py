import asyncpg
import pytest

from tests.substrate.postgres_fixture import _ADMIN_DB, _PASSWORD, _USER, fresh_database, postgres_dsn


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


def _admin_dsn(pg_container) -> str:
    _name, port = pg_container
    return f"postgresql://{_USER}:{_PASSWORD}@localhost:{port}/{_ADMIN_DB}"


@pytest.mark.asyncio
async def test_consecutive_databases_are_distinct_and_empty(pg_container):
    """Per-database isolation is equivalent to the old per-container isolation:
    a table created in one throwaway database is invisible to the next one."""
    with fresh_database(pg_container) as dsn_a:
        conn_a = await asyncpg.connect(dsn_a)
        try:
            await conn_a.execute("CREATE TABLE isolation_probe (id int)")
        finally:
            await conn_a.close()

        with fresh_database(pg_container) as dsn_b:
            assert dsn_a != dsn_b
            assert dsn_a.rsplit("/", 1)[-1] != dsn_b.rsplit("/", 1)[-1]
            conn_b = await asyncpg.connect(dsn_b)
            try:
                row = await conn_b.fetchrow(
                    "SELECT 1 FROM information_schema.tables"
                    " WHERE table_schema = 'public' AND table_name = 'isolation_probe'",
                )
                assert row is None, "second database saw a table created in the first"
            finally:
                await conn_b.close()


@pytest.mark.asyncio
async def test_teardown_drops_database_despite_leaked_connection(pg_container):
    """DROP ... WITH (FORCE) must kick connections an un-closed pool left behind.

    Without FORCE, postgres refuses the drop ('database is being accessed by
    other users') and the teardown — which ignores psql's exit code — would
    silently leak the database. So the assertion is on the outcome: after
    teardown runs with a connection still open, the database must be gone.
    """
    leaked = None
    try:
        with fresh_database(pg_container) as dsn:
            db = dsn.rsplit("/", 1)[-1]
            leaked = await asyncpg.connect(dsn)
            # Exiting the with-block runs the real teardown while `leaked`
            # is still connected to the throwaway database.

        admin = await asyncpg.connect(_admin_dsn(pg_container))
        try:
            row = await admin.fetchrow(
                "SELECT 1 FROM pg_database WHERE datname = $1", db,
            )
            assert row is None, f"database {db} survived teardown despite FORCE"
        finally:
            await admin.close()
    finally:
        if leaked is not None:
            # Server side was already terminated by FORCE; abort locally so
            # no unclosed-transport warning escapes (suite runs -W error).
            leaked.terminate()


# --- session scoping of pg_container -----------------------------------------
# These two tests observe the container identity from separate test functions.
# They run in definition order within this module; if pg_container ever
# regresses to function scope, each test would see a different (name, port)
# and the second test fails — instead of silently restoring ~4s per test.

_container_observations: list[tuple[str, int]] = []


def test_pg_container_first_observation(pg_container):
    _container_observations.append(pg_container)


def test_pg_container_is_reused_across_tests(pg_container):
    _container_observations.append(pg_container)
    if len(_container_observations) < 2:
        pytest.skip("needs test_pg_container_first_observation to have run first")
    first, second = _container_observations[-2:]
    assert first == second, (
        f"pg_container not session-scoped: {first} != {second}"
    )
