import pytest
from datetime import datetime, timezone
from pydantic import ValidationError
from novelizer.store.models import (
    WorldEntry, Character, CharacterRelationship, Event,
    Chapter, Flag, DirectorSignal, ThreadState, ThreadRecord,
    StructureScore,
    CanonStatus, EditorialStatus, FlagStatus, SignalKind, Domain,
    SecretRecord, CausalEdgeRecord, SecretReferenceRecord,
)


def test_world_entry_defaults():
    e = WorldEntry(title="The North", body="Cold and vast.")
    assert e.canon_status == CanonStatus.active
    assert e.domain == Domain.physical
    assert e.supersedes_id is None
    assert isinstance(e.id, str)
    assert isinstance(e.created_at, datetime)


def test_chapter_defaults():
    c = Chapter(title="Ch 1", prose="It began.")
    assert c.editorial_status == EditorialStatus.draft
    assert c.editor_notes is None


def test_retcon_request_defaults():
    r = Flag(
        category="contradiction",
        description="Contradiction in lore",
        related_entry_ids=["a", "b"],
        proposed_resolution="Remove entry a.",
    )
    assert r.status == FlagStatus.open
    assert r.resolved_by is None


def test_director_signal_defaults():
    s = DirectorSignal(kind=SignalKind.seed, body="The empire falls.")
    assert s.consumed is False
    assert s.target_agent is None


def test_director_signal_accepts_persisted_revise_payload():
    """Pin the exact shape the Editor persists for a revise verdict (captured
    from a live story's director_signal.created event log). Persisted events
    must stay readable forever."""
    payload = {
        "id": "f6007beb-6c64-45a2-849a-70f7a39c2ee8",
        "created_at": "2026-07-19T21:17:37.923882Z",
        "kind": "revise",
        "body": "Fix the pacing in the second scene.",
        "target_agent": "author",
        "target_entity": "ad81bf27-b13b-416a-968b-d57c8dae4273",
        "consumed": False,
    }
    s = DirectorSignal.model_validate(payload)
    assert s.kind == SignalKind.revise
    assert s.target_entity == "ad81bf27-b13b-416a-968b-d57c8dae4273"


def test_director_signal_tolerates_unknown_kind():
    """Event-sourced tolerant reader: a kind minted by a newer writer must not
    make persisted signals unreadable (a closed enum here wedged the live
    scheduler every cycle when readers lagged the writer's SignalKind)."""
    s = DirectorSignal.model_validate({"kind": "tempo", "body": "slow down"})
    assert s.kind == "tempo"
    again = DirectorSignal.model_validate_json(s.model_dump_json())
    assert again.kind == "tempo"


def test_director_signal_known_kind_normalizes_to_enum():
    s = DirectorSignal(kind="revise", body="x")
    assert s.kind is SignalKind.revise
    assert isinstance(DirectorSignal.model_validate_json(s.model_dump_json()).kind, SignalKind)


def test_director_signal_non_string_kind_still_rejected():
    with pytest.raises(ValidationError):
        DirectorSignal(kind=3, body="x")


def test_character_voice_defaults_to_empty_string():
    c = Character(name="Mira")
    assert c.voice == ""


def test_character_voice_roundtrips_through_json():
    c = Character(name="Mira", voice="Clipped sentences, never says 'I love you' outright.")
    dumped = c.model_dump_json()
    restored = Character.model_validate_json(dumped)
    assert restored.voice == "Clipped sentences, never says 'I love you' outright."


def test_thread_record_defaults():
    t = ThreadRecord(id="the-locket", name="The Locket")
    assert t.state == ThreadState.planted
    assert t.touch_count == 0
    assert t.last_note == ""
    assert t.last_chapter_id == ""


def test_thread_record_roundtrips_through_json():
    t = ThreadRecord(id="the-locket", name="The Locket", state=ThreadState.touched, touch_count=2, last_note="advanced")
    again = ThreadRecord.model_validate_json(t.model_dump_json())
    assert again == t


def test_structure_score_roundtrips_through_json():
    s = StructureScore(chapter_id="c1", tension=0.6, pacing_label="rising")
    again = StructureScore.model_validate_json(s.model_dump_json())
    assert again == s


def test_structure_score_tension_is_bounded():
    with pytest.raises(ValidationError):
        StructureScore(chapter_id="c1", tension=2.0)


def test_secret_record_defaults():
    s = SecretRecord(id="the-heir-lives", title="The Heir Lives")
    assert s.revealed is False


def test_secret_record_roundtrips_through_json():
    s = SecretRecord(id="the-heir-lives", title="The Heir Lives", revealed=True)
    again = SecretRecord.model_validate_json(s.model_dump_json())
    assert again == s


def test_causal_edge_record_defaults_and_roundtrips():
    e = CausalEdgeRecord(cause_chapter_id="c1", effect_chapter_id="c3")
    assert e.note == ""
    again = CausalEdgeRecord.model_validate_json(e.model_dump_json())
    assert again == e


def test_secret_reference_record_defaults_and_roundtrips():
    r = SecretReferenceRecord(secret_id="the-heir-lives", character_id="mara", chapter_id="c3")
    assert r.note == ""
    again = SecretReferenceRecord.model_validate_json(r.model_dump_json())
    assert again == r


def test_world_entry_retired_payload_and_status():
    from novelizer.canon.events import EventType, WorldEntryRetired
    from novelizer.store.models import CanonStatus

    assert EventType.WORLD_ENTRY_RETIRED == "world_entry.retired"
    assert CanonStatus.retired == "retired"
    p = WorldEntryRetired(entry_id="w1", reason="no longer serves the story", flag_id="f1")
    assert p.entry_id == "w1"
    assert p.reason == "no longer serves the story"
    assert p.flag_id == "f1"
    # defaults
    assert WorldEntryRetired(entry_id="w2").reason == ""
    assert WorldEntryRetired(entry_id="w2").flag_id == ""
