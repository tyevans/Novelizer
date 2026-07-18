import os
import tempfile
from click.testing import CliRunner
from novelizer.director.cli import cli


def _env(path):
    return {"NOVELIZER_DB_PATH": path}


def test_seed_then_chapters_roundtrip():
    fd, path = tempfile.mkstemp(suffix=".db"); os.close(fd)
    try:
        runner = CliRunner()
        r1 = runner.invoke(cli, ["seed", "a storm is coming"], env=_env(path))
        assert r1.exit_code == 0, r1.output
        assert "Seed" in r1.output
        r2 = runner.invoke(cli, ["chapters"], env=_env(path))
        assert r2.exit_code == 0, r2.output
        assert "No chapters" in r2.output  # none authored yet
    finally:
        os.unlink(path)


def test_retcons_command_empty():
    fd, path = tempfile.mkstemp(suffix=".db"); os.close(fd)
    try:
        r = CliRunner().invoke(cli, ["retcons"], env=_env(path))
        assert r.exit_code == 0, r.output
        assert "No open retcon" in r.output
    finally:
        os.unlink(path)
