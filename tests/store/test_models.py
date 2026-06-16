import pytest
from datetime import datetime, timezone
from novelizer.store.models import (
    WorldEntry, Character, CharacterRelationship, Event,
    Chapter, RetconRequest, DirectorSignal,
    CanonStatus, EditorialStatus, RetconStatus, SignalKind, Domain,
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
