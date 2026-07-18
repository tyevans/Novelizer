import os
import tempfile
from click.testing import CliRunner
from novelizer.director.cli import cli, format_voice_report
from novelizer.settings.toml_io import load_toml_file, write_toml_file
from novelizer.voices.models import ProseProfile, VoicePack
from novelizer.store.models import Character


def test_config_error_shown_as_friendly_message_not_traceback(monkeypatch, tmp_path):
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "xdg"))
    story_dir = tmp_path / "story"
    story_dir.mkdir()
    (story_dir / "story.toml").write_text('llm_api_key = "x"\n')
    runner = CliRunner()
    result = runner.invoke(cli, ["--story", str(story_dir), "chapters"])
    assert result.exit_code != 0
    assert result.exception is None or isinstance(result.exception, SystemExit)
    assert "Traceback" not in result.output
    assert "llm_api_key" in result.output


def _env(path, xdg_config_home):
    return {"NOVELIZER_DB_PATH": path, "XDG_CONFIG_HOME": str(xdg_config_home)}


def test_headless_subcommand_does_not_create_global_config_when_absent(tmp_path):
    """A fresh user's first command must not suppress the first-run wizard.

    update_global_config() creates global_config.toml as a side effect. The
    wizard only fires when that file is absent, so a headless subcommand run
    before the file exists must not create it.
    """
    fd, path = tempfile.mkstemp(suffix=".db"); os.close(fd)
    xdg = tmp_path / "xdg"
    try:
        gpath = xdg / "novelizer" / "config.toml"
        assert not gpath.exists()
        r = CliRunner().invoke(cli, ["seed", "a storm is coming"], env=_env(path, xdg))
        assert r.exit_code == 0, r.output
        assert not gpath.exists()
    finally:
        os.unlink(path)


def test_headless_subcommand_still_records_last_opened_when_config_exists(tmp_path):
    """Existing regression guard: once the config exists, last_opened_story keeps updating."""
    fd, path = tempfile.mkstemp(suffix=".db"); os.close(fd)
    xdg = tmp_path / "xdg"
    try:
        gpath = xdg / "novelizer" / "config.toml"
        gpath.parent.mkdir(parents=True)
        write_toml_file(gpath, {})
        r = CliRunner().invoke(cli, ["seed", "a storm is coming"], env=_env(path, xdg))
        assert r.exit_code == 0, r.output
        assert "last_opened_story" in load_toml_file(gpath)
    finally:
        os.unlink(path)


def test_seed_then_chapters_roundtrip(tmp_path):
    fd, path = tempfile.mkstemp(suffix=".db"); os.close(fd)
    xdg = tmp_path / "xdg"
    try:
        runner = CliRunner()
        r1 = runner.invoke(cli, ["seed", "a storm is coming"], env=_env(path, xdg))
        assert r1.exit_code == 0, r1.output
        assert "Seed" in r1.output
        r2 = runner.invoke(cli, ["chapters"], env=_env(path, xdg))
        assert r2.exit_code == 0, r2.output
        assert "No chapters" in r2.output  # none authored yet
    finally:
        os.unlink(path)


def test_retcons_command_empty(tmp_path):
    fd, path = tempfile.mkstemp(suffix=".db"); os.close(fd)
    xdg = tmp_path / "xdg"
    try:
        r = CliRunner().invoke(cli, ["retcons"], env=_env(path, xdg))
        assert r.exit_code == 0, r.output
        assert "No open retcon" in r.output
    finally:
        os.unlink(path)


def test_voices_lists_default_pack_profiles(tmp_path):
    fd, path = tempfile.mkstemp(suffix=".db"); os.close(fd)
    xdg = tmp_path / "xdg"
    try:
        runner = CliRunner()
        result = runner.invoke(cli, ["voices"], env=_env(path, xdg))
        assert result.exit_code == 0, result.output
        assert "sparse" in result.output
        assert "lush" in result.output
        assert "plain" in result.output
        assert "*" in result.output or "active" in result.output.lower()
    finally:
        os.unlink(path)


def test_voices_with_explicit_pack_path(tmp_path):
    fd, db_path = tempfile.mkstemp(suffix=".db"); os.close(fd)
    xdg = tmp_path / "xdg"
    try:
        fd2, custom_pack_path = tempfile.mkstemp(suffix=".toml"); os.close(fd2)
        try:
            with open(custom_pack_path, "w") as f:
                f.write('name = "custom"\n\n[prose_profiles.terse]\nname = "terse"\ncasting_note = "Very short sentences."\n')
            runner = CliRunner()
            result = runner.invoke(cli, ["voices", "--pack", str(custom_pack_path)], env=_env(db_path, xdg))
            assert result.exit_code == 0, result.output
            assert "terse" in result.output
            assert "sparse" not in result.output
        finally:
            os.unlink(custom_pack_path)
    finally:
        os.unlink(db_path)


def test_report_includes_prose_profiles_with_active_marker():
    pack = VoicePack(
        name="default",
        prose_profiles={
            "plain": ProseProfile(name="plain", casting_note="Clean and neutral."),
            "sparse": ProseProfile(name="sparse", casting_note="Spare, concrete, unadorned."),
        },
    )
    report = format_voice_report(pack, characters=[], active_profile="plain")
    assert "plain" in report and "sparse" in report
    assert "Clean and neutral." in report


def test_report_includes_agent_personalities():
    pack = VoicePack(name="default", agent_personalities={"editor": "A precise, unsentimental line editor."})
    report = format_voice_report(pack, characters=[], active_profile=None)
    assert "editor" in report
    assert "A precise, unsentimental line editor." in report


def test_report_includes_only_characters_with_nonempty_voice():
    pack = VoicePack(name="default")
    characters = [
        Character(id="c1", name="Mira", voice="Clipped sentences."),
        Character(id="c2", name="Jonas", voice=""),
    ]
    report = format_voice_report(pack, characters=characters, active_profile=None)
    assert "Mira" in report and "Clipped sentences." in report
    assert "Jonas" not in report
