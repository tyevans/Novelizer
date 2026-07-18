from novelizer.tui.app import format_event
from novelizer.canon.events import StoredEvent, EventType


def test_format_chapter_created_mentions_title():
    ev = StoredEvent(sequence=1, id="e1", event_type=EventType.CHAPTER_CREATED,
                     aggregate_id="c1", payload={"title": "The Salt Road"}, created_at="t")
    line = format_event(ev)
    assert "The Salt Road" in line and "Author" in line


def test_format_director_signal_created_mentions_body():
    ev = StoredEvent(sequence=2, id="e2", event_type=EventType.DIRECTOR_SIGNAL_CREATED,
                     aggregate_id="s1", payload={"body": "a storm is coming"}, created_at="t")
    assert "a storm is coming" in format_event(ev)


def test_format_retcon_created_labels_retcon():
    from novelizer.tui.app import format_event
    from novelizer.canon.events import StoredEvent, EventType
    ev = StoredEvent(sequence=1, id="e", event_type=EventType.RETCON_REQUEST_CREATED,
                     aggregate_id="r1", payload={"description": "scar mismatch"}, created_at="t")
    line = format_event(ev)
    assert "scar mismatch" in line and "Retcon" in line


def test_format_chapter_status_changed_labels_editor():
    from novelizer.tui.app import format_event
    from novelizer.canon.events import StoredEvent, EventType
    ev = StoredEvent(sequence=2, id="e", event_type=EventType.CHAPTER_STATUS_CHANGED,
                     aggregate_id="c1", payload={"title": "One", "editorial_status": "reviewed"}, created_at="t")
    line = format_event(ev)
    assert "One" in line and "Editor" in line


def test_status_line_shows_real_autonomy_level():
    from novelizer.tui.app import _status_line
    from novelizer.canon.autonomy import AutonomyLevel, AutonomyState
    line = _status_line(AutonomyState(global_level=AutonomyLevel.gated_canon))
    assert "gated_canon" in line
    assert "full-auto" not in line


def test_status_line_summarizes_overrides():
    from novelizer.tui.app import _status_line
    from novelizer.canon.autonomy import AutonomyLevel, AutonomyState
    line = _status_line(AutonomyState(global_level=AutonomyLevel.full_auto,
                                       overrides={"retconner": AutonomyLevel.gated_all}))
    assert "retconner=gated_all" in line
