import pytest
from datetime import datetime, timezone
from pydantic import ValidationError
from novelizer.store.models import (
    WorldEntry, Character, CharacterRelationship, Event,
    Chapter, RetconRequest, DirectorSignal, ThreadState, ThreadRecord,
    StructureScore,
    CanonStatus, EditorialStatus, RetconStatus, SignalKind, Domain,
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
    r = RetconRequest(
        description="Contradiction in lore",
        conflicting_entry_ids=["a", "b"],
        proposed_resolution="Remove entry a.",
    )
    assert r.status == RetconStatus.open
    assert r.resolved_by is None


def test_director_signal_defaults():
    s = DirectorSignal(kind=SignalKind.seed, body="The empire falls.")
    assert s.consumed is False
    assert s.target_agent is None


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
