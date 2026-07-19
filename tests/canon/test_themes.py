from novelizer.canon.themes import slugify_theme_name


def test_slugify_theme_name_lowercases_and_hyphenates():
    assert slugify_theme_name("The Cost of Ambition") == "the-cost-of-ambition"


def test_slugify_theme_name_collapses_punctuation():
    assert slugify_theme_name("Loyalty & Betrayal!!") == "loyalty-betrayal"


def test_slugify_theme_name_empty_falls_back():
    assert slugify_theme_name("   ") == "theme"


def test_slugify_theme_name_strips_leading_trailing_hyphens():
    assert slugify_theme_name("-- redemption --") == "redemption"
