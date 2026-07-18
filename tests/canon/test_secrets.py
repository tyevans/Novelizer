from novelizer.canon.secrets import slugify_secret_name, knowledge_cell_state


def test_slugify_lowercases_and_hyphenates():
    assert slugify_secret_name("The Heir Lives") == "the-heir-lives"


def test_slugify_strips_leading_trailing_punctuation():
    assert slugify_secret_name("  --Mara's Real Name!!--  ") == "mara-s-real-name"


def test_slugify_falls_back_when_title_has_no_alnum_chars():
    assert slugify_secret_name("###") == "secret"


def test_knowledge_cell_state_unknown_when_secret_missing():
    assert knowledge_cell_state({}, "the-heir-lives", "mara") == "unknown"


def test_knowledge_cell_state_unknown_when_not_learned():
    matrix = {"the-heir-lives": {"revealed": False, "known_by": set()}}
    assert knowledge_cell_state(matrix, "the-heir-lives", "mara") == "unknown"


def test_knowledge_cell_state_known_when_learned():
    matrix = {"the-heir-lives": {"revealed": False, "known_by": {"mara"}}}
    assert knowledge_cell_state(matrix, "the-heir-lives", "mara") == "known"


def test_knowledge_cell_state_revealed_applies_to_every_character():
    matrix = {"the-heir-lives": {"revealed": True, "known_by": set()}}
    assert knowledge_cell_state(matrix, "the-heir-lives", "a-character-created-after-the-reveal") == "revealed"
