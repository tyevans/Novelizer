from hypothesis import given, strategies as st

from novelizer.store.models import Chapter, StructureScore, ThreadRecord, ThreadState
from novelizer.tui.widgets.brain_model import (
    SHAPE_EMPTY,
    THREADS_EMPTY,
    chapter_label,
    chapter_number,
    shape_tab,
    thread_line,
    threads_tab,
)
from novelizer.tui.widgets.feed_model import ALARM_STYLE


def _chapters(*titles: str) -> list[Chapter]:
    return [Chapter(id=f"c{i + 1}", title=t, prose="p") for i, t in enumerate(titles)]


def test_chapter_number_is_one_based_position_in_chapter_order():
    chs = _chapters("One", "Two", "Three")
    assert chapter_number("c1", chs) == 1
    assert chapter_number("c3", chs) == 3
    assert chapter_number("ghost", chs) is None


def test_chapter_label_uses_number_and_title_never_the_id():
    chs = _chapters("One", "The Long Calm")
    assert chapter_label("c2", chs) == 'ch 2 "The Long Calm"'


def test_chapter_label_falls_back_to_raw_id_only_when_unknown():
    assert chapter_label("ghost", _chapters("One")) == "ghost"


def test_shape_tab_empty_is_one_dim_line_no_data():
    tab = shape_tab([], _chapters("One"))
    assert tab.tensions == []
    assert tab.meta.plain == SHAPE_EMPTY
    assert str(tab.meta.style) == "dim"
    assert tab.callouts == [] and tab.alarm_count == 0


def test_shape_tab_single_score_axis_and_pacing():
    tab = shape_tab(
        [StructureScore(chapter_id="c1", tension=0.6, pacing_label="rising")],
        _chapters("One"),
    )
    assert tab.tensions == [0.6]
    assert tab.meta.plain == "ch 1 · pacing: rising"
    assert tab.callouts == [] and tab.alarm_count == 0


def test_shape_tab_orders_tensions_by_chapter_position_not_score_order():
    chs = _chapters("One", "Two", "Three")
    scores = [
        StructureScore(chapter_id="c3", tension=0.9, pacing_label="climax"),
        StructureScore(chapter_id="c1", tension=0.2, pacing_label="calm"),
        StructureScore(chapter_id="c2", tension=0.5, pacing_label="rising"),
    ]
    tab = shape_tab(scores, chs)
    assert tab.tensions == [0.2, 0.5, 0.9]
    assert tab.meta.plain == "ch 1 ▸ ch 3 · pacing: climax"


def test_shape_tab_sag_callout_names_chapter_title_in_alarm_style():
    chs = _chapters("One", "Two", "The Long Calm")
    scores = [
        StructureScore(chapter_id=f"c{i + 1}", tension=t, pacing_label="")
        for i, t in enumerate([0.6, 0.6, 0.1])
    ]
    tab = shape_tab(scores, chs)
    assert len(tab.callouts) == 1 and tab.alarm_count == 1
    assert tab.callouts[0].plain == '⚠ sag: ch 3 "The Long Calm"'
    assert str(tab.callouts[0].style) == ALARM_STYLE


def test_shape_tab_spike_callout():
    chs = _chapters("One", "Two", "The Break")
    scores = [
        StructureScore(chapter_id=f"c{i + 1}", tension=t, pacing_label="")
        for i, t in enumerate([0.2, 0.2, 0.9])
    ]
    tab = shape_tab(scores, chs)
    assert tab.callouts[0].plain == '⚠ spike: ch 3 "The Break"'


def test_shape_tab_respects_explicit_delta():
    # M5.3: the sag/spike delta always flows in as a parameter (the app
    # passes settings.sag_spike_delta) — never re-typed inside brain_model.
    chs = _chapters("One", "Two")
    scores = [
        StructureScore(chapter_id="c1", tension=0.6, pacing_label=""),
        StructureScore(chapter_id="c2", tension=0.2, pacing_label=""),
    ]
    assert shape_tab(scores, chs).callouts == []  # default SAG_SPIKE_DELTA (0.3): quiet
    tight = shape_tab(scores, chs, delta=0.1)     # ±0.2 from the mean now flags both
    assert [c.plain for c in tight.callouts] == [
        '⚠ spike: ch 1 "One"',
        '⚠ sag: ch 2 "Two"',
    ]
    assert tight.alarm_count == 2


def test_shape_tab_score_for_unknown_chapter_keeps_its_data_at_the_end():
    chs = _chapters("One")
    scores = [
        StructureScore(chapter_id="ghost", tension=0.9, pacing_label=""),
        StructureScore(chapter_id="c1", tension=0.2, pacing_label="calm"),
    ]
    tab = shape_tab(scores, chs)
    assert tab.tensions == [0.2, 0.9]


@given(st.lists(st.floats(min_value=0.0, max_value=1.0), min_size=1, max_size=12))
def test_shape_tab_keeps_every_point_and_alarm_count_matches_callouts(tensions):
    chs = [Chapter(id=f"c{i}", title=f"T{i}", prose="p") for i in range(len(tensions))]
    scores = [
        StructureScore(chapter_id=f"c{i}", tension=t, pacing_label="")
        for i, t in enumerate(tensions)
    ]
    tab = shape_tab(scores, chs)
    assert len(tab.tensions) == len(tensions)
    assert tab.alarm_count == len(tab.callouts)


