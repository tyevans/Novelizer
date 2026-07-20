from novelizer.canon_fs.outline_render import (
    render_beats, render_blueprint, render_brief, render_ledger,
    render_threads_plan,
)
from novelizer.store.models import (
    BeatRecord, BlueprintRecord, BriefStatus, Chapter, ChapterBriefRecord,
    PromiseRecord, PromiseState, ThreadRecord, ThreadState,
)


def make_blueprint(**kw):
    defaults = dict(id="bp1", framework="six-position", target_chapter_count=10, genre="fantasy")
    defaults.update(kw)
    return BlueprintRecord(**defaults)


def make_beat(**kw):
    defaults = dict(
        id="bp1-catalyst", blueprint_id="bp1", slug="catalyst", name="Catalyst",
        ideal_pct=0.10, tolerance_pct=0.05,
    )
    defaults.update(kw)
    return BeatRecord(**defaults)


def make_brief(**kw):
    defaults = dict(id="brief1", target_ordinal=4, goal="Mara finds the bell.")
    defaults.update(kw)
    return ChapterBriefRecord(**defaults)


def make_thread(**kw):
    defaults = dict(id="t1", name="Bell's Curse")
    defaults.update(kw)
    return ThreadRecord(**defaults)


def make_promise(**kw):
    defaults = dict(id="p1", name="The scar's origin")
    defaults.update(kw)
    return PromiseRecord(**defaults)


# -- render_blueprint --

def test_render_blueprint_frontmatter_and_body():
    blueprint = make_blueprint()
    beats = [make_beat()]
    text = render_blueprint(blueprint, beats)
    assert "id: bp1" in text
    assert "kind: blueprint" in text
    assert "framework: six-position" in text
    assert "target_chapter_count: 10" in text
    assert "fantasy" in text
    assert "Catalyst" in text


def test_render_blueprint_no_blueprint_fallback():
    text = render_blueprint(None, [])
    assert "No blueprint adopted." in text
    assert "kind: blueprint" in text


def test_render_blueprint_beat_table_shows_window_and_status():
    blueprint = make_blueprint()
    beats = [make_beat(), make_beat(id="bp1-mid", slug="midpoint", name="Midpoint", ideal_pct=0.5, fulfilled_by_chapter_id="ch1")]
    text = render_blueprint(blueprint, beats)
    assert "Catalyst" in text and "Midpoint" in text
    # window numbers present somewhere
    assert "-" in text


# -- render_beats --

def test_render_beats_lists_window_polarity_fulfilled_by():
    blueprint = make_blueprint()
    beats = [make_beat(expected_polarity="up", fulfilled_by_chapter_id="ch1")]
    chapters = [Chapter(id="ch1", title="One", prose="x")]
    text = render_beats(blueprint, beats, chapters)
    assert "kind: beats" in text
    assert "Catalyst" in text
    assert "up" in text
    assert "ch1" in text or "One" in text


def test_render_beats_no_blueprint_fallback():
    text = render_beats(None, [], [])
    assert "No blueprint adopted." in text
    assert "kind: beats" in text


# -- render_brief --

def test_render_brief_frontmatter_and_body_fields():
    brief = make_brief(
        pov_character_id="mara", threads_to_touch=["t1"], beats_to_hit=["bp1-catalyst"],
        promises_to_progress=["p1"], value_shift="fear -> resolve",
        planned_outcome="Mara rings the bell.", synopsis="She rings it.",
    )
    text = render_brief(brief)
    assert "id: brief1" in text
    assert "kind: chapter_brief" in text
    assert "target_ordinal: 4" in text
    assert "status: open" in text
    assert "Mara finds the bell." in text
    assert "mara" in text
    assert "t1" in text
    assert "bp1-catalyst" in text
    assert "p1" in text
    assert "fear -> resolve" in text
    assert "Mara rings the bell." in text
    assert "She rings it." in text


# -- render_threads_plan --

def test_render_threads_plan_lists_non_terminal_threads():
    threads = [
        make_thread(state=ThreadState.planted, window_lo=2, window_hi=5, planned_payoff_note="pays off at the bell"),
        make_thread(id="t2", name="Dead End", state=ThreadState.paid_off),
    ]
    text = render_threads_plan(threads, [])
    assert "kind: threads_plan" in text
    assert "Bell's Curse" in text
    assert "Dead End" not in text
    assert "pays off at the bell" in text


def test_render_threads_plan_no_window_shows_dash():
    threads = [make_thread(state=ThreadState.touched)]
    text = render_threads_plan(threads, [])
    assert "—" in text or "—" in text


# -- render_ledger --

def test_render_ledger_open_promises_windows_and_overdue_flag():
    chapters = [Chapter(id=f"ch{i}", title=str(i), prose="x") for i in range(1, 6)]
    promises = [
        make_promise(state=PromiseState.open, window_lo=1, window_hi=3),  # overdue: now=5 > 3
        make_promise(id="p2", name="Second", state=PromiseState.open, window_lo=1, window_hi=10),
        make_promise(id="p3", name="Paid one", state=PromiseState.paid),
        make_promise(id="p4", name="Released one", state=PromiseState.released),
    ]
    text = render_ledger(promises, chapters)
    assert "kind: ledger" in text
    assert "The scar's origin" in text
    assert "Second" in text
    assert "overdue" in text.lower()
    assert "Paid one" not in text
    assert "Released one" not in text
    assert "paid: 1" in text or "1 paid" in text
    assert "released: 1" in text or "1 released" in text
