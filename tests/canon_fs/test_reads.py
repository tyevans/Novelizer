from hypothesis import given, strategies as st

from novelizer.canon_fs.reads import (
    FULL_READ_LIMIT, TRUNCATION_MARKER, sliced_read,
)


def _content(n_lines: int) -> str:
    return "".join(f"line {i}\n" for i in range(1, n_lines + 1))


def _text(result) -> str:
    return result.file_data["content"]


def test_window_covering_the_file_carries_no_notice():
    result = sliced_read(_content(40), offset=0, limit=100)
    assert TRUNCATION_MARKER not in _text(result)
    assert _text(result) == _content(40)


def test_short_window_announces_the_lines_it_withheld():
    result = sliced_read(_content(208), offset=0, limit=100)
    text = _text(result)
    assert TRUNCATION_MARKER in text
    # The counts must be exact: a vague "output truncated" leaves the agent
    # guessing whether it missed two lines or two hundred.
    assert "lines 1-100 of 208" in text
    assert "108 lines" in text
    assert text.splitlines()[99] == "line 100"


def test_notice_spells_out_the_follow_up_arguments():
    result = sliced_read(_content(208), offset=0, limit=100)
    assert f"offset=100, limit={FULL_READ_LIMIT}" in _text(result)


def test_notice_never_quotes_a_path():
    # CompositeBackend strips the route prefix before delegating, so a routed
    # backend never knows the path the agent typed. Quoting the path it does
    # see would hand back one that doesn't resolve.
    text = _text(sliced_read(_content(208), offset=0, limit=100))
    assert ".md" not in text.splitlines()[-1]
    assert "this same path" in text


def test_notice_is_flagged_as_machine_text_not_story_prose():
    # The author agent copies what it reads into chapters; the notice has to
    # announce itself as tooling output so it never lands in the novel.
    text = _text(sliced_read(_content(300), offset=0, limit=100))
    assert text.splitlines()[-1].startswith("[SYSTEM NOTICE")


def test_mid_file_window_reports_its_own_start():
    result = sliced_read(_content(208), offset=50, limit=10)
    text = _text(result)
    assert "lines 51-60 of 208" in text
    assert "offset=60," in text


def test_final_window_is_clean_even_when_it_started_late():
    result = sliced_read(_content(120), offset=100, limit=100)
    assert TRUNCATION_MARKER not in _text(result)


def test_offset_past_eof_passes_the_backend_error_through():
    result = sliced_read(_content(10), offset=50, limit=10)
    assert result.error and "exceeds file length" in result.error
    assert result.file_data is None


def test_empty_file_gets_no_notice():
    result = sliced_read("", offset=0, limit=100)
    assert TRUNCATION_MARKER not in _text(result)


@given(
    total=st.integers(min_value=1, max_value=400),
    offset=st.integers(min_value=0, max_value=399),
    limit=st.integers(min_value=1, max_value=400),
)
def test_notice_appears_exactly_when_content_was_withheld(total, offset, limit):
    result = sliced_read(_content(total), offset=offset, limit=limit)
    if result.error:
        assert offset >= total
        return
    withheld = offset + limit < total
    assert (TRUNCATION_MARKER in _text(result)) is withheld


@given(
    total=st.integers(min_value=1, max_value=200),
    limit=st.integers(min_value=1, max_value=200),
)
def test_content_lines_survive_verbatim_ahead_of_the_notice(total, limit):
    result = sliced_read(_content(total), offset=0, limit=limit)
    shown = min(limit, total)
    body = _text(result).splitlines()[:shown]
    assert body == [f"line {i}" for i in range(1, shown + 1)]
