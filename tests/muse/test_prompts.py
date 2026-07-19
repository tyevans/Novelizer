from novelizer.muse.prompts import (
    AI_TELL_BAN_NOTE, architect_settings_note, casting_pool_note,
    inspiration_note, name_uptake_matches,
)
from novelizer.store.models import InspirationHandRecord


def _hand(hand_id="h1", **kw):
    defaults = dict(
        id=hand_id, seed=1, corpus_version="2026.07", era="modern",
        names=["Doris Kimbrough", "Mateo Rafferty"], professions=["glazier"],
        settings=["salvage yard"], beats=["a debt is called in early"],
    )
    defaults.update(kw)
    return InspirationHandRecord(**defaults)


def test_casting_pool_note_lists_names_and_is_binding():
    note = casting_pool_note(_hand())
    assert "Doris Kimbrough" in note and "Mateo Rafferty" in note
    assert "NEW named character" in note


def test_inspiration_note_is_marked_optional():
    note = inspiration_note(_hand())
    assert "optional" in note.lower()
    assert "glazier" in note and "salvage yard" in note and "a debt is called in early" in note


def test_notes_are_empty_without_a_hand():
    assert casting_pool_note(None) == ""
    assert inspiration_note(None) == ""
    assert architect_settings_note(None) == ""
    assert inspiration_note(_hand(professions=[], settings=[], beats=[])) == ""


def test_architect_note_only_carries_settings():
    note = architect_settings_note(_hand())
    assert "salvage yard" in note and "glazier" not in note


def test_ban_note_names_the_tells():
    for tell in ("Elias", "Elara", "Mara", "Thorne", "lighthouse"):
        assert tell in AI_TELL_BAN_NOTE


def test_name_uptake_matches_full_and_given_name():
    hands = [_hand("h1"), _hand("h2", names=["Wanda Okafor"])]
    assert name_uptake_matches("Wanda Okafor", hands) == ("h2", "Wanda Okafor")
    # prose often drops the surname: given-name-token match still counts,
    # and the most recent hand wins
    assert name_uptake_matches("doris", hands) == ("h1", "Doris Kimbrough")
    assert name_uptake_matches("Prudence", hands) is None
    assert name_uptake_matches("", hands) is None
