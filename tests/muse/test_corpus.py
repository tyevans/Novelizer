from novelizer.muse.corpus import Corpora, CorpusError, load_corpora

# Words the Cornell study found in 88% of AI stories. Corpora must never
# contain them — they are the exact convergence this feature breaks.
AI_TELLS = {
    "elias", "elara", "mara", "thorne", "lighthouse", "keeper", "baker",
    "mayor", "clockmaker", "fisherman", "librarian", "conductor",
}


def test_load_corpora_returns_populated_buckets():
    corpora = load_corpora()
    assert corpora.version
    assert set(corpora.given_names) == {"victorian", "interwar", "midcentury", "late20th", "modern"}
    for bucket, names in corpora.given_names.items():
        assert len(names) >= 30, f"era bucket {bucket} too thin"
    assert len(corpora.surnames) >= 60
    assert len(corpora.professions) >= 40
    assert len(corpora.settings) >= 35
    assert len(corpora.beats) >= 35


def test_no_ai_tells_in_any_corpus():
    corpora = load_corpora()
    everything = (
        [n for names in corpora.given_names.values() for n in names]
        + corpora.surnames + corpora.professions + corpora.settings + corpora.beats
    )
    for entry in everything:
        for word in entry.lower().replace("-", " ").split():
            assert word not in AI_TELLS, f"AI-tell {word!r} found in corpus entry {entry!r}"


def test_no_duplicates_within_a_corpus():
    corpora = load_corpora()
    for label, entries in (
        ("surnames", corpora.surnames), ("professions", corpora.professions),
        ("settings", corpora.settings), ("beats", corpora.beats),
    ):
        assert len(entries) == len(set(entries)), f"duplicate in {label}"
    for bucket, names in corpora.given_names.items():
        assert len(names) == len(set(names)), f"duplicate in given_names[{bucket}]"


def test_missing_file_raises_corpus_error(monkeypatch):
    import novelizer.muse.corpus as corpus_mod
    monkeypatch.setattr(corpus_mod, "_DATA_PACKAGE", "novelizer.muse")  # package exists, files don't
    try:
        load_corpora()
    except CorpusError:
        pass
    else:
        raise AssertionError("expected CorpusError")
