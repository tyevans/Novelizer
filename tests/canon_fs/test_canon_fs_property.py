# tests/canon_fs/test_canon_fs_property.py
import re
from hypothesis import given, strategies as st
from novelizer.canon_fs.paths import build_path_index, slugify
from novelizer.canon_fs.render import render_chapter
from novelizer.store.models import Chapter, Character

SLUG_OK = re.compile(r"^[a-z0-9-]+$")


@given(st.text())
def test_slugify_total_and_filename_safe(text):
    slug = slugify(text)
    assert SLUG_OK.match(slug)
    assert slugify(slug) == slug  # idempotent


@given(st.lists(st.text(min_size=0, max_size=30), max_size=12))
def test_path_index_is_total_and_unique(titles):
    chapters = [Chapter(title=t, prose="p") for t in titles]
    characters = [Character(name=t) for t in titles]
    index = build_path_index(
        chapters=chapters, characters=characters,
        world_entries=[], threads=[], secrets=[], themes=[],
    )
    assert len(index) == len(chapters) + len(characters)  # no silent drops
    ids = {record_id for _, record_id in index.values()}
    assert ids == {c.id for c in chapters} | {c.id for c in characters}


@given(st.text(min_size=0, max_size=50), st.text(min_size=0, max_size=200))
def test_render_chapter_always_carries_exact_id(title, prose):
    ch = Chapter(title=title, prose=prose)
    out = render_chapter(ch)
    assert f"id: {ch.id}" in out
    assert out.startswith("---\n")
    fm_block = out.split("---")[1]
    assert [l for l in fm_block.splitlines() if l.startswith("id:")] == [f"id: {ch.id}"]
