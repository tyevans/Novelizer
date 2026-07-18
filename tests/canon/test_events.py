from novelizer.canon.events import StoredEvent, EventType


def test_event_type_naming_convention():
    for value in [
        EventType.WORLD_ENTRY_CREATED, EventType.CHARACTER_CREATED,
        EventType.CHAPTER_CREATED, EventType.DIRECTOR_SIGNAL_CREATED,
        EventType.DIRECTOR_SIGNAL_CONSUMED,
    ]:
        domain, _, verb = value.partition(".")
        assert domain and verb, f"{value} must be '<domain>.<verb>'"


def test_stored_event_roundtrips_through_json():
    ev = StoredEvent(
        sequence=1, id="abc", event_type=EventType.CHAPTER_CREATED,
        aggregate_id="ch1", payload={"title": "One"}, created_at="2026-07-17T00:00:00Z",
    )
    again = StoredEvent.model_validate_json(ev.model_dump_json())
    assert again == ev
