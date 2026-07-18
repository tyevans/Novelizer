from novelizer.tui.widgets.who_knows_what import who_knows_what_line
from novelizer.store.models import Character, SecretRecord


def test_revealed_secret_line_shows_revealed():
    secret = SecretRecord(id="the-map", title="The Map Is Forged", revealed=True)
    matrix = {"the-map": {"revealed": True, "known_by": set()}}
    line = who_knows_what_line(secret, [], matrix)
    assert "The Map Is Forged" in line and "REVEALED" in line


def test_secret_known_to_one_character_names_them():
    mara = Character(id="mara", name="Mara")
    kestrel = Character(id="kestrel", name="Kestrel")
    secret = SecretRecord(id="the-heir-lives", title="The Heir Lives")
    matrix = {"the-heir-lives": {"revealed": False, "known_by": {"mara"}}}
    line = who_knows_what_line(secret, [mara, kestrel], matrix)
    assert "Mara" in line and "Kestrel" not in line and "REVEALED" not in line


def test_secret_known_to_no_one_says_so():
    secret = SecretRecord(id="the-map", title="The Map Is Forged")
    matrix = {"the-map": {"revealed": False, "known_by": set()}}
    line = who_knows_what_line(secret, [], matrix)
    assert "no one" in line