def test_thread_line_stale_names_last_touched_chapter_and_gap():
    chs = _chapters("One", "Two", "Three", "Four", "Five")
    t = ThreadRecord(id="the-locket", name="The Locket", state=ThreadState.planted, last_chapter_id="c1")
    line = thread_line(t, chs)
    assert line.plain == "⚠ The Locket · stale — last touched ch 1, 4 chapters ago"
    assert str(line.style) == ALARM_STYLE
    assert "the-locket" not in line.plain


def test_thread_line_stale_with_no_known_chapter_reads_untouched():
    chs = _chapters("One", "Two", "Three")
    t = ThreadRecord(id="t", name="The Boy's Gift", state=ThreadState.planted, last_chapter_id="")
    assert thread_line(t, chs).plain == "⚠ The Boy's Gift · stale — untouched for 3 chapters"


def test_thread_line_live_shows_name_and_state_no_id():
    chs = _chapters("One")
    t = ThreadRecord(id="t", name="Fresh", state=ThreadState.touched, last_chapter_id="c1")
    assert thread_line(t, chs).plain == "· Fresh · touched"


def test_thread_line_terminal_is_dim_and_never_stale():
    chs = _chapters("One", "Two", "Three", "Four", "Five")
    t = ThreadRecord(id="t", name="Closed", state=ThreadState.paid_off, last_chapter_id="c1")
    line = thread_line(t, chs)
    assert line.plain == "✓ Closed · paid_off"
    assert "stale" not in line.plain
    assert str(line.style) == "dim"


def test_threads_tab_pins_stale_first_then_open_then_folds_terminal():
    chs = _chapters("One", "Two", "Three", "Four", "Five")
    threads = [
        ThreadRecord(id="a", name="Open A", state=ThreadState.touched, last_chapter_id="c5"),
        ThreadRecord(id="b", name="Done B", state=ThreadState.paid_off, last_chapter_id="c2"),
        ThreadRecord(id="c", name="Stale C", state=ThreadState.planted, last_chapter_id="c1"),
        ThreadRecord(id="d", name="Gone D", state=ThreadState.abandoned, last_chapter_id="c1"),
        ThreadRecord(id="e", name="Done E", state=ThreadState.paid_off, last_chapter_id="c3"),
    ]
    tab = threads_tab(threads, chs)
    plains = [line.plain for line in tab.lines]
    assert plains[0] == "⚠ Stale C · stale — last touched ch 1, 4 chapters ago"
    assert plains[1] == "· Open A · touched"
    assert plains[2] == "✓ 2 paid off · 1 abandoned"
    assert len(plains) == 3
    assert tab.alarm_count == 1
    assert str(tab.lines[2].style) == "dim"


def test_threads_tab_fold_line_omits_zero_parts():
    chs = _chapters("One")
    threads = [ThreadRecord(id="b", name="Done B", state=ThreadState.paid_off, last_chapter_id="c1")]
    tab = threads_tab(threads, chs)
    assert [line.plain for line in tab.lines] == ["✓ 1 paid off"]


def test_thread_line_and_threads_tab_respect_explicit_threshold():
    # M5.3: the staleness threshold always flows in as a parameter (the app
    # passes settings.staleness_threshold_chapters) — never re-typed here.
    # Carries forward test_thread_board_line_respects_explicit_threshold.
    chs = _chapters("One", "Two", "Three")  # two chapters elapsed since c1
    t = ThreadRecord(id="t", name="T", state=ThreadState.planted, last_chapter_id="c1")
    assert "stale" not in thread_line(t, chs).plain        # default threshold (3)
    assert "stale" in thread_line(t, chs, threshold=2).plain
    tab = threads_tab([t], chs, threshold=2)
    assert tab.alarm_count == 1
    assert tab.lines[0].plain == "⚠ T · stale — last touched ch 1, 2 chapters ago"


def test_threads_tab_empty_state():
    tab = threads_tab([], [])
    assert [line.plain for line in tab.lines] == [THREADS_EMPTY]
    assert str(tab.lines[0].style) == "dim"
    assert tab.alarm_count == 0


@given(st.lists(st.sampled_from(list(ThreadState)), max_size=8))
def test_threads_tab_alarm_count_matches_alarm_lines_and_stale_pinned_first(states):
    chs = _chapters("One", "Two", "Three", "Four")
    threads = [
        ThreadRecord(id=f"t{i}", name=f"T{i}", state=s, last_chapter_id="")
        for i, s in enumerate(states)
    ]
    tab = threads_tab(threads, chs)
    alarm_flags = [line.plain.startswith("⚠") for line in tab.lines]
    assert tab.alarm_count == sum(alarm_flags)
    # every alarm line precedes every non-alarm line
    assert alarm_flags == sorted(alarm_flags, reverse=True)
