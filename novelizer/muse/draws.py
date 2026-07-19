from __future__ import annotations
import random
from novelizer.canon.events import InspirationDrawn
from novelizer.muse.corpus import Corpora

HAND_NAMES = 5
HAND_PROFESSIONS = 3
HAND_SETTINGS = 2
HAND_BEATS = 2
DEFAULT_ERA = "modern"


def _sample(rng: random.Random, pool: list[str], exclude: set[str], count: int) -> list[str]:
    fresh = [entry for entry in pool if entry not in exclude]
    if len(fresh) < count:
        # The exclusion window exhausted the corpus: reuse is better than an
        # empty draw (spec: degraded, never blocked).
        fresh = list(pool)
    return rng.sample(fresh, min(count, len(fresh)))


def deal_hand(corpora: Corpora, seed: int, era: str, exclude: set[str], hand_id: str) -> InspirationDrawn:
    """Deal one hand. Pure: identical (corpora, seed, era, exclude) always
    deal the identical hand — the property event-log replay relies on.
    `exclude` holds items dealt in the recent-hand window verbatim; for names
    ("Given Surname") both components are individually excluded.
    """
    rng = random.Random(seed)
    bucket_era = era if era in corpora.given_names else DEFAULT_ERA
    excluded_givens = {entry.rsplit(" ", 1)[0] for entry in exclude if " " in entry}
    excluded_surnames = {entry.rsplit(" ", 1)[1] for entry in exclude if " " in entry}
    givens = _sample(rng, corpora.given_names[bucket_era], excluded_givens, HAND_NAMES)
    surnames = _sample(rng, corpora.surnames, excluded_surnames, HAND_NAMES)
    return InspirationDrawn(
        hand_id=hand_id,
        seed=seed,
        corpus_version=corpora.version,
        era=bucket_era,
        names=[f"{g} {s}" for g, s in zip(givens, surnames)],
        professions=_sample(rng, corpora.professions, exclude, HAND_PROFESSIONS),
        settings=_sample(rng, corpora.settings, exclude, HAND_SETTINGS),
        beats=_sample(rng, corpora.beats, exclude, HAND_BEATS),
    )
