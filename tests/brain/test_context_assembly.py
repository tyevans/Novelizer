"""Property proof for the context-assembly invariants (spec .specs/context-assembly-v2.md):
window coverage/overlap/termination, and advisory never-silent."""
from __future__ import annotations
from hypothesis import given, settings, strategies as st
from novelizer.brain.context_assembly import (
    AdvisoryEntry, CharHeuristicEstimator, ELISION_MARKER, OMITTED_HEADER_FMT,
    Window, assemble_advisory, assemble_verbatim,
)


def test_estimator_ceil():
    est = CharHeuristicEstimator()
    assert est.estimate("") == 0
    assert est.estimate("abcd") == 1
    assert est.estimate("abcde") == 2


def test_fits_budget_single_window():
    ws = assemble_verbatim("hello world", budget_tokens=100)
    assert ws == [Window(text="hello world", index=0, total=1)]


def test_empty_prose_single_empty_window():
    assert assemble_verbatim("", budget_tokens=10) == [Window(text="", index=0, total=1)]


@given(st.text(min_size=0, max_size=4000), st.integers(min_value=1, max_value=200))
@settings(max_examples=100, deadline=None)
def test_windows_cover_input_in_order_no_gaps(text: str, budget: int):
    ws = assemble_verbatim(text, budget_tokens=budget)
    assert len(ws) >= 1
    assert [w.index for w in ws] == list(range(len(ws)))
    assert all(w.total == len(ws) for w in ws)
    # Each window is a substring of the input at a monotonically advancing
    # start; consecutive windows leave no gap; the last window reaches the end.
    # (find() with a moving lower bound is deliberately implementation-blind.)
    search_from = 0
    covered_to = 0
    for w in ws:
        start = text.find(w.text, search_from)
        assert start != -1, "window is not a substring at/after the previous start"
        assert start <= covered_to, "gap between consecutive windows"
        covered_to = max(covered_to, start + len(w.text))
        search_from = start + 1 if len(ws) > 1 else search_from
    assert covered_to == len(text)


def test_advisory_prefers_summary():
    out = assemble_advisory(
        [AdvisoryEntry(label="Ch One", summary="Ana finds the key.")], budget_tokens=100,
    )
    assert "Ana finds the key." in out and ELISION_MARKER not in out


def test_advisory_fallback_is_labeled():
    out = assemble_advisory(
        [AdvisoryEntry(label="Ch One", verbatim="x" * 4000)], budget_tokens=50,
    )
    assert ELISION_MARKER in out and "Ch One" in out
    assert len(out) < 4000


def test_advisory_omission_is_announced():
    entries = [AdvisoryEntry(label=f"Ch {i}", summary="s" * 200) for i in range(10)]
    out = assemble_advisory(entries, budget_tokens=60)
    kept = sum(1 for i in range(10) if f"Ch {i}:" in out)
    assert 0 < kept < 10
    assert OMITTED_HEADER_FMT.format(n=10 - kept) in out
    assert "Ch 9:" in out  # newest survives; oldest dropped first


@given(
    st.lists(
        st.tuples(st.booleans(), st.text(min_size=1, max_size=300)), min_size=1, max_size=12
    ),
    st.integers(min_value=10, max_value=500),
)
@settings(max_examples=100, deadline=None)
def test_advisory_never_silent(items, budget):
    entries = [
        AdvisoryEntry(label=f"Ch {i}", summary=(txt if has_summary else None),
                      verbatim=(None if has_summary else txt))
        for i, (has_summary, txt) in enumerate(items)
    ]
    out = assemble_advisory(entries, budget_tokens=budget)
    # Every entry is either present by label, or covered by the omitted header.
    present = sum(1 for i in range(len(entries)) if f"Ch {i}" in out)
    if present < len(entries):
        assert "omitted]" in out


def test_advisory_empty_summary_falls_back_to_labeled_verbatim():
    """An empty-string summary must not displace the verbatim fallback —
    that would be a new silent-truncation path."""
    out = assemble_advisory(
        [AdvisoryEntry(label="Ch One", summary="", verbatim="x" * 4000)], budget_tokens=50,
    )
    assert ELISION_MARKER in out and "x" in out


def test_advisory_entry_with_no_content_is_still_named():
    out = assemble_advisory([AdvisoryEntry(label="Ch One")], budget_tokens=50)
    assert "Ch One" in out and "(no content)" in out
