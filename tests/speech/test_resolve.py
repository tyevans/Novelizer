from novelizer.speech.resolve import build_name_index, resolve_speaker
from novelizer.store.models import Character


def _roster():
    return [
        Character(id="mira", name="Mira", aliases=["The Warden"]),
        Character(id="jon-vale", name="Jon Vale", aliases=[]),
    ]


def test_resolves_canonical_name_case_insensitively():
    index = build_name_index(_roster())
    assert resolve_speaker("mira", index) == "mira"
    assert resolve_speaker("MIRA", index) == "mira"


def test_resolves_an_alias():
    index = build_name_index(_roster())
    assert resolve_speaker("The Warden", index) == "mira"


def test_resolves_by_slug_fallback_when_name_is_unknown():
    # "Jon Vale" slugs to "jon-vale"; a stray spelling that slugs the same still lands.
    index = build_name_index(_roster())
    assert resolve_speaker("jon  vale", index) == "jon-vale"


def test_unknown_speaker_returns_none():
    index = build_name_index(_roster())
    assert resolve_speaker("Nobody", index) is None


def test_blank_speaker_returns_none():
    index = build_name_index(_roster())
    assert resolve_speaker("   ", index) is None
