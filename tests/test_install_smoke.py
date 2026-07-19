"""CI-mechanical first-run smoke test: the literal first two acceptance-walkthrough
steps (no prior config; CLI survives an empty $XDG_CONFIG_HOME without a traceback).

Uses Click's CliRunner in-process — no `uv tool install`, no network, no LLM calls.
The real `uv tool install .` reproduction lives in `scripts/verify_install.sh` and is
run manually (too slow/networked for every CI run).
"""
import os
import tempfile
from click.testing import CliRunner
from novelizer.director.cli import cli


def _env(xdg_config_home, db_path=None):
    env = {"XDG_CONFIG_HOME": str(xdg_config_home)}
    if db_path is not None:
        env["NOVELIZER_DB_PATH"] = str(db_path)
    return env


def _assert_clean(result):
    """No pre-existing config or story directory should ever produce an unhandled
    traceback. A click.ClickException (exit_code != 0, no exception, or a handled
    SystemExit) is an acceptable clean failure; a bare Python traceback is not.
    """
    assert "Traceback" not in result.output, result.output
    if result.exception is not None:
        assert isinstance(result.exception, SystemExit), (
            f"unhandled exception: {result.exception!r}\n{result.output}"
        )


def test_help_exits_cleanly_with_no_config(tmp_path):
    xdg = tmp_path / "xdg"
    result = CliRunner().invoke(cli, ["--help"], env=_env(xdg))
    assert result.exit_code == 0, result.output
    assert not xdg.exists()
    for name in ("seed", "chapters", "read", "retcons", "voices", "autonomy"):
        assert name in result.output


def test_voices_against_empty_config_does_not_traceback(tmp_path):
    xdg = tmp_path / "xdg"
    fd, db_path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    try:
        result = CliRunner().invoke(cli, ["voices"], env=_env(xdg, db_path))
        _assert_clean(result)
    finally:
        os.unlink(db_path)


def test_chapters_against_empty_config_does_not_traceback(tmp_path):
    xdg = tmp_path / "xdg"
    fd, db_path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    try:
        result = CliRunner().invoke(cli, ["chapters"], env=_env(xdg, db_path))
        _assert_clean(result)
        assert result.exit_code == 0, result.output
    finally:
        os.unlink(db_path)


def test_no_subcommand_and_no_config_does_not_create_global_config(tmp_path):
    """Headless subcommands must never suppress the first-run wizard by
    accidentally writing the global config file before it's meant to exist.
    """
    xdg = tmp_path / "xdg"
    gpath = xdg / "novelizer" / "config.toml"
    fd, db_path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    try:
        result = CliRunner().invoke(cli, ["chapters"], env=_env(xdg, db_path))
        _assert_clean(result)
        assert not gpath.exists()
    finally:
        os.unlink(db_path)
