from novelizer.settings.discovery import StoryMeta, list_stories, order_stories, slugify
from novelizer.settings.story_dir import create_story
from novelizer.settings.toml_io import write_toml_file


def test_list_stories_reads_titles_and_skips_non_stories(tmp_path):
    create_story(tmp_path / "alpha", title="Alpha Novel")
    (tmp_path / "beta").mkdir()
    (tmp_path / "beta" / "world.db").write_bytes(b"")  # story dir without story.toml
    (tmp_path / "not-a-story").mkdir()
    (tmp_path / "loose-file.txt").write_text("x")

    stories = {s.root.name: s for s in list_stories(tmp_path)}
    assert set(stories) == {"alpha", "beta"}
    assert stories["alpha"].title == "Alpha Novel"
    assert stories["beta"].title == "beta"


def test_list_stories_missing_dir(tmp_path):
    assert list_stories(tmp_path / "absent") == []


def test_list_stories_bad_story_toml_falls_back_to_dirname(tmp_path):
    sd = create_story(tmp_path / "gamma", title="G")
    sd.story_toml.write_text("title = \n")  # invalid TOML
    [meta] = list_stories(tmp_path)
    assert meta.title == "gamma"


def test_order_stories_last_opened_first_then_mtime(tmp_path):
    a = StoryMeta(root=tmp_path / "a", title="a", mtime=100.0)
    b = StoryMeta(root=tmp_path / "b", title="b", mtime=300.0)
    c = StoryMeta(root=tmp_path / "c", title="c", mtime=200.0)
    ordered = order_stories([a, b, c], last_opened=str(tmp_path / "c"))
    assert [s.root.name for s in ordered] == ["c", "b", "a"]


def test_order_stories_no_last_opened(tmp_path):
    a = StoryMeta(root=tmp_path / "a", title="a", mtime=100.0)
    b = StoryMeta(root=tmp_path / "b", title="b", mtime=300.0)
    assert [s.root.name for s in order_stories([a, b], last_opened=None)] == ["b", "a"]


def test_slugify():
    assert slugify("My Great Novel!") == "my-great-novel"
    assert slugify("  --Weird__ Name--  ") == "weird-name"
    assert slugify("???") == "story"
