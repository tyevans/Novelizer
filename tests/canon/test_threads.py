from novelizer.canon.threads import slugify_thread_name, TERMINAL_STATES


def test_slugify_lowercases_and_hyphenates():
    assert slugify_thread_name("The Locket's Secret") == "the-locket-s-secret"


def test_slugify_strips_leading_trailing_punctuation():
    assert slugify_thread_name("  --Mira's Revenge!!--  ") == "mira-s-revenge"


def test_slugify_falls_back_when_name_has_no_alnum_chars():
    assert slugify_thread_name("###") == "thread"


def test_terminal_states_are_paid_off_and_abandoned():
    assert TERMINAL_STATES == {"paid_off", "abandoned"}
