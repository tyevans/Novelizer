from novelizer.agents.schemas import (
    WorldEntryDraft, WorldEntriesDraft, CharacterUpdate, RetconDraft,
    KeeperOutput, EditorVerdict, ContinuityOutput, RetconAmendments,
)


def test_world_entries_draft_roundtrip():
    d = WorldEntriesDraft(entries=[WorldEntryDraft(title="Brinemarsh", body="salt flats")])
    again = WorldEntriesDraft.model_validate_json(d.model_dump_json())
    assert again.entries[0].domain == "physical"


def test_keeper_output_defaults_empty():
    k = KeeperOutput()
    assert k.updated_characters == [] and k.retcon_requests == []


def test_editor_verdict_literal():
    assert EditorVerdict(verdict="revise", notes="tighten act two").verdict == "revise"


def test_retcon_amendments_carry_supersedes():
    a = RetconAmendments(amended_entries=[WorldEntryDraft(title="North", body="v2", supersedes_id="old1")])
    assert a.amended_entries[0].supersedes_id == "old1"


def test_continuity_and_character_shapes():
    ContinuityOutput(retcon_requests=[RetconDraft(description="x", proposed_resolution="y")])
    assert CharacterUpdate(id="c1", arc_status="wary").traits is None
