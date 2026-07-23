from __future__ import annotations
import shutil
import socket
import subprocess
import time
import uuid
from contextlib import contextmanager

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


_USER, _PASSWORD, _ADMIN_DB = "substrate", "substrate", "substrate"


def _psql(name: str, sql: str, *, db: str = _ADMIN_DB) -> subprocess.CompletedProcess:
    # -h 127.0.0.1 forces TCP: during image init a temporary postgres serves the
    # unix socket only, so a TCP `select 1` proves the *real* server is up in a
    # way `pg_isready` (socket-based) does not.
    return subprocess.run(
        [
            "docker", "exec", name,
            "psql", "-h", "127.0.0.1", "-U", _USER, "-d", db,
            "-v", "ON_ERROR_STOP=1", "-c", sql,
        ],
        capture_output=True, timeout=15,
    )


@pytest.fixture(scope="session")
def pg_container():
    """One pgvector container for the whole test session.

    Spinning a container per test cost ~4s setup + up to 10s `docker stop`
    teardown, times ~25 postgres tests (~2 min of a full run). Tests get
    isolation from a throwaway CREATE DATABASE each (see postgres_dsn), which
    is ~100ms. Torn down with `docker kill`: the data is disposable and --rm
    removes the container on kill.
    """
    if not _docker_available():
        pytest.skip("docker not available in this environment")

    port = _free_port()
    name = f"substrate-pg-test-{uuid.uuid4().hex[:8]}"

    subprocess.run(
        [
            "docker", "run", "-d", "--rm", "--name", name,
            "-e", f"POSTGRES_USER={_USER}",
            "-e", f"POSTGRES_PASSWORD={_PASSWORD}",
            "-e", f"POSTGRES_DB={_ADMIN_DB}",
            "-p", f"{port}:5432",
            "pgvector/pgvector:pg16",
        ],
        capture_output=True, check=True, timeout=30,
    )
    try:
        deadline = time.monotonic() + 30
        last_err = None
        while time.monotonic() < deadline:
            result = _psql(name, "SELECT 1")
            if result.returncode == 0:
                break
            last_err = result.stdout + result.stderr
            time.sleep(0.5)
        else:
            raise RuntimeError(f"postgres container never became ready: {last_err}")
        yield name, port
    finally:
        subprocess.run(["docker", "kill", name], capture_output=True, timeout=15)


@contextmanager
def fresh_database(container):
    """CREATE a throwaway database in the session container; DROP it on exit.

    This is the whole per-test isolation lifecycle, extracted from the
    postgres_dsn fixture so tests can drive the exact create/teardown code
    path directly (e.g. two consecutive databases, or teardown with a leaked
    connection still open).
    """
    name, port = container
    db = f"t_{uuid.uuid4().hex[:12]}"

    result = _psql(name, f'CREATE DATABASE "{db}"')
    if result.returncode != 0:
        raise RuntimeError(f"CREATE DATABASE failed: {result.stdout + result.stderr}")
    try:
        yield f"postgresql://{_USER}:{_PASSWORD}@localhost:{port}/{db}"
    finally:
        # FORCE: kicks any connections a test's un-closed pool left behind.
        _psql(name, f'DROP DATABASE IF EXISTS "{db}" WITH (FORCE)')


@pytest.fixture
def postgres_dsn(pg_container):
    """A DSN pointing at a fresh, empty database inside the session container."""
    with fresh_database(pg_container) as dsn:
        yield dsn
