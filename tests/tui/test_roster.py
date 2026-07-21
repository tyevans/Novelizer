from hypothesis import given, strategies as st

from novelizer.tui.identity import identity_for
from novelizer.tui.widgets.feed_model import ALARM_STYLE
from novelizer.tui.widgets.roster import (
    ERROR_MARK,
    IDLE_MARK,
    PAUSED_MARK,
    RUNNING_MARK,
    roster_glyphs,
    roster_summary,
)

CAST = (
    "author", "editor", "world_architect", "character_keeper",
    "continuity_checker", "retconner", "structure_analyst",
)


def _row(name, paused=False, running=False, last_error=None):
    # Real Scheduler.status() shape incl. the M5.3 fields the strip ignores.
    return {
        "name": name, "paused": paused, "running": running, "last_error": last_error,
        "last_completed": False, "run_count": 0, "next_ready_in": 0.0,
    }


def test_no_agents_renders_dim_placeholder():
    strip = roster_glyphs([])
    assert strip.plain == "no agents"
    assert str(strip.style) == "dim"
    assert roster_summary([]) == "no agents"


def test_idle_cast_renders_every_glyph_with_idle_mark():
    strip = roster_glyphs([_row(n) for n in CAST])
    assert strip.plain == "✎· §· ⌂· ♥· ⚖· ↺· ∿·"


def test_running_agent_carries_spinner_mark():
    strip = roster_glyphs([_row("author", running=True), _row("editor")])
    assert strip.plain == f"✎{RUNNING_MARK} §{IDLE_MARK}"


def test_paused_agent_carries_pause_mark():
    strip = roster_glyphs([_row("editor", paused=True)])
    assert strip.plain == f"§{PAUSED_MARK}"


def test_errored_agent_carries_alarm_mark_without_error_text():
    strip = roster_glyphs([_row("author", last_error="RuntimeError: boom" * 10)])
    assert strip.plain == f"✎{ERROR_MARK}"
    assert "boom" not in strip.plain  # errors land in the feed, not the bar


def test_error_wins_over_paused_and_running():
    strip = roster_glyphs([_row("author", paused=True, running=True, last_error="x")])
    assert strip.plain == f"✎{ERROR_MARK}"


def test_paused_wins_over_running():
    strip = roster_glyphs([_row("author", paused=True, running=True)])
    assert strip.plain == f"✎{PAUSED_MARK}"


def test_glyph_takes_agent_color_and_error_mark_takes_alarm_style():
    strip = roster_glyphs([_row("author", last_error="x")])
    styles = [(strip.plain[s.start:s.end], str(s.style)) for s in strip.spans]
    assert ("✎", identity_for("author").style) in styles
    assert (ERROR_MARK, ALARM_STYLE) in styles


def test_running_mark_takes_the_agent_color():
    strip = roster_glyphs([_row("retconner", running=True)])
    styles = [(strip.plain[s.start:s.end], str(s.style)) for s in strip.spans]
    assert (RUNNING_MARK, identity_for("retconner").style) in styles


@given(
    st.lists(
        st.tuples(st.sampled_from(CAST), st.booleans(), st.booleans(),
                  st.one_of(st.none(), st.just("err"))),
        max_size=8,
    )
)
def test_summary_is_the_plain_strip_and_one_cell_pair_per_agent(rows):
    status = [_row(n, paused=p, running=r, last_error=e) for n, p, r, e in rows]
    strip = roster_glyphs(status)
    assert roster_summary(status) == strip.plain
    if status:
        # glyph+mark per agent, single-space-joined: 2 cells per agent + gaps
        assert len(strip.plain) == 3 * len(status) - 1


from novelizer.canon.autonomy import AutonomyLevel, AutonomyState
from novelizer.tui.widgets.roster import (
    dial_meter,
    status_strip,
)


def test_dial_meter_gated_canon_is_two_filled_segments():
    meter = dial_meter(AutonomyState(global_level=AutonomyLevel.gated_canon))
    assert meter.plain == "AUTONOMY ▮▮▯▯ gated_canon"


def test_dial_meter_full_auto_all_filled_and_gated_all_one_filled():
    full = dial_meter(AutonomyState(global_level=AutonomyLevel.full_auto))
    assert full.plain == "AUTONOMY ▮▮▮▮ full_auto"
    floor = dial_meter(AutonomyState(global_level=AutonomyLevel.gated_all))
    assert floor.plain == "AUTONOMY ▮▯▯▯ gated_all"
    mid = dial_meter(AutonomyState(global_level=AutonomyLevel.gated_retcons))
    assert mid.plain == "AUTONOMY ▮▮▮▯ gated_retcons"


def test_dial_meter_color_steps_with_trust():
    full = dial_meter(AutonomyState(global_level=AutonomyLevel.full_auto))
    styles = [(full.plain[s.start:s.end], str(s.style)) for s in full.spans]
    assert ("▮▮▮▮", "green3") in styles
    floor = dial_meter(AutonomyState(global_level=AutonomyLevel.gated_all))
    styles = [(floor.plain[s.start:s.end], str(s.style)) for s in floor.spans]
    assert ("▮", "red") in styles


def test_dial_meter_summarizes_overrides_compactly():
    meter = dial_meter(AutonomyState(
        global_level=AutonomyLevel.full_auto,
        overrides={"retconner": AutonomyLevel.gated_all},
    ))
    assert meter.plain == "AUTONOMY ▮▮▮▮ full_auto (retconner=gated_all)"


def test_status_strip_composes_roster_then_dial():
    strip = status_strip([_row("author")], AutonomyState(global_level=AutonomyLevel.gated_canon))
    assert strip.plain == "✎·    AUTONOMY ▮▮▯▯ gated_canon"
