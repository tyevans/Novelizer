"""Characterisation of the one slug rule, across every place that mints a slug.

Seven modules had grown their own copy of `re.compile(r"[^a-z0-9]+")` plus the
same lower/sub/strip dance: five canon aggregates minting aggregate_ids, the
canon_fs path builder minting filenames, and settings discovery minting a story
directory name. Copies drift silently -- nothing in the codebase would have
failed if one of them had started stripping underscores or keeping dots -- and
two of the seven feed the filesystem, where a divergence changes where files
land rather than merely how an id reads.

So the mechanics are pinned here as one corpus applied to all seven, and each
function's *fallback* (the word it returns when a name slugifies to nothing) is
pinned separately, because those legitimately differ per aggregate.
"""
from __future__ import annotations

import pathlib

import pytest

from novelizer.canon.characters import slugify_character_name
from novelizer.canon.promises import slugify_promise_name
from novelizer.canon.secrets import slugify_secret_name
from novelizer.canon.themes import slugify_theme_name
from novelizer.canon.threads import slugify_thread_name
from novelizer.canon_fs.paths import slugify as slugify_path
from novelizer.settings.discovery import slugify as slugify_story_dir
from novelizer.slug import slugify

# (function, fallback) for every slug minter in the codebase. The fallback is
# part of each function's own contract, not of the shared rule.
MINTERS = [
    (slugify_thread_name, "thread"),
    (slugify_secret_name, "secret"),
    (slugify_theme_name, "theme"),
    (slugify_promise_name, "promise"),
    (slugify_character_name, "character"),
    (slugify_path, "untitled"),
    (slugify_story_dir, "story"),
]

# (input, expected slug) for inputs that produce a slug. One corpus, every
# minter: agreement here is what makes consolidating onto one helper safe.
CORPUS = [
    ("The Locket's Secret", "the-locket-s-secret"),           # apostrophe
    ("  --Mira's Revenge!!--  ", "mira-s-revenge"),            # leading/trailing junk
    ("Chapter 3: The Fall", "chapter-3-the-fall"),             # digits kept
    ("a...b---c___d", "a-b-c-d"),                              # punctuation runs collapse
    ("already-slugged", "already-slugged"),                    # idempotent shape
    ("MiXeD CaSe", "mixed-case"),                              # case folded
    ("Café Noir", "caf-noir"),                                 # non-ASCII letter dropped
    ("Мира and Ash", "and-ash"),                               # non-Latin script dropped
    ("snake_case_name", "snake-case-name"),                    # underscore is not id-safe
    ("tabs\tand\nnewlines", "tabs-and-newlines"),              # all whitespace alike
    ("trailing hyphen -", "trailing-hyphen"),
    ("2001", "2001"),                                          # digits only
    ("a" * 300, "a" * 300),                                    # no length cap anywhere
    ("one   two", "one-two"),                                  # internal whitespace run
]

# Inputs that slugify to nothing, where each minter returns its own fallback.
EMPTY_INPUTS = ["", "   ", "###", "---", "!!! ??? ...", "Мира", "…"]


@pytest.mark.parametrize("raw,expected", CORPUS, ids=[repr(c[0][:24]) for c in CORPUS])
def test_every_minter_produces_the_same_slug(raw, expected):
    produced = {fn.__module__: fn(raw) for fn, _ in MINTERS}
    assert set(produced.values()) == {expected}, f"minters disagree: {produced}"


@pytest.mark.parametrize("raw", EMPTY_INPUTS)
def test_an_input_with_nothing_slug_safe_falls_back_per_aggregate(raw):
    for fn, fallback in MINTERS:
        assert fn(raw) == fallback, f"{fn.__module__}.{fn.__name__}({raw!r})"


def test_a_slug_is_stable_under_reslugging():
    """Minting is called once per aggregate, but ids flow back through canon
    text and get re-slugged by canon_fs on the way to a filename. A slug that
    changed shape on a second pass would move a file for no reason."""
    for fn, _ in MINTERS:
        for raw, expected in CORPUS:
            assert fn(fn(raw)) == expected


# --- the rule lives in exactly one place ------------------------------------

SLUG_PATTERN = r"[^a-z0-9]+"
REPO_ROOT = pathlib.Path(__file__).resolve().parent.parent
SLUG_HOME = REPO_ROOT / "novelizer" / "slug.py"


def test_the_slug_pattern_is_written_down_exactly_once():
    """The corpus above proves the seven copies agree *today*; nothing made them
    agree tomorrow. This is the guard that does: the character class may appear
    in one module only, so a change to the rule is a change in one place and a
    new minter cannot quietly grow an eighth dialect."""
    owners = sorted(
        str(p.relative_to(REPO_ROOT))
        for p in REPO_ROOT.glob("novelizer/**/*.py")
        if SLUG_PATTERN in p.read_text()
    )
    assert owners == ["novelizer/slug.py"], f"the slug rule is spelled out in {owners}"


def test_the_shared_helper_takes_the_fallback_from_its_caller():
    """The mechanics are shared; the fallback is not. Each minter keeps its own
    word, so the helper cannot have a default one -- a default is how five
    aggregates end up all minting "untitled"."""
    assert slugify("Mira's Revenge", "thread") == "mira-s-revenge"
    assert slugify("###", "thread") == "thread"
    assert slugify("###", "untitled") == "untitled"
