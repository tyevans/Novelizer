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


def test_voices_lists_default_pack_profiles():
    fd, path = tempfile.mkstemp(suffix=".db"); os.close(fd)
    try:
        runner = CliRunner()
        result = runner.invoke(cli, ["voices"], env=_env(path))
        assert result.exit_code == 0, result.output
        assert "sparse" in result.output
        assert "lush" in result.output
        assert "plain" in result.output
        assert "*" in result.output or "active" in result.output.lower()
    finally:
        os.unlink(path)


def test_voices_with_explicit_pack_path():
    fd, db_path = tempfile.mkstemp(suffix=".db"); os.close(fd)
    try:
        fd2, custom_pack_path = tempfile.mkstemp(suffix=".toml"); os.close(fd2)
        try:
            with open(custom_pack_path, "w") as f:
                f.write('name = "custom"\n\n[prose_profiles.terse]\nname = "terse"\ncasting_note = "Very short sentences."\n')
            runner = CliRunner()
            result = runner.invoke(cli, ["voices", "--pack", str(custom_pack_path)], env=_env(db_path))
            assert result.exit_code == 0, result.output
            assert "terse" in result.output
            assert "sparse" not in result.output
        finally:
            os.unlink(custom_pack_path)
    finally:
        os.unlink(db_path)
