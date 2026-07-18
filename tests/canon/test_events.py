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


def test_autonomy_and_proposal_event_types_exist():
    from novelizer.canon.events import EventType
    assert EventType.PROPOSAL_CREATED == "proposal.created"
    assert EventType.PROPOSAL_APPROVED == "proposal.approved"
    assert EventType.PROPOSAL_REJECTED == "proposal.rejected"
    assert EventType.AUTONOMY_CHANGED == "autonomy.changed"


def test_agent_remarked_event_type_exists():
    from novelizer.canon.events import EventType
    assert EventType.AGENT_REMARKED == "agent.remarked"


def test_agent_remark_payload_model_roundtrips():
    from novelizer.canon.events import AgentRemark
    remark = AgentRemark(agent_name="author", note="Another storm, another chapter.")
    again = AgentRemark.model_validate_json(remark.model_dump_json())
    assert again == remark


def test_thread_event_types_exist():
    from novelizer.canon.events import EventType
    assert EventType.THREAD_PLANTED == "thread.planted"
    assert EventType.THREAD_TOUCHED == "thread.touched"
    assert EventType.THREAD_PAID_OFF == "thread.paid_off"
    assert EventType.THREAD_ABANDONED == "thread.abandoned"


def test_thread_payload_models_roundtrip():
    from novelizer.canon.events import ThreadPlanted, ThreadTouched, ThreadPaidOff, ThreadAbandoned
    planted = ThreadPlanted(id="the-locket", name="The Locket", chapter_id="c1", note="introduced")
    assert ThreadPlanted.model_validate_json(planted.model_dump_json()) == planted
    for cls in (ThreadTouched, ThreadPaidOff, ThreadAbandoned):
        inst = cls(id="the-locket", chapter_id="c2", note="advanced")
        assert cls.model_validate_json(inst.model_dump_json()) == inst
