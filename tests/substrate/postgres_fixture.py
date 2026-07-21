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
