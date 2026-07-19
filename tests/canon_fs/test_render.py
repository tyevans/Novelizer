from novelizer.canon_fs.render import render_chapter, render_world_entry
from novelizer.store.models import Chapter, Domain, WorldEntry


def test_render_chapter_has_id_status_cast_and_full_prose():
    ch = Chapter(title="The Drowned Bell", prose="Long prose here.", character_ids=["c1", "c2"])
    out = render_chapter(ch)
    assert out.startswith("---\n")
    assert f"id: {ch.id}" in out
    assert "kind: chapter" in out
    assert "status: draft" in out
    assert "characters: c1, c2" in out
    assert "# The Drowned Bell" in out
    assert "Long prose here." in out


def test_render_world_entry_has_domain_and_body():
    e = WorldEntry(title="The Bell Cult", body="They ring at dusk.", domain=Domain.social, tags=["cult"])
    out = render_world_entry(e)
    assert f"id: {e.id}" in out
    assert "kind: world" in out
    assert "domain: social" in out
    assert "tags: cult" in out
    assert "They ring at dusk." in out
