from novelizer.canon.promises import TERMINAL_PROMISE_STATES, slugify_promise_name


def test_slugify_promise_name_basic():
    assert slugify_promise_name("The Sealed Letter") == "the-sealed-letter"


def test_slugify_promise_name_collapses_and_strips():
    assert slugify_promise_name("  A -- rusty!! KEY  ") == "a-rusty-key"


def test_slugify_promise_name_empty_falls_back():
    assert slugify_promise_name("!!!") == "promise"


def test_terminal_promise_states():
    assert TERMINAL_PROMISE_STATES == {"paid", "released"}
