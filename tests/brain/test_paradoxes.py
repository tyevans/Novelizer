from novelizer.brain.paradoxes import PARADOX_SOURCE_TAG, ParadoxCandidate, find_paradoxes, paradox_description
from novelizer.store.models import CausalEdgeRecord


def test_forward_edge_is_not_a_paradox():
    edges = [CausalEdgeRecord(cause_chapter_id="c1", effect_chapter_id="c3")]
    assert find_paradoxes(edges, ["c1", "c2", "c3"]) == []


def test_effect_at_or_before_cause_is_an_ordering_paradox():
    edges = [CausalEdgeRecord(cause_chapter_id="c3", effect_chapter_id="c1")]
    result = find_paradoxes(edges, ["c1", "c2", "c3"])
    assert result == [ParadoxCandidate(cause_chapter_id="c3", effect_chapter_id="c1", reason="ordering")]


def test_effect_equal_to_cause_is_an_ordering_paradox():
    edges = [CausalEdgeRecord(cause_chapter_id="c2", effect_chapter_id="c2")]
    result = find_paradoxes(edges, ["c1", "c2", "c3"])
    assert result == [ParadoxCandidate(cause_chapter_id="c2", effect_chapter_id="c2", reason="ordering")]


def test_two_cycle_reports_both_edges():
    edges = [
        CausalEdgeRecord(cause_chapter_id="c1", effect_chapter_id="c2", note="fire"),
        CausalEdgeRecord(cause_chapter_id="c2", effect_chapter_id="c1", note="revenge"),
    ]
    result = find_paradoxes(edges, ["c1", "c2"])
    reasons = {(p.cause_chapter_id, p.effect_chapter_id, p.reason) for p in result}
    assert ("c1", "c2", "ordering") in reasons or ("c1", "c2", "cycle") in reasons
    assert ("c2", "c1", "ordering") in reasons
    assert len(result) == 2


def test_three_cycle_reports_all_three_edges_as_cycle_candidates():
    edges = [
        CausalEdgeRecord(cause_chapter_id="c1", effect_chapter_id="c2"),
        CausalEdgeRecord(cause_chapter_id="c2", effect_chapter_id="c3"),
        CausalEdgeRecord(cause_chapter_id="c3", effect_chapter_id="c1"),
    ]
    result = find_paradoxes(edges, ["c1", "c2", "c3"])
    pairs = {(p.cause_chapter_id, p.effect_chapter_id) for p in result}
    assert pairs == {("c1", "c2"), ("c2", "c3"), ("c3", "c1")}
    for p in result:
        assert p.reason in ("ordering", "cycle")


def test_acyclic_forward_graph_has_no_candidates():
    edges = [
        CausalEdgeRecord(cause_chapter_id="c1", effect_chapter_id="c2"),
        CausalEdgeRecord(cause_chapter_id="c2", effect_chapter_id="c3"),
        CausalEdgeRecord(cause_chapter_id="c1", effect_chapter_id="c3"),
    ]
    assert find_paradoxes(edges, ["c1", "c2", "c3"]) == []


def test_cross_edge_into_a_finished_subtree_that_still_closes_a_cycle_is_flagged():
    # c1->c2->c4->c1 and c1->c3->c4->c1 are both cycles sharing node c4.
    # With chapter order [c1,c2,c3,c4] and a path-DFS visiting c2 before c3,
    # c4 is already fully visited (black) by the time c3->c4 is examined, so
    # a naive back-edge-only DFS misses it even though it lies on a cycle.
    edges = [
        CausalEdgeRecord(cause_chapter_id="c1", effect_chapter_id="c2"),
        CausalEdgeRecord(cause_chapter_id="c1", effect_chapter_id="c3"),
        CausalEdgeRecord(cause_chapter_id="c2", effect_chapter_id="c4"),
        CausalEdgeRecord(cause_chapter_id="c3", effect_chapter_id="c4"),
        CausalEdgeRecord(cause_chapter_id="c4", effect_chapter_id="c1"),
    ]
    result = find_paradoxes(edges, ["c1", "c2", "c3", "c4"])
    got = {(p.cause_chapter_id, p.effect_chapter_id, p.reason) for p in result}
    assert got == {
        ("c1", "c2", "cycle"),
        ("c1", "c3", "cycle"),
        ("c2", "c4", "cycle"),
        ("c3", "c4", "cycle"),
        ("c4", "c1", "ordering"),
    }
    assert len(result) == 5


def test_paradox_description_starts_with_the_pinned_tag():
    p = ParadoxCandidate(cause_chapter_id="c3", effect_chapter_id="c1", reason="ordering")
    desc = paradox_description(p)
    assert desc.startswith(PARADOX_SOURCE_TAG)
    assert "c3" in desc and "c1" in desc
