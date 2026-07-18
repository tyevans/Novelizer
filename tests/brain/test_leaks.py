from novelizer.brain.leaks import LEAK_SOURCE_TAG, Leak, find_leaks, leak_description
from novelizer.store.models import SecretReferenceRecord


def test_reference_with_no_learn_or_reveal_is_a_leak():
    refs = [SecretReferenceRecord(secret_id="the-heir-lives", character_id="mara", chapter_id="c3")]
    matrix = {"the-heir-lives": {"revealed": False, "known_by": set()}}
    leaks = find_leaks(refs, matrix)
    assert leaks == [Leak(secret_id="the-heir-lives", character_id="mara", chapter_id="c3")]


def test_reference_by_a_character_who_learned_is_not_a_leak():
    refs = [SecretReferenceRecord(secret_id="the-heir-lives", character_id="mara", chapter_id="c3")]
    matrix = {"the-heir-lives": {"revealed": False, "known_by": {"mara"}}}
    assert find_leaks(refs, matrix) == []


def test_reference_to_a_revealed_secret_is_not_a_leak_even_if_unlearned():
    refs = [SecretReferenceRecord(secret_id="the-heir-lives", character_id="ren", chapter_id="c5")]
    matrix = {"the-heir-lives": {"revealed": True, "known_by": set()}}
    assert find_leaks(refs, matrix) == []


def test_reference_to_a_secret_missing_from_the_matrix_is_a_leak():
    refs = [SecretReferenceRecord(secret_id="unminted", character_id="mara", chapter_id="c1")]
    assert find_leaks(refs, {}) == [Leak(secret_id="unminted", character_id="mara", chapter_id="c1")]


def test_multiple_references_flag_only_the_unknown_ones():
    refs = [
        SecretReferenceRecord(secret_id="s1", character_id="mara", chapter_id="c1"),
        SecretReferenceRecord(secret_id="s1", character_id="ren", chapter_id="c2"),
    ]
    matrix = {"s1": {"revealed": False, "known_by": {"mara"}}}
    leaks = find_leaks(refs, matrix)
    assert leaks == [Leak(secret_id="s1", character_id="ren", chapter_id="c2")]


def test_leak_description_starts_with_the_pinned_tag_and_names_the_fact():
    leak = Leak(secret_id="the-heir-lives", character_id="mara", chapter_id="c3")
    desc = leak_description(leak)
    assert desc.startswith(LEAK_SOURCE_TAG)
    assert "the-heir-lives" in desc and "mara" in desc and "c3" in desc


def test_leak_description_is_deterministic_for_the_same_leak():
    leak = Leak(secret_id="s1", character_id="mara", chapter_id="c1")
    assert leak_description(leak) == leak_description(Leak(secret_id="s1", character_id="mara", chapter_id="c1"))
