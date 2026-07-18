import pytest

from novelizer.settings.story_dir import (
    StoryDirectory,
    create_story,
    is_story_dir,
    migrate_flat_layout,
)
from novelizer.settings.toml_io import load_toml_file


def test_derived_paths(tmp_path):
    sd = StoryDirectory(root=tmp_path / "novel")
    assert sd.db_path == tmp_path / "novel" / "world.db"
    assert sd.chroma_path == tmp_path / "novel" / "chroma"
    assert sd.story_toml == tmp_path / "novel" / "story.toml"


def test_is_story_dir(tmp_path):
    assert not is_story_dir(tmp_path)
    (tmp_path / "story.toml").write_text("")
    assert is_story_dir(tmp_path)


def test_is_story_dir_with_only_db(tmp_path):
    (tmp_path / "world.db").write_bytes(b"")
    assert is_story_dir(tmp_path)


def test_create_story(tmp_path):
    sd = create_story(tmp_path / "my-novel", title="My Novel")
    assert sd.root.is_dir()
    assert load_toml_file(sd.story_toml) == {"title": "My Novel"}
    assert is_story_dir(sd.root)


def test_migrate_flat_layout(tmp_path):
    (tmp_path / "world.db").write_bytes(b"dbdata")
    (tmp_path / "chroma").mkdir()
    (tmp_path / "chroma" / "seg").write_bytes(b"x")
    sd = migrate_flat_layout(tmp_path)
    assert sd.root == tmp_path / "default"
    assert sd.db_path.read_bytes() == b"dbdata"
    assert (sd.chroma_path / "seg").read_bytes() == b"x"
    assert not (tmp_path / "world.db").exists()
    assert not (tmp_path / "chroma").exists()
    assert load_toml_file(sd.story_toml) == {"title": "default"}


def test_migrate_flat_layout_without_chroma(tmp_path):
    (tmp_path / "world.db").write_bytes(b"dbdata")
    sd = migrate_flat_layout(tmp_path)
    assert sd.db_path.read_bytes() == b"dbdata"
    assert not sd.chroma_path.exists()


def test_migrate_flat_layout_nothing_to_migrate(tmp_path):
    with pytest.raises(FileNotFoundError):
        migrate_flat_layout(tmp_path)
