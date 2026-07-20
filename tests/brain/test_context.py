from novelizer.brain.context import stale_threads_note, pacing_flags_note
from novelizer.store.models import Chapter, ThreadRecord, ThreadState, StructureScore


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


from novelizer.brain.context import known_secrets_note, causal_flags_note
from novelizer.store.models import Character, SecretRecord, CausalEdgeRecord


def _character(id_, name):
    return Character(id=id_, name=name)


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
