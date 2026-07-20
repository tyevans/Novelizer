from novelizer.brain.context import (
    beat_drift_note, pacing_flags_note, stale_threads_note, tension_target_note,
)
from novelizer.store.models import BeatRecord, BlueprintRecord, Chapter, ThreadRecord, ThreadState, StructureScore


def _chapters(n: int) -> list[Chapter]:
    return [Chapter(id=f"c{i}", title=str(i), prose="p") for i in range(n)]


def test_stale_threads_note_empty_when_nothing_stale():
    chs = _chapters(2)
    fresh = ThreadRecord(id="t1", name="Fresh", state=ThreadState.touched, last_chapter_id="c1")
    assert stale_threads_note([fresh], chs) == ""


def test_stale_threads_note_lists_stale_thread_name_and_id():
    chs = _chapters(5)
    stale = ThreadRecord(id="the-locket", name="The Locket", state=ThreadState.planted, last_chapter_id="c0")
    note = stale_threads_note([stale], chs)
    assert "The Locket" in note
    assert "the-locket" in note
    assert note.startswith("\n\n")


def test_stale_threads_note_omits_terminal_threads():
    chs = _chapters(10)
    closed = ThreadRecord(id="t1", name="Closed", state=ThreadState.paid_off, last_chapter_id="c0")
    assert stale_threads_note([closed], chs) == ""


def test_pacing_flags_note_empty_when_no_flags():
    scores = [StructureScore(chapter_id=f"c{i}", tension=0.5, pacing_label="steady") for i in range(3)]
    assert pacing_flags_note(scores) == ""


def test_pacing_flags_note_lists_flagged_chapter_and_direction():
    scores = [
        StructureScore(chapter_id="c1", tension=0.9, pacing_label="climax"),
        StructureScore(chapter_id="c2", tension=0.1, pacing_label="flat"),
        StructureScore(chapter_id="c3", tension=0.85, pacing_label="climax"),
    ]
    note = pacing_flags_note(scores)
    assert "c2" in note and "sag" in note
    assert note.startswith("\n\n")


from novelizer.brain.context import known_secrets_note, causal_flags_note, arc_note
from novelizer.store.models import ArcRecord, Character, SecretRecord, CausalEdgeRecord


def _character(id_, name):
    return Character(id=id_, name=name)


def test_arc_note_empty_when_no_findings():
    arc = ArcRecord(id="a1", character_id="mara", arc_type="positive", last_chapter_id="c0")
    assert arc_note([arc], [_character("mara", "Mara")], _chapters(1), [], None) == ""


def test_arc_note_lists_contradiction_with_adjudicate_guidance():
    arc = ArcRecord(id="a1", character_id="mara", arc_type="fall", resolved=True, outcome="truth_embraced")
    note = arc_note([arc], [_character("mara", "Mara")], _chapters(1), [], None)
    assert note.startswith("\n\n")
    assert "Mara" in note
    assert "adjudicate: fall arc resolved truth_embraced" in note


def test_arc_note_lists_stagnant_with_route_into_brief_guidance():
    arc = ArcRecord(id="a1", character_id="mara", arc_type="positive", last_chapter_id="c0")
    note = arc_note([arc], [_character("mara", "Mara")], _chapters(5), [], None, stagnation_chapters=4)
    assert "Mara" in note
    assert "route Mara into the next brief" in note


def test_arc_note_falls_back_to_character_id_when_name_unknown():
    arc = ArcRecord(id="a1", character_id="ghost-id", arc_type="positive", last_chapter_id="c0")
    note = arc_note([arc], [], _chapters(5), [], None, stagnation_chapters=4)
    assert "ghost-id" in note


def test_known_secrets_note_empty_when_no_secrets():
    assert known_secrets_note([], [], {}) == ""


def test_known_secrets_note_omits_revealed_secrets():
    secret = SecretRecord(id="the-map", title="The Map Is Forged", revealed=True)
    assert known_secrets_note([secret], [], {"the-map": {"revealed": True, "known_by": set()}}) == ""


def test_known_secrets_note_lists_secret_id_and_known_characters():
    mara = _character("mara", "Mara")
    kestrel = _character("kestrel", "Kestrel")
    secret = SecretRecord(id="the-heir-lives", title="The Heir Lives")
    matrix = {"the-heir-lives": {"revealed": False, "known_by": {"mara"}}}
    note = known_secrets_note([secret], [mara, kestrel], matrix)
    assert note.startswith("\n\n")
    assert "the-heir-lives" in note
    assert "Mara" in note
    assert "Kestrel" not in note


def test_known_secrets_note_flags_secret_known_to_no_one():
    secret = SecretRecord(id="the-map", title="The Map Is Forged")
    matrix = {"the-map": {"revealed": False, "known_by": set()}}
    note = known_secrets_note([secret], [], matrix)
    assert "known to no one" in note


def test_causal_flags_note_empty_when_no_paradoxes():
    edges = [CausalEdgeRecord(cause_chapter_id="c1", effect_chapter_id="c2")]
    assert causal_flags_note(edges, ["c1", "c2"]) == ""


def test_causal_flags_note_lists_ordering_paradox():
    edges = [CausalEdgeRecord(cause_chapter_id="c2", effect_chapter_id="c1")]
    note = causal_flags_note(edges, ["c1", "c2"])
    assert note.startswith("\n\n")
    assert "c2" in note and "c1" in note and "ordering" in note


