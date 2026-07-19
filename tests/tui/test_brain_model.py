from hypothesis import given, strategies as st

from novelizer.store.models import Chapter, StructureScore, ThreadRecord, ThreadState
from novelizer.tui.widgets.brain_model import (
    SHAPE_EMPTY,
    SHAPE_GUTTER,
    SPARK_LEVELS,
    THREADS_EMPTY,
    chapter_label,
    chapter_number,
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


from novelizer.store.models import CausalEdgeRecord, Character, SecretRecord
from novelizer.tui.widgets.brain_model import (
    CELL_GLYPHS,
    CAUSEWAY_EMPTY,
    SECRETS_EMPTY,
    TITLE_WIDTH,
    alarm_strip,
    causeway_tab,
    char_initials,
    matrix_header,
    secret_row,
    secrets_tab,
)


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


def test_secret_row_glyph_cells_align_under_header_and_count_knowers():
    chars = [Character(id="elara", name="Elara"), Character(id="boy", name="The Boy")]
    secret = SecretRecord(id="the-heir-lives", title="The Heir Lives")
    matrix = {"the-heir-lives": {"revealed": False, "known_by": {"elara"}}}
    row = secret_row(secret, chars, matrix)
    assert row.plain == "The Heir Lives".ljust(TITLE_WIDTH) + "●  ○" + "   1 knows"
    assert "the-heir-lives" not in row.plain


def test_secret_row_known_to_no_one():
    secret = SecretRecord(id="s", title="The Map Is Forged")
    matrix = {"s": {"revealed": False, "known_by": set()}}
    row = secret_row(secret, [Character(id="k", name="Kestrel")], matrix)
    assert row.plain.endswith("no one knows")
    assert "●" not in row.plain and "○" in row.plain


def test_secret_row_plural_summary_matches_spec_sketch():
    chars = [Character(id="a", name="Ana"), Character(id="b", name="Bram"), Character(id="c", name="Cole")]
    secret = SecretRecord(id="s", title="The Tide Debt")
    matrix = {"s": {"revealed": False, "known_by": {"a", "b"}}}
    assert secret_row(secret, chars, matrix).plain.endswith("2 know")


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
        assert row.plain.count("○") + row.plain.count("●") == n_chars


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


def test_alarm_strip_matches_spec_format():
    assert alarm_strip(1, 2, 0, 1).plain == "Shape ⚠1 · Threads ⚠2 · Secrets · Cause ⚠1"


def test_alarm_strip_quiet_shows_bare_labels():
    assert alarm_strip(0, 0, 0, 0).plain == "Shape · Threads · Secrets · Cause"


def test_alarm_strip_alarm_segments_are_alarm_styled():
    strip = alarm_strip(1, 0, 0, 0)
    spans = [(strip.plain[s.start:s.end], str(s.style)) for s in strip.spans]
    assert (" ⚠1", ALARM_STYLE) in spans
