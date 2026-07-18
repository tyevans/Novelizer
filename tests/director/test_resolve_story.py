import pytest
from click import ClickException

from novelizer.director.cli import _resolve_story, _validated_story
from novelizer.settings import EffectiveSettings, create_story
from novelizer.settings.toml_io import load_toml_file


def _base(**kwargs) -> EffectiveSettings:
    return EffectiveSettings(**kwargs)


def test_explicit_story_path_must_exist(tmp_path):
    with pytest.raises(ClickException):
        _validated_story(str(tmp_path / "nope"))


def test_explicit_story_path_valid(tmp_path):
    create_story(tmp_path / "novel", title="N")
    sd = _validated_story(str(tmp_path / "novel"))
    assert sd.root == tmp_path / "novel"


def test_last_opened_story_used_when_valid(tmp_path):
    create_story(tmp_path / "recent", title="R")
    sd = _resolve_story(
        None,
        stories_root=tmp_path / "stories",
        base=_base(last_opened_story=str(tmp_path / "recent")),
        global_path=tmp_path / "config.toml",
    )
    assert sd.root == tmp_path / "recent"


def test_stale_last_opened_falls_through(tmp_path):
    root = tmp_path / "stories"
    sd = _resolve_story(
        None,
        stories_root=root,
        base=_base(last_opened_story=str(tmp_path / "deleted")),
        global_path=tmp_path / "config.toml",
    )
    assert sd.root == root / "default"


def test_flat_layout_migrates_when_confirmed(tmp_path):
    root = tmp_path / "stories"
    root.mkdir()
    (root / "world.db").write_bytes(b"db")
    sd = _resolve_story(
        None, stories_root=root, base=_base(),
        confirm=lambda *a, **k: True, global_path=tmp_path / "config.toml",
    )
    assert sd.root == root / "default"
    assert sd.db_path.read_bytes() == b"db"


def test_flat_layout_decline_persists_suppression(tmp_path):
    root = tmp_path / "stories"
    root.mkdir()
    (root / "world.db").write_bytes(b"db")
    gpath = tmp_path / "config.toml"
    sd = _resolve_story(
        None, stories_root=root, base=_base(),
        confirm=lambda *a, **k: False, global_path=gpath,
    )
    assert sd.root == root  # legacy paths keep working
    assert load_toml_file(gpath)["suppress_flat_migration_prompt"] is True


def test_flat_layout_suppressed_never_prompts(tmp_path):
    root = tmp_path / "stories"
    root.mkdir()
    (root / "world.db").write_bytes(b"db")

    def _boom(*a, **k):
        raise AssertionError("must not prompt when suppressed")

    sd = _resolve_story(
        None, stories_root=root,
        base=_base(suppress_flat_migration_prompt=True),
        confirm=_boom, global_path=tmp_path / "config.toml",
    )
    assert sd.root == root


def test_fresh_install_creates_default_story(tmp_path):
    root = tmp_path / "stories"
    sd = _resolve_story(
        None, stories_root=root, base=_base(), global_path=tmp_path / "config.toml"
    )
    assert sd.root == root / "default"
    assert sd.story_toml.exists()