def test_stale_threads_note_respects_explicit_threshold():
    chs = _chapters(3)
    thread = ThreadRecord(id="t1", name="T", state=ThreadState.planted, last_chapter_id="c0")
    assert stale_threads_note([thread], chs, threshold=3) == ""
    assert "t1" in stale_threads_note([thread], chs, threshold=1)


def test_pacing_flags_note_respects_explicit_delta():
    scores = [
        StructureScore(chapter_id="c1", tension=0.5, pacing_label="steady"),
        StructureScore(chapter_id="c2", tension=0.65, pacing_label="steady"),
    ]
    assert pacing_flags_note(scores, delta=0.3) == ""
    note = pacing_flags_note(scores, delta=0.05)
    assert "c1" in note and "c2" in note


def test_chapter_map_note_empty_when_no_chapters():
    from novelizer.brain.context import chapter_map_note

    assert chapter_map_note([]) == "None yet."


def test_chapter_map_note_formats_id_title_status_and_cast():
    from novelizer.brain.context import chapter_map_note

    chs = [Chapter(id="c1", title="The Salt Road", prose="p", character_ids=["mara", "eli"])]
    note = chapter_map_note(chs)
    assert note == "- [c1] 'The Salt Road' (draft) cast: mara, eli"


def test_chapter_map_note_none_cast_when_empty():
    from novelizer.brain.context import chapter_map_note

    chs = [Chapter(id="c1", title="One", prose="p")]
    note = chapter_map_note(chs)
    assert "cast: none" in note


from novelizer.brain.context import ledger_note, resolution_pacing_note
from novelizer.store.models import PromiseRecord, SecretRecord as _SecretRecord


def test_ledger_note_empty_when_no_promises():
    assert ledger_note([], _chapters(3)) == ""


def test_ledger_note_empty_when_no_open_or_due_promises():
    p = PromiseRecord(id="a", name="A")
    assert ledger_note([p], _chapters(10)) == ""


def test_ledger_note_lists_overdue_promise_first():
    p = PromiseRecord(id="a", name="A", window_lo=1, window_hi=2)
    note = ledger_note([p], _chapters(3))
    assert note.startswith("\n\nPromise ledger (pay or release these, citing ids exactly):\n")
    assert "OVERDUE — window closed ch 2" in note
    assert "A" in note and "id:a" in note


def test_ledger_note_lists_due_promise():
    p = PromiseRecord(id="a", name="A", window_lo=2, window_hi=4)
    note = ledger_note([p], _chapters(3))
    assert "due ch 2-4" in note
    assert "OVERDUE" not in note


def test_resolution_pacing_note_empty_when_quiet():
    assert resolution_pacing_note([], [], _chapters(5)) == ""


def test_resolution_pacing_note_lists_overdue_thread():
    t = ThreadRecord(id="t", name="The Locket", window_lo=1, window_hi=2)
    note = resolution_pacing_note([t], [], _chapters(3))
    assert note.startswith("\n\nResolution pacing:\n")
    assert "The Locket" in note and "window" in note and "OVERDUE" in note


def test_resolution_pacing_note_lists_overdue_reveal():
    s = _SecretRecord(id="s", title="The Heir Lives", reveal_window_lo=1, reveal_window_hi=2)
    note = resolution_pacing_note([], [s], _chapters(3))
    assert "The Heir Lives" in note and "OVERDUE" in note


def test_resolution_pacing_note_lists_congestion():
    ts = [ThreadRecord(id=f"t{i}", name=str(i), window_lo=19, window_hi=21) for i in range(3)]
    note = resolution_pacing_note(ts, [], _chapters(1))
    assert "resolve in the same window" in note


# --- beat_drift_note ---

def test_beat_drift_note_empty_when_quiet():
    assert beat_drift_note(None, [], _chapters(3)) == ""


def test_beat_drift_note_lists_drifting_beat():
    blueprint = BlueprintRecord(id="bp1", framework="six-position", target_chapter_count=10)
    beat = BeatRecord(id="b1", blueprint_id="bp1", slug="midpoint", name="Midpoint",
                       ideal_pct=0.5, tolerance_pct=0.1)
    note = beat_drift_note(blueprint, [beat], _chapters(7))
    assert note.startswith("\n\nBeat drift:\n")
    assert "Midpoint" in note


# --- tension_target_note ---

def test_tension_target_note_empty_when_quiet():
    blueprint = BlueprintRecord(id="bp1", framework="six-position", target_chapter_count=4)
    assert tension_target_note(blueprint, [], [], _chapters(4)) == ""
    assert tension_target_note(None, [], [], _chapters(4)) == ""


def test_tension_target_note_reports_worst_deviation_and_next_beat_guidance():
    blueprint = BlueprintRecord(id="bp1", framework="six-position", target_chapter_count=20)
    beats = [
        BeatRecord(id="b1", blueprint_id="bp1", slug="midpoint", name="Midpoint",
                   ideal_pct=0.5, tolerance_pct=0.1, expected_polarity="flip"),
    ]
    chapters = _chapters(20)
    scores = [StructureScore(chapter_id="c19", tension=0.99)]  # ch 20, target 0.5
    note = tension_target_note(blueprint, beats, scores, chapters)
    assert note.startswith("\n\nTension vs blueprint: ch 20 actual 0.99 vs target 0.5")
    assert "midpoint flip is planned for ch 8-12" in note
