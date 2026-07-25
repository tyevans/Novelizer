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
    """A port that was free a moment ago -- necessarily a hint, not a promise.

    The socket must be closed before docker can bind the port, so there is
    always a window in which something else can take it. Serially that window
    never mattered; under xdist every worker builds its own container at once
    (this fixture is session-scoped, and "session" means *per worker*), so two
    workers really can be handed the same number. `_start_container` retries
    rather than trying to close a window that cannot be closed.
    """
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("", 0))
        return s.getsockname()[1]


# Enough attempts to ride out concurrent workers colliding on a port, few
# enough that a genuinely broken docker still fails fast.
_PORT_ATTEMPTS = 5


def _start_container() -> tuple[str, int]:
    """Run the pgvector container, retrying if its port was taken in the race.

    Returns (name, port). Raises the last CalledProcessError if every attempt
    fails, so a real docker problem (no image, no daemon) still surfaces with
    its own message instead of being retried into a generic timeout.
    """
    last_error = None
    for _ in range(_PORT_ATTEMPTS):
        port = _free_port()
        name = f"substrate-pg-test-{uuid.uuid4().hex[:8]}"
        result = subprocess.run(
            [
                "docker", "run", "-d", "--rm", "--name", name,
                "-e", f"POSTGRES_USER={_USER}",
                "-e", f"POSTGRES_PASSWORD={_PASSWORD}",
                "-e", f"POSTGRES_DB={_ADMIN_DB}",
                "-p", f"{port}:5432",
                "pgvector/pgvector:pg16",
            ],
            capture_output=True, timeout=30,
        )
        if result.returncode == 0:
            return name, port
        last_error = (result.stdout + result.stderr).decode(errors="replace")
        # Only a port clash is worth another try; anything else recurs forever.
        if "port is already allocated" not in last_error and "address already in use" not in last_error:
            break
    raise RuntimeError(f"could not start pgvector container: {last_error}")


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

    Under xdist this is one container PER WORKER, not one per run: pytest
    instantiates session-scoped fixtures once in each worker process. That is
    correct but not free, so only workers that actually receive a postgres test
    pay for it -- a reason to keep the postgres files grouped (--dist loadfile)
    rather than scattered across every worker.
    """
    if not _docker_available():
        pytest.skip("docker not available in this environment")

    name, port = _start_container()
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
