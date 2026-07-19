from pathlib import Path

from novelizer.director.cli import _interactive_startup
from novelizer.settings import create_story
from novelizer.settings.toml_io import load_toml_file, write_toml_file


def _isolate(monkeypatch, tmp_path) -> Path:
    """Point the global config and env at a clean sandbox."""
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "xdg"))
    monkeypatch.chdir(tmp_path)
    from novelizer.settings.loader import EnvOverrides

    for name in EnvOverrides.model_fields:
        monkeypatch.delenv(f"NOVELIZER_{name.upper()}", raising=False)
    return tmp_path / "xdg" / "novelizer" / "config.toml"


def test_wizard_runs_when_config_missing_and_quit_aborts(monkeypatch, tmp_path):
    gpath = _isolate(monkeypatch, tmp_path)
    calls = []

    def fake_wizard():
        calls.append("wizard")
        return None  # user quit

    result = _interactive_startup(None, run_wizard=fake_wizard, run_picker=lambda *a: None)
    assert result is None
    assert calls == ["wizard"]
    assert not gpath.exists()


def test_wizard_result_written_0600_then_picker(monkeypatch, tmp_path):
    import os

    gpath = _isolate(monkeypatch, tmp_path)
    story = create_story(tmp_path / "stories" / "novel", title="N")

    def fake_wizard():
        return {"llm_base_url": "http://h:1/v1", "llm_api_key": "sk-x"}

    def fake_picker(stories, stories_dir, last_opened, base):
        assert stories_dir == Path("stories")
        assert base.prose_profile  # effective settings reach the picker
        return story.root

    settings = _interactive_startup(None, run_wizard=fake_wizard, run_picker=fake_picker)
    assert settings is not None
    assert settings.llm_base_url == "http://h:1/v1"
    assert settings.db_path == str(story.db_path)
    assert os.stat(gpath).st_mode & 0o777 == 0o600
    assert load_toml_file(gpath)["last_opened_story"] == str(story.root)


def test_existing_config_skips_wizard_and_honors_default_stories_dir(monkeypatch, tmp_path):
    gpath = _isolate(monkeypatch, tmp_path)
    write_toml_file(gpath, {"default_stories_dir": str(tmp_path / "novels")})
    story = create_story(tmp_path / "novels" / "one", title="One")
    seen = {}

    def fake_picker(stories, stories_dir, last_opened, base):
        seen["stories_dir"] = stories_dir
        seen["titles"] = [s.title for s in stories]
        return story.root

    settings = _interactive_startup(
        None,
        run_wizard=lambda: (_ for _ in ()).throw(AssertionError("wizard must not run")),
        run_picker=fake_picker,
    )
    assert seen["stories_dir"] == tmp_path / "novels"
    assert seen["titles"] == ["One"]
    assert settings.db_path == str(story.db_path)


def test_picker_quit_aborts_without_recording(monkeypatch, tmp_path):
    gpath = _isolate(monkeypatch, tmp_path)
    write_toml_file(gpath, {})
    result = _interactive_startup(None, run_wizard=lambda: {}, run_picker=lambda *a: None)
    assert result is None
    assert "last_opened_story" not in load_toml_file(gpath)


def test_explicit_story_bypasses_picker(monkeypatch, tmp_path):
    gpath = _isolate(monkeypatch, tmp_path)
    write_toml_file(gpath, {})
    story = create_story(tmp_path / "elsewhere" / "novel", title="N")

    settings = _interactive_startup(
        str(story.root),
        run_wizard=lambda: (_ for _ in ()).throw(AssertionError("no wizard")),
        run_picker=lambda *a: (_ for _ in ()).throw(AssertionError("no picker")),
    )
    assert settings.db_path == str(story.db_path)
    assert load_toml_file(gpath)["last_opened_story"] == str(story.root)
