from hypothesis import given, strategies as st
from novelizer.muse.corpus import load_corpora
from novelizer.muse.draws import (
    DEFAULT_ERA, HAND_BEATS, HAND_NAMES, HAND_PROFESSIONS, HAND_SETTINGS, deal_hand,
)

CORPORA = load_corpora()
SEEDS = st.integers(min_value=0, max_value=2**63 - 1)


@given(seed=SEEDS)
def test_same_seed_deals_identical_hand(seed):
    a = deal_hand(CORPORA, seed, "modern", set(), "h1")
    b = deal_hand(CORPORA, seed, "modern", set(), "h1")
    assert a == b  # this is what makes event-log replay deterministic


@given(seed=SEEDS)
def test_hand_sizes(seed):
    hand = deal_hand(CORPORA, seed, "modern", set(), "h1")
    assert len(hand.names) == HAND_NAMES
    assert len(hand.professions) == HAND_PROFESSIONS
    assert len(hand.settings) == HAND_SETTINGS
    assert len(hand.beats) == HAND_BEATS
    assert len(set(hand.names)) == HAND_NAMES  # no repeats within a hand


@given(seed=SEEDS, era=st.sampled_from(sorted(CORPORA.given_names)))
def test_given_names_come_from_requested_era_bucket(seed, era):
    hand = deal_hand(CORPORA, seed, era, set(), "h1")
    bucket = set(CORPORA.given_names[era])
    surnames = set(CORPORA.surnames)
    for full in hand.names:
        given_part, surname_part = full.rsplit(" ", 1)
        assert given_part in bucket and surname_part in surnames
    assert hand.era == era


@given(seed=SEEDS)
def test_unknown_era_falls_back_to_default(seed):
    hand = deal_hand(CORPORA, seed, "jurassic", set(), "h1")
    assert hand.era == DEFAULT_ERA


@given(seed=SEEDS)
def test_exclusion_respected_when_corpus_ample(seed):
    exclude = set(CORPORA.beats[:5]) | set(CORPORA.professions[:5]) | set(CORPORA.settings[:5])
    hand = deal_hand(CORPORA, seed, "modern", exclude, "h1")
    assert not (set(hand.beats) & exclude)
    assert not (set(hand.professions) & exclude)
    assert not (set(hand.settings) & exclude)


@given(seed=SEEDS)
def test_excluded_name_components_not_redealt(seed):
    excluded_full_names = {f"{CORPORA.given_names['modern'][0]} {CORPORA.surnames[0]}"}
    hand = deal_hand(CORPORA, seed, "modern", excluded_full_names, "h1")
    for full in hand.names:
        given_part, surname_part = full.rsplit(" ", 1)
        assert given_part != CORPORA.given_names["modern"][0]
        assert surname_part != CORPORA.surnames[0]


@given(seed=SEEDS)
def test_exhausted_exclusion_reuses_corpus_instead_of_failing(seed):
    hand = deal_hand(CORPORA, seed, "modern", set(CORPORA.beats), "h1")
    assert len(hand.beats) == HAND_BEATS  # falls back to the full pool
