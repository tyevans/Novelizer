from hypothesis import given, strategies as st

from novelizer.tui.widgets.proposals_model import BANNER_STYLE, banner_line


def test_banner_line_singular():
    line = banner_line(1)
    assert line.plain == "▼ 1 proposal awaiting approval — press a"
    assert str(line.style) == BANNER_STYLE


def test_banner_line_plural_matches_spec_mockup():
    assert banner_line(2).plain == "▼ 2 proposals awaiting approval — press a"


@given(st.integers(min_value=1, max_value=99))
def test_banner_line_always_counts_and_always_high_contrast(n):
    line = banner_line(n)
    assert line.plain.startswith(f"▼ {n} proposal")
    assert line.plain.endswith("— press a")
    assert str(line.style) == BANNER_STYLE


from novelizer.canon.autonomy import Proposal
from novelizer.tui.identity import identity_for
from novelizer.tui.widgets.proposals_model import (
    payload_summary,
    proposal_context,
    proposal_row,
)


def _proposal(**payload):
    return Proposal(id="abcdef12-0000-0000-0000-000000000000",
                    proposing_agent="author", target_event_type="chapter.created",
                    target_aggregate_id="c1", payload=payload)


def test_payload_summary_prefers_title_then_falls_through_the_human_keys():
    assert payload_summary({"title": "The Salt Road", "body": "x"}) == "The Salt Road"
    assert payload_summary({"name": "Elara"}) == "Elara"
    assert payload_summary({"note": "a note"}) == "a note"
    assert payload_summary({"description": "scar mismatch"}) == "scar mismatch"
    assert payload_summary({"body": "prose here"}) == "prose here"
    assert payload_summary({}) == ""
    assert payload_summary({"tension": 0.5}) == ""


def test_payload_summary_collapses_whitespace_and_clips_to_60():
    summary = payload_summary({"title": "word " * 40})
    assert "\n" not in summary
    assert len(summary) == 60 and summary.endswith("…")


def test_proposal_row_is_id_free_and_names_agent_and_target():
    row = proposal_row(_proposal(title="Pending One"))
    assert "abcdef12" not in row.plain
    assert row.plain == "✎ Author    → chapter.created  Pending One"
    styles = [(row.plain[s.start:s.end], str(s.style)) for s in row.spans]
    assert ("✎ Author    ", identity_for("author").style) in styles


def test_proposal_row_without_summary_has_no_trailing_gap():
    row = proposal_row(_proposal())
    assert row.plain == "✎ Author    → chapter.created"


def test_proposal_context_bold_header_and_payload_fields():
    ctx = proposal_context(_proposal(title="Pending One", prose="It began."))
    lines = ctx.plain.splitlines()
    assert lines[0] == "Author proposes chapter.created"
    assert "title: Pending One" in lines
    assert "prose: It began." in lines
    styles = [(ctx.plain[s.start:s.end], str(s.style)) for s in ctx.spans]
    assert ("Author proposes chapter.created", "bold") in styles


def test_proposal_context_skips_id_like_and_bookkeeping_keys_and_empties():
    ctx = proposal_context(_proposal(
        id="025bae36", supersedes_id="x", event_ids=["a"], character_ids=[],
        created_at="2026-07-18", provenance={"model": "m"},
        title="Kept", editor_notes=None, prose="",
    ))
    assert "025bae36" not in ctx.plain
    assert "supersedes_id" not in ctx.plain and "event_ids" not in ctx.plain
    assert "created_at" not in ctx.plain and "provenance" not in ctx.plain
    assert "editor_notes" not in ctx.plain and "prose:" not in ctx.plain
    assert "title: Kept" in ctx.plain
