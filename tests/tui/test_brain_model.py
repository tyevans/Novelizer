from hypothesis import given, strategies as st

from novelizer.store.models import (
    BeatRecord,
    BlueprintRecord,
    Chapter,
    ChapterBriefRecord,
    StructureScore,
    ThreadRecord,
    ThreadState,
)
from novelizer.tui.widgets.brain_model import (
    NAME_WIDTH,
    OUTLINE_EMPTY,
    SHAPE_EMPTY,
    SHAPE_GUTTER,
    SPARK_LEVELS,
    THREADS_EMPTY,
    WARN_STYLE,
    age_bar,
    chapter_label,
    chapter_number,
    outline_tab,
    shape_tab,
    spark_char,
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
    assert tab.spark is None
    assert tab.markers is None


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


def test_spark_char_maps_tension_onto_the_eight_block_levels():
    assert spark_char(0.0) == "▁"
    assert spark_char(1.0) == "█"
    assert spark_char(0.6) == "▅"
    assert spark_char(-3.0) == "▁"   # clamped low
    assert spark_char(9.0) == "█"    # clamped high


def test_shape_tab_spark_is_one_cell_per_chapter_after_the_gutter():
    chs = _chapters("One", "Two", "Three")
    scores = [
        StructureScore(chapter_id=f"c{i + 1}", tension=t, pacing_label="")
        for i, t in enumerate([0.3, 0.5, 0.7])
    ]
    tab = shape_tab(scores, chs)
    assert tab.spark.plain == SHAPE_GUTTER + "▃▅▆"
    assert tab.markers is None                    # quiet story: no marker row
    assert (SHAPE_GUTTER, "dim") in [
        (tab.spark.plain[s.start:s.end], str(s.style)) for s in tab.spark.spans
    ]
    glyph_start = len(SHAPE_GUTTER)
    assert not any(s.end > glyph_start for s in tab.spark.spans)  # glyphs carry no style span


def test_shape_tab_marker_row_aligns_alarm_glyphs_under_flagged_chapters():
    chs = _chapters("One", "Two", "The Long Calm")
    scores = [
        StructureScore(chapter_id=f"c{i + 1}", tension=t, pacing_label="")
        for i, t in enumerate([0.6, 0.6, 0.1])    # c3 sags
    ]
    tab = shape_tab(scores, chs)
    assert tab.markers.plain == " " * len(SHAPE_GUTTER) + "  ⚠"
    marker_spans = [
        (tab.markers.plain[s.start:s.end], str(s.style)) for s in tab.markers.spans
    ]
    assert ("⚠", ALARM_STYLE) in marker_spans


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
    assert len(tab.spark.plain) == len(SHAPE_GUTTER) + len(tensions)
    assert tab.markers is None or len(tab.markers.plain) == len(tab.spark.plain)


def test_shape_tab_no_blueprint_target_is_none_and_output_unchanged():
    chs = _chapters("One", "Two", "Three")
    scores = [
        StructureScore(chapter_id=f"c{i + 1}", tension=t, pacing_label="")
        for i, t in enumerate([0.3, 0.5, 0.7])
    ]
    tab = shape_tab(scores, chs)
    assert tab.target is None
    baseline = shape_tab(scores, chs, blueprint=None, beats=[])
    assert baseline.target is None
    assert baseline.spark.plain == tab.spark.plain
    assert baseline.callouts == tab.callouts
    assert baseline.alarm_count == tab.alarm_count


def test_shape_tab_with_blueprint_adds_target_row_labeled_plan():
    chs = _chapters("One", "Two", "Three")
    scores = [
        StructureScore(chapter_id=f"c{i + 1}", tension=t, pacing_label="")
        for i, t in enumerate([0.3, 0.5, 0.7])
    ]
    blueprint = BlueprintRecord(id="b1", framework="three_act", target_chapter_count=3)
    beats: list[BeatRecord] = []
    tab = shape_tab(scores, chs, blueprint=blueprint, beats=beats)
    assert tab.target is not None
    assert "plan" in tab.target.plain
    assert len(tab.target.plain) == len(tab.spark.plain)


def test_shape_tab_deviation_from_target_adds_off_plan_callout():
    chs = _chapters("One", "Two", "Three")
    # Blueprint anchors: (1, 0.3) implicit start, (3, 0.5) implicit end.
    # c1 tension of 0.95 deviates hard from the 0.3 target.
    scores = [
        StructureScore(chapter_id="c1", tension=0.95, pacing_label=""),
        StructureScore(chapter_id="c2", tension=0.4, pacing_label=""),
        StructureScore(chapter_id="c3", tension=0.5, pacing_label=""),
    ]
    blueprint = BlueprintRecord(id="b1", framework="three_act", target_chapter_count=3)
    beats: list[BeatRecord] = []
    tab = shape_tab(scores, chs, delta=0.25, blueprint=blueprint, beats=beats)
    assert any(c.plain == '⚠ tension off-plan: ch 1 "One"' for c in tab.callouts)
    assert any(str(c.style) == ALARM_STYLE for c in tab.callouts if "off-plan" in c.plain)
    assert tab.alarm_count == len(tab.callouts)


def test_age_bar_scales_fill_and_heat_with_elapsed_over_threshold():
    assert age_bar(0, 3).plain == "▱▱▱▱▱"
    assert str(age_bar(0, 3).style) == "dim"
    assert age_bar(1, 3).plain == "▰▰▱▱▱"          # round(1/3 · 5) = 2
    assert age_bar(2, 3).plain == "▰▰▰▱▱"          # round(2/3 · 5) = 3
    assert str(age_bar(2, 3).style) == WARN_STYLE  # 2/3 ≥ 0.6: warming
    assert age_bar(3, 3).plain == "▰▰▰▰▰"
    assert str(age_bar(3, 3).style) == ALARM_STYLE
    assert age_bar(9, 3).plain == "▰▰▰▰▰"          # clamped past threshold
    assert age_bar(0, 0).plain == "▰▰▰▰▰"          # degenerate threshold: elapsed >= threshold, full
    assert str(age_bar(0, 0).style) == ALARM_STYLE


def test_thread_line_stale_names_last_touched_chapter_and_gap():
    chs = _chapters("One", "Two", "Three", "Four", "Five")
    t = ThreadRecord(id="the-locket", name="The Locket", state=ThreadState.planted, last_chapter_id="c1")
    line = thread_line(t, chs)
    assert line.plain == (
        "⚠ " + "The Locket".ljust(20) + "  ▰▰▰▰▰  stale — last touched ch 1, 4 chapters ago"
    )
    assert str(line.style) == ALARM_STYLE
    assert "the-locket" not in line.plain


def test_thread_line_stale_with_no_known_chapter_reads_untouched():
    chs = _chapters("One", "Two", "Three")
    t = ThreadRecord(id="t", name="The Boy's Gift", state=ThreadState.planted, last_chapter_id="")
    line = thread_line(t, chs)
    assert line.plain == (
        "⚠ " + "The Boy's Gift".ljust(20) + "  ▰▰▰▰▰  stale — untouched for 3 chapters"
    )


def test_thread_line_live_shows_name_bar_state_and_chapter_no_id():
    chs = _chapters("One")
    t = ThreadRecord(id="t", name="Fresh", state=ThreadState.touched, last_chapter_id="c1")
    line = thread_line(t, chs)
    assert line.plain == "· " + "Fresh".ljust(20) + "  ▱▱▱▱▱  touched — ch 1"
    bar_spans = [(line.plain[s.start:s.end], str(s.style)) for s in line.spans]
    assert ("▱▱▱▱▱", "dim") in bar_spans


def test_thread_line_live_warming_bar_is_warn_styled_and_long_names_clip():
    chs = _chapters("One", "Two", "Three")          # elapsed since c1 = 2, threshold 3
    t = ThreadRecord(id="t", name="The Unraveling of Everything",
                     state=ThreadState.touched, last_chapter_id="c1")
    line = thread_line(t, chs)
    assert line.plain.startswith("· The Unraveling of E…")   # clipped to NAME_WIDTH
    bar_spans = [(line.plain[s.start:s.end], str(s.style)) for s in line.spans]
    assert ("▰▰▰▱▱", WARN_STYLE) in bar_spans


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
    assert plains[0] == "⚠ " + "Stale C".ljust(20) + "  ▰▰▰▰▰  stale — last touched ch 1, 4 chapters ago"
    assert plains[1] == "· " + "Open A".ljust(20) + "  ▱▱▱▱▱  touched — ch 5"
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
    assert tab.lines[0].plain == "⚠ " + "T".ljust(20) + "  ▰▰▰▰▰  stale — last touched ch 1, 2 chapters ago"


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


from novelizer.store.models import CausalEdgeRecord, Character, PromiseRecord, PromiseState, SecretRecord
from novelizer.tui.widgets.brain_model import (
    CELL_GLYPHS,
    CAUSEWAY_EMPTY,
    SECRETS_EMPTY,
    TITLE_WIDTH,
    alarm_strip,
    causeway_tab,
    char_initials,
    index_segment,
    matrix_header,
    secret_row,
    secrets_tab,
    spread_meter,
)


# --- M7: window badges + ledger section + alarms -------------------------


def test_thread_line_due_badge_before_window_is_dim():
    chs = _chapters("One", "Two")   # now = 2, window 3-5: before window
    t = ThreadRecord(id="t", name="Beacon", state=ThreadState.touched,
                      last_chapter_id="c1", window_lo=3, window_hi=5)
    line = thread_line(t, chs)
    assert line.plain.endswith("· due ch3-5")
    badge_start = line.plain.index("due ch3-5")
    assert ("due ch3-5", "dim") in [
        (line.plain[s.start:s.end], str(s.style)) for s in line.spans
        if s.start == badge_start
    ]


def test_thread_line_due_badge_inside_window_is_dim():
    chs = _chapters("One", "Two", "Three", "Four")  # now = 4, window 3-5: inside
    t = ThreadRecord(id="t", name="Beacon", state=ThreadState.touched,
                      last_chapter_id="c1", window_lo=3, window_hi=5)
    line = thread_line(t, chs)
    assert line.plain.endswith("· due ch3-5")


def test_thread_line_overdue_badge_past_window_hi_is_alarm_styled():
    chs = _chapters("One", "Two", "Three", "Four", "Five", "Six")  # now = 6 > 5
    t = ThreadRecord(id="t", name="Beacon", state=ThreadState.touched,
                      last_chapter_id="c1", window_lo=3, window_hi=5)
    line = thread_line(t, chs)
    assert line.plain.endswith("· OVERDUE ch5")
    assert str(line.style) == ALARM_STYLE


def test_thread_line_no_badge_when_window_unset():
    chs = _chapters("One")
    t = ThreadRecord(id="t", name="Beacon", state=ThreadState.touched, last_chapter_id="c1")
    line = thread_line(t, chs)
    assert "due" not in line.plain and "OVERDUE" not in line.plain


def _promise(id, name, kind="foreshadow", state=PromiseState.open, **kw):
    return PromiseRecord(id=id, name=name, kind=kind, state=state, **kw)


def test_threads_tab_ledger_section_absent_when_no_open_promises():
    chs = _chapters("One")
    tab = threads_tab([], chs, promises=[], secrets=[])
    assert "Ledger" not in [line.plain for line in tab.lines]


def test_threads_tab_ledger_header_and_open_promise_line():
    chs = _chapters("One", "Two")
    promises = [_promise("p1", "The Locket's Origin")]
    tab = threads_tab([], chs, promises=promises, secrets=[])
    plains = [line.plain for line in tab.lines]
    assert "Ledger" in plains
    idx = plains.index("Ledger")
    assert plains[idx + 1] == "◇ The Locket's Origin"


def test_threads_tab_ledger_red_herring_tag():
    chs = _chapters("One")
    promises = [_promise("p1", "The Wrong Trail", kind="red_herring")]
    tab = threads_tab([], chs, promises=promises, secrets=[])
    plains = [line.plain for line in tab.lines]
    assert "◇ The Wrong Trail (red herring)" in plains


def test_threads_tab_ledger_window_badges_match_thread_rules():
    chs = _chapters("One", "Two", "Three", "Four", "Five", "Six")  # now = 6
    promises = [
        _promise("due", "Due One", window_lo=3, window_hi=8),
        _promise("over", "Over One", window_lo=1, window_hi=4),
    ]
    tab = threads_tab([], chs, promises=promises, secrets=[])
    plains = [line.plain for line in tab.lines]
    assert "◇ Over One · OVERDUE ch4" in plains
    assert "◇ Due One · due ch3-8" in plains


def test_threads_tab_ledger_overdue_pinned_first_in_alarm_style():
    chs = _chapters("One", "Two", "Three", "Four", "Five")  # now = 5
    promises = [
        _promise("a", "Fresh A", window_lo=1, window_hi=10),
        _promise("b", "Late B", window_lo=1, window_hi=2),
    ]
    tab = threads_tab([], chs, promises=promises, secrets=[])
    ledger_lines = [line for line in tab.lines if line.plain.startswith("◇")]
    assert ledger_lines[0].plain == "◇ Late B · OVERDUE ch2"
    assert str(ledger_lines[0].style) == ALARM_STYLE
    assert ledger_lines[1].plain == "◇ Fresh A · due ch1-10"


def test_threads_tab_ledger_folds_paid_and_released_promises():
    chs = _chapters("One")
    promises = [
        _promise("a", "Paid A", state=PromiseState.paid),
        _promise("b", "Paid B", state=PromiseState.paid),
        _promise("c", "Released C", state=PromiseState.released),
        _promise("d", "Open D"),
    ]
    tab = threads_tab([], chs, promises=promises, secrets=[])
    plains = [line.plain for line in tab.lines]
    assert "✓ 2 paid · 1 released" in plains
    fold_line = tab.lines[plains.index("✓ 2 paid · 1 released")]
    assert str(fold_line.style) == "dim"


def test_threads_tab_congestion_warning_line_and_alarm_contribution():
    chs = _chapters(*[f"C{i}" for i in range(1, 6)])  # now = 5
    threads = [
        ThreadRecord(id="a", name="A", state=ThreadState.touched, last_chapter_id="c1",
                     window_lo=19, window_hi=21),
        ThreadRecord(id="b", name="B", state=ThreadState.touched, last_chapter_id="c1",
                     window_lo=19, window_hi=21),
        ThreadRecord(id="c", name="C", state=ThreadState.touched, last_chapter_id="c1",
                     window_lo=19, window_hi=21),
    ]
    tab = threads_tab(threads, chs, promises=[], secrets=[])
    warn_lines = [line for line in tab.lines if line.plain.startswith("⚠ ")]
    assert any(l.plain == "⚠ 3 resolutions target ch19-21" for l in warn_lines)
    congestion_line = next(l for l in warn_lines if l.plain == "⚠ 3 resolutions target ch19-21")
    assert str(congestion_line.style) == WARN_STYLE


def test_threads_tab_alarm_count_sums_all_sources():
    chs = _chapters("One", "Two", "Three", "Four", "Five", "Six")  # now = 6
    threads = [
        # stale (untouched 6 chapters >= threshold 3)
        ThreadRecord(id="s", name="Stale", state=ThreadState.planted, last_chapter_id=""),
        # overdue resolution, but recently touched so not also stale (window_hi=4 < now)
        ThreadRecord(id="o", name="Overdue", state=ThreadState.touched, last_chapter_id="c5",
                     window_lo=1, window_hi=4),
    ]
    promises = [
        # overdue promise (window_hi=3 < now)
        _promise("p", "Late Promise", window_lo=1, window_hi=3),
    ]
    tab = threads_tab(threads, chs, promises=promises, secrets=[])
    # 1 stale + 1 overdue resolution + 1 overdue promise + 0 congestion spans
    assert tab.alarm_count == 3


def test_threads_tab_empty_state_unchanged_when_no_threads_and_no_promises():
    tab = threads_tab([], [], promises=[], secrets=[])
    assert [line.plain for line in tab.lines] == [THREADS_EMPTY]
    assert tab.alarm_count == 0


def test_threads_tab_renders_a_line_per_overdue_reveal():
    chs = _chapters("One", "Two", "Three")  # now = 3
    secrets = [SecretRecord(id="s", title="The Forged Letter", reveal_window_lo=1, reveal_window_hi=2)]
    tab = threads_tab([], chs, promises=[], secrets=secrets)
    plains = [line.plain for line in tab.lines]
    assert any(l.startswith("⚠ reveal overdue: 'The Forged Letter'") and "ch2" in l for l in plains)
    overdue_line = next(l for l in tab.lines if l.plain.startswith("⚠ reveal overdue:"))
    assert str(overdue_line.style) == ALARM_STYLE
    assert tab.alarm_count == 1


def test_threads_tab_secrets_only_story_with_congested_reveal_windows_shows_warning_not_empty_state():
    chs = _chapters(*[f"C{i}" for i in range(1, 6)])  # now = 5
    secrets = [
        SecretRecord(id="a", title="A", reveal_window_lo=19, reveal_window_hi=21),
        SecretRecord(id="b", title="B", reveal_window_lo=19, reveal_window_hi=21),
        SecretRecord(id="c", title="C", reveal_window_lo=19, reveal_window_hi=21),
    ]
    tab = threads_tab([], chs, promises=[], secrets=secrets)
    plains = [line.plain for line in tab.lines]
    assert THREADS_EMPTY not in plains
    assert "⚠ 3 resolutions target ch19-21" in plains
    assert tab.alarm_count == 1


def test_cell_glyphs_cover_exactly_the_real_cell_states():
    # knowledge_cell_state's actual codomain — there is no "suspected" state.
    assert CELL_GLYPHS == {"known": "●", "unknown": "○", "revealed": "✓"}


def test_char_initials_short_names_from_words():
    assert char_initials("Elara") == "E"
    assert char_initials("The Boy") == "TB"
    assert char_initials("Mara Vane Kestrel") == "MV"
    assert char_initials("") == "?"


def test_matrix_header_aligns_initials_after_title_gutter():
    header = matrix_header([Character(id="elara", name="Elara"), Character(id="boy", name="The Boy")])
    assert header.plain == " " * TITLE_WIDTH + "E  TB"
    assert str(header.style) == "dim"


def test_spread_meter_heats_as_spread_approaches_everyone():
    assert spread_meter(0, 4).plain == "○○○○ 0/4"
    assert str(spread_meter(0, 4).style) == "dim"
    assert spread_meter(2, 4).plain == "●●○○ 2/4"
    assert str(spread_meter(2, 4).style) == WARN_STYLE   # half know: warming
    assert spread_meter(3, 4).plain == "●●●○ 3/4"
    assert str(spread_meter(3, 4).style) == ALARM_STYLE  # one reveal from public
    assert str(spread_meter(4, 4).style) == ALARM_STYLE  # everyone knows
    assert str(spread_meter(1, 4).style) == "dim"        # 1/4: still quiet
    assert str(spread_meter(1, 1).style) == ALARM_STYLE  # the whole cast of one knows


def test_secret_row_glyph_cells_align_under_header_and_show_spread_meter():
    chars = [Character(id="elara", name="Elara"), Character(id="boy", name="The Boy")]
    secret = SecretRecord(id="the-heir-lives", title="The Heir Lives")
    matrix = {"the-heir-lives": {"revealed": False, "known_by": {"elara"}}}
    row = secret_row(secret, chars, matrix)
    assert row.plain == "The Heir Lives".ljust(TITLE_WIDTH) + "●  ○" + "   ●○ 1/2"
    assert "the-heir-lives" not in row.plain


def test_secret_row_known_to_no_one_has_a_cold_meter():
    secret = SecretRecord(id="s", title="The Map Is Forged")
    matrix = {"s": {"revealed": False, "known_by": set()}}
    row = secret_row(secret, [Character(id="k", name="Kestrel")], matrix)
    assert row.plain.endswith("○ 0/1")
    meter_spans = [(row.plain[s.start:s.end], str(s.style)) for s in row.spans]
    assert ("○ 0/1", "dim") in meter_spans


def test_secret_row_two_of_three_know_is_leak_hot():
    chars = [Character(id="a", name="Ana"), Character(id="b", name="Bram"), Character(id="c", name="Cole")]
    secret = SecretRecord(id="s", title="The Tide Debt")
    matrix = {"s": {"revealed": False, "known_by": {"a", "b"}}}
    row = secret_row(secret, chars, matrix)
    assert row.plain.endswith("●●○ 2/3")
    meter_spans = [(row.plain[s.start:s.end], str(s.style)) for s in row.spans]
    assert ("●●○ 2/3", ALARM_STYLE) in meter_spans


def test_secret_row_clips_long_titles():
    secret = SecretRecord(id="s", title="A" * 40)
    row = secret_row(secret, [], {"s": {"revealed": False, "known_by": set()}})
    assert row.plain.startswith("A" * (TITLE_WIDTH - 1) + "…")


def test_secrets_tab_folds_revealed_and_renders_matrix_for_unrevealed():
    chars = [Character(id="elara", name="Elara")]
    secrets = [
        SecretRecord(id="s1", title="The Heir Lives"),
        SecretRecord(id="s2", title="The Map Is Forged", revealed=True),
        SecretRecord(id="s3", title="The Tide Debt", revealed=True),
    ]
    matrix = {
        "s1": {"revealed": False, "known_by": {"elara"}},
        "s2": {"revealed": True, "known_by": set()},
        "s3": {"revealed": True, "known_by": set()},
    }
    tab = secrets_tab(secrets, chars, matrix)
    plains = [line.plain for line in tab.lines]
    assert plains[0] == " " * TITLE_WIDTH + "E"
    assert plains[1].startswith("The Heir Lives")
    assert plains[2] == "✓ revealed (2)"
    assert len(plains) == 3
    assert tab.alarm_count == 0
    assert str(tab.lines[2].style) == "dim"


def test_secrets_tab_all_revealed_is_just_the_fold_line():
    secrets = [SecretRecord(id="s", title="Old News", revealed=True)]
    tab = secrets_tab(secrets, [], {"s": {"revealed": True, "known_by": set()}})
    assert [line.plain for line in tab.lines] == ["✓ revealed (1)"]


def test_secrets_tab_empty_state():
    tab = secrets_tab([], [], {})
    assert [line.plain for line in tab.lines] == [SECRETS_EMPTY]
    assert str(tab.lines[0].style) == "dim"


@given(n_secrets=st.integers(0, 5), n_chars=st.integers(0, 5))
def test_matrix_rows_cover_every_secret_by_character_pair(n_secrets, n_chars):
    chars = [Character(id=f"ch{i}", name=f"N{i}") for i in range(n_chars)]
    secrets = [SecretRecord(id=f"s{i}", title=f"S{i}") for i in range(n_secrets)]
    matrix = {s.id: {"revealed": False, "known_by": set()} for s in secrets}
    tab = secrets_tab(secrets, chars, matrix)
    rows = [line for line in tab.lines if line.plain.startswith("S")]
    assert len(rows) == n_secrets
    for row in rows:
        cells = row.plain.count("○") + row.plain.count("●")
        assert cells == (2 * n_chars if n_chars else 0)   # matrix cells + meter cells
        if n_chars:
            assert row.plain.endswith(f"0/{n_chars}")


def test_causeway_line_uses_chapter_titles_and_arrow_never_ids():
    chs = _chapters("The Gift", "The Price")
    edges = [CausalEdgeRecord(cause_chapter_id="c1", effect_chapter_id="c2", note="sets up the reveal")]
    tab = causeway_tab(edges, chs)
    assert tab.lines[0].plain == 'ch 1 "The Gift" ──▶ ch 2 "The Price": sets up the reveal'
    assert tab.alarm_count == 0
    assert "c1" not in tab.lines[0].plain and "c2" not in tab.lines[0].plain


def test_causeway_edge_without_note_has_no_colon():
    chs = _chapters("One", "Two")
    tab = causeway_tab([CausalEdgeRecord(cause_chapter_id="c1", effect_chapter_id="c2")], chs)
    assert tab.lines[0].plain == 'ch 1 "One" ──▶ ch 2 "Two"'


def test_causeway_ordering_paradox_edge_is_alarm_with_marker():
    chs = _chapters("One", "Two")
    tab = causeway_tab(
        [CausalEdgeRecord(cause_chapter_id="c2", effect_chapter_id="c1", note="the fall")], chs
    )
    assert tab.lines[0].plain == 'ch 2 "Two" ──▶ ch 1 "One": the fall  ⚠ PARADOX'
    assert str(tab.lines[0].style) == ALARM_STYLE
    assert tab.alarm_count == 1


def test_causeway_cycle_paradox_flags_both_directions():
    chs = _chapters("One", "Two", "Three")
    edges = [
        CausalEdgeRecord(cause_chapter_id="c1", effect_chapter_id="c2"),
        CausalEdgeRecord(cause_chapter_id="c2", effect_chapter_id="c1"),
    ]
    tab = causeway_tab(edges, chs)
    assert tab.alarm_count == 2
    assert all("⚠ PARADOX" in line.plain for line in tab.lines)


def test_causeway_unknown_chapter_id_falls_back_to_raw_id():
    chs = _chapters("One")
    tab = causeway_tab([CausalEdgeRecord(cause_chapter_id="ghost", effect_chapter_id="c1")], chs)
    assert tab.lines[0].plain == 'ghost ──▶ ch 1 "One"'


def test_causeway_sorts_by_chapter_position():
    chs = _chapters("One", "Two", "Three")
    edges = [
        CausalEdgeRecord(cause_chapter_id="c2", effect_chapter_id="c3"),
        CausalEdgeRecord(cause_chapter_id="c1", effect_chapter_id="c2"),
    ]
    tab = causeway_tab(edges, chs)
    assert tab.lines[0].plain.startswith('ch 1 "One"')
    assert tab.lines[1].plain.startswith('ch 2 "Two"')


def test_causeway_empty_state():
    tab = causeway_tab([], [])
    assert [line.plain for line in tab.lines] == [CAUSEWAY_EMPTY]
    assert str(tab.lines[0].style) == "dim"


def test_causeway_paradoxes_sort_above_normal_edges():
    chs = _chapters("One", "Two", "Three")
    edges = [
        CausalEdgeRecord(cause_chapter_id="c1", effect_chapter_id="c2"),   # normal, earliest
        CausalEdgeRecord(cause_chapter_id="c3", effect_chapter_id="c2"),   # paradox, latest
    ]
    tab = causeway_tab(edges, chs)
    assert "⚠ PARADOX" in tab.lines[0].plain
    assert tab.lines[0].plain.startswith('ch 3 "Three"')
    assert tab.lines[1].plain.startswith('ch 1 "One"')


def test_causeway_normal_edge_arrow_is_dim():
    chs = _chapters("One", "Two")
    tab = causeway_tab([CausalEdgeRecord(cause_chapter_id="c1", effect_chapter_id="c2")], chs)
    spans = [(tab.lines[0].plain[s.start:s.end], str(s.style)) for s in tab.lines[0].spans]
    assert ("──▶", "dim") in spans


def test_alarm_strip_matches_spec_format():
    assert alarm_strip(1, 2, 0, 1, 0, 0).plain == "Shape ⚠1 · Threads ⚠2 · Secrets · Cause ⚠1 · Outline · Arcs"


def test_alarm_strip_quiet_shows_bare_labels():
    assert alarm_strip(0, 0, 0, 0, 0, 0).plain == "Shape · Threads · Secrets · Cause · Outline · Arcs"


def test_alarm_strip_outline_segment_shows_count():
    assert alarm_strip(0, 0, 0, 0, 3, 0).plain == "Shape · Threads · Secrets · Cause · Outline ⚠3 · Arcs"


def test_alarm_strip_arcs_segment_shows_count():
    assert alarm_strip(0, 0, 0, 0, 0, 2).plain == "Shape · Threads · Secrets · Cause · Outline · Arcs ⚠2"


def test_alarm_strip_zero_lag_shows_no_index_segment():
    assert alarm_strip(0, 0, 0, 0, 0, 0, lag=0).plain == "Shape · Threads · Secrets · Cause · Outline · Arcs"


def test_alarm_strip_nonzero_lag_appends_index_segment():
    assert (
        alarm_strip(0, 0, 0, 0, 0, 0, lag=12).plain
        == "Shape · Threads · Secrets · Cause · Outline · Arcs · Index ⚠12 behind"
    )


def test_alarm_strip_lag_segment_is_alarm_styled():
    strip = alarm_strip(0, 0, 0, 0, 0, 0, lag=3)
    spans = [(strip.plain[s.start:s.end], str(s.style)) for s in strip.spans]
    assert (" ⚠3 behind", ALARM_STYLE) in spans


# --- the semantic index's size, three honest states ----------------------
# Derivation/formatting is pinned here, on the pure segment, so the
# three-state logic does not depend on the widget rendering it.


def test_index_segment_is_empty_when_nothing_to_report():
    assert index_segment().plain == ""


def test_index_segment_shows_a_populated_count():
    assert index_segment(1284).plain == "Index 1284"


def test_index_segment_populated_count_is_quiet():
    seg = index_segment(1284)
    assert all(str(s.style) == "dim" for s in seg.spans)


def test_index_segment_zero_documents_reads_as_an_alarm():
    assert index_segment(0).plain == "Index ⚠empty"


def test_index_segment_zero_documents_is_alarm_styled():
    seg = index_segment(0)
    spans = [(seg.plain[s.start:s.end], str(s.style)) for s in seg.spans]
    assert (" ⚠empty", ALARM_STYLE) in spans


def test_index_segment_unknown_is_not_an_empty_index():
    # None means "could not be read". Rendering it as ⚠empty would manufacture
    # a false alarm -- the mirror of the bug this readout exists to expose.
    assert index_segment(None).plain == "Index ?"


def test_index_segment_unknown_is_quiet():
    seg = index_segment(None)
    assert all(str(s.style) == "dim" for s in seg.spans)


def test_index_segment_carries_the_count_and_the_lag_together():
    assert index_segment(0, lag=7).plain == "Index ⚠empty ⚠7 behind"


def test_alarm_strip_appends_the_document_count():
    assert (
        alarm_strip(0, 0, 0, 0, 0, 0, docs=1284).plain
        == "Shape · Threads · Secrets · Cause · Outline · Arcs · Index 1284"
    )


def test_alarm_strip_appends_the_empty_index_alarm():
    assert (
        alarm_strip(0, 0, 0, 0, 0, 0, docs=0).plain
        == "Shape · Threads · Secrets · Cause · Outline · Arcs · Index ⚠empty"
    )


def test_alarm_strip_appends_the_unknown_index_marker():
    assert (
        alarm_strip(0, 0, 0, 0, 0, 0, docs=None).plain
        == "Shape · Threads · Secrets · Cause · Outline · Arcs · Index ?"
    )


def test_alarm_strip_shows_a_dead_index_and_its_lag_in_one_segment():
    assert (
        alarm_strip(0, 0, 0, 0, 0, 0, lag=7, docs=0).plain
        == "Shape · Threads · Secrets · Cause · Outline · Arcs · Index ⚠empty ⚠7 behind"
    )


def test_outline_tab_empty_state_no_blueprint():
    tab = outline_tab(None, [], [], [], [])
    assert len(tab.lines) == 1
    assert tab.lines[0].plain == OUTLINE_EMPTY
    assert str(tab.lines[0].style) == "dim"
    assert tab.alarm_count == 0


def test_outline_tab_header_line():
    blueprint = BlueprintRecord(id="b1", framework="three_act", target_chapter_count=20, genre="mystery")
    tab = outline_tab(blueprint, [], [], [], _chapters(*[f"C{i}" for i in range(12)]))
    assert tab.lines[0].plain == "three_act · ch 12/20 · mystery"
    assert str(tab.lines[0].style) == "dim"


def test_outline_tab_header_line_no_genre():
    blueprint = BlueprintRecord(id="b1", framework="three_act", target_chapter_count=20)
    tab = outline_tab(blueprint, [], [], [], _chapters(*[f"C{i}" for i in range(12)]))
    assert tab.lines[0].plain == "three_act · ch 12/20"


def test_outline_tab_header_line_complete_blueprint():
    blueprint = BlueprintRecord(
        id="b1", framework="three_act", target_chapter_count=20, genre="mystery", completed=True,
    )
    tab = outline_tab(blueprint, [], [], [], _chapters(*[f"C{i}" for i in range(20)]))
    assert tab.lines[0].plain == "✓ COMPLETE · three_act · ch 20/20 · mystery"
    assert str(tab.lines[0].style) != "dim"


def test_outline_tab_header_line_not_complete_no_check():
    blueprint = BlueprintRecord(id="b1", framework="three_act", target_chapter_count=20)
    tab = outline_tab(blueprint, [], [], [], _chapters(*[f"C{i}" for i in range(20)]))
    assert "✓ COMPLETE" not in tab.lines[0].plain


def test_outline_tab_beat_strip_one_of_each_glyph():
    blueprint = BlueprintRecord(id="b1", framework="three_act", target_chapter_count=20)
    chs = _chapters(*[f"C{i}" for i in range(20)])
    beats = [
        BeatRecord(id="fulfilled-in", blueprint_id="b1", slug="catalyst", name="Catalyst",
                   ideal_pct=0.10, tolerance_pct=0.05, fulfilled_by_chapter_id="c2"),
        BeatRecord(id="fulfilled-off", blueprint_id="b1", slug="midpoint", name="Midpoint",
                   ideal_pct=0.50, tolerance_pct=0.05, fulfilled_by_chapter_id="c15"),
        BeatRecord(id="late", blueprint_id="b1", slug="climax", name="Climax",
                   ideal_pct=0.90, tolerance_pct=0.05),
        BeatRecord(id="pending", blueprint_id="b1", slug="denouement", name="Denouement",
                   ideal_pct=0.99, tolerance_pct=0.05),
    ]
    tab = outline_tab(blueprint, beats, [], [], chs)
    beat_lines = tab.lines[1:5]
    assert beat_lines[0].plain.startswith("✓ Catalyst @ch")
    assert beat_lines[1].plain.startswith("≈ Midpoint @ch")
    assert beat_lines[2].plain.startswith("! Climax @ch")
    assert str(beat_lines[2].style) == ALARM_STYLE
    assert beat_lines[3].plain.startswith("· Denouement @ch")
    assert tab.alarm_count == 1  # only the late beat


def test_outline_tab_grid_rows_are_non_terminal_threads_only():
    threads = [
        ThreadRecord(id="a", name="Open A", state=ThreadState.touched, last_chapter_id="c1"),
        ThreadRecord(id="b", name="Open B", state=ThreadState.planted, last_chapter_id="c2"),
        ThreadRecord(id="c", name="Done C", state=ThreadState.paid_off, last_chapter_id="c1"),
    ]
    chs = _chapters("One", "Two")
    # No blueprint short-circuits to the empty state, so exercise via a blueprint.
    blueprint = BlueprintRecord(id="b1", framework="three_act", target_chapter_count=2)
    tab = outline_tab(blueprint, [], [], threads, chs)
    grid_lines = tab.lines[1:]
    assert len(grid_lines) == 2
    assert grid_lines[0].plain.startswith("Open A".ljust(NAME_WIDTH))
    assert grid_lines[1].plain.startswith("Open B".ljust(NAME_WIDTH))


def test_outline_tab_grid_window_span_renders_shade():
    thread = ThreadRecord(id="t", name="Waiting", state=ThreadState.touched,
                           last_chapter_id="", window_lo=2, window_hi=4)
    chs = _chapters("One", "Two", "Three", "Four")
    blueprint = BlueprintRecord(id="b1", framework="three_act", target_chapter_count=4)
    tab = outline_tab(blueprint, [], [], [thread], chs)
    row = tab.lines[1].plain
    cells = row[NAME_WIDTH:].split(" ")
    cells = [c for c in cells if c]
    assert cells == ["·", "░", "░", "░"]


def test_outline_tab_grid_future_columns_are_dim():
    thread = ThreadRecord(id="t", name="Waiting", state=ThreadState.touched, last_chapter_id="")
    chs = _chapters("One", "Two")
    briefs = [ChapterBriefRecord(id="br1", target_ordinal=5, goal="push forward")]
    blueprint = BlueprintRecord(id="b1", framework="three_act", target_chapter_count=5)
    tab = outline_tab(blueprint, [], briefs, [thread], chs)
    row = tab.lines[1]
    cell_spans = [s for s in row.spans]
    dim_ranges = [(s.start, s.end) for s in cell_spans if str(s.style) == "dim"]
    # columns 3, 4, 5 (index past len(chapters)=2) render dim.
    assert row.plain[dim_ranges[0][0]:dim_ranges[0][1]] == "·"
    assert len(dim_ranges) == 3


def test_outline_tab_briefs_strip_and_stale_alarm():
    blueprint = BlueprintRecord(id="b1", framework="three_act", target_chapter_count=5)
    chs = _chapters("One", "Two", "Three")
    briefs = [
        ChapterBriefRecord(id="stale", target_ordinal=2, goal="already past"),
        ChapterBriefRecord(id="future", target_ordinal=5, goal="not yet"),
    ]
    tab = outline_tab(blueprint, [], briefs, [], chs)
    stale_line, future_line = tab.lines[-2], tab.lines[-1]
    assert stale_line.plain == "! ch 2: already past"
    assert str(stale_line.style) == ALARM_STYLE
    assert future_line.plain == "ch 5: not yet"
    assert str(future_line.style) == "dim"
    assert tab.alarm_count == 1


def test_outline_tab_alarm_count_sums_late_beats_and_stale_briefs():
    blueprint = BlueprintRecord(id="b1", framework="three_act", target_chapter_count=20)
    chs = _chapters(*[f"C{i}" for i in range(20)])
    beats = [
        BeatRecord(id="late", blueprint_id="b1", slug="climax", name="Climax",
                   ideal_pct=0.90, tolerance_pct=0.05),
    ]
    briefs = [
        ChapterBriefRecord(id="stale1", target_ordinal=1, goal="a"),
        ChapterBriefRecord(id="stale2", target_ordinal=2, goal="b"),
    ]
    tab = outline_tab(blueprint, beats, briefs, [], chs)
    assert tab.alarm_count == 3


def test_outline_tab_ignores_superseded_and_fulfilled_briefs():
    from novelizer.store.models import BriefStatus

    blueprint = BlueprintRecord(id="b1", framework="three_act", target_chapter_count=5)
    chs = _chapters("One", "Two", "Three")
    briefs = [
        ChapterBriefRecord(id="s", target_ordinal=1, goal="a", status=BriefStatus.superseded),
        ChapterBriefRecord(id="f", target_ordinal=1, goal="b", status=BriefStatus.fulfilled),
    ]
    tab = outline_tab(blueprint, [], briefs, [], chs)
    assert len(tab.lines) == 1  # header only, no briefs strip lines
    assert tab.alarm_count == 0


def test_alarm_strip_alarm_segments_are_alarm_styled():
    strip = alarm_strip(1, 0, 0, 0, 0, 0)
    spans = [(strip.plain[s.start:s.end], str(s.style)) for s in strip.spans]
    assert (" ⚠1", ALARM_STYLE) in spans


from novelizer.store.models import ArcPivot, ArcRecord
from novelizer.tui.widgets.brain_model import ARCS_EMPTY, DIM, ArcsTab, arcs_tab


def test_arcs_tab_empty_state():
    tab = arcs_tab([], [], [], [], None)
    assert [t.plain for t in tab.lines] == [ARCS_EMPTY]
    assert tab.lines[0].style == DIM
    assert tab.alarm_count == 0


def test_arcs_tab_healthy_arc_glyph_and_detail_line():
    chars = [Character(id="ch1", name="Elara")]
    chs = _chapters("One", "Two")
    arc = ArcRecord(
        id="a1", character_id="ch1", arc_type="positive", lie="I am unworthy",
        truth="I belong", active=True, resolved=False, advance_count=2, last_chapter_id="c2",
    )
    tab = arcs_tab([arc], chars, chs, [], None)
    assert tab.lines[0].plain == "· Elara · positive"
    assert tab.lines[1].plain == "lie 'I am unworthy' → truth 'I belong' · advances 2 · last ch 2 \"Two\""
    assert tab.lines[1].style == DIM
    assert tab.alarm_count == 0


def test_arcs_tab_stagnant_arc_alarm_glyph():
    from novelizer.brain.arc_alignment import STAGNATION_CHAPTERS

    chars = [Character(id="ch1", name="Elara")]
    chs = _chapters(*[f"Ch{i}" for i in range(STAGNATION_CHAPTERS)])
    arc = ArcRecord(id="a1", character_id="ch1", arc_type="positive", active=True, resolved=False)
    tab = arcs_tab([arc], chars, chs, [], None)
    assert tab.lines[0].plain == "! Elara · positive"
    assert tab.lines[0].style == ALARM_STYLE
    assert tab.alarm_count == 1


def test_arcs_tab_resolved_contradiction_full_and_alarm():
    # A resolved, contradictory arc that is still the character's active arc
    # (not yet superseded by a re-declaration) keeps the alarm.
    chars = [Character(id="ch1", name="Elara")]
    chs = _chapters("One")
    arc = ArcRecord(
        id="a1", character_id="ch1", arc_type="positive", outcome="lie_embraced",
        active=True, resolved=True,
    )
    tab = arcs_tab([arc], chars, chs, [], None)
    assert tab.lines[0].plain == "⚠ Elara · positive"
    assert tab.lines[0].style == ALARM_STYLE
    assert tab.alarm_count == 1


def test_arcs_tab_superseded_resolved_contradiction_clears_alarm():
    # Once superseded (active=False) by a re-declaration, the contradiction
    # alarm clears -- the re-declaration IS the adjudication.
    chars = [Character(id="ch1", name="Elara")]
    chs = _chapters("One")
    arc = ArcRecord(
        id="a1", character_id="ch1", arc_type="positive", outcome="lie_embraced",
        active=False, resolved=True,
    )
    tab = arcs_tab([arc], chars, chs, [], None)
    assert tab.lines[0].plain == "✓ Elara · positive"
    assert tab.lines[0].style == DIM
    assert tab.alarm_count == 0


def test_arcs_tab_resolved_consistent_folds_to_one_dim_line():
    chars = [Character(id="ch1", name="Elara")]
    chs = _chapters("One")
    arc = ArcRecord(
        id="a1", character_id="ch1", arc_type="positive", outcome="truth_embraced",
        active=False, resolved=True,
    )
    tab = arcs_tab([arc], chars, chs, [], None)
    assert len(tab.lines) == 1
    assert tab.lines[0].plain == "✓ Elara · positive"
    assert tab.lines[0].style == DIM
    assert tab.alarm_count == 0


def test_arcs_tab_pivot_lines_missed_fulfilled_pending():
    chars = [Character(id="ch1", name="Elara")]
    chs = _chapters(*[f"Ch{i}" for i in range(1, 12)])
    blueprint = BlueprintRecord(id="b1", framework="three_act", target_chapter_count=20)
    beats = [
        BeatRecord(id="beat_missed", blueprint_id="b1", slug="missed", name="Missed Beat",
                   ideal_pct=0.35, tolerance_pct=0.05),
        BeatRecord(id="beat_fulfilled", blueprint_id="b1", slug="fulfilled", name="Fulfilled Beat",
                   ideal_pct=0.1, tolerance_pct=0.05, fulfilled_by_chapter_id="c2"),
        BeatRecord(id="beat_pending", blueprint_id="b1", slug="pending", name="Pending Beat",
                   ideal_pct=0.9, tolerance_pct=0.05),
    ]
    arc = ArcRecord(
        id="a1", character_id="ch1", arc_type="positive", active=True, resolved=False,
        last_chapter_id="c4",
        pivots=[
            ArcPivot(beat_id="beat_missed"),
            ArcPivot(beat_id="beat_fulfilled"),
            ArcPivot(beat_id="beat_pending"),
        ],
    )
    tab = arcs_tab([arc], chars, chs, beats, blueprint)
    pivot_lines = {line.plain.split("@ch")[0].strip(): line for line in tab.lines[2:]}
    missed = [l for l in tab.lines if "Missed Beat" in l.plain][0]
    fulfilled = [l for l in tab.lines if "Fulfilled Beat" in l.plain][0]
    pending = [l for l in tab.lines if "Pending Beat" in l.plain][0]
    assert missed.plain.endswith("missed")
    assert missed.style == ALARM_STYLE
    assert fulfilled.plain.endswith("✓")
    assert pending.plain.endswith("pending")
    assert pending.style == DIM
    assert tab.alarm_count == 2  # stagnant (7 chapters since last advance) + missed pivot


def test_arcs_tab_pivot_matching_is_by_beat_id_not_name_prefix():
    # Two beats whose names collide as string prefixes ("Reveal" is a
    # prefix of "Reveal Twist"): matching must be by beat_id, not by
    # detail-string prefix reconstruction, or the wrong pivot gets flagged.
    chars = [Character(id="ch1", name="Elara")]
    chs = _chapters(*[f"Ch{i}" for i in range(1, 12)])
    blueprint = BlueprintRecord(id="b1", framework="three_act", target_chapter_count=20)
    beats = [
        BeatRecord(id="beat_a", blueprint_id="b1", slug="a", name="Reveal",
                   ideal_pct=0.9, tolerance_pct=0.05),
        BeatRecord(id="beat_b", blueprint_id="b1", slug="b", name="Reveal Twist",
                   ideal_pct=0.35, tolerance_pct=0.05),
    ]
    arc = ArcRecord(
        id="a1", character_id="ch1", arc_type="positive", active=True, resolved=False,
        last_chapter_id="c1",
        pivots=[ArcPivot(beat_id="beat_a"), ArcPivot(beat_id="beat_b")],
    )
    tab = arcs_tab([arc], chars, chs, beats, blueprint)
    reveal_line = [l for l in tab.lines if l.plain.strip().startswith("◈ Reveal @ch") or " Reveal @ch" in l.plain][0]
    twist_line = [l for l in tab.lines if "Reveal Twist" in l.plain][0]
    assert reveal_line.plain.endswith("pending")
    assert twist_line.plain.endswith("missed")


def test_arcs_tab_orphaned_pivot_beat_shown_dim_not_dropped():
    chars = [Character(id="ch1", name="Elara")]
    chs = _chapters(*[f"Ch{i}" for i in range(1, 3)])
    blueprint = BlueprintRecord(id="b1", framework="three_act", target_chapter_count=20)
    arc = ArcRecord(
        id="a1", character_id="ch1", arc_type="positive", active=True, resolved=False,
        last_chapter_id="c2",
        pivots=[ArcPivot(beat_id="dead-beat")],
    )
    tab = arcs_tab([arc], chars, chs, [], blueprint)
    orphan = [l for l in tab.lines if "re-pin" in l.plain]
    assert len(orphan) == 1
    assert orphan[0].style == DIM


def test_arcs_tab_alarm_count_equals_len_of_arc_findings():
    from novelizer.brain.arc_alignment import arc_findings

    chars = [Character(id="ch1", name="Elara"), Character(id="ch2", name="Bram")]
    chs = _chapters(*[f"Ch{i}" for i in range(1, 6)])
    arcs = [
        ArcRecord(id="a1", character_id="ch1", arc_type="positive", active=True, resolved=False),
        ArcRecord(
            id="a2", character_id="ch2", arc_type="fall", outcome="truth_embraced",
            active=False, resolved=True,
        ),
    ]
    tab = arcs_tab(arcs, chars, chs, [], None)
    assert tab.alarm_count == len(arc_findings(arcs, chars, chs, [], None))
    assert tab.alarm_count > 0
