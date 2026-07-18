from novelizer.director.cli import _resolve_story
from novelizer.settings import create_story


def test_explicit_story_path(tmp_path):
    sd = _resolve_story(str(tmp_path / "novel"), stories_root=tmp_path / "stories")
    assert sd.root == tmp_path / "novel"


def test_flat_layout_migrates_when_confirmed(tmp_path):
    root = tmp_path / "stories"
    root.mkdir()
    (root / "world.db").write_bytes(b"db")
    sd = _resolve_story(None, stories_root=root, confirm=lambda *a, **k: True)
    assert sd.root == root / "default"
    assert sd.db_path.read_bytes() == b"db"


def test_flat_layout_kept_when_declined(tmp_path):
    root = tmp_path / "stories"
    root.mkdir()
    (root / "world.db").write_bytes(b"db")
    sd = _resolve_story(None, stories_root=root, confirm=lambda *a, **k: False)
    # Declining keeps legacy paths working: the root itself acts as the story dir.
    assert sd.root == root
    assert sd.db_path == root / "world.db"


def test_existing_default_story_used(tmp_path):
    root = tmp_path / "stories"
    create_story(root / "default", title="default")
    sd = _resolve_story(None, stories_root=root)
    assert sd.root == root / "default"


def test_fresh_install_creates_default_story(tmp_path):
    root = tmp_path / "stories"
    sd = _resolve_story(None, stories_root=root)
    assert sd.root == root / "default"
    assert sd.story_toml.exists()
