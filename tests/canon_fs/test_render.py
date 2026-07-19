from novelizer.canon_fs.render import render_chapter, render_world_entry, render_character
from novelizer.store.models import Chapter, Domain, WorldEntry, Character, CharacterRelationship, SecretRecord


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


def test_empty_list_fields_omit_frontmatter_lines():
    ch = Chapter(title="Solo", prose="p")
    out = render_chapter(ch)
    assert "characters:" not in out
    assert f"id: {ch.id}" in out

    e = WorldEntry(title="Bare", body="b")
    out = render_world_entry(e)
    assert "tags:" not in out
    assert f"id: {e.id}" in out


def test_render_character_dossier_and_knows():
    c = Character(
        name="Mara", traits="stubborn", motivations="find the bell", arc_status="rising",
        relationships=[CharacterRelationship(target_character_id="c2", description="rival")],
    )
    secrets = [SecretRecord(id="s1", title="The scar's origin"), SecretRecord(id="s2", title="Hidden door")]
    matrix = {"s1": {"revealed": False, "known_by": {c.id}}, "s2": {"revealed": False, "known_by": set()}}
    out = render_character(c, matrix, secrets)
    assert f"id: {c.id}" in out
    assert "kind: character" in out
    assert "# Mara" in out
    assert "traits: stubborn" in out
    assert "- c2: rival" in out
    assert "- s1 (The scar's origin)" in out
    assert "s2" not in out.split("## Knows")[1]


def test_render_character_no_secrets_omits_knows_section():
    c = Character(name="Bo")
    out = render_character(c, {}, [])
    assert "## Knows" not in out


def test_render_character_revealed_secret_excluded_from_knows():
    c = Character(name="Mara")
    secrets = [SecretRecord(id="s1", title="Out in the open", revealed=True)]
    matrix = {"s1": {"revealed": True, "known_by": {c.id}}}
    out = render_character(c, matrix, secrets)
    assert "## Knows" not in out
    assert "s1" not in out


from novelizer.canon_fs.render import render_secret, render_theme, render_thread
from novelizer.store.models import ThemeRecord, ThreadRecord, ThreadState


def test_render_thread_state_and_touches():
    t = ThreadRecord(id="t1", name="Bell's Curse", state=ThreadState.touched,
                     touch_count=3, last_note="rang again", last_chapter_id="ch9")
    out = render_thread(t)
    assert "id: t1" in out and "kind: thread" in out
    assert "state: touched" in out and "touch_count: 3" in out
    assert "last_chapter_id: ch9" in out and "rang again" in out


def test_render_secret_who_knows():
    mara = Character(name="Mara")
    s = SecretRecord(id="s1", title="The scar's origin")
    matrix = {"s1": {"revealed": False, "known_by": {mara.id}}}
    out = render_secret(s, matrix, [mara])
    assert "id: s1" in out and "kind: secret" in out
    assert "revealed: False" in out
    assert "known to: Mara" in out


def test_render_secret_known_to_no_one():
    s = SecretRecord(id="s2", title="Hidden door")
    out = render_secret(s, {"s2": {"revealed": False, "known_by": set()}}, [])
    assert "known to no one" in out


def test_render_theme():
    th = ThemeRecord(id="th1", title="Drowning as memory", touch_count=2, last_chapter_id="ch4")
    out = render_theme(th)
    assert "id: th1" in out and "kind: theme" in out
    assert "touch_count: 2" in out and "last_chapter_id: ch4" in out
