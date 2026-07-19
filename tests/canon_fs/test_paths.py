from novelizer.canon_fs.paths import slugify, build_path_index
from novelizer.store.models import (
    Chapter, Character, SecretRecord, ThemeRecord, ThreadRecord, WorldEntry,
)


def test_slugify_basic():
    assert slugify("The Drowned Bell") == "the-drowned-bell"


def test_slugify_punctuation_collapses():
    assert slugify("Mara's  Scar!!") == "mara-s-scar"


def test_slugify_never_empty():
    assert slugify("") == "untitled"
    assert slugify("???") == "untitled"


def _index(**kw):
    empty = dict(chapters=[], characters=[], world_entries=[], threads=[], secrets=[], themes=[])
    empty.update(kw)
    return build_path_index(**empty)


def test_chapter_paths_are_ordinal_prefixed():
    chapters = [Chapter(title="First Light", prose="p"), Chapter(title="The Drowned Bell", prose="p")]
    index = _index(chapters=chapters)
    assert index["/chapters/001-first-light.md"] == ("chapter", chapters[0].id)
    assert index["/chapters/002-the-drowned-bell.md"] == ("chapter", chapters[1].id)


def test_name_collision_gets_id_suffix():
    a, b = Character(name="Mara"), Character(name="Mara")
    index = _index(characters=[a, b])
    assert index["/characters/mara.md"] == ("character", a.id)
    assert index[f"/characters/mara-{b.id[:8]}.md"] == ("character", b.id)


def test_all_kinds_present():
    index = _index(
        chapters=[Chapter(title="C", prose="p")],
        characters=[Character(name="N")],
        world_entries=[WorldEntry(title="W", body="b")],
        threads=[ThreadRecord(id="t1", name="T")],
        secrets=[SecretRecord(id="s1", title="S")],
        themes=[ThemeRecord(id="th1", title="Th")],
    )
    kinds = {kind for kind, _ in index.values()}
    assert kinds == {"chapter", "character", "world", "thread", "secret", "theme"}
    assert len(index) == 6
