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


def test_format_agent_remarked_renders_personality_voiced_line():
    from novelizer.tui.app import format_event
    from novelizer.canon.events import StoredEvent, EventType
    ev = StoredEvent(sequence=1, id="e1", event_type=EventType.AGENT_REMARKED,
                     aggregate_id="author", payload={"agent_name": "author", "note": "Another storm, another chapter."},
                     created_at="t")
    line = format_event(ev)
    assert "Another storm, another chapter." in line
    assert "Author" in line
    assert "💬" in line


def test_format_agent_remarked_labels_each_agent_distinctly():
    from novelizer.tui.app import format_event
    from novelizer.canon.events import StoredEvent, EventType
    for agent_name, expected_label in [
        ("author", "Author"), ("editor", "Editor"), ("world_architect", "Architect"),
        ("character_keeper", "Keeper"), ("continuity_checker", "Continuity"), ("retconner", "Retconner"),
    ]:
        ev = StoredEvent(sequence=1, id="e1", event_type=EventType.AGENT_REMARKED,
                         aggregate_id=agent_name, payload={"agent_name": agent_name, "note": "hm."},
                         created_at="t")
        assert expected_label in format_event(ev)


def test_format_agent_remarked_falls_back_for_unknown_agent_name():
    from novelizer.tui.app import format_event
    from novelizer.canon.events import StoredEvent, EventType
    ev = StoredEvent(sequence=1, id="e1", event_type=EventType.AGENT_REMARKED,
                     aggregate_id="mystery_agent", payload={"agent_name": "mystery_agent", "note": "?"},
                     created_at="t")
    assert "Mystery Agent" in format_event(ev)


def test_room_toggle_still_works_after_agent_remarked_rendering_change():
    # action_toggle_room is a pure CSS-class toggle on #body; regression guard
    # that adding the agent.remarked branch to format_event didn't touch it.
    import inspect
    from novelizer.tui.app import NovelizerApp
    src = inspect.getsource(NovelizerApp.action_toggle_room)
    assert 'toggle_class("room")' in src
