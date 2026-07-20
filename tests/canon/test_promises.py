from novelizer.canon.promises import TERMINAL_PROMISE_STATES, slugify_promise_name


def test_slugify_promise_name_basic():
    assert slugify_promise_name("The Sealed Letter") == "the-sealed-letter"


def test_slugify_promise_name_collapses_and_strips():
    assert slugify_promise_name("  A -- rusty!! KEY  ") == "a-rusty-key"


def test_slugify_promise_name_empty_falls_back():
    assert slugify_promise_name("!!!") == "promise"


def test_terminal_promise_states():
    assert TERMINAL_PROMISE_STATES == {"paid", "released"}


def test_promise_record_defaults():
    from novelizer.store.models import PromiseRecord, PromiseState
    p = PromiseRecord(id="p", name="P")
    assert p.state == PromiseState.open
    assert p.kind == "foreshadow"
    assert p.progress_count == 0 and p.window_lo == 0 and p.window_hi == 0


def test_thread_and_secret_records_accept_window_fields_with_back_compat_defaults():
    from novelizer.store.models import SecretRecord, ThreadRecord
    t = ThreadRecord(id="t", name="T")
    assert t.window_lo == 0 and t.window_hi == 0 and t.planned_payoff_note == ""
    # pre-M7 serialized rows must still validate
    assert ThreadRecord.model_validate_json(t.model_dump_json()).window_hi == 0
    s = SecretRecord(id="s", title="S")
    assert s.reveal_window_lo == 0 and s.reveal_window_hi == 0
