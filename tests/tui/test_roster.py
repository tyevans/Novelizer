from novelizer.canon.autonomy import AutonomyLevel, AutonomyState
from novelizer.tui.widgets.roster import dial_meter, status_strip


def _row(name, paused=False, running=False, last_error=None):
    return {"name": name, "paused": paused, "running": running, "last_error": last_error,
            "last_completed": False, "run_count": 0, "next_ready_in": 0.0}


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
