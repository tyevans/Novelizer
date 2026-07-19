import pytest

from novelizer.settings.story_dir import (
    StoryDirectory,
    create_story,
    is_story_dir,
    migrate_flat_layout,
)
from novelizer.settings.toml_io import load_toml_file, write_toml_file


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


def test_migrate_flat_layout_refuses_to_overwrite_existing_db(tmp_path):
    (tmp_path / "world.db").write_bytes(b"flatdata")
    target = tmp_path / "default"
    target.mkdir()
    (target / "world.db").write_bytes(b"existing")
    with pytest.raises(FileExistsError) as exc:
        migrate_flat_layout(tmp_path)
    msg = str(exc.value)
    assert str(tmp_path / "world.db") in msg
    assert str(target / "world.db") in msg
    # nothing moved
    assert (tmp_path / "world.db").read_bytes() == b"flatdata"
    assert (target / "world.db").read_bytes() == b"existing"


def test_migrate_flat_layout_refuses_to_overwrite_existing_chroma(tmp_path):
    (tmp_path / "world.db").write_bytes(b"flatdata")
    target = tmp_path / "default"
    target.mkdir()
    (target / "chroma").mkdir()
    with pytest.raises(FileExistsError):
        migrate_flat_layout(tmp_path)
    assert (tmp_path / "world.db").exists()


def test_migrate_flat_layout_preserves_existing_story_toml(tmp_path):
    (tmp_path / "world.db").write_bytes(b"flatdata")
    target = tmp_path / "default"
    target.mkdir()
    write_toml_file(target / "story.toml", {"title": "Custom Title", "prose_profile": "lush"})
    sd = migrate_flat_layout(tmp_path)
    assert load_toml_file(sd.story_toml) == {"title": "Custom Title", "prose_profile": "lush"}


def test_create_story_with_overrides_writes_them(tmp_path):
    sd = create_story(
        tmp_path / "s", title="S",
        overrides={"prose_profile": "lush", "voice_pack": "/packs/noir.toml"},
    )
    assert load_toml_file(sd.story_toml) == {
        "title": "S", "prose_profile": "lush", "voice_pack": "/packs/noir.toml",
    }


def test_create_story_without_overrides_unchanged(tmp_path):
    sd = create_story(tmp_path / "s", title="S")
    assert load_toml_file(sd.story_toml) == {"title": "S"}


def test_create_story_rejects_unknown_and_forbidden_keys(tmp_path):
    from novelizer.settings.layers import StoryConfigError

    with pytest.raises(StoryConfigError):
        create_story(tmp_path / "s", title="S", overrides={"llm_api_key": "sk-x"})
    with pytest.raises(StoryConfigError):
        create_story(tmp_path / "s", title="S", overrides={"nonsense": "x"})
    # validation happens before mkdir: no half-created story dir
    assert not (tmp_path / "s").exists()
