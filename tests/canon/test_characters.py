from novelizer.canon.characters import slugify_character_name


def test_slugify_lowercases_and_hyphenates():
    assert slugify_character_name("Silas Vane") == "silas-vane"


def test_slugify_strips_leading_trailing_punctuation():
    assert slugify_character_name("  --Mrs. Gable!!--  ") == "mrs-gable"


def test_slugify_falls_back_when_name_has_no_alnum_chars():
    assert slugify_character_name("###") == "character"
