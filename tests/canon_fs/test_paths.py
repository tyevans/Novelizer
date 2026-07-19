from novelizer.canon_fs.paths import slugify


def test_slugify_basic():
    assert slugify("The Drowned Bell") == "the-drowned-bell"


def test_slugify_punctuation_collapses():
    assert slugify("Mara's  Scar!!") == "mara-s-scar"


def test_slugify_never_empty():
    assert slugify("") == "untitled"
    assert slugify("???") == "untitled"
